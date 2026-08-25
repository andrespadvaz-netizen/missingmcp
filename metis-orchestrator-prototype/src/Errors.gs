/**
 * Errors.gs — errores tipados + redacción.
 *
 * Regla dura (spec §11, §12, §14): ningún secreto, header, cookie ni body crudo
 * de proveedor puede salir hacia logs, ledger, handoffs o contexto de modelo.
 * Todo error de proveedor pasa por `redact()` ANTES de ser expuesto.
 */
var Errors = (function () {
  var CODES = {
    CONFIG: 'CONFIG_ERROR',
    MISSING_CREDENTIAL: 'MISSING_CREDENTIAL',
    CONTEXT_AMBIGUOUS: 'CONTEXT_AMBIGUOUS',
    AUTHORITY_CONFLICT: 'AUTHORITY_CONFLICT',
    AUTHORITY_DENIED: 'AUTHORITY_DENIED',
    AUTHORITY_WIDENED: 'AUTHORITY_WIDENED',
    TOOL_NOT_ALLOWED: 'TOOL_NOT_ALLOWED',
    PLAN_INVALID: 'PLAN_INVALID',
    LEDGER_UNAVAILABLE: 'LEDGER_UNAVAILABLE',
    LEDGER_CONSISTENCY: 'LEDGER_CONSISTENCY',
    LEDGER_SUBSTANCE: 'LEDGER_SUBSTANCE',
    HANDOFF_EXPIRED: 'HANDOFF_EXPIRED',
    HANDOFF_REPLAY: 'HANDOFF_REPLAY',
    HANDOFF_INVALID: 'HANDOFF_INVALID',
    LIMIT_EXCEEDED: 'LIMIT_EXCEEDED',
    PROVIDER: 'PROVIDER_ERROR',
    READ_FAILED: 'READ_FAILED',
    SCHEMA: 'SCHEMA_ERROR',
    REAL_WRITE_ATTEMPT: 'REAL_WRITE_ATTEMPT',
    LEVEL_VIOLATION: 'LEVEL_VIOLATION'
  };

  /** Fragmentos que nunca deben aparecer en un mensaje expuesto. */
  var SECRET_MARKERS = [
    /sk-[A-Za-z0-9_\-]{8,}/g,
    /sk-ant-[A-Za-z0-9_\-]{8,}/g,
    /secret_[A-Za-z0-9_\-]{8,}/g,
    /Bearer\s+[A-Za-z0-9._\-]{8,}/gi,
    /"?(authorization|x-api-key|api[_-]?key|cookie|set-cookie)"?\s*[:=]\s*"?[^"\s,}]+/gi
  ];

  function make(code, message, details) {
    var err = new Error(message);
    err.name = 'MetisError';
    err.code = code;
    err.details = details || {};
    err.metis = true;
    return err;
  }

  function is(err, code) {
    return !!err && err.metis === true && err.code === code;
  }

  function configError(msg) { return make(CODES.CONFIG, msg); }
  function missingCredential(symbolicKey) {
    // Sólo la clave simbólica; nunca el nombre de propiedad ni el valor.
    return make(CODES.MISSING_CREDENTIAL, 'Credencial ausente para capacidad: ' + symbolicKey, { capability: symbolicKey });
  }
  function contextAmbiguous(candidates) {
    return make(CODES.CONTEXT_AMBIGUOUS, 'Contexto ambiguo entre candidatos incompatibles', { candidates: candidates });
  }
  function authorityDenied(reason, details) { return make(CODES.AUTHORITY_DENIED, reason, details); }
  function planInvalid(reason, details) { return make(CODES.PLAN_INVALID, reason, details); }
  function ledgerUnavailable(reason) { return make(CODES.LEDGER_UNAVAILABLE, 'Ledger no disponible: ' + reason); }
  function ledgerConsistency(actionId) {
    return make(CODES.LEDGER_CONSISTENCY, 'Payload distinto para el mismo ordinal: ' + actionId, { action_id: actionId });
  }
  function handoffExpired(handoffId) { return make(CODES.HANDOFF_EXPIRED, 'Handoff caducado: ' + handoffId, { handoff_id: handoffId }); }
  function handoffReplay(handoffId) { return make(CODES.HANDOFF_REPLAY, 'Handoff ya reconciliado: ' + handoffId, { handoff_id: handoffId }); }
  function limitExceeded(limitName, value) {
    return make(CODES.LIMIT_EXCEEDED, 'Límite alcanzado: ' + limitName, { limit: limitName, value: value });
  }
  function toolNotAllowed(tool) { return make(CODES.TOOL_NOT_ALLOWED, 'Herramienta fuera de la whitelist: ' + tool, { tool: tool }); }
  function schemaError(msg, details) { return make(CODES.SCHEMA, msg, details); }
  function realWriteAttempt(tool) {
    return make(CODES.REAL_WRITE_ATTEMPT, 'Intento de escritura real bloqueado: ' + tool, { tool: tool });
  }
  function levelViolation(msg) { return make(CODES.LEVEL_VIOLATION, msg); }
  function readFailed(source, safeReason) {
    return make(CODES.READ_FAILED, 'Lectura fallida en ' + source + ': ' + redactText(safeReason), { source: source });
  }

  /** Redacta cualquier cadena antes de log/retorno. */
  function redactText(text) {
    if (text === null || text === undefined) { return ''; }
    var out = String(text);
    for (var i = 0; i < SECRET_MARKERS.length; i++) {
      out = out.replace(SECRET_MARKERS[i], '[REDACTED]');
    }
    return out;
  }

  /**
   * Redacción de error de proveedor (spec §11 `redactProviderError`).
   * Devuelve SOLO: proveedor, status, código estable y mensaje saneado.
   * Nunca headers, nunca body crudo, nunca la petición.
   */
  function redactProviderError(provider, error, status) {
    var safeMessage = '';
    if (error && typeof error === 'object' && error.message) {
      safeMessage = redactText(error.message);
    } else if (typeof error === 'string') {
      safeMessage = redactText(error);
    }
    // Truncado: evita arrastrar bodies completos al contexto del modelo.
    if (safeMessage.length > 240) { safeMessage = safeMessage.slice(0, 240) + '…'; }
    return {
      provider: provider,
      status: (status === undefined || status === null) ? null : status,
      code: CODES.PROVIDER,
      message: safeMessage
    };
  }

  function providerError(provider, status, safeMessage) {
    return make(CODES.PROVIDER, 'Error de proveedor ' + provider, {
      provider: provider,
      status: (status === undefined ? null : status),
      message: redactText(safeMessage)
    });
  }

  return {
    CODES: CODES,
    make: make,
    is: is,
    configError: configError,
    missingCredential: missingCredential,
    contextAmbiguous: contextAmbiguous,
    authorityDenied: authorityDenied,
    planInvalid: planInvalid,
    ledgerUnavailable: ledgerUnavailable,
    ledgerConsistency: ledgerConsistency,
    handoffExpired: handoffExpired,
    handoffReplay: handoffReplay,
    limitExceeded: limitExceeded,
    toolNotAllowed: toolNotAllowed,
    schemaError: schemaError,
    realWriteAttempt: realWriteAttempt,
    levelViolation: levelViolation,
    readFailed: readFailed,
    redactText: redactText,
    redactProviderError: redactProviderError,
    providerError: providerError
  };
})();
