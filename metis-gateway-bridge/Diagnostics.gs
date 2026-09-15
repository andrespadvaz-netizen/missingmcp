/** Manual, read-only probe. Logs booleans/version only; never secret values. */
function checkBridgeCapabilities() {
  console.log(JSON.stringify(inspectBridge()));
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
