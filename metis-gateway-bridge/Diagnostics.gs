/** Manual, read-only probe. Logs booleans/version only; never secret values. */
function checkBridgeCapabilities() {
  console.log(JSON.stringify(inspectBridge()));
  inspectCachedTransport();
  console.log(JSON.stringify({daily_estimated_usd:Engine.Ledger.spend('DAILY'),monthly_estimated_usd:Engine.Ledger.spend('MONTHLY')}));
}

/** Initial provisioning only. Refuses to replace any existing credential. */
function initializeBridgeKeys() {
  var p = PropertiesService.getScriptProperties();
  if (p.getProperty('GATEWAY_BRIDGE_SECRET') || p.getProperty('GATEWAY_OPERATOR_KEY')) {
    throw new Error('Keys already provisioned; refusing rotation');
  }
  function randomKey() { return (Utilities.getUuid()+Utilities.getUuid()).replace(/-/g,''); }
  p.setProperties({GATEWAY_BRIDGE_SECRET:randomKey(), GATEWAY_OPERATOR_KEY:randomKey()});
  console.log('Initial connection keys provisioned. Values are not logged.');
}
/** Read cached transport result only; never invoke the engine or expose keys. */
function inspectCachedTransport() {
  var p = PropertiesService.getScriptProperties();
  var r = JSON.parse(p.getProperty('receipt') || '{}');
  console.log(JSON.stringify({seq:r.seq,state:r.state}));
  try {
    var c = cached_(p,r);
    console.log(JSON.stringify({status:c.result.status,engine_execution_id:c.result.engine_execution_id,cost_usd:c.result.cost_usd}));
  } catch(e) { console.log('Cache decoding failed: '+e.message); }
}

/** Operator-only incident closure. Not exposed through doPost or the client tools.
 * Evidence: provider usage + Andres' confirmation of exclusive use in this window.
 * No model invocation, limit change, success substitution or engine source edit.
 */
function reconcileInterruptedSeq4() {
  var review = {
    execution_id:'5e151e04-02f3-42b1-8464-b5b8370a27ad', seq:4,
    day:'2026-09-15', cost_usd:0.072375,
    daily_before:0.39979124999999993, monthly_before:5.139077499999999,
    evidence_sha256:'8ebe9f1018bb9766dd073ec8bbadd70f681bae9bb3f0353f38a6eba40f806571',
    provider_request_id:'req_011Cf4b2ZzVN6kukcNnHX2Rn'
  };
  applyAccountingReview_(review);
  console.log('Accounting reviewed; execution remains FAILED. No model was called.');
}

function applyAccountingReview_(review) {
  var lock=LockService.getScriptLock();
  if (!lock.tryLock(100)) { throw new Error('Execution still locked'); }
  try {
    var props=PropertiesService.getScriptProperties();
    var receipt=JSON.parse(props.getProperty('receipt') || '{}');
    if (receipt.id!==review.execution_id || receipt.seq!==review.seq || receipt.state!=='DONE') {
      throw new Error('Receipt changed; review required');
    }
    var result=cached_(props,receipt).result;
    if (result.accounting_reconciliation && result.accounting_reconciliation.evidence_sha256===review.evidence_sha256) { return; }
    if (result.status!=='FAILED' || result.error!=='ENGINE_INTERRUPTED' || result.cost_known!==false ||
        Engine.Config.runLevel()!==Engine.Config.LEVELS.LEVEL_0 ||
        new Date().toISOString().slice(0,10)!==review.day) { throw new Error('Review precondition changed'); }
    var daily=Engine.Ledger.spend('DAILY'), monthly=Engine.Ledger.spend('MONTHLY');
    function equal(a,b) {return Math.abs(a-b)<1e-9;}
    var journalKey='accounting_review_'+review.seq;
    var journal=props.getProperty(journalKey);
    if (journal && JSON.parse(journal).evidence_sha256!==review.evidence_sha256) { throw new Error('Conflicting review'); }
    if (equal(daily,review.daily_before) && equal(monthly,review.monthly_before)) {
      props.setProperty(journalKey,JSON.stringify(review));
      Engine.Ledger.addSpend(review.cost_usd);
    } else if (!journal || !equal(daily,review.daily_before+review.cost_usd) ||
               !equal(monthly,review.monthly_before+review.cost_usd)) {
      // Includes partial ledger writes: never add again or guess the missing side.
      throw new Error('Ledger changed or partially updated; keep paused');
    }
    if (!equal(Engine.Ledger.spend('DAILY'),review.daily_before+review.cost_usd) ||
        !equal(Engine.Ledger.spend('MONTHLY'),review.monthly_before+review.cost_usd)) {
      throw new Error('Ledger verification failed');
    }
    result.cost_usd=review.cost_usd; result.cost_known=true; result.requires_review=false;
    result.accounting_reconciliation={execution_id:review.execution_id,seq:review.seq,
      evidence_sha256:review.evidence_sha256,ledger_verified:true,
      provider_request_id:review.provider_request_id,cost_basis:'provider_usage_at_published_rate',
      reviewed_at:new Date().toISOString()};
    finish_(props,receipt,result);
  } finally {lock.releaseLock();}
}
