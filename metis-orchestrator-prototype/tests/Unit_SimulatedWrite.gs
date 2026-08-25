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
    var partition = { partition: Fixtures.PARTITIONS.METIS.notion, context: 'METIS' };
    t.throwsCode(Errors.CODES.LEVEL_VIOLATION, function () { NotionReadAdapter.search('gate', partition); },
      'Notion real bloqueado en Nivel 0 aunque la partición sea válida');
    t.throwsCode(Errors.CODES.LEVEL_VIOLATION, function () {
      AsanaReadAdapter.search('tarea', { partition: Fixtures.PARTITIONS.METIS.asana, context: 'METIS' });
    }, 'Asana real bloqueado en Nivel 0');
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


  // --------------------------------------------------- presupuesto y precios
  TestRunner.unit('Presupuesto', 'sin precio configurado el costo es desconocido, no cero', function (t) {
    var openai = OpenAIAdapter.create();
    t.equals(Config.priceFor('OPENAI', 'gpt-5'), null, 'no hay tabla de precios en el código');
    t.equals(openai.normalizeResponse({
      id: 'r', status: 'completed', output: [], model: 'gpt-5',
      usage: { input_tokens: 1000, output_tokens: 500 }
    }).usage.estimated_cost_usd, null, 'el costo sale null, no un número inventado');

    t.equals(AnthropicAdapter.estimateCost({ input_tokens: 1000, output_tokens: 500 }, 'claude-opus-5'), null,
      'lo mismo del lado de Anthropic');
  });

  TestRunner.unit('Presupuesto', 'un costo desconocido detiene la corrida', function (t) {
    var sinPrecio = Fixtures.providers([
      { text: '', tool_requests: [], usage: { input_tokens: 100, output_tokens: 50, estimated_cost_usd: null } }
    ], []);
    var result = Orchestrator.run('¿Cuál es el estado del gate en Metis?',
      { current_model: 'OPENAI', providers: sinPrecio });
    t.equals(result.status, 'FAILED', 'la corrida falla cerrada');
    t.includes(result.final_answer, Errors.CODES.PRICE_UNKNOWN, 'y nombra la causa');
    t.equals(result.limits.cost_known, false, 'el costo queda declarado como desconocido');
    t.equals(result.limits.estimated_cost_usd, null, 'no se reporta un total falso');
  });

  TestRunner.unit('Presupuesto', 'los límites se pueden externalizar', function (t) {
    t.equals(Config.limits().MAX_TOOL_CALLS, Config.DEFAULT_LIMITS.MAX_TOOL_CALLS, 'por defecto, el default');
    Config._setLimits({ MAX_TOOL_CALLS: 3, INVENTADO: 99 });
    t.equals(Config.limits().MAX_TOOL_CALLS, 3, 'el override externo se aplica');
    t.equals(Config.limits().INVENTADO, undefined, 'una clave desconocida se ignora');
    t.equals(Config.limits().MAX_MODEL_INTERVENTIONS, Config.DEFAULT_LIMITS.MAX_MODEL_INTERVENTIONS,
      'lo no sobreescrito conserva su default');
  });

  TestRunner.unit('Presupuesto', 'los techos monetarios no tienen default en el código', function (t) {
    Config._setLimits(null);
    var sinTechos = Config.limits();
    Config.REQUIRED_BUDGET_KEYS.forEach(function (k) {
      t.equals(sinTechos[k], null, k + ' no tiene default: es del entorno');
      t.equals(Config.DEFAULT_LIMITS[k], undefined, k + ' no aparece en DEFAULT_LIMITS');
    });
    t.equals(Config.missingBudgets().length, 3, 'los tres faltan mientras no se declaren');
    t.throwsCode(Errors.CODES.BUDGET_UNCONFIGURED, function () {
      Config.assertBudgetsConfigured();
    }, 'la puerta previa al gasto se cierra');

    // Lo no monetario sí conserva su default de política.
    t.equals(sinTechos.MAX_TOOL_CALLS, Config.DEFAULT_LIMITS.MAX_TOOL_CALLS, 'los límites de política siguen');
  });

  TestRunner.unit('Presupuesto', 'ningún proveedor real acepta llamada sin techos declarados', function (t) {
    Config._setLimits(null);
    Config._setRunLevel(Config.LEVELS.LEVEL_2);
    var peticion = { system: 'x', prompt: 'y' };

    // Falla ANTES de tocar la red: el arnés bloquea UrlFetchApp, así que un
    // código distinto de BUDGET_UNCONFIGURED probaría que la puerta llegó tarde.
    t.throwsCode(Errors.CODES.BUDGET_UNCONFIGURED, function () {
      OpenAIAdapter.create().completeWithTools(peticion, null);
    }, 'OpenAI se detiene antes de gastar');
    t.throwsCode(Errors.CODES.BUDGET_UNCONFIGURED, function () {
      AnthropicAdapter.create().completeWithTools(peticion, null);
    }, 'Anthropic también');

    // Con techos declarados, la puerta se abre y falla más adelante, por
    // credencial ausente: prueba que el orden de las guardas es el correcto.
    Config._setLimits(Fixtures.TEST_BUDGETS);
    t.throwsCode(Errors.CODES.MISSING_CREDENTIAL, function () {
      OpenAIAdapter.create().completeWithTools(peticion, null);
    }, 'con techos declarados, la siguiente guarda es la credencial');
  });

  TestRunner.unit('Presupuesto', 'un techo sin declarar no se compara contra el gasto', function (t) {
    Config._setLimits(null);
    Ledger.addSpend(9999);
    t.ok(Ledger.assertAggregateBudget(), 'sin techo no hay comparación posible');
    Config._setLimits(Fixtures.TEST_BUDGETS);
    t.throwsCode(Errors.CODES.LIMIT_EXCEEDED, function () {
      Ledger.assertAggregateBudget();
    }, 'declarado el techo, el gasto acumulado lo supera y detiene');
  });

})();
