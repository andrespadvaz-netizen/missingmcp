/** App-view reader for weekly descriptive trends (Política Operativa v0.5, section 7).
 *  Returns weekly aggregates only, through an explicit field allowlist; never
 *  single readings, provenance or credentials.  Not for the e-mail Brief. */
var HealthOSTrendsAdapter = (function () {
  var FIELDS = ['metric', 'unit', 'week_start', 'aggregate', 'value', 'valid_days', 'baseline_value',
    'baseline_days', 'change_vs_baseline', 'total_minutes', 'strength_sessions', 'rule'];
  function pick(row) {
    var out = {};
    FIELDS.forEach(function (f) {
      var v = row[f];
      if (v === null || typeof v === 'number' || (typeof v === 'string' && v.length <= 64)) { out[f] = v === undefined ? null : v; }
    });
    return out;
  }
  function read() {
    var base = Config.setting('HEALTH_OS_BASE_URL');
    if (!base || !/^https:\/\/[a-z0-9.-]+(?:\/[^?#]*)?$/i.test(base) || !Config.hasSecret('HEALTH_OS_READ_KEY')) {
      return { available: false, reason: 'configuración Health OS ausente', weeks: [] };
    }
    try {
      var response = UrlFetchApp.fetch(base.replace(/\/+$/, '') + '/health-os/trends', {
        method: 'get', muteHttpExceptions: true,
        headers: { Authorization: 'Bearer ' + Config.secret('HEALTH_OS_READ_KEY') }
      });
      if (response.getResponseCode() !== 200) { return { available: false, reason: 'Health OS HTTP ' + response.getResponseCode(), weeks: [] }; }
      var body = JSON.parse(response.getContentText());
      var trends = body && body.status === 'OK' ? body.trends : null;
      if (!trends || trends.available !== true || Object.prototype.toString.call(trends.weeks) !== '[object Array]') {
        return { available: false, reason: (trends && trends.reason) || 'respuesta Health OS inválida', weeks: [] };
      }
      return { available: true, reason: null, weeks: trends.weeks.slice(0, 40).map(pick) };
    } catch (error) { return { available: false, reason: 'Health OS no respondió', weeks: [] }; }
  }
  return { read: read };
})();

/** Entry point for the app view (not the e-mail Brief). */
function readHealthOSTrendsForApp() { return HealthOSTrendsAdapter.read(); }
