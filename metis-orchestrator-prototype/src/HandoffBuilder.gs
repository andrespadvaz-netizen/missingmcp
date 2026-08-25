/**
 * HandoffBuilder.gs — handoff interno generado por el sistema (spec §15).
 *
 *  - Lo genera el runtime, nunca Andrés: no hay copy-paste dentro de una corrida.
 *  - Transporta PUNTEROS y RESTRICCIONES, no un dump de contexto.
 *  - Lleva emisión, caducidad, `handoff_id`, `execution_id` y estado `reconciled`.
 *  - Replay de handoff caducado o ya reconciliado: rechazo visible.
 *  - Conserva la etiqueta epistémica de cada evidencia.
 *
 * El estado de consumo/reconciliación se PERSISTE en el ledger técnico, no en
 * memoria del proceso: el replay que de verdad importa llega en otra ejecución,
 * y un registro process-local se pierde entre corridas. Sin ledger disponible
 * no se puede probar que un handoff no fue consumido ya, así que consumirlo
 * falla cerrado.
 */
var HandoffBuilder = (function () {

  /** Topes de transporte: fuerzan punteros en vez de sustancia. */
  var LIMITS = {
    OBJECTIVE: 300,
    CONTEXT: 600,
    EXPECTED_OUTPUT: 300,
    RESTRICTION: 200,
    MAX_EVIDENCE_REFS: 8
  };

  /**
   * Espejo en memoria del registro persistido: acelera la corrida en curso,
   * pero NUNCA es la autoridad. Toda decisión de replay consulta el ledger.
   */
  var _registry = {};

  function resetRegistry() { _registry = {}; }

  function _cap(text, max, field) {
    var s = String(text === null || text === undefined ? '' : text);
    if (s.length > max) {
      throw Errors.make(Errors.CODES.HANDOFF_INVALID,
        'Handoff: ' + field + ' excede el tope de transporte (' + s.length + '>' + max + '). ' +
        'El handoff transporta punteros, no sustancia.');
    }
    return s;
  }

  /**
   * Construye el handoff. `authority` debe ser un AuthorityGrant ya estrechado.
   */
  function build(execution, params) {
    var emittedMs = Schemas.nowMs();
    var ttl = (params.ttl_ms === undefined || params.ttl_ms === null) ? Config.HANDOFF_TTL_MS : params.ttl_ms;

    if (params.origin_model === params.target_model) {
      throw Errors.make(Errors.CODES.HANDOFF_INVALID, 'Handoff hacia el mismo modelo: no aporta independencia');
    }

    var refs = (params.evidence_refs || []).slice(0, LIMITS.MAX_EVIDENCE_REFS);
    for (var i = 0; i < refs.length; i++) {
      // Etiqueta epistémica obligatoria en transporte.
      if (!refs[i].epistemic_status) {
        throw Errors.make(Errors.CODES.HANDOFF_INVALID, 'Evidencia sin etiqueta epistémica en handoff');
      }
    }

    var restrictions = (params.restrictions || []).map(function (r, idx) {
      return _cap(r, LIMITS.RESTRICTION, 'restrictions[' + idx + ']');
    });

    var handoff = {
      handoff_id: Schemas.uuid(),
      execution_id: execution.execution_id,
      emitted_at: Schemas.toIso(new Date(emittedMs)),
      expires_at: Schemas.toIso(new Date(emittedMs + ttl)),
      origin_model: params.origin_model,
      target_model: params.target_model,
      objective: _cap(params.objective, LIMITS.OBJECTIVE, 'objective'),
      context: _cap(params.context, LIMITS.CONTEXT, 'context'),
      evidence_refs: refs,
      restrictions: restrictions,
      authority: params.authority,
      expected_output: _cap(params.expected_output, LIMITS.EXPECTED_OUTPUT, 'expected_output'),
      reconciled: false
    };

    Schemas.assertValid('Handoff', handoff);
    // Emisión registrada de forma persistente: es lo que permite detectar un
    // replay llegado en otra ejecución.
    Ledger.recordHandoffIssued(handoff);
    _registry[handoff.handoff_id] = { reconciled: false, consumed_count: 0 };
    return handoff;
  }

  function isExpired(handoff, nowMs) {
    var t = (nowMs === undefined) ? Schemas.nowMs() : nowMs;
    return new Date(handoff.expires_at).getTime() <= t;
  }

  /**
   * Autoridad = ledger persistido. El objeto en mano puede venir de un replay
   * con `reconciled:false` fabricado; el registro no.
   */
  function isReconciled(handoff) {
    var persisted = Ledger.handoffRecord(handoff.handoff_id);
    if (persisted && persisted.reconciled) { return true; }
    var local = _registry[handoff.handoff_id];
    if (local && local.reconciled) { return true; }
    return handoff.reconciled === true;
  }

  /**
   * Puerta única de consumo. Rechazo VISIBLE (excepción tipada) si caducó o si
   * ya fue reconciliado (spec §7.7, §15, §16.9).
   */
  function assertConsumable(handoff, nowMs) {
    Schemas.assertValid('Handoff', handoff);

    // Sin ledger no se puede demostrar que no fue consumido: fail closed.
    var persisted = Ledger.handoffRecord(handoff.handoff_id);
    if (!persisted) {
      throw Errors.make(Errors.CODES.HANDOFF_INVALID,
        'Handoff sin emisión registrada: ' + handoff.handoff_id +
        '. No se acepta un handoff que este runtime no emitió.');
    }
    // La caducidad manda la del registro, no la del objeto presentado.
    if (persisted.expires_at !== handoff.expires_at) {
      throw Errors.make(Errors.CODES.HANDOFF_INVALID,
        'Caducidad alterada respecto de la emisión: ' + handoff.handoff_id);
    }
    if (isReconciled(handoff)) {
      throw Errors.handoffReplay(handoff.handoff_id);
    }
    if (isExpired(handoff, nowMs)) {
      throw Errors.handoffExpired(handoff.handoff_id);
    }
    return true;
  }

  function consume(handoff, nowMs) {
    assertConsumable(handoff, nowMs);
    var count = Ledger.recordHandoffConsumed(handoff.handoff_id);
    _registry[handoff.handoff_id] = { reconciled: false, consumed_count: count };
    return count;
  }

  /** Cierre de ciclo: el handoff queda reconciliado y no se reejecuta. */
  function reconcile(handoff) {
    var persisted = Ledger.recordHandoffReconciled(handoff.handoff_id);
    _registry[handoff.handoff_id] = { reconciled: true, consumed_count: persisted.consumed_count };
    handoff.reconciled = true;
    return handoff;
  }

  return {
    LIMITS: LIMITS,
    resetRegistry: resetRegistry,
    build: build,
    isExpired: isExpired,
    isReconciled: isReconciled,
    assertConsumable: assertConsumable,
    consume: consume,
    reconcile: reconcile
  };
})();
