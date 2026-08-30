/**
 * TestRunner.gs — runner manual (spec §21.4). No hay triggers ni ejecución
 * automática: se invoca desde el editor con `runUnitTests`, `runAcceptanceCases`
 * o `runAllTests` (ver `Main.gs`).
 */
var TestRunner = (function () {

  var _unit = [];
  var _acceptance = [];

  function unit(suite, name, fn) {
    _unit.push({ suite: suite, name: name, fn: fn, kind: 'unit' });
  }

  function acceptance(id, name, fn) {
    _acceptance.push({ suite: 'Aceptación', id: id, name: name, fn: fn, kind: 'acceptance' });
  }

  /**
   * Archivos que registran casos. Se invocan AQUÍ, no en tiempo de carga.
   *
   * Apps Script concatena los .gs en un orden que no controlamos: si un archivo
   * de pruebas llamaba a `TestRunner` mientras se cargaba y caía antes que este
   * archivo, `TestRunner` valía `undefined` y el error de carga tumbaba el
   * proyecto ENTERO — incluidas funciones sin relación alguna con los tests.
   * Una declaración `function` sí se hoistea globalmente, así que invocarlas
   * desde aquí es seguro sea cual sea el orden de los archivos.
   */
  var REGISTRARS = [
    'registerUnitContextResolver',
    'registerUnitRouter',
    'registerUnitAuthorityPolicy',
    'registerUnitPlanValidator',
    'registerUnitLedger',
    'registerUnitHandoffBuilder',
    'registerUnitSimulatedWrite',
    'registerUnitAislamientoMulticontexto',
    'registerUnitProveedoresReales',
    'registerAcceptanceCases'
  ];

  var _registered = false;

  /** Idempotente: registrar dos veces duplicaría cada caso. */
  function _ensureRegistered() {
    if (_registered) { return; }
    _registered = true;
    for (var i = 0; i < REGISTRARS.length; i++) {
      var name = REGISTRARS[i];
      var fn = globalThis[name];
      if (typeof fn !== 'function') {
        // Falla ruidosamente: un registrador ausente daría 0 casos y un PASS
        // vacío, que es peor que un error.
        throw new Error('Falta el registrador de pruebas ' + name +
          ': el archivo que lo define no está en el proyecto.');
      }
      fn();
    }
  }

  function _ctx() {
    var checks = [];
    function record(ok, msg) {
      checks.push({ ok: !!ok, msg: msg });
      if (!ok) { throw new Error('ASSERT FALLÓ: ' + msg); }
      return true;
    }
    return {
      checks: checks,
      ok: function (cond, msg) { return record(!!cond, msg); },
      notOk: function (cond, msg) { return record(!cond, msg); },
      equals: function (actual, expected, msg) {
        return record(actual === expected, msg + ' [esperado=' + JSON.stringify(expected) +
                                            ' obtenido=' + JSON.stringify(actual) + ']');
      },
      deepEquals: function (actual, expected, msg) {
        return record(Schemas.canonicalize(actual) === Schemas.canonicalize(expected),
          msg + ' [esperado=' + JSON.stringify(expected) + ' obtenido=' + JSON.stringify(actual) + ']');
      },
      includes: function (haystack, needle, msg) {
        var arr = Object.prototype.toString.call(haystack) === '[object Array]' ? haystack : String(haystack);
        var found = Object.prototype.toString.call(arr) === '[object Array]'
          ? arr.indexOf(needle) !== -1
          : arr.indexOf(needle) !== -1;
        return record(found, msg + ' [buscado=' + JSON.stringify(needle) + ']');
      },
      throwsCode: function (code, fn, msg) {
        var thrown = null;
        try { fn(); } catch (e) { thrown = e; }
        return record(!!thrown && thrown.code === code,
          msg + ' [esperado código=' + code + ' obtenido=' + (thrown ? (thrown.code || thrown.message) : 'sin excepción') + ']');
      },
      throwsAny: function (fn, msg) {
        var thrown = null;
        try { fn(); } catch (e) { thrown = e; }
        return record(!!thrown, msg);
      },
      note: function (msg) { checks.push({ ok: true, msg: '· ' + msg }); }
    };
  }

  function _runOne(test) {
    var ctx = _ctx();
    var started = 'n/a';
    try {
      Fixtures.resetAll();
      test.fn(ctx);
      return { suite: test.suite, id: test.id || null, name: test.name, ok: true,
               checks: ctx.checks, error: null };
    } catch (e) {
      return { suite: test.suite, id: test.id || null, name: test.name, ok: false,
               checks: ctx.checks, error: (e && e.message) ? e.message : String(e) };
    } finally {
      try { Fixtures.resetAll(); } catch (ignored) { started = 'n/a'; }
    }
  }

  function _report(title, tests) {
    var results = [];
    var passed = 0;
    var failed = 0;
    var checks = 0;
    for (var i = 0; i < tests.length; i++) {
      var r = _runOne(tests[i]);
      checks += r.checks.length;
      if (r.ok) { passed++; } else { failed++; }
      results.push(r);
    }
    return {
      title: title,
      total: tests.length,
      passed: passed,
      failed: failed,
      assertions: checks,
      results: results,
      level: Config.runLevel()
    };
  }

  function runUnitTests() {
    _ensureRegistered();
    return _report('Tests unitarios (spec §17)', _unit);
  }
  function runAcceptance() {
    _ensureRegistered();
    return _report('Casos de aceptación (spec §16)', _acceptance);
  }

  function runAll() {
    _ensureRegistered();
    var unitReport = _report('Tests unitarios (spec §17)', _unit);
    var accReport = _report('Casos de aceptación (spec §16)', _acceptance);
    return {
      title: 'Suite completa',
      total: unitReport.total + accReport.total,
      passed: unitReport.passed + accReport.passed,
      failed: unitReport.failed + accReport.failed,
      assertions: unitReport.assertions + accReport.assertions,
      reports: [unitReport, accReport],
      results: unitReport.results.concat(accReport.results),
      level: Config.runLevel()
    };
  }

  function _renderOne(report, verbose) {
    var lines = [];
    lines.push('=== ' + report.title + ' ===');
    var currentSuite = null;
    for (var i = 0; i < report.results.length; i++) {
      var r = report.results[i];
      if (r.suite !== currentSuite) {
        currentSuite = r.suite;
        lines.push('');
        lines.push('-- ' + currentSuite);
      }
      var label = (r.id ? ('[' + r.id + '] ') : '') + r.name;
      lines.push('  ' + (r.ok ? 'PASS' : 'FAIL') + '  ' + label +
                 '  (' + r.checks.length + ' asserts)');
      if (verbose || !r.ok) {
        for (var c = 0; c < r.checks.length; c++) {
          lines.push('        ' + (r.checks[c].ok ? 'ok  ' : 'FAIL') + ' ' + r.checks[c].msg);
        }
      }
      if (!r.ok) { lines.push('        ERROR: ' + r.error); }
    }
    lines.push('');
    lines.push('Total: ' + report.total + ' | PASS: ' + report.passed + ' | FAIL: ' + report.failed +
               ' | asserts: ' + report.assertions + ' | nivel: ' + report.level);
    return lines.join('\n');
  }

  function render(report, verbose) {
    if (report.reports) {
      var out = [];
      for (var i = 0; i < report.reports.length; i++) {
        out.push(_renderOne(report.reports[i], verbose));
      }
      out.push('');
      out.push('===== VEREDICTO GLOBAL =====');
      out.push('Total: ' + report.total + ' | PASS: ' + report.passed + ' | FAIL: ' + report.failed +
               ' | asserts: ' + report.assertions);
      out.push(report.failed === 0 ? 'RESULTADO: PASS' : 'RESULTADO: FAIL');
      return out.join('\n');
    }
    return _renderOne(report, verbose);
  }

  return {
    unit: unit,
    acceptance: acceptance,
    runUnitTests: runUnitTests,
    runAcceptance: runAcceptance,
    runAll: runAll,
    render: render
  };
})();
