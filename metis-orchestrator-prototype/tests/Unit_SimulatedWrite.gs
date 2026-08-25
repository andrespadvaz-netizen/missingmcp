/**
 * Unit_SimulatedWrite.gs — spec §17 / SimulatedWriteAdapter.
 *
 * Nota de ubicación: la spec §3 fija la lista de archivos de test, de modo que
 * las pruebas de contrato de `ProviderAdapter` y de los adaptadores de lectura
 * (que también demuestran "ninguna superficie de escritura real") viven aquí,
 * al final del archivo, en lugar de en un archivo nuevo.
 */
(function () {

  var EXECUTION = { execution_id: 'exec-sim' };

  function grant() {
    return AuthorityPolicy.postContextGrant(
      AuthorityPolicy.declaredCeiling(), 'METIS',
      { source: 'LIVE_OPERATOR', requests_execution: true });
  }

  function plannedAction(overrides) {
    var base = {
      tool: 'simulate.asana_write',
      operation: 'update',
      destination: 'asana:and-task-77',
      destination_provenance: 'LIVE_OPERATOR',
      payload: { nota: 'decisión registrada', prioridad: 'alta' },
      destination_meta: { context: 'METIS', tags: ['TASK'] },
      source_context: 'METIS'
    };
    var keys = Object.keys(overrides || {});
    for (var i = 0; i < keys.length; i++) { base[keys[i]] = overrides[keys[i]]; }
    return Orchestrator.buildPlannedActions(EXECUTION, [base])[0];
  }

  TestRunner.unit('SimulatedWriteAdapter', 'jamás llama API real', function (t) {
    var exported = Object.keys(SimulatedWriteAdapter);
    var peligrosas = exported.filter(function (k) {
      return /fetch|post|patch|put|send$|delete|create$|upload/i.test(k);
    });
    t.deepEquals(peligrosas, [], 'la superficie exportada no contiene ninguna operación de red');

    t.throwsCode(Errors.CODES.REAL_WRITE_ATTEMPT, function () {
      SimulatedWriteAdapter.simulate(plannedAction({ tool: 'asana.update_real' }), {});
    }, 'una herramienta que no sea simulate.* se rechaza');

    var planned = plannedAction();
    planned.step.effect = 'READ';
    t.throwsCode(Errors.CODES.REAL_WRITE_ATTEMPT, function () {
      SimulatedWriteAdapter.simulate(planned, {});
    }, 'un efecto distinto de SIMULATED_WRITE se rechaza');

    var ok = SimulatedWriteAdapter.simulate(plannedAction(), { policy_decision: { decision: 'ALLOW', reason: 'WITHIN_GRANT' } });
    t.equals(ok.simulated, true, 'la acción queda simulada');
    t.equals(Ledger.get(EXECUTION.execution_id, 1).provider_object_id, null,
      'no hay identificador de objeto de proveedor: nada salió del runtime');

    // Toda herramienta expuesta como escritura es `simulate.*`.
    var noSimuladas = AuthorityPolicy.SIMULATED_WRITE_TOOLS.filter(function (name) {
      return name.indexOf('simulate.') !== 0;
    });
    t.deepEquals(noSimuladas, [], 'no existe ninguna herramienta de escritura no simulada');
  });

  TestRunner.unit('SimulatedWriteAdapter', 'produce representación exacta de lo que habría ocurrido', function (t) {
    var planned = plannedAction();
    var result = SimulatedWriteAdapter.simulate(planned, {
      policy_decision: { decision: 'ALLOW', reason: 'WITHIN_GRANT' }, model_role: 'LOCAL'
    });

    var campos = Object.keys(result).sort();
    t.deepEquals(campos, ['block_reason', 'blocked_by_policy', 'effect_summary', 'simulated', 'would_call', 'would_target'],
      'el contrato de simulación es exactamente el de la spec §10');
    t.equals(result.would_call, 'PUT https://app.asana.com/api/1.0/tasks/{gid}',
      'declara la llamada real que se habría hecho');
    t.equals(result.would_target, 'asana:and-task-77', 'declara el destino exacto');
    t.includes(result.effect_summary, 'nota, prioridad', 'enumera los campos del payload');
    t.includes(result.effect_summary, 'LIVE_OPERATOR', 'declara la procedencia del destino');
    t.equals(result.blocked_by_policy, false, 'acción permitida: no bloqueada');
    t.equals(result.block_reason, null, 'sin motivo de bloqueo');

    var entry = Ledger.get(EXECUTION.execution_id, 1);
    t.equals(entry.status, 'SIMULATED', 'el ledger termina la acción en SIMULATED');
    t.equals(planned.step.status, 'SIMULATED', 'el paso del plan queda en SIMULATED');

    var bloqueada = SimulatedWriteAdapter.simulate(
      Orchestrator.buildPlannedActions({ execution_id: 'exec-sim-2' }, [{
        tool: 'simulate.gmail_send', operation: 'send', destination: 'correo@ejemplo.invalid',
        destination_provenance: 'RETRIEVED_CONTENT', payload: { asunto: 'x' },
        destination_meta: {}, source_context: 'METIS'
      }])[0],
      { policy_decision: { decision: 'REQUIRES_ANDRES', reason: 'DESTINATION_FROM_RETRIEVED_CONTENT' } });
    t.equals(bloqueada.blocked_by_policy, true, 'la acción degradada se marca bloqueada');
    t.equals(bloqueada.block_reason, 'DESTINATION_FROM_RETRIEVED_CONTENT', 'el motivo viaja en la simulación');
    t.equals(bloqueada.would_call, 'GmailApp.sendEmail(destinatario, asunto, cuerpo)',
      'aun bloqueada, se muestra qué habría ocurrido');
  });

  TestRunner.unit('SimulatedWriteAdapter', 'sin ledger la acción material falla cerrada', function (t) {
    Fixtures.ledgerStore().setAvailable(false);
    t.throwsCode(Errors.CODES.LEDGER_UNAVAILABLE, function () {
      SimulatedWriteAdapter.simulate(plannedAction(), {});
    }, 'ledger caído: la acción simulada se bloquea y la corrida falla cerrada');
  });

  TestRunner.unit('SimulatedWriteAdapter', 'la reconciliabilidad se declara por acción', function (t) {
    t.equals(Orchestrator.reconciliationFor('simulate.asana_write', 'update'), 'RECONCILABLE', 'update en Asana es reconciliable');
    t.equals(Orchestrator.reconciliationFor('simulate.gmail_send', 'send'), 'NON_RECONCILABLE', 'un envío no es reconciliable');
  });

  // ------------------------------------------------------- ProviderAdapter
  TestRunner.unit('ProviderAdapter', 'OpenAI y Anthropic normalizan al mismo objeto', function (t) {
    var openai = OpenAIAdapter.create();
    var anthropic = AnthropicAdapter.create();
    t.ok(ProviderAdapter.conforms(openai), 'OpenAIAdapter cumple la interfaz');
    t.ok(ProviderAdapter.conforms(anthropic), 'AnthropicAdapter cumple la interfaz');

    var openaiRaw = {
      id: 'resp_1',
      status: 'completed',
      output: [
        { type: 'message', content: [{ type: 'output_text', text: 'hola' }] },
        { type: 'function_call', call_id: 'c1', name: 'notion__search', arguments: '{"query":"gate"}' }
      ],
      usage: { input_tokens: 100, output_tokens: 20 }
    };
    var anthropicRaw = {
      id: 'msg_1',
      stop_reason: 'tool_use',
      content: [
        { type: 'text', text: 'hola' },
        { type: 'tool_use', id: 'c1', name: 'notion__search', input: { query: 'gate' } }
      ],
      usage: { input_tokens: 100, output_tokens: 20 }
    };

    var a = openai.normalizeResponse(openaiRaw);
    var b = anthropic.normalizeResponse(anthropicRaw);

    t.deepEquals(Object.keys(a).sort(), ProviderAdapter.normalizedFields(), 'OpenAI produce el contrato exacto');
    t.deepEquals(Object.keys(b).sort(), ProviderAdapter.normalizedFields(), 'Anthropic produce el contrato exacto');
    t.equals(a.text, b.text, 'mismo texto normalizado');
    t.deepEquals(a.tool_requests, b.tool_requests, 'mismas tool_requests, con el nombre del contrato común');
    t.equals(a.tool_requests[0].name, 'notion.search', 'el nombre vuelve al contrato interno');
  });

  TestRunner.unit('ProviderAdapter', 'los errores de proveedor se redactan antes de exponerse', function (t) {
    var openai = OpenAIAdapter.create();
    var sucio = new Error('401 {"error":{"message":"Incorrect API key provided: sk-abcdef1234567890"}} ' +
                          'headers={"authorization":"Bearer sk-abcdef1234567890","x-api-key":"sk-ant-secreto12345"}');
    var limpio = openai.redactProviderError(sucio, 401);

    t.deepEquals(Object.keys(limpio).sort(), ['code', 'message', 'provider', 'status'],
      'sólo proveedor, status, código y mensaje saneado');
    t.equals(limpio.message.indexOf('sk-abcdef1234567890'), -1, 'la API key desaparece del mensaje');
    t.equals(limpio.message.indexOf('sk-ant-secreto12345'), -1, 'la clave de Anthropic desaparece del mensaje');
    t.ok(limpio.message.length <= 241, 'el mensaje se trunca: no arrastra bodies completos');
    t.equals(Errors.redactText('Bearer sk-1234567890abcdef').indexOf('sk-1234'), -1, 'la redacción cubre Bearer');
  });

  // -------------------------------------------------- adaptadores de lectura
  TestRunner.unit('ReadAdapters', 'no exponen ninguna operación de escritura', function (t) {
    var adaptadores = {
      NotionReadAdapter: NotionReadAdapter,
      AsanaReadAdapter: AsanaReadAdapter,
      DriveReadAdapter: DriveReadAdapter,
      CalendarReadAdapter: CalendarReadAdapter
    };
    var nombres = Object.keys(adaptadores);
    for (var i = 0; i < nombres.length; i++) {
      var claves = Object.keys(adaptadores[nombres[i]]);
      var escritura = claves.filter(function (k) {
        return /create|update|delete|insert|write|send|append|archive|trash/i.test(k);
      });
      t.deepEquals(escritura, [], nombres[i] + ' no exporta ninguna operación de mutación');
    }
  });

  TestRunner.unit('ReadAdapters', 'las lecturas reales están bloqueadas en Nivel 0', function (t) {
    NotionReadAdapter.resetBackend();
    AsanaReadAdapter.resetBackend();
    t.equals(Config.runLevel(), Config.LEVELS.LEVEL_0, 'la corrida de test está en Nivel 0');
    t.throwsCode(Errors.CODES.LEVEL_VIOLATION, function () { NotionReadAdapter.search('gate'); },
      'Notion real bloqueado en Nivel 0');
    t.throwsCode(Errors.CODES.LEVEL_VIOLATION, function () { AsanaReadAdapter.search('tarea'); },
      'Asana real bloqueado en Nivel 0');
    Fixtures.installBackends();
  });

  TestRunner.unit('Config', 'no hay secretos en el código ni en la configuración', function (t) {
    var nombres = Object.keys(Config.SECRET_PROPERTY_NAMES);
    for (var i = 0; i < nombres.length; i++) {
      var valor = Config.SECRET_PROPERTY_NAMES[nombres[i]];
      t.ok(/^METIS_[A-Z_]+$/.test(valor), nombres[i] + ' referencia un nombre simbólico, no un valor');
    }
    t.throwsCode(Errors.CODES.MISSING_CREDENTIAL, function () { Config.secret('OPENAI_API_KEY'); },
      'sin Script Property cargada, la credencial falta y la corrida se detiene');
    t.throwsCode(Errors.CODES.CONFIG, function () { Config.secret('CLAVE_INEXISTENTE'); },
      'una clave simbólica desconocida se rechaza');
  });

})();
