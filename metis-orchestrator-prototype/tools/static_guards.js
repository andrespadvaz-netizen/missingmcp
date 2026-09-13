#!/usr/bin/env node
/**
 * static_guards.js — ARNÉS LOCAL. NO FORMA PARTE DEL PROYECTO APPS SCRIPT.
 *
 * Verifica de forma estática, sobre el código que SÍ se sube, las afirmaciones
 * que la spec exige demostrar (§18, §19, §20):
 *
 *   G1  cero triggers: ninguna referencia ejecutable a ScriptApp y ninguna
 *       función de trigger simple/instalable (onOpen, onEdit, doGet, ...);
 *   G2  cero superficies de escritura productiva: ninguna referencia ejecutable
 *       a GmailApp, ni a métodos de mutación de DriveApp/CalendarApp;
 *   G3  ningún verbo HTTP de mutación (PUT/PATCH/DELETE) en ningún adaptador;
 *   G4  UrlFetchApp sólo existe en los cuatro adaptadores autorizados;
 *   G5  `SimulatedWriteAdapter.gs` no referencia ninguna superficie externa;
 *   G6  `appsscript.json` declara exactamente los scopes de sólo lectura, sin
 *       scope de escritura ni de `script.scriptapp` (que permitiría triggers);
 *   G7  no hay literales que parezcan secretos en el código ni en los fixtures;
 *   G8  no hay tablas de precio NI techos de gasto en el código: ambos se leen
 *       de configuración;
 *   G9  todo adaptador de lectura exige partición declarada antes de leer.
 *
 * El análisis se hace sobre el código con COMENTARIOS Y LITERALES DE CADENA
 * ELIMINADOS, de modo que la tabla documental `WOULD_CALL` (que sí menciona
 * `GmailApp.sendEmail(...)` como texto) no produzca falsos positivos ni tape
 * una referencia real.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'src');
const TESTS = path.join(ROOT, 'tests');

/** Elimina comentarios y literales de cadena; deja sólo código ejecutable. */
function strip(code) {
  let out = '';
  let i = 0;
  let state = 'code';
  let quote = null;
  while (i < code.length) {
    const c = code[i];
    const next = code[i + 1];
    if (state === 'code') {
      if (c === '/' && next === '*') { state = 'block'; i += 2; continue; }
      if (c === '/' && next === '/') { state = 'line'; i += 2; continue; }
      if (c === '"' || c === "'" || c === '`') { state = 'string'; quote = c; i += 1; out += ' '; continue; }
      out += c; i += 1; continue;
    }
    if (state === 'block') {
      if (c === '*' && next === '/') { state = 'code'; i += 2; out += ' '; continue; }
      i += 1; continue;
    }
    if (state === 'line') {
      if (c === '\n') { state = 'code'; out += '\n'; }
      i += 1; continue;
    }
    if (state === 'string') {
      if (c === '\\') { i += 2; continue; }
      if (c === quote) { state = 'code'; quote = null; i += 1; continue; }
      i += 1; continue;
    }
  }
  return out;
}

function srcFiles(dir) {
  return fs.readdirSync(dir).filter((f) => f.endsWith('.gs')).sort()
    .map((f) => ({ name: f, path: path.join(dir, f) }));
}

const results = [];
function guard(id, description, fn) {
  let failures = [];
  try {
    failures = fn() || [];
  } catch (e) {
    failures = ['error del guard: ' + e.message];
  }
  results.push({ id, description, ok: failures.length === 0, failures });
}

const files = srcFiles(SRC).map((f) => Object.assign({}, f, {
  raw: fs.readFileSync(f.path, 'utf8'),
  code: strip(fs.readFileSync(f.path, 'utf8'))
}));

const testFiles = srcFiles(TESTS).map((f) => Object.assign({}, f, {
  raw: fs.readFileSync(f.path, 'utf8'),
  code: strip(fs.readFileSync(f.path, 'utf8'))
}));

// -------------------------------------------------------------------- G1
guard('G1', 'cero triggers', () => {
  const fails = [];
  for (const f of files.concat(testFiles)) {
    const scriptCode = f.name === 'ValidationEndpoint.gs'
      ? f.code.replace(/ScriptApp\.getService\(\)\.getUrl\(\)/g, '') : f.code;
    if (/\bScriptApp\b/.test(scriptCode)) { fails.push(f.name + ': referencia ejecutable a ScriptApp'); }
    if (/\bnewTrigger\b/.test(f.code)) { fails.push(f.name + ': newTrigger'); }
    const triggerFns = /\bfunction\s+(onOpen|onEdit|onInstall|onFormSubmit|onChange|doGet|doPost)\s*\(/g;
    let m;
    while ((m = triggerFns.exec(f.code)) !== null) {
      if (f.name === 'ValidationEndpoint.gs' && ['doGet', 'doPost'].includes(m[1])) continue;
      fails.push(f.name + ': define la función de trigger/endpoint `' + m[1] + '`');
    }
  }
  return fails;
});

// -------------------------------------------------------------------- G2
guard('G2', 'cero superficies de escritura productiva', () => {
  const fails = [];
  const driveWrite = /DriveApp[\s\S]{0,80}?\.(createFile|createFolder|setTrashed|removeFile|addFile|setContent|setName|setSharing|setOwner)/;
  const calendarWrite = /CalendarApp[\s\S]{0,80}?\.(createEvent|createAllDayEvent|createCalendar|deleteEvent|setTitle|setTime)/;
  for (const f of files.concat(testFiles)) {
    if (/\bGmailApp\b/.test(f.code)) { fails.push(f.name + ': referencia ejecutable a GmailApp'); }
    if (/\bSpreadsheetApp\b|\bDocumentApp\b/.test(f.code)) { fails.push(f.name + ': servicio de documentos no autorizado'); }
    if (driveWrite.test(f.code)) { fails.push(f.name + ': método de mutación de DriveApp'); }
    if (calendarWrite.test(f.code)) { fails.push(f.name + ': método de mutación de CalendarApp'); }
  }
  // DriveApp / CalendarApp sólo pueden aparecer en su adaptador de lectura.
  for (const f of files) {
    if (/\bDriveApp\b/.test(f.code) && f.name !== 'DriveReadAdapter.gs') {
      fails.push(f.name + ': DriveApp fuera de DriveReadAdapter');
    }
    if (/\bCalendarApp\b/.test(f.code) && f.name !== 'CalendarReadAdapter.gs') {
      fails.push(f.name + ': CalendarApp fuera de CalendarReadAdapter');
    }
  }
  return fails;
});

// -------------------------------------------------------------------- G3
guard('G3', 'ningún verbo HTTP de mutación', () => {
  const fails = [];
  for (const f of files) {
    const m = f.raw.match(/method:\s*'([a-z]+)'/g) || [];
    for (const hit of m) {
      const verb = hit.match(/'([a-z]+)'/)[1];
      if (['put', 'patch', 'delete'].indexOf(verb) !== -1) {
        fails.push(f.name + ": method: '" + verb + "'");
      }
    }
  }
  return fails;
});

// -------------------------------------------------------------------- G4
guard('G4', 'UrlFetchApp sólo en los adaptadores autorizados', () => {
  const allowed = ['NotionReadAdapter.gs', 'AsanaReadAdapter.gs', 'OpenAIAdapter.gs', 'AnthropicAdapter.gs'];
  const fails = [];
  for (const f of files) {
    if (/\bUrlFetchApp\b/.test(f.code) && allowed.indexOf(f.name) === -1) {
      fails.push(f.name + ': UrlFetchApp fuera de los adaptadores autorizados');
    }
  }
  return fails;
});

// -------------------------------------------------------------------- G5
guard('G5', 'SimulatedWriteAdapter no toca ninguna superficie externa', () => {
  const f = files.filter((x) => x.name === 'SimulatedWriteAdapter.gs')[0];
  if (!f) { return ['no se encontró SimulatedWriteAdapter.gs']; }
  const fails = [];
  const forbidden = ['UrlFetchApp', 'DriveApp', 'CalendarApp', 'GmailApp', 'ScriptApp', 'fetch('];
  for (const token of forbidden) {
    if (f.code.indexOf(token) !== -1) { fails.push('SimulatedWriteAdapter.gs referencia ' + token); }
  }
  return fails;
});

// -------------------------------------------------------------------- G6
guard('G6', 'appsscript.json declara sólo scopes de lectura', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, 'appsscript.json'), 'utf8'));
  const expected = [
    'https://www.googleapis.com/auth/script.external_request',
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/calendar.readonly'
  ].sort();
  const actual = (manifest.oauthScopes || []).slice().sort();
  const fails = [];
  if (JSON.stringify(expected) !== JSON.stringify(actual)) {
    fails.push('scopes inesperados: ' + JSON.stringify(actual));
  }
  if (!manifest.webapp || manifest.webapp.access !== 'MYSELF' || manifest.webapp.executeAs !== 'USER_DEPLOYING') {
    fails.push('validación web debe ser exclusiva del operador que despliega');
  }
  for (const scope of actual) {
    if (/gmail|scriptapp/.test(scope)) { fails.push('scope peligroso: ' + scope); }
    if (/auth\/(drive|calendar)$/.test(scope)) { fails.push('scope de escritura: ' + scope); }
  }
  return fails;
});

// -------------------------------------------------------------------- G7
guard('G7', 'sin literales que parezcan secretos', () => {
  const fails = [];
  const patterns = [/sk-[A-Za-z0-9]{16,}/, /secret_[A-Za-z0-9]{16,}/, /AIza[A-Za-z0-9_\-]{20,}/];
  for (const f of files.concat(testFiles)) {
    for (const p of patterns) {
      const hit = f.raw.match(p);
      // Los tests de redacción usan cadenas de ejemplo cortas y explícitas.
      if (hit && f.name !== 'Unit_SimulatedWrite.gs') {
        fails.push(f.name + ': posible secreto literal ' + hit[0].slice(0, 12) + '...');
      }
    }
  }
  return fails;
});

// -------------------------------------------------------------------- G8
guard('G8', 'sin precios ni techos de gasto en el código', () => {
  const fails = [];
  for (const f of files) {
    if (/PRICE_PER_1K|PRICE_TABLE/.test(f.code)) {
      fails.push(f.name + ': tabla de precios embebida');
    }
    // Un precio por token escrito a mano en el código queda obsoleto y produce
    // un contador de costo falso; debe venir de METIS_PRICING.
    if (/(input|output)_per_1k\s*:\s*[0-9]/.test(f.code)) {
      fails.push(f.name + ': precio numérico literal');
    }
  }
  for (const name of ['OpenAIAdapter.gs', 'AnthropicAdapter.gs']) {
    const f = files.filter((x) => x.name === name)[0];
    if (!f) { fails.push('falta ' + name); continue; }
    if (f.code.indexOf('Config.priceFor') === -1) {
      fails.push(name + ': no lee el precio de la configuración');
    }
    // Antes de gastar, los techos deben existir.
    if (f.code.indexOf('Config.assertBudgetsConfigured') === -1) {
      fails.push(name + ': no exige techos de gasto declarados antes de llamar');
    }
  }

  // Cuánto está dispuesto a gastar el operador no es una decisión del código:
  // un default plausible se vuelve el presupuesto de todos sin que nadie lo
  // haya decidido.
  const config = files.filter((x) => x.name === 'Config.gs')[0];
  if (config) {
    const defaults = config.code.match(/DEFAULT_LIMITS\s*=\s*\{[\s\S]*?\}/);
    if (defaults && /_USD\s*:\s*[0-9]/.test(defaults[0])) {
      fails.push('Config.gs: techo monetario con default en DEFAULT_LIMITS');
    }
    if (config.code.indexOf('REQUIRED_BUDGET_KEYS') === -1) {
      fails.push('Config.gs: no declara los techos monetarios como exigidos');
    }
  }
  return fails;
});

// -------------------------------------------------------------------- G9
guard('G9', 'todo adaptador de lectura exige partición', () => {
  const adapters = ['NotionReadAdapter.gs', 'AsanaReadAdapter.gs',
                    'DriveReadAdapter.gs', 'CalendarReadAdapter.gs'];
  const fails = [];
  for (const name of adapters) {
    const f = files.filter((x) => x.name === name)[0];
    if (!f) { fails.push('falta ' + name); continue; }
    if (f.code.indexOf('sourcePartitionUndeclared') === -1) {
      fails.push(name + ': no rechaza la lectura sin partición declarada');
    }
  }
  const broker = files.filter((x) => x.name === 'ToolBroker.gs')[0];
  if (!broker || broker.code.indexOf('Config.partitionFor') === -1) {
    fails.push('ToolBroker.gs: no resuelve la partición antes de invocar la lectura');
  }
  return fails;
});

// -------------------------------------------------------------------- salida
const lines = ['=== Guards estáticos sobre el código que se sube a Apps Script ==='];
let failed = 0;
for (const r of results) {
  lines.push('  ' + (r.ok ? 'PASS' : 'FAIL') + '  ' + r.id + '  ' + r.description);
  for (const f of r.failures) { lines.push('        - ' + f); }
  if (!r.ok) { failed += 1; }
}
lines.push('');
lines.push('Archivos analizados: ' + files.length + ' en src/, ' + testFiles.length + ' en tests/');
lines.push(failed === 0 ? 'RESULTADO: PASS' : 'RESULTADO: FAIL (' + failed + ' guards)');
process.stdout.write(lines.join('\n') + '\n');
process.exit(failed === 0 ? 0 : 1);
