const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const assert = require('node:assert/strict');
let tests = 0;
function fixture() {
  const secret = 'test-bridge-secret'.repeat(3);
  const data = {GATEWAY_BRIDGE_SECRET: secret};
  let calls = 0, locked = false, level = 'LEVEL_0';
  const blob = (x,contentType) => ({contentType,getBytes:()=>Buffer.isBuffer(x)?x:Buffer.from(x), getDataAsString:()=>Buffer.from(x).toString()});
  const props = {getProperty:k=>data[k]??null, setProperty:(k,v)=>{data[k]=v;},
    getProperties:()=>({...data}), deleteProperty:k=>{delete data[k];}};
  const context = {
    PropertiesService:{getScriptProperties:()=>props},
    LockService:{getScriptLock:()=>({tryLock:()=>{if(locked)return false;locked=true;return true;},releaseLock:()=>{locked=false;}})},
    ContentService:{MimeType:{JSON:'json'},createTextOutput:s=>({setMimeType:()=>JSON.parse(s)})},
    Utilities:{Charset:{UTF_8:'utf8'}, DigestAlgorithm:{SHA_256:'sha256'},
      computeHmacSha256Signature:(p,s)=>[...crypto.createHmac('sha256',s).update(p).digest()],
      computeDigest:(a,p)=>[...crypto.createHash('sha256').update(p).digest()],
      newBlob:blob, gzip:b=>blob(zlib.gzipSync(b.getBytes()),'application/x-gzip'), ungzip:b=>{assert.equal(b.contentType,'application/x-gzip');return blob(zlib.gunzipSync(b.getBytes()));},
      base64Encode:b=>Buffer.from(b).toString('base64'),base64Decode:s=>Buffer.from(s,'base64')},
    Engine:{Config:{runLevel:()=>level,LEVELS:{LEVEL_0:'LEVEL_0',LEVEL_2:'LEVEL_2'},
      _setRunLevel:x=>{level=x;},hasSecret:()=>true},
      OpenAIAdapter:{create:()=>({})},AnthropicAdapter:{create:()=>({})},
      Orchestrator:{run:()=>{calls++;return {status:'COMPLETED',execution_id:'engine-1',route:'CROSS_AUDIT',
        final_answer:'Texto íntegro 漢🙂'.repeat(5000),limits:{estimated_cost_usd:.2,cost_known:true},
        handoff:{reconciled:true},audit:{blocks_materially:false},degradation:null};}}},
    Date,Number,JSON,Object,Error
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(__dirname+'/Bridge.gs','utf8'), context);
  function request(seq=1,action='run',override={}) {
    const p = {seq,id:'11111111-1111-4111-8111-111111111111',action,request:'Consulta Metis',
      fingerprint:crypto.createHash('sha256').update('Consulta Metis').digest('hex'),timestamp:Math.floor(Date.now()/1000),...override};
    const payload=JSON.stringify(p), signature=crypto.createHmac('sha256',secret).update(payload).digest('hex');
    return context.doPost({postData:{contents:JSON.stringify({payload,signature})}});
  }
  return {context,data,request,calls:()=>calls,level:()=>level};
}
function test(name, fn) {fn(); tests++; console.log('PASS '+name);}
test('Authenticated run; exact full answer, engine ID, route, cost, restored level',()=>{
  const f=fixture(), r=f.request();
  assert.equal(r.state,'DONE');assert.equal(r.result.engine_execution_id,'engine-1');
  assert.equal(r.result.final_answer,'Texto íntegro 漢🙂'.repeat(5000));
  assert.equal(r.result.route,'CROSS_AUDIT');assert.equal(r.result.cost_usd,.2);
  assert.equal(f.level(),'LEVEL_0');assert.equal(f.calls(),1);
});
test('Retry after response loss does not repeat paid run',()=>{
  const f=fixture();f.request();f.request();f.request(1,'status');assert.equal(f.calls(),1);
});
test('Old sequence never runs after cache advances',()=>{
  const f=fixture();f.request();f.request(2);assert.equal(f.request().state,'GONE');assert.equal(f.calls(),2);
});
test('Conflicting identity or payload cannot reuse receipt',()=>{
  const f=fixture();f.request();assert.equal(f.request(1,'run',{fingerprint:'0'.repeat(64)}).state,'CONFLICT');assert.equal(f.calls(),1);
});
test('Missing delivery becomes tombstone; late run cannot spend',()=>{
  const f=fixture();assert.equal(f.request(1,'status').result.error,'DISPATCH_NOT_DELIVERED');
  f.request();assert.equal(f.calls(),0);
});
test('Terminated engine is technical failure, no automatic restart',()=>{
  const f=fixture();f.request();let receipt=JSON.parse(f.data.receipt);receipt.state='RUNNING';
  f.data.receipt=JSON.stringify(receipt);assert.equal(f.request(1,'status').result.error,'ENGINE_INTERRUPTED');
  f.request();assert.equal(f.calls(),1);
});
test('Engine human decision and budget failure retained, not substituted',()=>{
  for (const status of ['REQUIRES_ANDRES','FAILED']) {
    const f=fixture();f.context.Engine.Orchestrator.run=()=>({status,execution_id:'blocked',final_answer:'Límite alcanzado',limits:{cost_known:true,estimated_cost_usd:0}});
    assert.equal(f.request().result.status,status);
  }
});
test('Fail closed on missing secret, invalid signature, stale request and fingerprint',()=>{
  const f=fixture();
  assert.equal(f.request(1,'run',{timestamp:1}).error,'invalid_request');
  assert.equal(f.request(1,'run',{fingerprint:'0'.repeat(64)}).state,'CONFLICT');
  assert.equal(f.context.doPost({postData:{contents:JSON.stringify({payload:'{}',signature:'0'.repeat(64)})}}).error,'unauthorized');
  delete f.data.GATEWAY_BRIDGE_SECRET;assert.equal(f.request().error,'unauthorized');assert.equal(f.calls(),0);
});
test('Pin remains immutable and bridge contains no log/trigger/provider endpoint',()=>{
  const manifest=JSON.parse(fs.readFileSync(__dirname+'/appsscript.json'));
  assert.equal(manifest.dependencies.libraries[0].version,'5');
  assert.equal(manifest.dependencies.libraries[0].developmentMode,false);
  assert.doesNotMatch(fs.readFileSync(__dirname+'/Bridge.gs','utf8'),/Logger\.|console\.|newTrigger|api\.openai\.com|api\.anthropic\.com/);
});
console.log(`${tests} bridge checks passed`);
