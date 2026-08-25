/**
 * Schemas.gs — contratos del prototipo (spec §4) + validación estricta.
 *
 * "Estricta" significa: conjunto de campos EXACTO. Un campo desconocido es un
 * error, no un extra tolerado. Eso es lo que hace demostrable, por ejemplo,
 * que el LedgerEntry no puede contener sustancia de negocio (spec §4.7, §17).
 */
var Schemas = (function () {
  var ENUMS = {
    INTENT: ['retrieval', 'analysis', 'creation', 'decision', 'execution', 'audit'],
    STATUS: ['DETECTED', 'CONTEXT_RESOLVED', 'READY', 'RUNNING', 'REQUIRES_ANDRES', 'COMPLETED', 'FAILED', 'UNCERTAIN'],
    ROUTE: ['LOCAL', 'OPENAI', 'ANTHROPIC', 'CROSS_AUDIT', 'REQUIRES_ANDRES', 'ABSTAIN'],
    SOURCE: ['NOTION', 'ASANA', 'DRIVE', 'CALENDAR', 'OTHER'],
    EPISTEMIC: ['VERIFIED', 'INFERRED', 'PROPOSAL', 'REGISTERED_DECISION', 'HISTORICAL'],
    MODEL: ['OPENAI', 'ANTHROPIC'],
    AUTHORITY_SOURCE: ['LIVE_OPERATOR', 'FIXED_POLICY'],
    PROVENANCE: ['LIVE_OPERATOR', 'PRE_RETRIEVAL_SOURCE', 'RETRIEVED_CONTENT', 'FIXED_POLICY'],
    EFFECT: ['READ', 'SIMULATED_WRITE'],
    RECONCILIATION: ['RECONCILABLE', 'NON_RECONCILABLE', 'NOT_APPLICABLE'],
    ACTION_STATUS: ['PLANNED', 'INTENT_RECORDED', 'SENT', 'CONFIRMED', 'FAILED', 'UNCERTAIN', 'SIMULATED'],
    MODEL_ROLE: ['PRODUCER', 'AUDITOR', 'LOCAL']
  };

  var FIELDS = {
    Execution: ['execution_id', 'created_at', 'operator_request', 'candidate_contexts', 'resolved_context',
                'intent', 'status', 'route', 'evidence_refs', 'handoff', 'action_plan', 'final_answer'],
    EvidenceRef: ['source', 'object_id', 'title', 'context', 'epistemic_status', 'retrieved_at'],
    Handoff: ['handoff_id', 'execution_id', 'emitted_at', 'expires_at', 'origin_model', 'target_model',
              'objective', 'context', 'evidence_refs', 'restrictions', 'authority', 'expected_output', 'reconciled'],
    AuthorityGrant: ['source', 'allowed_tools', 'allowed_operations', 'allowed_contexts', 'forbidden_effects',
                     'writes_are_simulated_only'],
    ActionPlan: ['execution_id', 'actions', 'invariants_checked', 'valid', 'block_reason'],
    ActionStep: ['ordinal', 'action_id', 'tool', 'operation', 'destination', 'destination_provenance',
                 'payload_hash', 'effect', 'reconciliation', 'status'],
    LedgerEntry: ['execution_id', 'ordinal', 'action_id', 'payload_hash', 'tool', 'operation', 'destination_hash',
                  'status', 'provider_object_id', 'model_role', 'timestamp']
  };

  // ---------------------------------------------------------------- runtime
  var _clock = function () { return new Date(); };
  var _idFactory = function () {
    if (typeof Utilities !== 'undefined' && Utilities.getUuid) { return Utilities.getUuid(); }
    throw Errors.configError('No hay generador de UUID disponible');
  };

  /** Sólo para pruebas deterministas; una corrida real usa el reloj del runtime. */
  function setClock(fn) { _clock = fn; }
  function setIdFactory(fn) { _idFactory = fn; }
  function resetRuntime() {
    _clock = function () { return new Date(); };
    _idFactory = function () { return Utilities.getUuid(); };
  }

  function now() { return _clock(); }
  function nowIso() { return toIso(_clock()); }
  function nowMs() { return _clock().getTime(); }
  function uuid() { return _idFactory(); }

  function toIso(date) { return new Date(date.getTime()).toISOString(); }

  /** SHA-256 hex. Usa Utilities en Apps Script; el harness local inyecta el mismo contrato. */
  function sha256Hex(text) {
    var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(text), Utilities.Charset.UTF_8);
    var hex = '';
    for (var i = 0; i < bytes.length; i++) {
      var b = bytes[i] < 0 ? bytes[i] + 256 : bytes[i];
      var s = b.toString(16);
      hex += (s.length === 1 ? '0' + s : s);
    }
    return hex;
  }

  /** Huella normalizada del payload: guarda de consistencia, NO identidad (spec §5). */
  function payloadHash(payload) {
    return sha256Hex(canonicalize(payload));
  }

  function destinationHash(destination) {
    if (destination === null || destination === undefined) { return null; }
    return sha256Hex(String(destination));
  }

  /** Serialización canónica: claves ordenadas, sin espacios. */
  function canonicalize(value) {
    if (value === null || value === undefined) { return 'null'; }
    if (typeof value !== 'object') { return JSON.stringify(value); }
    if (Object.prototype.toString.call(value) === '[object Array]') {
      var parts = [];
      for (var i = 0; i < value.length; i++) { parts.push(canonicalize(value[i])); }
      return '[' + parts.join(',') + ']';
    }
    var keys = Object.keys(value).sort();
    var out = [];
    for (var k = 0; k < keys.length; k++) {
      out.push(JSON.stringify(keys[k]) + ':' + canonicalize(value[keys[k]]));
    }
    return '{' + out.join(',') + '}';
  }

  // ------------------------------------------------------------- validación
  function _checkFields(name, obj) {
    var errors = [];
    if (!obj || typeof obj !== 'object') {
      return [' ' + name + ': no es un objeto'];
    }
    var expected = FIELDS[name];
    var present = Object.keys(obj);
    for (var i = 0; i < expected.length; i++) {
      if (present.indexOf(expected[i]) === -1) { errors.push(name + ': falta campo ' + expected[i]); }
    }
    for (var j = 0; j < present.length; j++) {
      if (expected.indexOf(present[j]) === -1) { errors.push(name + ': campo desconocido ' + present[j]); }
    }
    return errors;
  }

  function _enum(errors, name, field, value, allowed, nullable) {
    if (value === null || value === undefined) {
      if (!nullable) { errors.push(name + '.' + field + ': nulo no permitido'); }
      return;
    }
    if (allowed.indexOf(value) === -1) {
      errors.push(name + '.' + field + ': valor fuera de enum (' + value + ')');
    }
  }

  function validateEvidenceRef(ref) {
    var errors = _checkFields('EvidenceRef', ref);
    if (errors.length && errors[0].charAt(0) === ' ') { return errors; }
    _enum(errors, 'EvidenceRef', 'source', ref.source, ENUMS.SOURCE, false);
    _enum(errors, 'EvidenceRef', 'epistemic_status', ref.epistemic_status, ENUMS.EPISTEMIC, false);
    if (typeof ref.object_id !== 'string' || !ref.object_id) { errors.push('EvidenceRef.object_id: requerido'); }
    if (typeof ref.retrieved_at !== 'string' || !ref.retrieved_at) { errors.push('EvidenceRef.retrieved_at: requerido'); }
    return errors;
  }

  function validateAuthorityGrant(grant) {
    var errors = _checkFields('AuthorityGrant', grant);
    if (errors.length && errors[0].charAt(0) === ' ') { return errors; }
    _enum(errors, 'AuthorityGrant', 'source', grant.source, ENUMS.AUTHORITY_SOURCE, false);
    ['allowed_tools', 'allowed_operations', 'allowed_contexts', 'forbidden_effects'].forEach(function (f) {
      if (Object.prototype.toString.call(grant[f]) !== '[object Array]') {
        errors.push('AuthorityGrant.' + f + ': debe ser array');
      }
    });
    if (grant.writes_are_simulated_only !== true) {
      errors.push('AuthorityGrant.writes_are_simulated_only: debe ser true en Nivel 0-2');
    }
    return errors;
  }

  function validateActionStep(step) {
    var errors = _checkFields('ActionStep', step);
    if (errors.length && errors[0].charAt(0) === ' ') { return errors; }
    if (typeof step.ordinal !== 'number') { errors.push('ActionStep.ordinal: numérico requerido'); }
    if (typeof step.action_id !== 'string' || !step.action_id) { errors.push('ActionStep.action_id: requerido'); }
    _enum(errors, 'ActionStep', 'destination_provenance', step.destination_provenance, ENUMS.PROVENANCE, false);
    _enum(errors, 'ActionStep', 'effect', step.effect, ENUMS.EFFECT, false);
    _enum(errors, 'ActionStep', 'reconciliation', step.reconciliation, ENUMS.RECONCILIATION, false);
    _enum(errors, 'ActionStep', 'status', step.status, ENUMS.ACTION_STATUS, false);
    if (typeof step.payload_hash !== 'string' || !step.payload_hash) { errors.push('ActionStep.payload_hash: requerido'); }
    return errors;
  }

  function validateActionPlan(plan) {
    var errors = _checkFields('ActionPlan', plan);
    if (errors.length && errors[0].charAt(0) === ' ') { return errors; }
    if (Object.prototype.toString.call(plan.actions) !== '[object Array]') {
      errors.push('ActionPlan.actions: debe ser array');
      return errors;
    }
    for (var i = 0; i < plan.actions.length; i++) {
      errors = errors.concat(validateActionStep(plan.actions[i]));
    }
    if (typeof plan.valid !== 'boolean') { errors.push('ActionPlan.valid: booleano requerido'); }
    if (typeof plan.invariants_checked !== 'boolean') { errors.push('ActionPlan.invariants_checked: booleano requerido'); }
    return errors;
  }

  function validateHandoff(handoff) {
    var errors = _checkFields('Handoff', handoff);
    if (errors.length && errors[0].charAt(0) === ' ') { return errors; }
    _enum(errors, 'Handoff', 'origin_model', handoff.origin_model, ENUMS.MODEL, false);
    _enum(errors, 'Handoff', 'target_model', handoff.target_model, ENUMS.MODEL, false);
    if (handoff.origin_model === handoff.target_model) {
      errors.push('Handoff: origen y destino no pueden coincidir');
    }
    if (typeof handoff.reconciled !== 'boolean') { errors.push('Handoff.reconciled: booleano requerido'); }
    if (Object.prototype.toString.call(handoff.evidence_refs) !== '[object Array]') {
      errors.push('Handoff.evidence_refs: debe ser array');
    } else {
      for (var i = 0; i < handoff.evidence_refs.length; i++) {
        errors = errors.concat(validateEvidenceRef(handoff.evidence_refs[i]));
      }
    }
    if (Object.prototype.toString.call(handoff.restrictions) !== '[object Array]') {
      errors.push('Handoff.restrictions: debe ser array');
    }
    errors = errors.concat(validateAuthorityGrant(handoff.authority));
    return errors;
  }

  function validateLedgerEntry(entry) {
    var errors = _checkFields('LedgerEntry', entry);
    if (errors.length && errors[0].charAt(0) === ' ') { return errors; }
    _enum(errors, 'LedgerEntry', 'model_role', entry.model_role, ENUMS.MODEL_ROLE, false);
    _enum(errors, 'LedgerEntry', 'status', entry.status, ENUMS.ACTION_STATUS, false);
    if (typeof entry.ordinal !== 'number') { errors.push('LedgerEntry.ordinal: numérico requerido'); }
    if (typeof entry.payload_hash !== 'string' || entry.payload_hash.length !== 64) {
      errors.push('LedgerEntry.payload_hash: hash sha256 requerido');
    }
    if (entry.destination_hash !== null && (typeof entry.destination_hash !== 'string' || entry.destination_hash.length !== 64)) {
      errors.push('LedgerEntry.destination_hash: hash sha256 o null');
    }
    return errors;
  }

  function validateExecution(execution) {
    var errors = _checkFields('Execution', execution);
    if (errors.length && errors[0].charAt(0) === ' ') { return errors; }
    _enum(errors, 'Execution', 'intent', execution.intent, ENUMS.INTENT, true);
    _enum(errors, 'Execution', 'status', execution.status, ENUMS.STATUS, false);
    _enum(errors, 'Execution', 'route', execution.route, ENUMS.ROUTE, true);
    if (Object.prototype.toString.call(execution.candidate_contexts) !== '[object Array]') {
      errors.push('Execution.candidate_contexts: debe ser array');
    }
    if (Object.prototype.toString.call(execution.evidence_refs) !== '[object Array]') {
      errors.push('Execution.evidence_refs: debe ser array');
    } else {
      for (var i = 0; i < execution.evidence_refs.length; i++) {
        errors = errors.concat(validateEvidenceRef(execution.evidence_refs[i]));
      }
    }
    if (execution.handoff !== null) { errors = errors.concat(validateHandoff(execution.handoff)); }
    if (execution.action_plan !== null) { errors = errors.concat(validateActionPlan(execution.action_plan)); }
    return errors;
  }

  function assertValid(name, obj) {
    var validators = {
      Execution: validateExecution,
      EvidenceRef: validateEvidenceRef,
      Handoff: validateHandoff,
      AuthorityGrant: validateAuthorityGrant,
      ActionPlan: validateActionPlan,
      ActionStep: validateActionStep,
      LedgerEntry: validateLedgerEntry
    };
    var errors = validators[name](obj);
    if (errors.length) {
      throw Errors.schemaError(name + ' inválido: ' + errors.join('; '), { errors: errors });
    }
    return obj;
  }

  // ------------------------------------------------------------ constructores
  function newExecution(operatorRequest) {
    return {
      execution_id: uuid(),
      created_at: nowIso(),
      operator_request: String(operatorRequest),
      candidate_contexts: [],
      resolved_context: null,
      intent: null,
      status: 'DETECTED',
      route: null,
      evidence_refs: [],
      handoff: null,
      action_plan: null,
      final_answer: null
    };
  }

  function newEvidenceRef(source, objectId, title, context, epistemicStatus) {
    return {
      source: source,
      object_id: objectId,
      title: (title === undefined ? null : title),
      context: (context === undefined ? null : context),
      epistemic_status: epistemicStatus,
      retrieved_at: nowIso()
    };
  }

  function actionId(executionId, ordinal) {
    return executionId + ':' + ordinal;
  }

  return {
    ENUMS: ENUMS,
    FIELDS: FIELDS,
    setClock: setClock,
    setIdFactory: setIdFactory,
    resetRuntime: resetRuntime,
    now: now,
    nowIso: nowIso,
    nowMs: nowMs,
    uuid: uuid,
    toIso: toIso,
    sha256Hex: sha256Hex,
    canonicalize: canonicalize,
    payloadHash: payloadHash,
    destinationHash: destinationHash,
    actionId: actionId,
    newExecution: newExecution,
    newEvidenceRef: newEvidenceRef,
    validateExecution: validateExecution,
    validateEvidenceRef: validateEvidenceRef,
    validateHandoff: validateHandoff,
    validateAuthorityGrant: validateAuthorityGrant,
    validateActionPlan: validateActionPlan,
    validateActionStep: validateActionStep,
    validateLedgerEntry: validateLedgerEntry,
    assertValid: assertValid
  };
})();
