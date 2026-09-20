/** Transport only. Separate project; Engine library pinned to reviewed v9.
 * One monotonic receipt survives forever; only the latest result is cached.
 * Railway persists it before sending the next sequence. Old sequences never run.
 * Never log requests, results, exceptions or signing/provider credentials.
 */
function doPost(e) {
  try {
    var raw = e && e.postData && e.postData.contents;
    if (!raw || raw.length > 150000) { return json_({error: 'invalid_request'}); }
    var envelope = JSON.parse(raw);
    var secret = PropertiesService.getScriptProperties().getProperty('GATEWAY_BRIDGE_SECRET');
    if (!secret || secret.length < 32 || typeof envelope.payload !== 'string' || typeof envelope.signature !== 'string') {
      return json_({error: 'unauthorized'});
    }
    var signature = Utilities.computeHmacSha256Signature(envelope.payload, secret, Utilities.Charset.UTF_8)
      .map(function (b) { return ('0' + ((b + 256) % 256).toString(16)).slice(-2); }).join('');
    var mismatch = signature.length ^ envelope.signature.length;
    for (var i = 0; i < signature.length; i++) { mismatch |= signature.charCodeAt(i) ^ envelope.signature.charCodeAt(i); }
    if (mismatch) { return json_({error: 'unauthorized'}); }
    var p = JSON.parse(envelope.payload);
    if (!Number.isSafeInteger(p.timestamp) || Math.abs(Date.now()/1000-p.timestamp) > 90 ||
        !Number.isSafeInteger(p.seq) || p.seq < 1 || !/^[a-f0-9-]{36}$/.test(p.id) ||
        !/^[a-f0-9]{64}$/.test(p.fingerprint) || ['run','status','write','write_status'].indexOf(p.action) < 0) {
      return json_({error: 'invalid_request'});
    }
    return json_(p.action === 'write' || p.action === 'write_status' ? dispatchWrite_(p) : dispatch_(p));
  } catch (_) { return json_({error: 'bridge_failure'}); }
}

function json_(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}

function dispatch_(p) {
  var props = PropertiesService.getScriptProperties();
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(100)) { return {id: p.id, seq: p.seq, state: 'BUSY'}; }
  try {
    var receipt = JSON.parse(props.getProperty('receipt') || '{"seq":0}');
    if (p.seq < receipt.seq) { return {id:p.id, seq:p.seq, state:'GONE'}; }
    if (p.seq === receipt.seq) {
      if (p.id !== receipt.id || p.fingerprint !== receipt.fingerprint) {
        return {id:p.id, seq:p.seq, state:'CONFLICT'};
      }
      if (receipt.state === 'RUNNING') {
        // Acquiring this lock proves the prior executor ended (including timeout).
        finish_(props, receipt, {status:'FAILED', error:'ENGINE_INTERRUPTED',
          cost_usd:null, cost_known:false, engine_execution_id:null,
          final_answer:null, requires_review:true});
      }
      return cached_(props, receipt);
    }
    if (p.seq !== receipt.seq + 1) { return {id:p.id, seq:p.seq, state:'SEQUENCE_GAP'}; }
    if (p.action === 'run') {
      if (p.origin_model !== undefined && ['OPENAI','ANTHROPIC'].indexOf(p.origin_model) < 0) {
        return {id:p.id,seq:p.seq,state:'INVALID_ORIGIN'};
      }
      if (typeof p.request !== 'string' || !p.request.trim() || Utilities.newBlob(p.request).getBytes().length > 16000) {
        return {id:p.id, seq:p.seq, state:'INVALID_REQUEST'};
      }
      var hash = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, p.request, Utilities.Charset.UTF_8)
        .map(function(b) { return ('0'+((b+256)%256).toString(16)).slice(-2); }).join('');
      if (hash !== p.fingerprint) { return {id:p.id, seq:p.seq, state:'CONFLICT'}; }
    }
    // Commit spend receipt BEFORE the engine. Never erase or decrement seq.
    receipt = {seq:p.seq, id:p.id, fingerprint:p.fingerprint, state:'RUNNING', chunks:0};
    props.setProperty('receipt', JSON.stringify(receipt));
    if (p.action === 'status') {
      // Close an undelivered dispatch. Any delayed run now observes this tombstone.
      finish_(props, receipt, {status:'FAILED', error:'DISPATCH_NOT_DELIVERED',
        cost_usd:0, cost_known:true, engine_execution_id:null, final_answer:null});
      return cached_(props, receipt);
    }
    var previous = Engine.Config.runLevel();
    try {
      if (previous !== Engine.Config.LEVELS.LEVEL_0) { throw new Error('unexpected_level'); }
      var productive = props.getProperty('GATEWAY_PRODUCTIVE_ENABLED') === 'true';
      Engine.Config._setRunLevel(productive ? 'LEVEL_3' : Engine.Config.LEVELS.LEVEL_2);
      var result = Engine.Orchestrator.run(p.request, {
        // First client is Claude. Identity is server-declared, never client-selected.
        current_model: p.origin_model || 'ANTHROPIC',
        mandate: {source:'FIXED_POLICY',requests_execution:productive},
        providers: {OPENAI:Engine.OpenAIAdapter.create(), ANTHROPIC:Engine.AnthropicAdapter.create()}
      });
      var output = {
        status: ['COMPLETED','FAILED','REQUIRES_ANDRES'].indexOf(result.status) >= 0 ? result.status : 'REQUIRES_ANDRES',
        engine_status:result.status, engine_execution_id:result.execution_id,
        route:result.route, final_answer:result.final_answer,
        cost_usd:result.limits.estimated_cost_usd, cost_known:result.limits.cost_known,
        limits:result.limits, handoff:result.handoff, audit:result.audit,
        degradation:result.degradation, blocks:result.blocks,
        reconciliation_stop_reason:result.reconciliation_stop_reason,
        requires_review:result.limits.cost_known !== true,
        engine_version:9,
        resolved_context:result.resolved_context,
        write_plan:result.write_plan || [],
        write_plan_verified:productive && result.status === 'COMPLETED' && !!result.action_plan && result.action_plan.valid === true
      };
      finish_(props, receipt, output);
    } catch (_) {
      finish_(props, receipt, {status:'FAILED', error:'ENGINE_OR_PERSISTENCE_FAILURE',
        cost_usd:null, cost_known:false, engine_execution_id:null, final_answer:null, requires_review:true});
    } finally { Engine.Config._setRunLevel(previous); }
    return cached_(props, receipt);
  } finally { lock.releaseLock(); }
}

function finish_(props, receipt, result) {
  var packed = Utilities.base64Encode(Utilities.gzip(Utilities.newBlob(JSON.stringify(result))).getBytes());
  if (packed.length > 360000) {
    result = {status:'FAILED', error:'RESULT_STORAGE_CAPACITY', requires_review:true,
      engine_execution_id:result.engine_execution_id, cost_usd:result.cost_usd,
      cost_known:result.cost_known, final_answer:null};
    packed = Utilities.base64Encode(Utilities.gzip(Utilities.newBlob(JSON.stringify(result))).getBytes());
  }
  // Latest result only; receipts block repeats even if this write is interrupted.
  var old = props.getProperties();
  Object.keys(old).filter(function(k) {return /^result_\d+$/.test(k);}).forEach(function(k) {props.deleteProperty(k);});
  var chunks = Math.ceil(packed.length / 8000);
  for (var i=0; i<chunks; i++) { props.setProperty('result_'+i, packed.slice(i*8000,(i+1)*8000)); }
  receipt.state = 'DONE'; receipt.chunks = chunks;
  props.setProperty('receipt', JSON.stringify(receipt));
}

function cached_(props, receipt) {
  var packed = '';
  for (var i=0;i<receipt.chunks;i++) {packed += props.getProperty('result_'+i) || '';}
  return {id:receipt.id, seq:receipt.seq, state:'DONE',
    result:JSON.parse(Utilities.ungzip(Utilities.newBlob(Utilities.base64Decode(packed), 'application/x-gzip')).getDataAsString('UTF-8'))};
}

/** Read-only capability probe: no credential values and no provider calls. */
function inspectBridge() {
  return {engine_version:9, level:Engine.Config.runLevel(),
    productive_enabled:PropertiesService.getScriptProperties().getProperty('GATEWAY_PRODUCTIVE_ENABLED')==='true',
    providers_ready:Engine.Config.hasSecret('OPENAI_API_KEY') && Engine.Config.hasSecret('ANTHROPIC_API_KEY'),
    signing_ready:!!PropertiesService.getScriptProperties().getProperty('GATEWAY_BRIDGE_SECRET')};
}
