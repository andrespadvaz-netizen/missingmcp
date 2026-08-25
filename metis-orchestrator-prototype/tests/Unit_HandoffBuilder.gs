/** Unit_HandoffBuilder.gs — spec §17 / HandoffBuilder. */
(function () {

  var EXECUTION = { execution_id: 'exec-handoff' };

  function grant() {
    return AuthorityPolicy.postContextGrant(
      AuthorityPolicy.declaredCeiling(), 'METIS',
      { source: 'FIXED_POLICY', requests_execution: false });
  }

  function refs() {
    return [
      Schemas.newEvidenceRef('NOTION', 'met-dec-014', 'D-014', 'METIS', 'REGISTERED_DECISION'),
      Schemas.newEvidenceRef('NOTION', 'met-phi-047', 'PHI-047', 'METIS', 'HISTORICAL'),
      Schemas.newEvidenceRef('NOTION', 'met-prop-01', 'Propuesta', 'METIS', 'PROPOSAL')
    ];
  }

  function build(overrides) {
    var params = {
      origin_model: 'OPENAI',
      target_model: 'ANTHROPIC',
      objective: 'Auditar la propuesta de gobernanza.',
      context: 'Contexto METIS. Recupera desde la fuente.',
      evidence_refs: refs(),
      restrictions: ['Sólo lectura real; toda escritura es simulada.'],
      authority: grant(),
      expected_output: 'Veredicto de auditoría.'
    };
    var keys = Object.keys(overrides || {});
    for (var i = 0; i < keys.length; i++) { params[keys[i]] = overrides[keys[i]]; }
    return HandoffBuilder.build(EXECUTION, params);
  }

  TestRunner.unit('HandoffBuilder', 'incluye TTL', function (t) {
    var handoff = build();
    t.equals(handoff.execution_id, 'exec-handoff', 'lleva la corrida originadora');
    t.ok(!!handoff.handoff_id, 'lleva handoff_id único');
    t.equals(handoff.reconciled, false, 'nace sin reconciliar');
    var emitted = new Date(handoff.emitted_at).getTime();
    var expires = new Date(handoff.expires_at).getTime();
    t.equals(expires - emitted, Config.HANDOFF_TTL_MS, 'la caducidad respeta el TTL configurado');
    t.notOk(HandoffBuilder.isExpired(handoff), 'recién emitido no está caducado');

    Fixtures.advance(Config.HANDOFF_TTL_MS + 1000);
    t.ok(HandoffBuilder.isExpired(handoff), 'pasado el TTL queda caducado');
    t.throwsCode(Errors.CODES.HANDOFF_EXPIRED, function () {
      HandoffBuilder.assertConsumable(handoff);
    }, 'un handoff caducado se rechaza de forma visible');
  });

  TestRunner.unit('HandoffBuilder', 'rechaza replay', function (t) {
    var handoff = build();
    t.equals(HandoffBuilder.consume(handoff), 1, 'primer consumo aceptado');
    HandoffBuilder.reconcile(handoff);
    t.ok(handoff.reconciled, 'queda reconciliado al cerrar el ciclo');
    t.throwsCode(Errors.CODES.HANDOFF_REPLAY, function () {
      HandoffBuilder.consume(handoff);
    }, 'el replay de un handoff reconciliado se rechaza');
  });

  TestRunner.unit('HandoffBuilder', 'conserva etiquetas epistémicas', function (t) {
    var handoff = build();
    var labels = handoff.evidence_refs.map(function (r) { return r.epistemic_status; });
    t.deepEquals(labels, ['REGISTERED_DECISION', 'HISTORICAL', 'PROPOSAL'],
      'cada evidencia conserva su etiqueta en transporte');

    var sinEtiqueta = {
      source: 'NOTION', object_id: 'x', title: null, context: 'METIS',
      epistemic_status: null, retrieved_at: Schemas.nowIso()
    };
    t.throwsAny(function () { build({ evidence_refs: [sinEtiqueta] }); },
      'una evidencia sin etiqueta epistémica no puede viajar');
  });

  TestRunner.unit('HandoffBuilder', 'transporta punteros, no un dump de contexto', function (t) {
    t.throwsCode(Errors.CODES.HANDOFF_INVALID, function () {
      build({ context: new Array(HandoffBuilder.LIMITS.CONTEXT + 50).join('x') });
    }, 'un contexto masivo se rechaza');

    var handoff = build();
    var serializado = JSON.stringify(handoff);
    t.equals(serializado.indexOf('snippet'), -1, 'las evidencias no llevan sustancia');
    t.ok(handoff.authority.writes_are_simulated_only, 'la autoridad transportada es sólo-simulación');

    t.throwsCode(Errors.CODES.HANDOFF_INVALID, function () {
      build({ target_model: 'OPENAI' });
    }, 'un handoff al mismo modelo no aporta independencia y se rechaza');
  });


  TestRunner.unit('HandoffBuilder', 'el anti-replay sobrevive al fin del proceso', function (t) {
    var handoff = build();
    HandoffBuilder.consume(handoff);
    HandoffBuilder.reconcile(handoff);

    // Otra ejecución: la memoria del proceso se pierde, el ledger no.
    Fixtures.newProcess();
    t.deepEquals(HandoffBuilder.isReconciled({ handoff_id: handoff.handoff_id, reconciled: false }), true,
      'el estado reconciliado se lee del registro persistido, no de memoria');

    var falsificado = JSON.parse(JSON.stringify(handoff));
    falsificado.reconciled = false;
    t.throwsCode(Errors.CODES.HANDOFF_REPLAY, function () {
      HandoffBuilder.assertConsumable(falsificado);
    }, 'presentarlo como no reconciliado no sirve');
  });

  TestRunner.unit('HandoffBuilder', 'sólo se aceptan handoffs que este runtime emitió', function (t) {
    var ajeno = build();
    var copia = JSON.parse(JSON.stringify(ajeno));
    copia.handoff_id = 'handoff-de-otro-runtime';
    t.throwsCode(Errors.CODES.HANDOFF_INVALID, function () {
      HandoffBuilder.assertConsumable(copia);
    }, 'un handoff sin emisión registrada se rechaza');

    var estirado = JSON.parse(JSON.stringify(ajeno));
    estirado.expires_at = Schemas.toIso(new Date(Schemas.nowMs() + 86400000));
    t.throwsCode(Errors.CODES.HANDOFF_INVALID, function () {
      HandoffBuilder.assertConsumable(estirado);
    }, 'alargar la caducidad respecto de la emisión se rechaza');
  });

  TestRunner.unit('HandoffBuilder', 'sin ledger no se puede probar que no hubo replay', function (t) {
    var handoff = build();
    Fixtures.ledgerStore().setAvailable(false);
    t.throwsCode(Errors.CODES.LEDGER_UNAVAILABLE, function () {
      HandoffBuilder.assertConsumable(handoff);
    }, 'fail closed: sin registro no se consume');
  });

})();