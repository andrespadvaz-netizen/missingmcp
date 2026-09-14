/**
 * Regresiones del cap de lecturas por turno y del contrato del turno final.
 * Todas recorren Orchestrator.run() con Fixtures.scriptedProvider: el mismo
 * arnés simulado que usan aceptación y las pruebas existentes de despacho.
 */
function registerUnitTurnToolCap() {

  function request(name, args) {
    return { id: 'call-' + name, name: name, arguments: args || {} };
  }

  function eightSameReads() {
    var reads = [];
    for (var i = 0; i < 8; i++) {
      reads.push(request('notion.search', { query: 'lectura-' + i }));
    }
    return reads;
  }

  function runEightReads() {
    var providers = Fixtures.providers([
      { text: '', tool_requests: eightSameReads() },
      { text: 'respuesta final', tool_requests: [] }
    ], []);
    return {
      providers: providers,
      result: Orchestrator.run('Prueba controlada del cap por turno.', {
        current_model: 'OPENAI',
        operator_context: 'METIS',
        intent: 'analysis',
        providers: providers
      })
    };
  }

  TestRunner.unit('Cap de lecturas por turno', '8 lecturas ejecutan exactamente 5', function (t) {
    var run = runEightReads();
    t.equals(run.result.status, 'COMPLETED', 'la corrida completa sin disparar el fusible global');
    t.equals(run.result.limits.tool_calls, 5, 'sólo cinco solicitudes alcanzan ToolBroker.invoke');
    t.equals(run.providers.OPENAI.calls.length, 2, 'el productor continúa después del lote truncado');
  });

  TestRunner.unit('Cap de lecturas por turno', '3 omitidas no aumentan tool_calls ni tool_errors', function (t) {
    var run = runEightReads();
    t.equals(run.result.turn_tool_truncations[0].skipped, 3, 'las tres lecturas excedentes quedan omitidas');
    t.equals(run.result.limits.tool_calls, 5, 'las omitidas no consumen el fusible global');
    t.equals(run.result.tool_errors.length, 0, 'las omitidas no se fabrican como errores');
  });

  TestRunner.unit('Cap de lecturas por turno', 'simulate.* intercalada se ejecuta y conserva el orden relativo', function (t) {
    var originalInvoke = ToolBroker.invoke;
    var observed = [];
    ToolBroker.invoke = function (session, name, args) {
      observed.push(name);
      return originalInvoke(session, name, args);
    };

    try {
      var batch = [
        request('notion.search', { query: 'gate' }),
        request('asana.search', { query: 'registrar' }),
        request('simulate.notion_write', {
          operation: 'create', destination: 'destino-1',
          destination_provenance: 'LIVE_OPERATOR', payload: { value: 1 },
          destination_meta: { context: 'METIS' }
        }),
        request('drive.search', { query: 'notas' }),
        request('calendar.read', { query: 'ritual', time_window: {} }),
        request('notion.decisions', {}),
        request('notion.fetch', { id: 'met-canon-01' }),
        request('simulate.asana_write', {
          operation: 'create', destination: 'destino-2',
          destination_provenance: 'LIVE_OPERATOR', payload: { value: 2 },
          destination_meta: { context: 'METIS' }
        }),
        request('asana.get', { id: 'met-task-12' }),
        request('drive.fetch', { id: 'drv-01' })
      ];
      var providers = Fixtures.providers([
        { text: '', tool_requests: batch },
        { text: 'respuesta final', tool_requests: [] }
      ], []);
      var result = Orchestrator.run('Prueba controlada con simulaciones intercaladas.', {
        current_model: 'OPENAI', operator_context: 'METIS', intent: 'analysis',
        mandate: { source: 'LIVE_OPERATOR', requests_execution: true },
        providers: providers
      });

      t.deepEquals(observed, [
        'notion.search', 'asana.search', 'simulate.notion_write',
        'drive.search', 'calendar.read', 'notion.decisions',
        'simulate.asana_write'
      ], 'se conserva el orden original quitando únicamente las lecturas excedentes');
      t.equals(result.actions.length, 2, 'ambas simulate.* se ejecutan fuera del cap de lectura');
      t.equals(result.turn_tool_truncations[0].skipped, 3, 'sólo se omiten las tres lecturas excedentes');
    } finally {
      ToolBroker.invoke = originalInvoke;
    }
  });

  TestRunner.unit('Contrato del turno final', 'el turno final forzado del auditor recibe contrato null', function (t) {
    var read = request('notion.search', { query: 'canon' });
    var providers = Fixtures.providers([
      { text: 'entregable del productor', tool_requests: [] },
      { text: 'conclusión reconciliada', tool_requests: [] }
    ], [
      { text: '', tool_requests: [read] },
      { text: '', tool_requests: [read] },
      { text: '', tool_requests: [read] },
      { text: 'BLOQUEO_MATERIAL: NO\nVeredicto final.', tool_requests: [] }
    ]);

    var result = Orchestrator.run('Prueba controlada de auditoría Metis.', {
      current_model: 'OPENAI', operator_context: 'METIS', intent: 'analysis',
      requires_cross_audit: true, providers: providers
    });

    t.equals(result.status, 'COMPLETED', 'la auditoría y reconciliación completan');
    t.equals(providers.ANTHROPIC.calls.length, 4, 'tres turnos de lectura más el final forzado');
    t.equals(providers.ANTHROPIC.calls[3].tools.length, 0, 'el final forzado del auditor recibe null, sin herramientas');
    t.includes(providers.ANTHROPIC.calls[0].prompt, 'máximo 5 lecturas', 'el prompt inicial del auditor declara el cap');
    t.includes(providers.ANTHROPIC.calls[1].prompt, 'poder probatorio', 'la continuación también declara priorización');
  });

  TestRunner.unit('Contrato del turno final', 'el productor final conserva contrato de acción', function (t) {
    var read = request('notion.search', { query: 'gate' });
    var providers = Fixtures.providers([
      { text: '', tool_requests: [read] },
      { text: '', tool_requests: [read] },
      { text: '', tool_requests: [read] },
      { text: 'respuesta final del productor', tool_requests: [] }
    ], []);

    var result = Orchestrator.run('Prueba controlada del productor Metis.', {
      current_model: 'OPENAI', operator_context: 'METIS', intent: 'analysis',
      mandate: { source: 'LIVE_OPERATOR', requests_execution: true },
      providers: providers
    });

    t.equals(result.status, 'COMPLETED', 'el productor completa tras el turno final forzado');
    t.equals(providers.OPENAI.calls.length, 4, 'tres turnos de lectura más el final forzado');
    t.includes(providers.OPENAI.calls[3].tools, 'simulate.asana_write', 'el final conserva el contrato de acción');
    t.includes(providers.OPENAI.calls[0].prompt, 'máximo 5 lecturas', 'el prompt inicial del productor declara el cap');
    t.includes(providers.OPENAI.calls[1].prompt, 'poder probatorio', 'la continuación declara priorización');
  });

  TestRunner.unit('Salida de truncamiento', 'el retorno expone truncamiento real y el límite efectivo', function (t) {
    var run = runEightReads();
    t.equals(run.result.turn_tool_truncations.length, 1, 'hay un registro sólo porque ocurrió truncamiento real');
    t.deepEquals(run.result.turn_tool_truncations[0], {
      requested: 8,
      executed: 5,
      skipped: 3,
      skipped_tools: ['notion.search']
    }, 'el registro expone conteos y nombres omitidos únicos');
    t.equals(run.result.limits.max_tool_calls_per_turn, 5, 'el retorno publica el cap efectivo por turno');
    t.equals(run.result.limits.max_tool_calls, 16, 'el fusible global permanece en dieciséis');
  });

  TestRunner.unit('Objeto de auditoría', 'la solicitud original sobrevive productor, auditor y reconciliación', function (t) {
    var marker = 'PILOTO-METIS-001';
    var operatorRequest = 'Audita exclusivamente la solicitud ' + marker + '.';

    function promptAwareProvider(name, responder) {
      var provider = Fixtures.scriptedProvider(name, []);
      provider.complete = function (requestData) {
        return provider.completeWithTools(requestData, null);
      };
      provider.completeWithTools = function (requestData, toolContract) {
        provider.calls.push({
          system: requestData.system,
          prompt: requestData.prompt,
          tools: (toolContract || []).map(function (tool) { return tool.name; })
        });
        return provider.normalizeResponse(responder(requestData, provider.calls.length));
      };
      return provider;
    }

    var openai = promptAwareProvider('OPENAI', function (requestData, callNumber) {
      var receivedMarker = requestData.prompt.indexOf(marker) !== -1;
      if (callNumber === 1) {
        return {
          text: receivedMarker
            ? 'Análisis del productor sin repetir el identificador.'
            : 'ERROR: el productor no recibió la solicitud original.',
          tool_requests: []
        };
      }
      return {
        text: receivedMarker
          ? 'Respuesta final para ' + marker + '.'
          : 'ERROR: la reconciliación perdió la solicitud original.',
        tool_requests: []
      };
    });

    var anthropic = promptAwareProvider('ANTHROPIC', function (requestData) {
      return {
        text: requestData.prompt.indexOf(marker) !== -1
          ? 'BLOQUEO_MATERIAL: NO\nSolicitud específica evaluada sin repetir el identificador.'
          : 'BLOQUEO_MATERIAL: SI\nERROR: el auditor no recibió la solicitud original.',
        tool_requests: []
      };
    });

    var result = Orchestrator.run(operatorRequest, {
      current_model: 'OPENAI',
      operator_context: 'METIS',
      intent: 'audit',
      providers: { OPENAI: openai, ANTHROPIC: anthropic }
    });

    t.equals(result.status, 'COMPLETED', 'el flujo end-to-end completa sin bloqueo material');
    t.equals(anthropic.calls.length, 1, 'el auditor interviene una vez');
    t.includes(anthropic.calls[0].prompt, marker, 'el prompt recibido por el auditor contiene la solicitud original');
    t.includes(anthropic.calls[0].prompt, 'Objeto de auditoría: evalúa exclusivamente esa solicitud original.',
      'el auditor recibe la restricción explícita sobre el objeto');
    t.equals(openai.calls.length, 2, 'el productor y la reconciliación usan el proveedor de origen');
    t.includes(openai.calls[1].prompt, marker, 'el prompt de reconciliación contiene la solicitud original');
    t.includes(openai.calls[1].prompt, 'Debes responder exclusivamente a esa solicitud original.',
      'la reconciliación recibe la restricción explícita de decisión');
    t.includes(result.final_answer, marker,
      'la respuesta final sólo recupera el marcador porque llegó al prompt de reconciliación');
  });
}
