/** Operator-only validation web app. No user-controlled dispatch or HTML/RPC. */
function doGet(e) {
  var result;
  if (!e || e.queryString || e.pathInfo ||
      (e.parameter && Object.keys(e.parameter).length) ||
      (e.parameters && Object.keys(e.parameters).length)) {
    result = { status: 'REJECTED', reason: 'VALIDATION_ONLY' };
  } else {
    // Platform URL is a locator, NOT evidence of source identity. The external
    // evidence collector resolves its version and compares that version's files.
    var deploymentUrl = ScriptApp.getService().getUrl();
    if (!deploymentUrl || !/\/exec$/.test(deploymentUrl)) {
      result = { status: 'REJECTED', reason: 'FIXED_DEPLOYMENT_REQUIRED' };
    } else {
      result = validationSuite_();
      result.deployment_url = deploymentUrl;
      result.version_evidence = 'RESOLVE_DEPLOYMENT_AND_COMPARE_VERSION_EXTERNALLY';
      // A compact independent log survives a blocked ContentService redirect.
      Logger.log('VALIDATION_SUMMARY ' + JSON.stringify({
        status: result.status, total: result.total, passed: result.passed,
        failed: result.failed, assertions: result.assertions, level: result.level,
        deployment_url: deploymentUrl, reason: result.reason || null
      }));
      (result.tests || []).filter(function (test) { return !test.passed; }).forEach(function (test) {
        Logger.log('VALIDATION_FAILED_TEST ' + JSON.stringify(test));
      });
    }
  }
  return ContentService.createTextOutput(JSON.stringify(result))
    .setMimeType(ContentService.MimeType.JSON);
}

function doPost() {
  return ContentService.createTextOutput(JSON.stringify({
    status: 'REJECTED', reason: 'VALIDATION_ONLY'
  })).setMimeType(ContentService.MimeType.JSON);
}

/** Execution-local service isolation; never reads or restores stored secrets. */
function validationIsolated_(run) {
  var names = ['UrlFetchApp', 'DriveApp', 'CalendarApp', 'GmailApp',
    'SpreadsheetApp', 'DocumentApp', 'ScriptApp', 'PropertiesService',
    'CacheService', 'LockService'];
  var saved = [];
  var memory = Object.create(null);
  var properties = {
    getProperty: function (key) { return Object.prototype.hasOwnProperty.call(memory, key) ? memory[key] : null; },
    setProperty: function (key, value) { memory[key] = String(value); },
    deleteProperty: function (key) { delete memory[key]; },
    getKeys: function () { return Object.keys(memory); }
  };
  var denied = new Proxy({}, { get: function () { throw new Error('VALIDATION_EXTERNAL_ACCESS_BLOCKED'); } });
  try {
    for (var i = 0; i < names.length; i++) {
      var name = names[i];
      saved.push({ name: name, value: globalThis[name] });
      var replacement = name === 'PropertiesService'
        ? { getScriptProperties: function () { return properties; } } : denied;
      globalThis[name] = replacement;
      if (globalThis[name] !== replacement) { throw new Error('VALIDATION_ISOLATION_FAILED'); }
    }
    return run();
  } finally {
    Config._setRunLevel(Config.LEVELS.LEVEL_0);
    for (var j = saved.length - 1; j >= 0; j--) {
      globalThis[saved[j].name] = saved[j].value;
    }
  }
}

function validationSuite_() {
  try {
    return validationIsolated_(function () {
      var report = TestRunner.runAll();
      return {
        status: report.failed === 0 && report.total > 0 && report.level === Config.LEVELS.LEVEL_0 ? 'PASS' : 'FAIL',
        total: report.total, passed: report.passed, failed: report.failed,
        assertions: report.assertions, level: report.level,
        // Do not serialize raw errors, provider responses, assertions or settings.
        tests: report.results.map(function (r) {
          return { suite: r.suite, name: r.name, passed: r.ok };
        })
      };
    });
  } catch (ignored) {
    return { status: 'FAIL', reason: 'VALIDATION_FAILED', level: Config.runLevel() };
  }
}
