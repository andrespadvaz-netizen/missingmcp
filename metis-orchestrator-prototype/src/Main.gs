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

/**
 * Smoke test de Nivel 1: una lectura real acotada por fuente y contexto.
 *
 * Qué hace: comprueba nivel y credenciales, y ejecuta UNA lectura por cada
 * (contexto, fuente) con partición declarada, incluida `calendar.read` sobre
 * una ventana temporal corta (`calendar_window_days`, 7 por defecto).
 * Qué NO hace: no invoca ningún modelo, no construye plan, no simula ninguna
 * acción y no escribe absolutamente nada. Es sólo lectura.
 *
 * Qué VALIDA: los documentos realmente ADMITIDOS en `session.documents`, no el
 * array crudo que devolvió el adaptador. Si una fuente devuelve algo de otro
 * contexto, el smoke test FALLA: significa que la partición no acotó en origen.
 *
 * Devuelve conteos y errores redactados; nunca contenido recuperado.
 */
function smokeTestLevel1(options) {
  var opts = options || {};
  var report = {
    level: Config.runLevel(),
    started_at: Schemas.nowIso(),
    ok: true,
    checks: [],
    models_invoked: 0,
    writes_attempted: 0
  };

  function check(name, ok, detail) {
    report.checks.push({ check: name, ok: !!ok, detail: detail === undefined ? null : detail });
    if (!ok) { report.ok = false; }
  }

  if (!Config.levelAllowsRealReads()) {
    check('nivel', false, 'RUN_LEVEL es ' + Config.runLevel() +
      '. Cambia Config.RUN_LEVEL a LEVEL_1 para ejecutar este smoke test.');
    Logger.log(JSON.stringify(report, null, 2));
    return report;
  }
  check('nivel', true, Config.runLevel());

  ['NOTION_API_KEY', 'ASANA_API_KEY'].forEach(function (key) {
    check('credencial:' + key, Config.hasSecret(key), Config.hasSecret(key) ? 'presente' : 'ausente');
  });

  var contexts = opts.contexts ? opts.contexts : Config.declaredPartitionContexts();
  check('particiones_declaradas', contexts.length > 0, contexts.join(', '));

  var scopeOf = function (context) {
    return ContextResolver.scopeFor([context], context);
  };

  // Ventana temporal ACOTADA para Calendar: nunca "todo el calendario".
  var windowDays = (typeof opts.calendar_window_days === 'number') ? opts.calendar_window_days : 7;
  var nowMs = Schemas.nowMs();
  var timeWindow = {
    start: Schemas.toIso(new Date(nowMs)),
    end: Schemas.toIso(new Date(nowMs + windowDays * 24 * 60 * 60 * 1000))
  };

  contexts.forEach(function (context) {
    var session = ToolBroker.newSession({ execution_id: 'smoke-' + context },
      scopeOf(context), AuthorityPolicy.preRetrievalGrant([context]));

    [['notion.search', { query: opts.query ? opts.query : '', page_size: 3 }],
     ['notion.decisions', { page_size: 5 }],
     ['asana.search', { query: opts.query ? opts.query : '', page_size: 3 }],
     ['drive.search', { query: opts.query ? opts.query : '', page_size: 3 }],
     ['calendar.read', { query: opts.query ? opts.query : '', time_window: timeWindow }]
    ].forEach(function (pair) {
      var tool = pair[0];
      if (!Config.partitionFor(context, ToolBroker.READ_TOOLS[tool].source)) {
        check(context + '/' + tool, true, 'sin partición declarada: no se lee (correcto)');
        return;
      }
      // Lo que cuenta es lo que ENTRÓ a la sesión, no lo que devolvió la fuente:
      // un documento puede volver de la fuente y ser descartado por contexto.
      // Contar el array crudo del adaptador daría por buena una lectura que la
      // sesión rechazó entera.
      var before = session.documents.length;
      try {
        var result = ToolBroker.invoke(session, tool, pair[1]);
        var admitted = session.documents.length - before;
        var returned = (result.returned_by_source === undefined) ? admitted : result.returned_by_source;
        var discarded = returned - admitted;
        if (result.ok === false) {
          check(context + '/' + tool, false, result.error);
        } else {
          check(context + '/' + tool, discarded === 0,
            'admitidos en sesión: ' + admitted + ' de ' + returned + ' devueltos' +
            (discarded > 0 ? ' — ' + discarded + ' DESCARTADOS por contexto' : ''));
        }
      } catch (e) {
        check(context + '/' + tool, false, (e.code ? e.code : 'ERROR') + ': ' + Errors.redactText(e.message));
      }
    });

    // Contaminación: cualquier descarte por contexto es un fallo del smoke test,
    // no una nota al margen. Significa que la partición dejó pasar algo ajeno.
    var descartes = session.risk_signals.filter(function (s) {
      return s.signal === 'CROSS_CONTEXT_RESULT_DISCARDED';
    });
    check(context + '/sin_contaminacion', descartes.length === 0,
      descartes.length ? (descartes.length + ' resultados de otro contexto llegaron de la fuente')
                       : 'ningún resultado ajeno llegó de la fuente');

    check(context + '/documentos_en_sesion', true,
      'total admitido: ' + session.documents.length +
      ', evidencias: ' + session.evidence_refs.length);

    // Vigencia: sólo se reporta si el registro devolvió decisiones.
    if (session.decisions.length) {
      var registry = RetrievalPolicy.currentDecisions(session.decisions);
      check(context + '/vigencia', registry.conflicting.length === 0,
        'decisiones: ' + session.decisions.length +
        ', vigentes: ' + registry.current.length +
        ', con Estado vacío: ' + registry.default_applied_count +
        ', contradictorias: ' + registry.conflicting.length);
    }
  });

  report.finished_at = Schemas.nowIso();
  Logger.log(JSON.stringify(report, null, 2));
  return report;
}

/** Poda del ledger por retención (spec §5). Manual, nunca programada. */
function purgeLedger() {
  var removed = Ledger.purge();
  Logger.log('Entradas de ledger podadas: ' + removed);
  return removed;
}
