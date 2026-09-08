function registerUnitValidationEndpoint() {
  var suite = 'Validación desplegada';
  TestRunner.unit(suite, 'GET fijado ejecuta una sola suite; dev se rechaza', function (t) {
    var content = globalThis['ContentService'];
    var script = globalThis['ScriptApp'];
    var runner = TestRunner.runAll;
    var calls = 0;
    var url = 'https://script.google.com/macros/s/synthetic/exec';
    globalThis['ContentService'] = { MimeType: { JSON: 'json' }, createTextOutput: function (text) {
      return { setMimeType: function () { return JSON.parse(text); } };
    } };
    globalThis['ScriptApp'] = { getService: function () { return { getUrl: function () { return url; } }; } };
    TestRunner.runAll = function () {
      calls++;
      return { total:1, passed:1, failed:0, assertions:1, level:'LEVEL_0', results:[] };
    };
    try {
      var report = doGet({});
      t.equals(report.status, 'PASS', 'suite invocada');
      t.equals(report.deployment_url, url, 'localizador de deployment');
      t.equals(calls, 1, 'una sola suite');
      url = 'https://script.google.com/macros/s/synthetic/dev';
      t.equals(doGet({}).status, 'REJECTED', 'dev no acredita versión');
      t.equals(calls, 1, 'dev no ejecuta');
    } finally {
      globalThis['ContentService'] = content;
      globalThis['ScriptApp'] = script;
      TestRunner.runAll = runner;
    }
  });
  TestRunner.unit(suite, 'Bloquea cada servicio externo y restaura tras fallo', function (t) {
    var names = ['UrlFetchApp', 'DriveApp', 'CalendarApp', 'GmailApp',
      'SpreadsheetApp', 'DocumentApp', 'ScriptApp', 'CacheService', 'LockService'];
    var originals = names.map(function (name) { return globalThis[name]; });
    t.throwsAny(function () {
      validationIsolated_(function () {
        names.forEach(function (name) {
          t.throwsAny(function () { globalThis[name].anyOperation(); }, name + ' bloqueado');
        });
        throw new Error('synthetic failure');
      });
    }, 'propaga fallo interno');
    names.forEach(function (name, i) { t.ok(globalThis[name] === originals[i], name + ' restaurado'); });
    t.equals(Config.runLevel(), Config.LEVELS.LEVEL_0, 'retorno inerte');
  });
  TestRunner.unit(suite, 'Propiedades de validación vacías y efímeras', function (t) {
    var original = globalThis['PropertiesService'];
    validationIsolated_(function () {
      var properties = globalThis['PropertiesService'].getScriptProperties();
      t.equals(properties.getProperty('OPENAI_API_KEY'), null, 'sin credencial heredada');
      properties.setProperty('validation-marker', 'synthetic');
      t.equals(properties.getProperty('validation-marker'), 'synthetic', 'store sólo en memoria');
    });
    t.ok(globalThis['PropertiesService'] === original, 'restaura servicio sin leerlo');
    validationIsolated_(function () {
      t.equals(globalThis['PropertiesService'].getScriptProperties().getProperty('validation-marker'), null, 'no persiste');
    });
  });
  TestRunner.unit(suite, 'Rechaza parámetros, rutas, POST y ejecución sin evento', function (t) {
    var saved = globalThis['ContentService'];
    globalThis['ContentService'] = { MimeType: { JSON: 'json' }, createTextOutput: function (text) {
      return { setMimeType: function () { return JSON.parse(text); } };
    } };
    try {
      [undefined, {queryString:'provider=OPENAI'}, {pathInfo:'runSmoke'},
        {parameter:{level:'LEVEL_2'}}, {parameters:{function:['runAllTests']}}].forEach(function (event) {
        t.equals(doGet(event).status, 'REJECTED', 'no despacha entrada arbitraria');
      });
      t.equals(doPost().status, 'REJECTED', 'POST cerrado');
    } finally { globalThis['ContentService'] = saved; }
  });
  TestRunner.unit(suite, 'No expone errores ni respuestas del proveedor', function (t) {
    var original = TestRunner.runAll;
    try {
      TestRunner.runAll = function () { throw new Error('synthetic-sensitive-payload'); };
      var result = validationSuite_();
      t.equals(result.status, 'FAIL', 'fallo visible');
      t.notOk(JSON.stringify(result).indexOf('synthetic-sensitive-payload') >= 0, 'error sin contenido sensible');
      t.equals(result.level, Config.LEVELS.LEVEL_0, 'inerte tras excepción');
    } finally { TestRunner.runAll = original; }
  });
  TestRunner.unit(suite, 'Rechaza PASS vacío, fallos y nivel no inerte', function (t) {
    var original = TestRunner.runAll;
    try {
      [{total:0, failed:0, level:'LEVEL_0'}, {total:1, failed:1, level:'LEVEL_0'},
        {total:1, failed:0, level:'LEVEL_2'}].forEach(function (report) {
        TestRunner.runAll = function () { report.results = []; return report; };
        t.equals(validationSuite_().status, 'FAIL', 'informe inválido nunca PASS');
      });
    } finally { TestRunner.runAll = original; }
  });
}
