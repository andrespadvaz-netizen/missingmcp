#!/usr/bin/env node
/**
 * run_local.js — ARNÉS DE EJECUCIÓN LOCAL. NO FORMA PARTE DEL PROYECTO APPS SCRIPT.
 *
 * Motivo de existir: la especificación exige entregar el RESULTADO de los tests
 * unitarios y de los 10 casos de aceptación, y Apps Script sólo puede ejecutarse
 * dentro de Google. Este arnés concatena los mismos archivos `.gs` que se suben
 * al proyecto y los evalúa con los globals de Apps Script emulados, para poder
 * imprimir esos resultados sin desplegar nada.
 *
 * No se sube a Apps Script, no lo importa ningún `.gs`, y no cambia el runtime
 * del prototipo: el runtime congelado sigue siendo Google Apps Script.
 *
 * Además, este arnés BLOQUEA la red y todos los servicios de Google: cualquier
 * intento de UrlFetchApp / DriveApp / CalendarApp / GmailApp / ScriptApp durante
 * los tests lanza una excepción. Es la evidencia de "cero escrituras externas".
 *
 * Uso:  node tools/run_local.js [unit|acceptance|all] [--verbose]
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');

const ROOT = path.resolve(__dirname, '..');

/**
 * Los archivos se cargan en ORDEN ALFABÉTICO DE RUTA, que es el mismo criterio
 * con el que Apps Script concatena y evalúa el proyecto.
 *
 * Antes había aquí dos listas ordenadas a mano (`SRC_ORDER` / `TEST_ORDER`) que
 * ponían `TestRunner.gs` antes que `AcceptanceCases.gs`. Ese orden artificial
 * ocultó un error real: en Apps Script, `AcceptanceCases.gs` se evalúa primero y
 * llamaba a `TestRunner` cuando aún valía `undefined`, tumbando el proyecto
 * entero al cargar. La suite daba 76/76 en verde mientras el proyecto real ni
 * arrancaba. Un arnés que impone su propio orden no prueba el runtime de
 * destino: prueba el arnés.
 */
function orderedFiles() {
  const dirs = ['src', 'tests'];
  const out = [];
  for (const dir of dirs) {
    const abs = path.join(ROOT, dir);
    const names = fs.readdirSync(abs).filter((f) => f.endsWith('.gs')).sort();
    for (const name of names) { out.push(path.join(abs, name)); }
  }
  return out;
}

// --------------------------------------------------------- globals emulados
let uuidCounter = 0;

function blocked(serviceName) {
  return new Proxy({}, {
    get(_target, prop) {
      if (prop === Symbol.toPrimitive || prop === 'toString' || prop === 'inspect' ||
          prop === Symbol.toStringTag || prop === 'then') {
        return undefined;
      }
      throw new Error(
        'BLOQUEADO_POR_ARNES: acceso a ' + serviceName + '.' + String(prop) +
        ' durante los tests. El arnés local prohíbe toda salida externa.');
    }
  });
}

const scriptProperties = new Map();

const sandbox = {
  console,
  JSON,
  Math,
  Date,
  Object,
  Array,
  String,
  Number,
  Boolean,
  Error,
  RegExp,
  isNaN,
  parseInt,
  parseFloat,
  encodeURIComponent,
  decodeURIComponent,

  Utilities: {
    DigestAlgorithm: { SHA_256: 'SHA_256' },
    Charset: { UTF_8: 'UTF_8' },
    computeDigest(_algorithm, text, _charset) {
      const digest = crypto.createHash('sha256').update(String(text), 'utf8').digest();
      // Apps Script devuelve bytes con signo.
      return Array.from(digest).map((b) => (b > 127 ? b - 256 : b));
    },
    getUuid() {
      uuidCounter += 1;
      return 'local-uuid-' + String(uuidCounter).padStart(6, '0');
    }
  },

  PropertiesService: {
    getScriptProperties() {
      return {
        getProperty: (k) => (scriptProperties.has(k) ? scriptProperties.get(k) : null),
        setProperty: (k, v) => { scriptProperties.set(k, String(v)); },
        deleteProperty: (k) => { scriptProperties.delete(k); },
        getKeys: () => Array.from(scriptProperties.keys())
      };
    }
  },

  Logger: {
    log: (msg) => { process.stdout.write(String(msg) + '\n'); }
  },

  // Toda superficie externa queda bloqueada durante los tests.
  UrlFetchApp: blocked('UrlFetchApp'),
  DriveApp: blocked('DriveApp'),
  CalendarApp: blocked('CalendarApp'),
  GmailApp: blocked('GmailApp'),
  ScriptApp: blocked('ScriptApp'),
  SpreadsheetApp: blocked('SpreadsheetApp'),
  DocumentApp: blocked('DocumentApp')
};

sandbox.globalThis = sandbox;

const context = vm.createContext(sandbox);

function loadAll() {
  const files = orderedFiles();
  for (const file of files) {
    if (!fs.existsSync(file)) {
      throw new Error('Falta el archivo esperado: ' + file);
    }
    const code = fs.readFileSync(file, 'utf8');
    try {
      vm.runInContext(code, context, { filename: file });
    } catch (e) {
      throw new Error('Error cargando ' + path.basename(file) + ': ' + e.message);
    }
  }
}

const ENTRIES = {
  validation: 'validationSuite_',
  unit: 'TestRunner.runUnitTests',
  acceptance: 'TestRunner.runAcceptance',
  all: 'TestRunner.runAll'
};

function main() {
  const key = process.argv[2] || 'all';
  const verbose = process.argv.indexOf('--verbose') !== -1;
  const entry = ENTRIES[key];
  if (!entry) {
    throw new Error('Entrada desconocida: ' + key + ' (usa unit | acceptance | all)');
  }
  loadAll();
  if (key === 'validation') {
    const result = vm.runInContext('validationSuite_()', context);
    process.stdout.write(JSON.stringify(result, null, 2) + '\n');
    process.exit(result.status === 'PASS' ? 0 : 1);
  }
  const report = vm.runInContext(entry + '()', context, { filename: 'entry' });
  const rendered = vm.runInContext('TestRunner.render', context)(report, verbose);
  process.stdout.write(rendered + '\n');
  const failed = report.failed || 0;
  process.exit(failed === 0 ? 0 : 1);
}

main();
