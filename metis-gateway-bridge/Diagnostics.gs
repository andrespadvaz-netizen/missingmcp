/** Manual, read-only probe. Logs booleans/version only; never secret values. */
function checkBridgeCapabilities() {
  console.log(JSON.stringify(inspectBridge()));
  inspectCachedTransport();
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
