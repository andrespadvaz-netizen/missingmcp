const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const properties = new Map([['GATEWAY_BRIDGE_SECRET', 'a'.repeat(32)]]);
let runLevel = 'LEVEL_0';
let calls = [];
const sandbox = {
  Date, JSON, Math, RegExp, String, Number, Object, Array,
  PropertiesService: {getScriptProperties: () => ({
    getProperty: key => properties.get(key) || null,
    setProperty: (key, value) => properties.set(key, value),
    deleteProperty: key => properties.delete(key)
  })},
  Utilities: {
    Charset: {UTF_8: 'UTF_8'}, getUuid: () => 'id',
    newBlob: value => ({getBytes: () => Array.from(Buffer.from(value, 'utf8'))}),
    computeHmacSha256Signature: (value, secret) =>
      Array.from(crypto.createHmac('sha256', secret).update(value).digest()).map(n => n > 127 ? n - 256 : n)
  },
  ContentService: {MimeType: {JSON: 'application/json'}, createTextOutput: text => ({text, setMimeType() { return this; }})},
  dispatch_: () => { throw new Error('Lens may not use the durable dispatcher'); },
  dispatchWrite_: () => { throw new Error('Lens may not use the write dispatcher'); },
  Engine: {
    Config: {
      LEVELS: {LEVEL_0: 'LEVEL_0', LEVEL_1: 'LEVEL_1'}, runLevel: () => runLevel,
      _setRunLevel: value => { runLevel = value; },
      // Andrea intentionally has only Drive in this test, as in the active scope.
      partitionFor: (_context, source) => source === 'DRIVE' ? {folder_ids: ['folder']} : null,
      contextNames: () => ['ANDREA', 'FINAL_FINAL']
    },
    ContextResolver: {
      resolve: request => request.includes('ambigua')
        ? {candidates: ['ANDREA', 'FINAL_FINAL'], status: 'REQUIRES_ANDRES', route: 'REQUIRES_ANDRES'}
        : request.includes('Andrea')
          ? {candidates: ['ANDREA'], resolved_context: 'ANDREA', status: 'CONTEXT_RESOLVED'}
          : {candidates: [], status: 'UNCERTAIN', route: 'ABSTAIN'},
      scopeFor: (candidates, resolved) => ({candidates, resolved}),
      normalize: value => value.toLowerCase()
    },
    AuthorityPolicy: {preRetrievalGrant: contexts => ({allowed_contexts: contexts})},
    ToolBroker: {
      newSession: () => ({}),
      invoke: (_session, tool, args) => {
        calls.push({tool, args});
        if (tool === 'drive.search') return {documents: [
          {id: 'old', updated_at: '2026-01-01T00:00:00.000Z'},
          {id: 'recent', updated_at: '2026-10-08T00:00:00.000Z'}
        ]};
        if (tool === 'drive.fetch') return {documents: [{
          id: args.id, title: args.id, kind: 'FILE', epistemic_status: 'EVIDENCE',
          snippet: 'safe evidence', updated_at: args.id === 'recent' ? '2026-10-08T00:00:00.000Z' : '2026-01-01T00:00:00.000Z'
        }]};
        throw new Error('unexpected tool');
      }
    }
  }
};
vm.createContext(sandbox);
for (const file of ['Bridge.gs', 'LensContext.gs']) vm.runInContext(fs.readFileSync(path.join(__dirname, file), 'utf8'), sandbox);
function post(payload) {
  const encoded = JSON.stringify(payload);
  const signature = crypto.createHmac('sha256', 'a'.repeat(32)).update(encoded).digest('hex');
  return JSON.parse(sandbox.doPost({postData: {contents: JSON.stringify({payload: encoded, signature})}}).text);
}
const sid = 'b'.repeat(64);
const first = post({action: 'lens_context', timestamp: Math.floor(Date.now() / 1000), session_id: sid, request: 'Contexto: Andrea. ¿Qué fue lo último que trabajé?'});
assert.equal(first.status, 'READY');
assert.equal(first.resolved_context, 'ANDREA');
assert.deepEqual(first.documents.map(d => d.id), ['recent', 'old']);
assert.ok(first.documents.every(d => d.context === 'ANDREA' && d.source === 'DRIVE'));
assert.equal(calls.find(c => c.tool === 'drive.search').args.query, '');
assert.equal(runLevel, 'LEVEL_0');
assert.equal(properties.has('receipt'), false);
assert.equal(properties.has('metis_lens_context_' + sid), true);

calls = [];
const continued = post({action: 'lens_context', timestamp: Math.floor(Date.now() / 1000), session_id: sid, request: '¿Qué fue lo último que trabajé?'});
assert.equal(continued.status, 'READY');
assert.equal(continued.resolved_context, 'ANDREA');
const beforeAmbiguous = calls.length;
const ambiguous = post({action: 'lens_context', timestamp: Math.floor(Date.now() / 1000), session_id: sid, request: 'pregunta ambigua'});
assert.equal(ambiguous.status, 'REQUIRES_CONTEXT');
assert.equal(calls.length, beforeAmbiguous);
console.log('Bridge Lens contract: PASS');
