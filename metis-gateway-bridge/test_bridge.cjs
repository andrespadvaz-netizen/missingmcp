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
  vm.runInContext(fs.readFileSync(__dirname+'/WriteBridge.gs','utf8'), context);
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
  assert.equal(manifest.dependencies.libraries[0].version,'8');
  assert.equal(manifest.dependencies.libraries[0].developmentMode,false);
  assert.doesNotMatch(fs.readFileSync(__dirname+'/Bridge.gs','utf8'),/Logger\.|console\.|newTrigger|api\.openai\.com|api\.anthropic\.com/);
});
console.log(`${tests} bridge checks passed`);

function writeFixture() {
  const f=fixture();let effects=0;f.data.GATEWAY_PRODUCTIVE_ENABLED='true';
  f.context.Engine.ProductiveAdapter={apply:()=>{
    const r=JSON.parse(f.data.write_receipt);assert.equal(r.state,'RUNNING');
    assert.equal(f.level(),'LEVEL_3');effects++;
    return {status:'CONFIRMED',verified:true,provider_object_id:'synthetic-object'};
  }};
  const write={context:'TEST',payload:{text:'Acentos á y 漢🙂'}};
  const fingerprint=crypto.createHash('sha256').update(f.context.canonicalWrite_(write)).digest('hex');
  return {...f,effects:()=>effects,send:(seq=1,action='write',overrides={})=>f.request(seq,action,{write,fingerprint,...overrides})};
}
test('Write receipt precedes effect and exact replay cannot duplicate',()=>{
  const f=writeFixture();assert.equal(f.send().result.status,'CONFIRMED');
  assert.equal(f.send().result.provider_object_id,'synthetic-object');
  assert.equal(f.send(1,'write_status').result.status,'CONFIRMED');
  assert.equal(f.effects(),1);assert.equal(f.level(),'LEVEL_0');
});
test('Undelivered write status seals a tombstone against late delivery',()=>{
  const f=writeFixture();assert.equal(f.send(1,'write_status').result.error,'WRITE_NOT_DELIVERED');
  assert.equal(f.send().result.status,'REJECTED');assert.equal(f.effects(),0);
});
test('Interrupted write reports uncertainty without replay',()=>{
  const f=writeFixture();f.send();const r=JSON.parse(f.data.write_receipt);r.state='RUNNING';delete r.result;
  f.data.write_receipt=JSON.stringify(r);
  assert.equal(f.send(1,'write_status').result.status,'UNCERTAIN');f.send();assert.equal(f.effects(),1);
});
test('Disabled, conflicting, stale and out of sequence writes cause zero effects',()=>{
  const f=writeFixture();assert.equal(f.send(2).state,'SEQUENCE_GAP');
  assert.equal(f.send(1,'write',{fingerprint:'0'.repeat(64)}).state,'CONFLICT');
  assert.equal(f.send(1,'write',{timestamp:1}).error,'invalid_request');
  f.data.GATEWAY_PRODUCTIVE_ENABLED='false';assert.equal(f.send().result.status,'REJECTED');assert.equal(f.effects(),0);
});
test('Lost write receipt save preserves RUNNING barrier',()=>{
  const f=writeFixture(),props=f.context.PropertiesService.getScriptProperties(),save=props.setProperty;
  let lost=false;props.setProperty=(k,v)=>{if(k==='write_receipt' && JSON.parse(v).state==='DONE' && !lost){lost=true;throw Error('storage cut');}save(k,v);};
  f.send();assert.equal(f.effects(),1);
  assert.equal(f.send(1,'write_status').result.status,'UNCERTAIN');assert.equal(f.effects(),1);
});

function accountingFixture() {
  const f=fixture(); f.request();
  const receipt=JSON.parse(f.data.receipt);
  f.context.finish_(f.context.PropertiesService.getScriptProperties(),receipt,
    {status:'FAILED',error:'ENGINE_INTERRUPTED',cost_known:false,cost_usd:null,requires_review:true,final_answer:null});
  vm.runInContext(fs.readFileSync(__dirname+'/Diagnostics.gs','utf8'),f.context);
  let daily=.4,monthly=5,counterCalls=0;
  f.context.Engine.Ledger={spend:s=>s==='DAILY'?daily:monthly,addSpend:n=>{daily+=n;monthly+=n;counterCalls++;}};
  const review={execution_id:receipt.id,seq:receipt.seq,day:new Date().toISOString().slice(0,10),
    cost_usd:.07,daily_before:.4,monthly_before:5,evidence_sha256:'a'.repeat(64),provider_request_id:'req-test'};
  return {f,review,counterCalls:()=>counterCalls,resetDaily:n=>{daily=n;}};
}
test('Operator accounting review adds spend exactly once and preserves FAILED',()=>{
  const {f,review,counterCalls}=accountingFixture();
  f.context.applyAccountingReview_(review); f.context.applyAccountingReview_(review);
  const result=f.request(1,'status').result;
  assert.equal(result.status,'FAILED');assert.equal(result.final_answer,null);
  assert.equal(result.cost_usd,.07);assert.equal(result.accounting_reconciliation.ledger_verified,true);
  assert.equal(counterCalls(),1);assert.equal(f.calls(),1);
});
test('Interrupted accounting write resumes without charging again',()=>{
  const {f,review,counterCalls}=accountingFixture();
  const finish=f.context.finish_;f.context.finish_=()=>{throw Error('storage failure');};
  assert.throws(()=>f.context.applyAccountingReview_(review));
  f.context.finish_=finish;f.context.applyAccountingReview_(review);
  assert.equal(counterCalls(),1);assert.equal(f.request(1,'status').result.cost_known,true);
});
test('Changed counters or receipt keep the incident paused',()=>{
  const {f,review,counterCalls,resetDaily}=accountingFixture();resetDaily(.43);
  assert.throws(()=>f.context.applyAccountingReview_(review));assert.equal(counterCalls(),0);
  assert.equal(f.request(1,'status').result.cost_known,false);
  review.seq=2;assert.throws(()=>f.context.applyAccountingReview_(review));
});
console.log(`${tests} total bridge checks passed`);
