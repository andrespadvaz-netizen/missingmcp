/** Presentation-only Health OS reader: it never returns raw samples or credentials. */
var HealthOSBriefAdapter = (function () {
  // Política Operativa v0.5: URGENTE only from the closed symptom list.
  var ALLOWED_DECISIONS = ['INFORMATIVO', 'REVISAR_TECNICO', 'ABSTENERSE', 'URGENTE'];
  var ALLOWED_CONFIDENCE = ['low', 'medium', 'high'];
  // Fixed v0.5 legend: shown on every output, including failures.
  var LEGEND = 'Health OS apoya la decisión; no diagnostica ni sustituye el juicio clínico ni los ' +
    'servicios de urgencia (911). Ante dolor o presión en el pecho, debilidad súbita, alteración del ' +
    'habla, falta de aire intensa o pérdida de respuesta, llama al 911 sin esperar al sistema.';

  function unavailable(reason) {
    return { available: false, status: 'HEALTH_OS_NO_DISPONIBLE', reason: reason, snapshot: null, legend: LEGEND };
  }
  function strings(value, limit) {
    if (Object.prototype.toString.call(value) !== '[object Array]') { return []; }
    return value.filter(function (x) { return typeof x === 'string'; }).slice(0, limit)
      .map(function (x) { return x.slice(0, 280); });
  }
  function parse(value) {
    if (!value || typeof value !== 'object' || ALLOWED_DECISIONS.indexOf(value.decision) < 0) { return null; }
    var urgent = value.decision === 'URGENTE';
    if (urgent && (typeof value.instruction !== 'string' || value.instruction.indexOf('911') < 0)) { return null; }
    var quality = value.quality || {};
    return {
      decision: value.decision,
      instruction: urgent ? value.instruction.slice(0, 280) : null,
      legend: LEGEND,
      confidence: ALLOWED_CONFIDENCE.indexOf(value.confidence) >= 0 ? value.confidence : 'low',
      factors: strings(value.factors, 2),
      missing: strings(value.missing, 3),
      // Section 7: coverage is day counts per readiness metric, never values.
      coverage: (Object.prototype.toString.call(value.coverage) === '[object Array]' ? value.coverage : [])
        .slice(0, 4).filter(function (c) {
          return c && typeof c.metric === 'string' && typeof c.valid_days === 'number' && typeof c.required_days === 'number';
        }).map(function (c) { return { metric: c.metric.slice(0, 40), valid_days: c.valid_days, required_days: c.required_days }; }),
      quality: { pass: quality.pass === true, stale: quality.stale === true,
        legacy_after_transition: strings(quality.legacy_after_transition, 3) }
    };
  }
  function read(subjectiveFlags) {
    var base = Config.setting('HEALTH_OS_BASE_URL');
    if (!base || !/^https:\/\/[a-z0-9.-]+(?:\/[^?#]*)?$/i.test(base) || !Config.hasSecret('HEALTH_OS_READ_KEY')) {
      return unavailable('configuración Health OS ausente');
    }
    try {
      var response = UrlFetchApp.fetch(base.replace(/\/+$/, '') + '/health-os/brief', {
        method: 'post', contentType: 'application/json',
        payload: JSON.stringify({ subjective_flags: strings(subjectiveFlags, 8) }),
        muteHttpExceptions: true,
        headers: { Authorization: 'Bearer ' + Config.secret('HEALTH_OS_READ_KEY') }
      });
      if (response.getResponseCode() !== 200) {
        return unavailable('Health OS HTTP ' + response.getResponseCode());
      }
      var body = JSON.parse(response.getContentText());
      var snapshot = body && body.status === 'OK' ? parse(body.brief) : null;
      return snapshot ? { available: true, status: 'OK', reason: null, snapshot: snapshot, legend: LEGEND } : unavailable('respuesta Health OS inválida');
    } catch (error) { return unavailable('Health OS no respondió'); }
  }
  return { read: read };
})();
