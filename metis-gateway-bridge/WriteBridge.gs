/** Separate monotonic receipt for writes. The caller commits its SQLite intent
 * before invoking this function. Cache is only a replay barrier, not the ledger.
 * Status never executes a mutation, even if the original request arrives late.
 */
function canonicalWrite_(v) {
  if(v===null || typeof v!=='object') { return JSON.stringify(v); }
  if(Array.isArray(v)) { return '['+v.map(canonicalWrite_).join(',')+']'; }
  return '{'+Object.keys(v).sort().map(function(k){return JSON.stringify(k)+':'+canonicalWrite_(v[k]);}).join(',')+'}';
}
function dispatchWrite_(p) {
  if (!p.write || typeof p.write!=='object') { return {error:'invalid_write'}; }
  var hash=Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,canonicalWrite_(p.write),Utilities.Charset.UTF_8)
    .map(function(b){return ('0'+((b+256)%256).toString(16)).slice(-2);}).join('');
  if(hash!==p.fingerprint) { return {id:p.id,seq:p.seq,state:'CONFLICT'}; }
  var props=PropertiesService.getScriptProperties(), lock=LockService.getScriptLock();
  if(!lock.tryLock(100)) { return {id:p.id,seq:p.seq,state:'BUSY'}; }
  try {
    var r=JSON.parse(props.getProperty('write_receipt') || '{"seq":0}');
    if(p.seq<r.seq) { return {id:p.id,seq:p.seq,state:'GONE'}; }
    if(p.seq===r.seq) {
      if(r.id!==p.id || r.fingerprint!==p.fingerprint) { return {id:p.id,seq:p.seq,state:'CONFLICT'}; }
      if(r.state==='RUNNING') {
        r.state='DONE'; r.result={status:'UNCERTAIN',verified:false,error:'WRITE_INTERRUPTED',requires_review:true};
        props.setProperty('write_receipt',JSON.stringify(r));
      }
      return {id:p.id,seq:p.seq,state:'DONE',result:r.result};
    }
    if(p.seq!==r.seq+1) { return {id:p.id,seq:p.seq,state:'SEQUENCE_GAP'}; }
    r={seq:p.seq,id:p.id,fingerprint:p.fingerprint,state:'RUNNING'};
    props.setProperty('write_receipt',JSON.stringify(r));
    var previous=Engine.Config.runLevel(), result;
    try {
      if(p.action==='write_status') { result={status:'REJECTED',verified:false,error:'WRITE_NOT_DELIVERED'}; }
      else if(previous!=='LEVEL_0' || props.getProperty('GATEWAY_PRODUCTIVE_ENABLED')!=='true') {
        result={status:'REJECTED',verified:false,error:'PRODUCTIVE_WRITES_DISABLED'};
      } else {
        Engine.Config._setRunLevel('LEVEL_3');
        result=Engine.ProductiveAdapter.apply(p.write);
      }
    } catch(_) { result={status:'UNCERTAIN',verified:false,error:'WRITE_OR_PERSISTENCE_FAILURE',requires_review:true}; }
    finally { Engine.Config._setRunLevel(previous); }
    r.state='DONE'; r.result=result;
    props.setProperty('write_receipt',JSON.stringify(r));
    return {id:p.id,seq:p.seq,state:'DONE',result:result};
  } finally { lock.releaseLock(); }
}
