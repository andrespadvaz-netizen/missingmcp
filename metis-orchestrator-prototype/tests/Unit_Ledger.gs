/** Unit_Ledger.gs — spec §17 / Ledger. */
(function () {

  function intent(ordinal, payload) {
    return {
      execution_id: 'exec-ledger',
      ordinal: ordinal,
      payload_hash: Schemas.payloadHash(payload),
      tool: 'simulate.asana_write',
      operation: 'update',
      destination_hash: Schemas.destinationHash('asana:and-task-77'),
      model_role: 'LOCAL'
    };
  }

  TestRunner.unit('Ledger', 'identidad por execution_id + ordinal', function (t) {
    var entry = Ledger.recordIntent(intent(1, { nota: 'a' }));
    t.equals(entry.action_id, 'exec-ledger:1', 'action_id = execution_id + ordinal');
    t.equals(entry.status, 'INTENT_RECORDED', 'nace en INTENT_RECORDED');

    var again = Ledger.recordIntent(intent(1, { nota: 'a' }));
    t.equals(again.action_id, entry.action_id, 'el mismo ordinal con el mismo payload es idempotente');
    t.equals(Ledger.entriesFor('exec-ledger').length, 1, 'no se duplica la entrada');

    var second = Ledger.recordIntent(intent(2, { nota: 'b' }));
    t.equals(second.action_id, 'exec-ledger:2', 'otro ordinal es otra acción');
    t.equals(Ledger.entriesFor('exec-ledger').length, 2, 'dos acciones registradas');
  });

  TestRunner.unit('Ledger', 'payload distinto en mismo ordinal falla', function (t) {
    Ledger.recordIntent(intent(1, { nota: 'a' }));
    t.throwsCode(Errors.CODES.LEDGER_CONSISTENCY, function () {
      Ledger.recordIntent(intent(1, { nota: 'DISTINTO' }));
    }, 'la huella actúa como guarda de consistencia');
    t.equals(Ledger.get('exec-ledger', 1).status, 'FAILED', 'la acción queda FAILED, la corrida no continúa');
  });

  TestRunner.unit('Ledger', 'máquina de estados de la acción', function (t) {
    Ledger.recordIntent(intent(1, { nota: 'a' }));
    Ledger.transition('exec-ledger', 1, 'SENT');
    t.equals(Ledger.get('exec-ledger', 1).status, 'SENT', 'INTENT_RECORDED -> SENT');
    Ledger.transition('exec-ledger', 1, 'SIMULATED');
    t.equals(Ledger.get('exec-ledger', 1).status, 'SIMULATED', 'la escritura simulada termina en SIMULATED');
    t.throwsCode(Errors.CODES.LEDGER_CONSISTENCY, function () {
      Ledger.transition('exec-ledger', 1, 'CONFIRMED');
    }, 'un estado terminal no admite más transiciones');

    Ledger.recordIntent(intent(2, { nota: 'b' }));
    t.throwsCode(Errors.CODES.LEDGER_CONSISTENCY, function () {
      Ledger.transition('exec-ledger', 2, 'CONFIRMED');
    }, 'no se puede saltar de INTENT_RECORDED a CONFIRMED');
  });

  TestRunner.unit('Ledger', 'no contiene sustancia ni secretos', function (t) {
    var entry = Ledger.recordIntent(intent(1, { nota: 'texto de negocio muy sensible', destino: 'x' }));
    var campos = Object.keys(entry).sort();
    t.deepEquals(campos, Schemas.FIELDS.LedgerEntry.slice().sort(), 'el conjunto de campos es exacto');

    var serializado = JSON.stringify(entry);
    t.equals(serializado.indexOf('texto de negocio'), -1, 'la sustancia del payload no viaja al ledger');
    t.equals(serializado.indexOf('asana:and-task-77'), -1, 'el destino viaja hasheado, no en claro');
    t.equals(entry.destination_hash.length, 64, 'destination_hash es sha256');
    t.equals(entry.payload_hash.length, 64, 'payload_hash es sha256');

    t.throwsCode(Errors.CODES.LEDGER_SUBSTANCE, function () {
      Ledger.assertNoSubstance({
        execution_id: 'e', ordinal: 1, action_id: 'e:1',
        payload_hash: Schemas.payloadHash({}), tool: 'nota libre con sustancia',
        operation: 'update', destination_hash: null, status: 'SIMULATED',
        provider_object_id: null, model_role: 'LOCAL', timestamp: Schemas.nowIso()
      });
    }, 'texto libre en `tool` se rechaza');

    // El ledger se construye campo a campo (whitelist constructiva): un campo
    // ajeno en la entrada de origen no puede llegar al registro.
    var conIntruso = Ledger.recordIntent({
      execution_id: 'exec-ledger', ordinal: 9,
      payload_hash: Schemas.payloadHash({}), tool: 'simulate.asana_write',
      operation: 'update', destination_hash: null, model_role: 'LOCAL',
      secreto: 'sk-no-deberia-existir'
    });
    t.equals(conIntruso.secreto, undefined, 'un campo ajeno (p. ej. un secreto) no entra al ledger');
    t.equals(JSON.stringify(conIntruso).indexOf('sk-no-deberia-existir'), -1, 'el secreto no queda persistido');

    // Y el esquema estricto rechaza cualquier entrada con campos desconocidos.
    t.throwsCode(Errors.CODES.SCHEMA, function () {
      Schemas.assertValid('LedgerEntry', {
        execution_id: 'e', ordinal: 1, action_id: 'e:1', payload_hash: Schemas.payloadHash({}),
        tool: 'simulate.asana_write', operation: 'update', destination_hash: null,
        status: 'SIMULATED', provider_object_id: null, model_role: 'LOCAL',
        timestamp: Schemas.nowIso(), secreto: 'sk-x'
      });
    }, 'el esquema estricto rechaza un LedgerEntry con campo desconocido');
  });

  TestRunner.unit('Ledger', 'fail closed sin ledger y retención configurable', function (t) {
    Ledger.recordIntent(intent(1, { nota: 'a' }));
    t.equals(Ledger.entriesFor('exec-ledger').length, 1, 'entrada presente');

    Fixtures.advance(Config.LEDGER.RETENTION_MS + 1000);
    t.equals(Ledger.purge(), 1, 'la poda por retención elimina la entrada vencida');
    t.equals(Ledger.entriesFor('exec-ledger').length, 0, 'ledger podado');

    Fixtures.ledgerStore().setAvailable(false);
    t.equals(Ledger.available(), false, 'el ledger se declara no disponible');
    t.throwsCode(Errors.CODES.LEDGER_UNAVAILABLE, function () {
      Ledger.recordIntent(intent(3, { nota: 'c' }));
    }, 'sin ledger no se registra intención: fail closed');
  });

  TestRunner.unit('Ledger', 'techos agregados producen parada dura', function (t) {
    t.ok(Ledger.assertAggregateBudget(), 'con contador en cero no bloquea');
    Ledger.addSpend(Config.LIMITS.MAX_DAILY_BUDGET_USD);
    t.throwsCode(Errors.CODES.LIMIT_EXCEEDED, function () {
      Ledger.assertAggregateBudget();
    }, 'alcanzar el techo diario detiene la corrida');
  });

})();
