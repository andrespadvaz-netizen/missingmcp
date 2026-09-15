/** Prepare one operator-copyable block in the same protected property store. */
function prepareConnectionCopy() {
  var p = PropertiesService.getScriptProperties();
  var signing = p.getProperty('GATEWAY_BRIDGE_SECRET');
  var operator = p.getProperty('GATEWAY_OPERATOR_KEY');
  if (!signing || !operator) { throw new Error('Initial keys not ready'); }
  p.setProperty('RAILWAY_SETUP', JSON.stringify({
    METIS_BRIDGE_URL:'https://script.google.com/macros/s/AKfycbzQsyCh1k2aGCib_8TVBbWvrGCNcyAUZiGbg2Ho-JoDC6w16jMaBuQfzdvzJyUp_Typ/exec',
    METIS_BRIDGE_SECRET:signing, METIS_OPERATOR_KEY:operator}));
  console.log('Copy block ready in RAILWAY_SETUP. No key values logged.');
}
