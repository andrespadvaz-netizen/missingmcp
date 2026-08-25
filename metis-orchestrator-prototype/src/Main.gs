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
 *   { query, min_results, expect_id, expect_title_contains }
 * Todos opcionales; `min_results` vale 1 por defecto, así que incluso sin
 * canario declarado hace falta AL MENOS UN resultado real para validar.
 *
 * @return {{status:string, detail:string}}
 */
function smokeEvaluateCanary(canary, admitted) {
  var c = canary || {};
  var min = (typeof c.min_results === 'number') ? c.min_results : 1;

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
            (c.expect_id ? ', canario `' + c.expect_id + '` presente' : '') +
            (c.expect_title_contains ? ', título esperado presente' : '')
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
     ['notion.decisions', { page_size: 5 }],
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

/** Poda del ledger por retención (spec §5). Manual, nunca programada. */
function purgeLedger() {
  var removed = Ledger.purge();
  Logger.log('Entradas de ledger podadas: ' + removed);
  return removed;
}
