/**
 * HealthOSBriefAdapter.gs — private, minimum-necessary reader for Block 2.
 *
 * This is deliberately not a general health-data adapter.  It requests only
 * the Health OS Brief snapshot and returns an allowlisted presentation model;
 * raw samples, provenance ids and the bearer credential never cross into the
 * prompt, ledger or Notion.
 */
var HealthOSBriefAdapter = (function () {
  // Política Operativa v0.5: URGENTE only from the closed symptom list.
  var ALLOWED_DECISIONS = ['INFORMATIVO', 'REVISAR_TECNICO', 'ABSTENERSE', 'URGENTE'];
  // Fixed v0.5 legend: shown on every output, including failures.
  var LEGEND = 'Health OS apoya la decisión; no diagnostica ni sustituye el juicio clínico ni los ' +
    'servicios de urgencia (911). Ante dolor o presión en el pecho, debilidad súbita, alteración del ' +
    'habla, falta de aire intensa o pérdida de respuesta, llama al 911 sin esperar al sistema.';
  var ALLOWED_CONFIDENCE = ['low', 'medium', 'high'];

  function unavailable(reason) {
    return {
      available: false,
      status: 'HEALTH_OS_NO_DISPONIBLE',
      reason: reason,
      snapshot: null,
      legend: LEGEND
    };
  }

  function _baseUrl() {
    var value = Config.setting('HEALTH_OS_BASE_URL');
    if (!value || !/^https:\/\/[a-z0-9.-]+(?:\/[^?#]*)?$/i.test(value)) {
      return null;
    }
    return value.replace(/\/+$/, '');
  }

  function _strings(value, limit) {
    if (Object.prototype.toString.call(value) !== '[object Array]') { return []; }
    return value.filter(function (item) { return typeof item === 'string'; })
      .slice(0, limit).map(function (item) { return item.slice(0, 280); });
  }

  function _snapshot(value) {
    if (!value || typeof value !== 'object' || ALLOWED_DECISIONS.indexOf(value.decision) < 0) {
      return null;
    }
    var quality = value.quality || {};
    var urgent = value.decision === 'URGENTE';
    if (urgent && (typeof value.instruction !== 'string' || value.instruction.indexOf('911') < 0)) {
      return null;
    }
    return {
      decision: value.decision,
      instruction: urgent ? value.instruction.slice(0, 280) : null,
      legend: LEGEND,
      confidence: ALLOWED_CONFIDENCE.indexOf(value.confidence) >= 0 ? value.confidence : 'low',
      factors: _strings(value.factors, 2),
      missing: _strings(value.missing, 3),
      quality: {
        pass: quality.pass === true,
        stale: quality.stale === true,
        legacy_after_transition: _strings(quality.legacy_after_transition, 3)
      }
    };
  }

  /**
   * Return a presentation-only snapshot. Failure is closed and non-fatal so
   * the rest of the daily Brief can continue while visibly naming this source.
   */
  function read(subjectiveFlags) {
    var base = _baseUrl();
    if (!base || !Config.hasSecret('HEALTH_OS_READ_KEY')) {
      return unavailable('configuracion Health OS ausente');
    }
    var flags = _strings(subjectiveFlags, 8);
    try {
      var response = UrlFetchApp.fetch(base + '/health-os/brief', {
        method: 'post',
        contentType: 'application/json',
        payload: JSON.stringify({ subjective_flags: flags }),
        muteHttpExceptions: true,
        headers: { Authorization: 'Bearer ' + Config.secret('HEALTH_OS_READ_KEY') }
      });
      if (response.getResponseCode() !== 200) { return unavailable('Health OS no respondio'); }
      var body = JSON.parse(response.getContentText());
      var snapshot = body && body.status === 'OK' ? _snapshot(body.brief) : null;
      return snapshot ? { available: true, status: 'OK', reason: null, snapshot: snapshot, legend: LEGEND } :
        unavailable('respuesta Health OS invalida');
    } catch (e) {
      return unavailable('Health OS no respondio');
    }
  }

  return { read: read };
})();
