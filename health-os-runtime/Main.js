/** Authorized entry point for the Daily Brief. */
function readHealthOSBriefForDailyBrief(subjectiveFlags) {
  return HealthOSBriefAdapter.read(subjectiveFlags || []);
}

/**
 * Manual, PHI-minimised proof that the protected read path is live.
 * A completed Apps Script execution is not sufficient: this function throws
 * unless Health OS actually returned a valid allowlisted snapshot.
 */
function verifyHealthOSBriefRuntime() {
  var result = readHealthOSBriefForDailyBrief([]);
  if (!result || result.available !== true || result.status !== 'OK') {
    throw new Error('Health OS read verification failed: ' +
      (result && result.status ? result.status : 'unknown') + '; ' +
      (result && result.reason ? result.reason : 'no reason returned'));
  }
  var snapshot = result.snapshot || {};
  var receipt = {
    available: true,
    status: result.status,
    decision: snapshot.decision || null,
    quality_pass: !!(snapshot.quality && snapshot.quality.pass),
    stale: !!(snapshot.quality && snapshot.quality.stale),
    legend_present: typeof result.legend === 'string' && result.legend.indexOf('911') >= 0
  };
  console.log(JSON.stringify(receipt));
  return receipt;
}
