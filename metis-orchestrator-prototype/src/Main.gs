/**
 * Main.gs — puntos de entrada MANUALES del prototipo.
 *
 * CERO TRIGGERS. Este proyecto no instala ni programa nada:
 *   - no existe ninguna llamada a `ScriptApp.newTrigger(...)`;
 *   - no hay funciones `onOpen`, `onEdit`, `onFormSubmit`, `doGet` ni `doPost`;
 *   - `appsscript.json` no declara scope de `script.scriptapp`, de modo que el
 *     proyecto ni siquiera está autorizado a crear un trigger;
 *   - todas las funciones de abajo se ejecutan a mano desde el editor.
 *
 * CERO ESCRITURAS PRODUCTIVAS. Los scopes declarados son `drive.readonly`,
 * `calendar.readonly` y `script.external_request`. No hay scope de escritura de
 * Drive, Calendar ni Gmail, y las únicas herramientas de mutación expuestas son
 * `simulate.*`, que no tocan ningún endpoint.
 */

/**
 * Corrida manual del prototipo.
 * Sin `options.providers`, no hay proveedores: la función lo dice y no llama a
 * ninguna API. Para una corrida real, pasar adaptadores creados con
 * `OpenAIAdapter.create()` / `AnthropicAdapter.create()`.
 */
function runPrototype(operatorRequest, options) {
  var opts = options || {};
  if (!opts.providers) {
    return {
      status: 'NO_EJECUTADO',
      reason: 'Sin adaptadores de proveedor. Ver README → Corrida manual.',
      level: Config.runLevel()
    };
  }
  var result = Orchestrator.run(operatorRequest, opts);
  Logger.log(JSON.stringify(result, null, 2));
  return result;
}

/** Corrida manual con proveedores reales (requiere Script Properties cargadas). */
function runPrototypeLive(operatorRequest, options) {
  var opts = options || {};
  opts.providers = {
    OPENAI: OpenAIAdapter.create(),
    ANTHROPIC: AnthropicAdapter.create()
  };
  return runPrototype(operatorRequest, opts);
}

/** Ejecuta los tests unitarios (spec §17). */
function runUnitTests() {
  var report = TestRunner.runUnitTests();
  Logger.log(TestRunner.render(report));
  return report;
}

/** Ejecuta los 10 casos de aceptación (spec §16). */
function runAcceptanceCases() {
  var report = TestRunner.runAcceptance();
  Logger.log(TestRunner.render(report));
  return report;
}

/** Ejecuta todo y devuelve el veredicto de PASS/FAIL (spec §18). */
function runAllTests() {
  var report = TestRunner.runAll();
  Logger.log(TestRunner.render(report));
  return report;
}

/**
 * Diagnóstico de configuración. Informa PRESENCIA de cada Script Property,
 * nunca su valor (spec §12).
 */
function checkConfiguration() {
  var secrets = {};
  Object.keys(Config.SECRET_PROPERTY_NAMES).forEach(function (symbolic) {
    secrets[symbolic] = { property: Config.SECRET_PROPERTY_NAMES[symbolic], present: Config.hasSecret(symbolic) };
  });
  var settings = {};
  Object.keys(Config.SETTING_PROPERTY_NAMES).forEach(function (symbolic) {
    settings[symbolic] = {
      property: Config.SETTING_PROPERTY_NAMES[symbolic],
      present: !!Config.setting(symbolic)
    };
  });

  // Qué contextos tienen partición declarada y sobre qué fuentes. Sólo cuenta
  // contenedores; no imprime ids.
  var partitions = {};
  Config.contextNames().forEach(function (context) {
    var sources = {};
    ['NOTION', 'ASANA', 'DRIVE', 'CALENDAR'].forEach(function (source) {
      var partition = Config.partitionFor(context, source);
      sources[source] = partition ? _containerCount(partition) : 0;
    });
    partitions[context] = sources;
  });

  var report = {
    level: Config.runLevel(),
    real_reads_enabled: Config.levelAllowsRealReads(),
    secrets: secrets,
    settings: settings,
    partitions: partitions,
    limits: Config.limits(),
    pricing_configured: !!Config.priceFor('OPENAI', Config.PROVIDERS.OPENAI.model) ||
                        !!Config.priceFor('ANTHROPIC', Config.PROVIDERS.ANTHROPIC.model),
    triggers_declared: 0,
    productive_write_tools: 0,
    simulated_write_tools: AuthorityPolicy.SIMULATED_WRITE_TOOLS.length,
    forbidden_surfaces: Config.FORBIDDEN_SURFACES
  };
  Logger.log(JSON.stringify(report, null, 2));
  return report;
}

function _containerCount(partition) {
  return (partition.data_sources || partition.project_gids ||
          partition.folder_ids || partition.calendar_ids || []).length;
}

/** Estados de una comprobación del smoke test. */
var SMOKE_STATUS = {
  PASS: 'PASS',                    // demostrado con al menos un resultado real
  FAIL: 'FAIL',                    // algo salió mal, o el canario no apareció
  NO_DEMOSTRADO: 'NO_DEMOSTRADO',  // no hubo error, pero tampoco evidencia
  OMITIDA: 'OMITIDA',              // no se leyó por diseño (sin partición)
  INFO: 'INFO'                     // informativo; nunca decide el veredicto
};

/**
 * Evalúa un canario contra los documentos ADMITIDOS en la sesión.
 *
 * Un canario es un objeto que el operador sabe que existe y que la fuente
 * DEBE devolver. Sin él, "0 resultados" es indistinguible de "la partición
 * apunta a un contenedor vacío", de "el filtro por Proyecto no casa" y de
 * "la credencial no ve nada": los tres devuelven cero y ninguno es un PASS.
 *
 * `canary` admite:
 *   { query, min_results, expect_id, expect_title_contains,
 *     forbid_id, forbid_title_contains, expect_exactly }
 * Todos opcionales; `min_results` vale 1 por defecto, así que incluso sin
 * canario declarado hace falta AL MENOS UN resultado real para validar.
 *
 * CANARIO NEGATIVO (`forbid_id`, `forbid_title_contains`): un objeto que el
 * operador sabe que existe en OTRO contexto y que esta corrida NO debe ver
 * nunca. Hace falta porque el contador de descartes no cubre todos los casos:
 * sólo detecta objetos ajenos que la fuente devolvió Y que el ToolBroker supo
 * clasificar como ajenos. Un objeto ajeno que llegue con contexto nulo o mal
 * derivado se ADMITE sin descarte, y entonces "descartes = 0" se lee como
 * aislamiento correcto cuando es exactamente lo contrario. El canario negativo
 * comprueba la ausencia directamente, sobre los admitidos, sin depender de que
 * la capa que falló se autodenuncie.
 *
 * `expect_exactly` fija el número exacto de admitidos cuando el operador conoce
 * la cardinalidad real de la partición para esa consulta. Un conteo de más es
 * fuga; uno de menos es sobre-filtrado. Ambos son fallos y ninguno se ve con
 * `min_results`, que sólo pone un piso.
 *
 * @return {{status:string, detail:string}}
 */
function smokeEvaluateCanary(canary, admitted) {
  var c = canary || {};
  var min = (typeof c.min_results === 'number') ? c.min_results : 1;

  // Los canarios negativos se evalúan PRIMERO y sobre el conjunto completo de
  // admitidos: una fuga es un fallo aunque el resto de la sonda no demuestre
  // nada. Nunca puede quedar tapada por un NO_DEMOSTRADO.
  if (c.forbid_id) {
    var prohibidos = admitted.filter(function (d) { return d.id === c.forbid_id; });
    if (prohibidos.length) {
      return {
        status: SMOKE_STATUS.FAIL,
        detail: 'FUGA DE CONTEXTO: el objeto `' + c.forbid_id +
                '` pertenece a otro contexto y fue ADMITIDO en esta sesión.'
      };
    }
  }

  if (c.forbid_title_contains) {
    var vetado = ContextResolver.normalize(c.forbid_title_contains);
    var coincidencias = admitted.filter(function (d) {
      return ContextResolver.normalize(d.title || '').indexOf(vetado) !== -1;
    });
    if (coincidencias.length) {
      return {
        status: SMOKE_STATUS.FAIL,
        detail: 'FUGA DE CONTEXTO: ' + coincidencias.length + ' admitido(s) contienen "' +
                c.forbid_title_contains + '", que sólo existe en otro contexto.'
      };
    }
  }

  if (typeof c.expect_exactly === 'number' && admitted.length !== c.expect_exactly) {
    return {
      status: SMOKE_STATUS.FAIL,
      detail: 'cardinalidad incorrecta: admitidos ' + admitted.length +
              ', esperados exactamente ' + c.expect_exactly +
              (admitted.length > c.expect_exactly
                ? '. De más: la partición deja pasar objetos ajenos.'
                : '. De menos: la partición filtra objetos propios.')
    };
  }

  if (admitted.length < min) {
    return {
      status: SMOKE_STATUS.NO_DEMOSTRADO,
      detail: 'admitidos ' + admitted.length + ', se exigían ' + min +
              '. Sin resultado real no se puede afirmar que la fuente esté bien leída.'
    };
  }

  if (c.expect_id) {
    var porId = admitted.filter(function (d) { return d.id === c.expect_id; });
    if (!porId.length) {
      return {
        status: SMOKE_STATUS.FAIL,
        detail: 'el canario `' + c.expect_id + '` NO está entre los ' +
                admitted.length + ' admitidos: la fuente devuelve algo, pero no lo esperado.'
      };
    }
  }

  if (c.expect_title_contains) {
    var aguja = ContextResolver.normalize(c.expect_title_contains);
    var porTitulo = admitted.filter(function (d) {
      return ContextResolver.normalize(d.title || '').indexOf(aguja) !== -1;
    });
    if (!porTitulo.length) {
      return {
        status: SMOKE_STATUS.FAIL,
        detail: 'ningún admitido contiene "' + c.expect_title_contains + '" en el título'
      };
    }
  }

  return {
    status: SMOKE_STATUS.PASS,
    detail: 'admitidos: ' + admitted.length +
            (typeof c.expect_exactly === 'number' ? ' (cardinalidad exacta esperada)' : '') +
            (c.expect_id ? ', canario positivo `' + c.expect_id + '` presente' : '') +
            (c.expect_title_contains ? ', título esperado presente' : '') +
            (c.forbid_id ? ', canario negativo `' + c.forbid_id + '` ausente' : '') +
            (c.forbid_title_contains ? ', título vetado ausente' : '')
  };
}

/**
 * Smoke test de Nivel 1: una lectura real acotada por fuente y contexto.
 *
 * Qué hace: comprueba el nivel y las particiones declaradas; después hace UNA
 * lectura acotada por cada (contexto, fuente) con partición declarada, incluida
 * `calendar.read` sobre una ventana temporal corta (`calendar_window_days`, 7
 * por defecto).
 *
 * Qué NO hace: no invoca ningún modelo, no construye plan, no simula ninguna
 * acción y no escribe absolutamente nada. Es sólo lectura.
 *
 * Qué VALIDA: los documentos realmente ADMITIDOS en `session.documents`, no el
 * array crudo del adaptador. Y exige **al menos un resultado real admitido**
 * para dar una fuente por validada: cero resultados es `NO_DEMOSTRADO`, nunca
 * `PASS`. Una fuente vacía y una fuente mal apuntada se ven igual desde fuera,
 * y sólo una de las dos es aceptable.
 *
 * Canarios (`opts.canaries`): objetos que el operador sabe que existen.
 *
 *   smokeTestLevel1({
 *     contexts: ['METIS'],
 *     canaries: {
 *       METIS: {
 *         'notion.search':    { query: 'gate', expect_id: '<page-id>' },
 *         'notion.decisions': { min_results: 3 },
 *         'asana.search':     { query: 'registrar', expect_title_contains: 'gobernanza' },
 *         'drive.search':     { query: 'notas' },
 *         'calendar.read':    { min_results: 1 }
 *       }
 *     }
 *   });
 *
 * Si una fuente devuelve resultados pero NO el canario, es `FAIL`: está
 * leyendo, pero no lo que se creía.
 *
 * Veredicto global (`report.status`): `FAIL` si algo falló; si no,
 * `NO_DEMOSTRADO` si alguna fuente quedó sin demostrar o si no se validó
 * ninguna; `PASS` sólo cuando todo lo leído quedó demostrado.
 * `report.ok` es true únicamente con `PASS`.
 *
 * Devuelve conteos y errores redactados; nunca contenido recuperado.
 */
function smokeTestLevel1(options) {
  var opts = options || {};
  var report = {
    level: Config.runLevel(),
    started_at: Schemas.nowIso(),
    status: SMOKE_STATUS.PASS,
    ok: true,
    validated: 0,
    undemonstrated: 0,
    failed: 0,
    omitted: 0,
    checks: [],
    models_invoked: 0,
    writes_attempted: 0
  };

  function check(name, status, detail) {
    report.checks.push({ check: name, status: status, detail: detail === undefined ? null : detail });
    if (status === SMOKE_STATUS.FAIL) { report.failed++; }
    else if (status === SMOKE_STATUS.NO_DEMOSTRADO) { report.undemonstrated++; }
    else if (status === SMOKE_STATUS.OMITIDA) { report.omitted++; }
    else if (status === SMOKE_STATUS.PASS) { report.validated++; }
  }

  function finish() {
    // Sin ninguna fuente validada no hay nada demostrado, aunque nada haya
    // fallado: un smoke test que no leyó nada no es un smoke test verde.
    if (report.failed > 0) {
      report.status = SMOKE_STATUS.FAIL;
    } else if (report.undemonstrated > 0 || report.validated === 0) {
      report.status = SMOKE_STATUS.NO_DEMOSTRADO;
    } else {
      report.status = SMOKE_STATUS.PASS;
    }
    report.ok = (report.status === SMOKE_STATUS.PASS);
    report.finished_at = Schemas.nowIso();
    Logger.log(JSON.stringify(report, null, 2));
    return report;
  }

  if (!Config.levelAllowsRealReads()) {
    check('nivel', SMOKE_STATUS.FAIL, 'RUN_LEVEL es ' + Config.runLevel() +
      '. Cambia Config.RUN_LEVEL a LEVEL_1 para ejecutar este smoke test.');
    return finish();
  }
  check('nivel', SMOKE_STATUS.INFO, Config.runLevel());

  // La presencia de credencial es informativa: la prueba real es si la lectura
  // funciona. Una credencial cargada que no ve nada seguiría siendo un fallo.
  ['NOTION_API_KEY', 'ASANA_API_KEY'].forEach(function (key) {
    check('credencial:' + key, SMOKE_STATUS.INFO, Config.hasSecret(key) ? 'presente' : 'ausente');
  });

  var contexts = opts.contexts ? opts.contexts : Config.declaredPartitionContexts();
  if (!contexts.length) {
    check('particiones_declaradas', SMOKE_STATUS.FAIL, 'ningún contexto tiene partición declarada');
    return finish();
  }
  check('particiones_declaradas', SMOKE_STATUS.INFO, contexts.join(', '));

  var canaries = opts.canaries || {};
  var declaredCanaries = 0;
  Object.keys(canaries).forEach(function (ctx) {
    declaredCanaries += Object.keys(canaries[ctx] || {}).length;
  });
  check('canarios_declarados', SMOKE_STATUS.INFO, String(declaredCanaries) +
    (declaredCanaries ? '' : ' — sin canarios sólo se exige >=1 resultado por fuente'));

  // Ventana temporal ACOTADA para Calendar: nunca "todo el calendario".
  var windowDays = (typeof opts.calendar_window_days === 'number') ? opts.calendar_window_days : 7;
  var nowMs = Schemas.nowMs();
  var timeWindow = {
    start: Schemas.toIso(new Date(nowMs)),
    end: Schemas.toIso(new Date(nowMs + windowDays * 24 * 60 * 60 * 1000))
  };

  contexts.forEach(function (context) {
    var session = ToolBroker.newSession({ execution_id: 'smoke-' + context },
      ContextResolver.scopeFor([context], context),
      AuthorityPolicy.preRetrievalGrant([context]));
    var contextCanaries = canaries[context] || {};

    [['notion.search', { page_size: 3 }],
     // Sin `page_size`: `notion.decisions` es EXHAUSTIVO por diseño — recorre
     // todas las páginas o falla, porque la cadena de vigencia puede cruzarlas.
     // Un page_size pequeño no acorta la sonda, sólo multiplica las páginas
     // hasta chocar con el tope defensivo (164 filas de METIS a 5 por página
     // son 33 páginas, y el tope es 20). El default de 100 las resuelve en 2.
     ['notion.decisions', {}],
     ['asana.search', { page_size: 3 }],
     ['drive.search', { page_size: 3 }],
     ['calendar.read', { time_window: timeWindow }]
    ].forEach(function (pair) {
      var tool = pair[0];
      var etiqueta = context + '/' + tool;

      if (!Config.partitionFor(context, ToolBroker.READ_TOOLS[tool].source)) {
        check(etiqueta, SMOKE_STATUS.OMITIDA, 'sin partición declarada: no se lee (correcto)');
        return;
      }

      var canary = contextCanaries[tool];
      var args = {};
      Object.keys(pair[1]).forEach(function (k) { args[k] = pair[1][k]; });
      if (canary && canary.query !== undefined) { args.query = canary.query; }
      else if (opts.query !== undefined) { args.query = opts.query; }

      // Lo que cuenta es lo que ENTRÓ a la sesión, no lo que devolvió la fuente.
      var before = session.documents.length;
      try {
        var result = ToolBroker.invoke(session, tool, args);
        var admitted = session.documents.slice(before);
        var returned = (result.returned_by_source === undefined) ? admitted.length : result.returned_by_source;
        var discarded = returned - admitted.length;

        if (result.ok === false) {
          check(etiqueta, SMOKE_STATUS.FAIL, result.error);
          return;
        }
        if (discarded > 0) {
          check(etiqueta, SMOKE_STATUS.FAIL,
            discarded + ' de ' + returned + ' resultados DESCARTADOS por contexto: la partición no acotó en origen');
          return;
        }
        var veredicto = smokeEvaluateCanary(canary, admitted);
        check(etiqueta, veredicto.status, veredicto.detail);
      } catch (e) {
        check(etiqueta, SMOKE_STATUS.FAIL, (e.code ? e.code : 'ERROR') + ': ' + Errors.redactText(e.message));
      }
    });

    // Contaminación: cualquier descarte por contexto es un fallo del smoke test.
    var descartes = session.risk_signals.filter(function (s) {
      return s.signal === 'CROSS_CONTEXT_RESULT_DISCARDED';
    });
    check(context + '/sin_contaminacion',
      descartes.length === 0 ? SMOKE_STATUS.INFO : SMOKE_STATUS.FAIL,
      descartes.length ? (descartes.length + ' resultados de otro contexto llegaron de la fuente')
                       : 'ningún resultado ajeno llegó de la fuente');

    check(context + '/documentos_en_sesion', SMOKE_STATUS.INFO,
      'total admitido: ' + session.documents.length +
      ', evidencias: ' + session.evidence_refs.length);

    // Vigencia: sólo se reporta si el registro devolvió decisiones.
    if (session.decisions.length) {
      var registry = RetrievalPolicy.currentDecisions(session.decisions);
      check(context + '/vigencia',
        registry.conflicting.length === 0 ? SMOKE_STATUS.INFO : SMOKE_STATUS.FAIL,
        'decisiones: ' + session.decisions.length +
        ', vigentes: ' + registry.current.length +
        ', con Estado vacío: ' + registry.default_applied_count +
        ', contradictorias: ' + registry.conflicting.length);
    }
  });

  return finish();
}

/**
 * PRIMER ENSAYO REAL — smoke test de Nivel 1 acotado a METIS.
 *
 * Ejecútala a mano desde el editor. Es la única función que necesitas correr.
 *
 * Alcance deliberado del primer ensayo:
 *   - contexto: METIS y sólo METIS;
 *   - fuentes: Notion y Asana. Drive y Calendar quedan OMITIDAS porque no se
 *     declara partición para ellas, y sin partición no se leen (fail closed);
 *   - canarios: dos objetos verificados que las fuentes DEBEN devolver.
 *
 * Cero modelos, cero escrituras. No se invoca OpenAI ni Anthropic: este camino
 * no pasa por `Orchestrator.run` ni toca ningún ProviderAdapter.
 *
 * `expect_id` de Asana va como CADENA: el adaptador normaliza `gid` a string y
 * la comparación es estricta. Un número aquí nunca casaría.
 */
function smokeMetisPrimerEnsayo() {
  return smokeTestLevel1({
    contexts: ['METIS'],
    canaries: {
      METIS: {
        'notion.search': {
          query: 'Auditoría cruzada PAC demolición',
          expect_id: '3c7df4e9-5ef0-8133-a653-c270d1f26e41',
          min_results: 1
        },
        'notion.decisions': {
          min_results: 1
        },
        'asana.search': {
          query: 'Diseñar orquestación inter-modelo',
          expect_id: '1216646666201920',
          min_results: 1
        }
      }
    }
  });
}

/** Poda del ledger por retención (spec §5). Manual, nunca programada. */
function purgeLedger() {
  var removed = Ledger.purge();
  Logger.log('Entradas de ledger podadas: ' + removed);
  return removed;
}

/**
 * ENSAYO DE AISLAMIENTO MULTICONTEXTO — Metis y Shokko, Nivel 1.
 *
 * Ejecútala a mano desde el editor, con RUN_LEVEL en LEVEL_1, y devuelve el
 * nivel a LEVEL_0 al terminar, en la misma sesión.
 *
 * QUÉ PRUEBA Y POR QUÉ ASÍ
 *
 * El registro de decisiones no está partido por contenedor: las 316 filas de
 * los siete proyectos viven en UN solo data source, y la única frontera entre
 * contextos es el valor de la propiedad `Proyecto`. Eso convierte a esta base
 * en el peor caso disponible, que es justo el que hay que probar: si el
 * aislamiento aguanta aquí, aguanta donde los contenedores sí están separados.
 *
 * La consulta de Notion es LA MISMA en los dos contextos y no contiene ninguna
 * señal de contexto. "Barrido agosto" devuelve filas de cinco proyectos
 * distintos si nadie filtra. Verificado el 2026-08-29: dos filas en Metis, una
 * en Shokko, dos en Andrea, seis en .Final_Final, dos en Venture Quest. Si el
 * filtro por `Proyecto` no acota en origen, la sonda devuelve trece filas y el
 * conteo exacto lo delata; si la clasificación por contexto también falla, el
 * canario negativo de Andrea lo delata por separado.
 *
 * Andrea entra SÓLO como canario negativo: un identificador y un fragmento de
 * título que deben estar AUSENTES. No se lee sustancia de Andrea, no se declara
 * partición de Andrea y ninguna corrida se resuelve a ese contexto. La
 * separación de contexto del CANON §3.3 se mantiene intacta.
 *
 * DRIVE. Las carpetas declaradas son `07_Templates` para Metis, donde viven el
 * CANON y el CANON-Core, e `Indicadores` dentro de la carpeta de Shokko en la
 * unidad compartida de Venture Quest. Ambas existían antes de esta prueba y
 * ambas se usan operativamente: ninguna se creó para poner el ensayo en verde.
 * El adaptador NO es recursivo, así que `Indicadores` vale como superficie
 * acotada y observable de Shokko, no como definición de "todo el Drive de
 * Shokko".
 *
 * CALENDAR queda deliberadamente fuera. Sólo existen el calendario primario y
 * dos de feriados; no hay calendario por contexto. El primario está declarado
 * superficie prohibida, y reutilizarlo para los dos contextos convertiría la
 * partición en ficticia. Sale OMITIDA y la fuente queda como deuda explícita.
 * Crear dos calendarios vacíos para satisfacer a la prueba sería exactamente
 * la sobre-gobernanza decorativa que este sistema intenta evitar.
 *
 * LIMITACIONES DECLARADAS — no las tapes al leer el resultado
 *
 * 1. Los canarios negativos de Asana y Drive son más débiles que los de Notion.
 *    En Notion la consulta es idéntica en ambos contextos, así que sólo la
 *    partición puede separar los resultados. En Asana y Drive las consultas
 *    difieren, de modo que el canario negativo detecta que el adaptador ignore
 *    el filtro y vuelque el contenedor entero, pero NO detecta un identificador
 *    de proyecto o de carpeta mal declarado en la partición. Eso se revisa a
 *    mano.
 * 2. Por el mismo motivo, Asana y Drive no llevan comprobación de cardinalidad
 *    exacta. En Drive además el emparejamiento por título depende de cómo
 *    tokeniza Google, comportamiento que no se puede predecir sin ejecutarlo:
 *    una cardinalidad fijada a ciegas produciría un fallo falso que no
 *    enseñaría nada.
 *
 * Cero modelos, cero escrituras, cero triggers.
 */
function smokeAislamientoMetisShokko() {
  // Identificadores verificados en vivo contra Notion y Asana el 2026-08-29.
  var CONSULTA_COMUN = 'Barrido agosto';
  var ANDREA_ID_VETADO = '3c4df4e9-5ef0-8171-9f9c-f09cb9c10ff2';
  var ANDREA_TITULO_VETADO = 'Andrea Plan Comercial';

  return smokeTestLevel1({
    contexts: ['METIS', 'SHOKKO'],
    canaries: {
      METIS: {
        'notion.search': {
          query: CONSULTA_COMUN,
          expect_id: '3c4df4e9-5ef0-81c0-870e-d8f1627a797f',
          // Dos filas de Metis casan con esta consulta. Un tercer resultado es
          // fuga; uno solo es sobre-filtrado. Ambos son FAIL.
          expect_exactly: 2,
          forbid_id: ANDREA_ID_VETADO,
          forbid_title_contains: ANDREA_TITULO_VETADO
        },
        'notion.decisions': {
          min_results: 1,
          forbid_id: ANDREA_ID_VETADO,
          forbid_title_contains: ANDREA_TITULO_VETADO
        },
        'asana.search': {
          query: 'Diseñar orquestación inter-modelo',
          expect_id: '1216646666201920',
          min_results: 1,
          forbid_id: '1217748648074744'
        },
        'drive.search': {
          query: 'CANON-Core',
          expect_id: '1mCacaShHv9XiQ9ecvMVlcO6L9o_2c__S',
          min_results: 1,
          forbid_id: '10b1toOo-hCKO8E2x4CTQf58osDtTtpt3',
          forbid_title_contains: 'Tablero Shokko'
        }
      },
      SHOKKO: {
        'notion.search': {
          query: CONSULTA_COMUN,
          expect_id: '3c4df4e9-5ef0-8147-ac7a-e30fb92e6e92',
          expect_exactly: 1,
          forbid_id: ANDREA_ID_VETADO,
          forbid_title_contains: ANDREA_TITULO_VETADO
        },
        'notion.decisions': {
          min_results: 1,
          forbid_id: ANDREA_ID_VETADO,
          forbid_title_contains: ANDREA_TITULO_VETADO
        },
        'asana.search': {
          query: 'nueva home Shopify',
          expect_id: '1217748648074744',
          min_results: 1,
          forbid_id: '1216646666201920'
        },
        'drive.search': {
          query: 'Tablero',
          expect_id: '10b1toOo-hCKO8E2x4CTQf58osDtTtpt3',
          min_results: 1,
          forbid_id: '1mCacaShHv9XiQ9ecvMVlcO6L9o_2c__S',
          forbid_title_contains: 'CANON'
        }
      }
    }
  });
}

/**
 * ENSAYO DE PROVEEDORES REALES — OpenAI y Anthropic aislados, Nivel 2.
 *
 * Ejecútala a mano desde el editor con RUN_LEVEL en LEVEL_0, y DÉJALO ahí. A
 * diferencia de los ensayos de lectura, esta función NO te pide subir el nivel
 * en el archivo: lo eleva ella misma en memoria, sólo durante las dos
 * llamadas, y lo restituye en un `finally` que se ejecuta pase lo que pase.
 *
 * POR QUÉ AL REVÉS QUE LOS OTROS ENSAYOS
 *
 * El modo de fallo que importa aquí no es que alguien ejecute esto sin querer:
 * hay que seleccionar esta función y pulsar Ejecutar, que es tan deliberado
 * como editar una línea. El modo de fallo que importa es que una llamada falle,
 * el operador se distraiga, y el proyecto quede GUARDADO en LEVEL_2, con
 * credenciales cargadas y capacidad de gastar, hasta que alguien se acuerde.
 * Un nivel elevado que vive en un archivo dura hasta que se corrige; uno que
 * vive en memoria dura lo que dura la ejecución.
 *
 * Por eso la función EXIGE que el archivo esté en LEVEL_0 y se niega a correr
 * si no lo está: un LEVEL_2 persistido es precisamente el estado peligroso.
 *
 * ESTA FUNCIÓN GASTA DINERO. Es la primera del prototipo que lo hace. Dos
 * llamadas, una por proveedor, con un prompt mínimo y un techo de salida
 * pequeño. El coste esperado está muy por debajo de un centavo, pero el punto
 * no es el importe: es que a partir de aquí el nivel del runtime tiene
 * consecuencia económica y no sólo de lectura.
 *
 * QUÉ PRUEBA
 *
 * 1. Que cada proveedor responde y su respuesta cumple el contrato interno.
 * 2. Qué identificador de modelo devuelve REALMENTE cada uno. Es el dato que
 *    más falta hace: el precio se busca por el modelo de la respuesta, no por
 *    el pedido, y algunos proveedores responden con una instantánea fechada.
 *    Si no hay precio declarado para ese identificador exacto, el costo queda
 *    desconocido y la corrida falla cerrada. Eso es correcto y deliberado: no
 *    hay coincidencia por prefijo, porque un identificador parecido puede
 *    tener tarifa distinta.
 * 3. Que el costo informado se puede recalcular a mano desde los tokens y el
 *    precio declarado, y coincide salvo redondeo. La función hace ese
 *    recálculo ella misma y lo reporta, para no depender de que alguien lo
 *    haga con una calculadora a las dos de la mañana.
 * 4. Que el enrutador manda cada contexto a su proveedor primario.
 *
 * REPETICIÓN SELECTIVA
 *
 *   smokeProveedoresReales()             prueba los dos
 *   smokeProveedoresReales('OPENAI')     prueba sólo OpenAI
 *   smokeProveedoresReales('ANTHROPIC')  prueba sólo Anthropic
 *
 * Hace falta porque el desenlace esperable de la primera corrida es que un
 * proveedor devuelva un identificador de modelo sin precio declarado. En ese
 * caso se declara ese identificador y se repite SÓLO ese proveedor: repetir el
 * otro sería pagar otra vez una llamada que ya funcionó.
 *
 * Y porque un costo desconocido CORTA la ventana de gasto: no se llama a
 * ningún proveedor posterior. Perder el conocimiento del costo es perder el
 * contador contra el que se vigilan los techos, y seguir gastando sabiendo eso
 * contradice el fail closed que la puerta de gasto promete.
 *
 * QUÉ NO PRUEBA
 *
 * La corrida dual completa a través del orquestador. Eso es el paso siguiente
 * y necesita el orquestador cableado con proveedores reales, que es otra
 * superficie. No lo mezclo aquí para que un fallo sea diagnosticable.
 *
 * Cero herramientas, cero escrituras, cero lecturas de fuentes.
 */
function smokeProveedoresReales(soloProveedor) {
  var reporte = {
    level: Config.runLevel(),
    started_at: new Date().toISOString(),
    checks: [],
    observed_model_ids: {},
    total_cost_usd: 0,
    cost_known: true
  };

  function anota(check, status, detail) {
    reporte.checks.push({ check: check, status: status, detail: detail });
  }

  if (Config.runLevel() !== Config.LEVELS.LEVEL_0) {
    anota('nivel', SMOKE_STATUS.FAIL,
      'RUN_LEVEL persistido es ' + Config.runLevel() + ' y debe ser LEVEL_0. ' +
      'Esta función eleva el nivel ella misma y lo restituye al terminar; un ' +
      'LEVEL_2 guardado en el archivo es el estado que hay que evitar.');
    reporte.status = SMOKE_STATUS.FAIL;
    reporte.finished_at = new Date().toISOString();
    Logger.log(JSON.stringify(reporte, null, 2));
    return reporte;
  }
  anota('nivel_persistido', 'INFO', 'LEVEL_0 — correcto; la elevación será temporal');

  // Techos y precios ANTES de gastar: si faltan, el fallo debe verse aquí y no
  // a mitad de una corrida ya pagada.
  try {
    Config.assertBudgetsConfigured();
    var l = Config.limits();
    anota('techos', 'INFO',
      'corrida ' + l.MAX_RUN_BUDGET_USD + ', día ' + l.MAX_DAILY_BUDGET_USD +
      ', mes ' + l.MAX_MONTHLY_BUDGET_USD);
  } catch (e) {
    anota('techos', SMOKE_STATUS.FAIL, Errors.redactText(e.message));
    reporte.status = SMOKE_STATUS.FAIL;
    reporte.finished_at = new Date().toISOString();
    Logger.log(JSON.stringify(reporte, null, 2));
    return reporte;
  }

  var proveedores = [
    { nombre: 'OPENAI', adapter: OpenAIAdapter.create(), pedido: Config.PROVIDERS.OPENAI.model },
    { nombre: 'ANTHROPIC', adapter: AnthropicAdapter.create(), pedido: Config.PROVIDERS.ANTHROPIC.model }
  ];

  // Repetición selectiva. Cuando un proveedor devuelve un identificador sin
  // precio declarado hay que añadirlo y volver a probar SÓLO ese: repetir el
  // otro sería pagar otra vez una llamada que ya funcionó.
  if (soloProveedor) {
    var filtro = String(soloProveedor).toUpperCase();
    proveedores = proveedores.filter(function (x) { return x.nombre === filtro; });
    if (!proveedores.length) {
      anota('proveedor_pedido', SMOKE_STATUS.FAIL,
        'No existe un proveedor llamado ' + filtro + '. Usa OPENAI o ANTHROPIC, ' +
        'o llama sin argumento para probar los dos.');
      reporte.status = SMOKE_STATUS.FAIL;
      reporte.finished_at = new Date().toISOString();
      Logger.log(JSON.stringify(reporte, null, 2));
      return reporte;
    }
    anota('proveedor_pedido', 'INFO', 'sólo ' + filtro);
  }

  // Ventana de gasto. Todo lo que puede costar dinero vive dentro de este try,
  // y el `finally` devuelve el runtime a estado inerte aunque una llamada
  // lance, aunque lancen las dos, y aunque falle algo que no habíamos previsto.
  Config._setRunLevel(Config.LEVELS.LEVEL_2);
  try {
  // Acumulador de la corrida. Es el mismo objeto que usa el orquestador, para
  // que el tope por corrida se aplique aquí igual que allí.
  var runtime = { cost_usd: 0, cost_known: true };

  for (var i = 0; i < proveedores.length; i++) {
    var p = proveedores[i];
    var etiqueta = p.nombre + '/complete';
    try {
      // MISMA puerta que el orquestador. Antes se llamaba al adaptador
      // directamente y eso saltaba la contabilidad entera: los techos estaban
      // declarados y ninguno se comprobaba.
      var normalized = ProviderAdapter.callBudgeted(p.adapter, {
        system: 'Responde en una sola palabra.',
        prompt: 'Di la palabra: aislamiento',
        max_output_tokens: 16
      }, null, runtime);

      reporte.observed_model_ids[p.nombre] =
        normalized.provider_model ? normalized.provider_model : '(no informado)';

      if (!normalized.usage) {
        anota(etiqueta, SMOKE_STATUS.FAIL,
          'el proveedor respondió sin bloque de uso: no hay tokens que contar');
        continue;
      }

      var costo = normalized.usage.estimated_cost_usd;
      var texto = normalized.text ? String(normalized.text).slice(0, 60) : '(sin texto)';

      // Recálculo independiente, aquí y no a mano: el costo informado debe ser
      // aritmética reproducible desde los tokens y el precio declarado.
      var precio = Config.priceFor(p.nombre, normalized.provider_model);
      var recalculado = precio
        ? (normalized.usage.input_tokens / 1000) * precio.input_per_1k +
          (normalized.usage.output_tokens / 1000) * precio.output_per_1k
        : null;
      var coincide = (recalculado !== null) && Math.abs(recalculado - costo) < 1e-9;

      anota(etiqueta, coincide ? SMOKE_STATUS.PASS : SMOKE_STATUS.FAIL,
        'modelo devuelto `' + reporte.observed_model_ids[p.nombre] + '`; ' +
        'respondió "' + texto + '"; tokens entrada ' + normalized.usage.input_tokens +
        ', salida ' + normalized.usage.output_tokens +
        '; costo informado ' + costo +
        (coincide
          ? '; recálculo independiente coincide'
          : '; RECÁLCULO NO COINCIDE, esperado ' + recalculado));

    } catch (e) {
      if (Errors.is(e, Errors.CODES.PRICE_UNKNOWN)) {
        // El caso que hay que OBSERVAR, no evitar. La sonda ya se pagó; lo que
        // el sistema se niega a hacer es contabilizarla con un precio inventado.
        //
        // Y AQUÍ SE CORTA. Perder el conocimiento del costo es perder el
        // contador contra el que se vigilan los techos: seguir con el
        // proveedor siguiente sería pagar otra llamada sabiendo que ya no se
        // puede saber cuánto va gastado. El comentario de la puerta dice que
        // no se sigue gastando contra un contador ciego; esto lo cumple.
        reporte.cost_known = false;
        anota(etiqueta, SMOKE_STATUS.FAIL,
          'COSTO DESCONOCIDO: se gastó una sonda mínima y el sistema se negó a ' +
          'contabilizarla falsamente. ' + Errors.redactText(e.message) +
          ' Declara ESE identificador exacto en METIS_PRICING, verifica su tarifa, ' +
          'y repite SÓLO este proveedor con smokeProveedoresReales(\'' + p.nombre + '\').');
        anota('corte', 'INFO',
          'ventana de gasto cerrada tras el costo desconocido: no se llama a ' +
          'ningún proveedor posterior');
        break;
      }
      if (Errors.is(e, Errors.CODES.LIMIT_EXCEEDED)) {
        // Un techo alcanzado vale para toda la corrida, no para un proveedor.
        anota(etiqueta, SMOKE_STATUS.FAIL,
          'TECHO ALCANZADO: ' + Errors.redactText(e.message));
        anota('corte', 'INFO', 'ventana de gasto cerrada por techo alcanzado');
        break;
      }
      // Un fallo propio de un proveedor —credencial ausente, error de red— no
      // impide probar el otro: es información útil y no compromete la
      // contabilidad. Sólo los errores de dinero cortan la corrida entera.
      anota(etiqueta, SMOKE_STATUS.FAIL,
        (e.code ? e.code + ': ' : '') + Errors.redactText(e.message));
    }
  }

  } finally {
    Config._setRunLevel(Config.LEVELS.LEVEL_0);
    anota('nivel_al_terminar', 'INFO',
      Config.runLevel() + ' — runtime devuelto a estado inerte');
  }

  // Enrutamiento: el modelo primario de cada contexto declarado, sin invocar.
  var nombres = Config.contextNames();
  var ruteo = [];
  for (var c = 0; c < nombres.length; c++) {
    ruteo.push(nombres[c] + '→' + Config.primaryFor(nombres[c]));
  }
  anota('primario_por_contexto', 'INFO', ruteo.join(', '));

  reporte.total_cost_usd = runtime.cost_usd;
  anota('gasto_contabilizado', 'INFO',
    'corrida ' + runtime.cost_usd + ' USD, registrado en el ledger agregado');

  var fallos = reporte.checks.filter(function (c) { return c.status === SMOKE_STATUS.FAIL; });
  reporte.status = fallos.length ? SMOKE_STATUS.FAIL : SMOKE_STATUS.PASS;
  reporte.ok = !fallos.length;
  reporte.finished_at = new Date().toISOString();
  Logger.log(JSON.stringify(reporte, null, 2));
  return reporte;
}
