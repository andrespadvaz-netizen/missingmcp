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
  var report = {
    level: Config.runLevel(),
    real_reads_enabled: Config.levelAllowsRealReads(),
    secrets: secrets,
    settings: settings,
    triggers_declared: 0,
    productive_write_tools: 0,
    simulated_write_tools: AuthorityPolicy.SIMULATED_WRITE_TOOLS.length,
    forbidden_surfaces: Config.FORBIDDEN_SURFACES
  };
  Logger.log(JSON.stringify(report, null, 2));
  return report;
}

/** Poda del ledger por retención (spec §5). Manual, nunca programada. */
function purgeLedger() {
  var removed = Ledger.purge();
  Logger.log('Entradas de ledger podadas: ' + removed);
  return removed;
}
