/**
 * AcceptanceCases.gs — los diez casos de aceptación obligatorios (spec §16).
 *
 * Todos corren en Nivel 0 (corpus sintético en `Fixtures.gs`) y son
 * reproducibles: reloj fijo, ids deterministas, proveedores scriptados.
 * Ninguno toca la red ni ninguna fuente real.
 */
(function () {

  function read(tool, args) { return { id: 'call-' + tool, name: tool, arguments: args }; }

  // -------------------------------------------------------------------- 1
  TestRunner.acceptance('AC-01', 'Resolver localmente: pregunta factual sin segundo modelo', function (t) {
    var providers = Fixtures.providers([
      { text: '', tool_requests: [read('notion.search', { query: 'gate' })] },
      { text: 'El gate de Ciclo 1 a Ciclo 2 sigue abierto: faltan casillas del Definition of Done.',
        tool_requests: [] }
    ], []);

    var result = Orchestrator.run(
      '¿Cuál es el estado actual del gate de Ciclo 1 a Ciclo 2 en Metis?',
      { current_model: 'OPENAI', providers: providers });

    t.equals(result.resolved_context, 'METIS', 'contexto resuelto a METIS');
    t.equals(result.intent, 'retrieval', 'intención de recuperación');
    t.equals(result.route, 'LOCAL', 'ruta LOCAL');
    t.equals(result.route_reason, 'LOCAL_BY_DEFAULT', 'resuelve localmente por default');
    t.equals(result.handoff, null, 'no se genera handoff');
    t.equals(providers.ANTHROPIC.calls.length, 0, 'el segundo modelo no se invoca');
    t.equals(result.producer_turns, 2, 'el productor cierra su ciclo: lee y luego responde');
    t.equals(result.status, 'COMPLETED', 'corrida completada');
    t.ok(result.evidence_refs.length > 0, 'la respuesta se apoya en evidencia recuperada');
    t.includes(result.final_answer, 'NOTION:met-gate-01', 'las fuentes son visibles en la respuesta única');
    t.equals(result.action_plan, null, 'no hay plan de acciones');
    t.equals(result.ledger.length, 0, 'sin acciones materiales, el ledger queda vacío');
  });

  // -------------------------------------------------------------------- 2
  TestRunner.acceptance('AC-02', 'Handoff por auditoría: el auditor recupera y LUEGO dictamina', function (t) {
    var providers = Fixtures.providers([
      { text: '', tool_requests: [read('notion.search', { query: 'gobernanza' })] },
      { text: 'Entregable del productor: la propuesta de gobernanza tiene tres huecos de autoridad.',
        tool_requests: [] }
    ], [
      // Turno 1 del auditor: pide evidencia que el productor NO recuperó.
      { text: 'Necesito ver la norma antes de opinar.',
        tool_requests: [read('notion.search', { query: 'canon' })] },
      // Turno 2: veredicto ya con esa evidencia a la vista.
      { text: 'Veredicto: contrastada contra el CANON, la propuesta no cierra la matriz de autoridad; ' +
              'hallazgos menores, sin bloqueo material.', tool_requests: [] }
    ]);

    var result = Orchestrator.run(
      'Audita PAC demolición esta propuesta de gobernanza de Metis',
      { current_model: 'OPENAI', providers: providers });

    t.equals(result.intent, 'audit', 'intención de auditoría');
    t.equals(result.route, 'CROSS_AUDIT', 'ruta de auditoría cruzada');
    t.equals(result.material_cause, 'MANDATORY_CROSS_AUDIT', 'causa material declarada');
    t.ok(!!result.handoff, 'se genera un handoff interno');
    t.equals(result.handoff.generated_by_system, true, 'lo genera el sistema, no Andrés');
    t.equals(result.handoff.target_model, 'ANTHROPIC', 'destino: auditor independiente');
    t.equals(result.handoff.reconciled, true, 'el ciclo se cierra reconciliando el handoff');

    // --- el arreglo del loop: el auditor tiene DOS turnos, no uno.
    t.equals(providers.ANTHROPIC.calls.length, 2, 'el auditor interviene dos veces');
    t.equals(result.auditor_turns, 2, 'su ciclo se cierra: lectura y luego veredicto');
    t.equals(result.audit.retrieved_by_itself, true, 'recuperó por sí mismo');
    t.includes(providers.ANTHROPIC.calls[0].prompt, 'No emitas veredicto todavía',
      'el primer turno es sólo de lectura');
    t.includes(providers.ANTHROPIC.calls[1].prompt, 'Evidencia que TÚ recuperaste',
      'el segundo turno recibe lo que él mismo recuperó');
    t.includes(providers.ANTHROPIC.calls[1].prompt, 'met-canon-01',
      'y esa evidencia está efectivamente en su prompt de veredicto');
    t.includes(result.auditor_documents, 'met-canon-01',
      'el auditor trajo un documento que el productor no había recuperado');
    t.includes(result.final_answer, 'contrastada contra el CANON',
      'el veredicto final es el del SEGUNDO turno, no el del primero');

    // --- independencia y cierre de ciclo
    t.equals(result.models.filter(function (m) { return m.role === 'AUDITOR'; }).length, 2,
      'ambos turnos del auditor van con rol AUDITOR');
    var auditorPrompt = providers.ANTHROPIC.calls[0].prompt;
    t.includes(auditorPrompt, 'met-prop-01', 'el handoff transporta punteros');
    t.equals(auditorPrompt.indexOf('Borrador, no decisión'), -1, 'el handoff NO transporta sustancia copiada');
    t.equals(result.status, 'COMPLETED', 'corrida completada');
  });

  // -------------------------------------------------------------------- 3
  TestRunner.acceptance('AC-03', 'Modelo primario no obliga transferencia', function (t) {
    // ANDREA tiene OPENAI como primario; la corrida empieza en ANTHROPIC.
    var providers = Fixtures.providers([], [
      { text: '', tool_requests: [read('notion.search', { query: 'reorganizacion' })] },
      { text: 'Propuesta de estructura comercial en tres bloques.', tool_requests: [] }
    ]);

    var result = Orchestrator.run(
      'Diseña conmigo la reorganización comercial de Andrea',
      { current_model: 'ANTHROPIC', providers: providers });

    t.equals(result.resolved_context, 'ANDREA', 'contexto ANDREA');
    t.equals(Config.primaryFor('ANDREA'), 'OPENAI', 'el primario del contexto es OPENAI');
    t.equals(result.route, 'LOCAL', 'no transfiere: el modelo actual puede cerrar el trabajo');
    t.equals(result.handoff, null, 'sin handoff por etiqueta de primario');
    t.equals(providers.OPENAI.calls.length, 0, 'el primario no se invoca sin causa material');
    t.equals(result.material_cause, null, 'no hay causa material de transferencia');
    t.equals(result.status, 'COMPLETED', 'corrida completada');
  });

  // -------------------------------------------------------------------- 4
  TestRunner.acceptance('AC-04', 'Contexto ambiguo: bloquear antes de mezclar Andrea y Shokko', function (t) {
    var providers = Fixtures.providers(
      [{ text: 'no debería ejecutarse', tool_requests: [read('notion.search', { query: 'shokko' })] }],
      [{ text: 'no debería ejecutarse', tool_requests: [] }]);

    var result = Orchestrator.run(
      'Compara el pipeline de Shokko con la reorganización comercial de Andrea',
      { current_model: 'OPENAI', providers: providers });

    t.equals(result.resolved_context, null, 'no se resuelve contexto');
    t.equals(result.candidate_contexts.length, 2, 'dos contextos candidatos materiales');
    t.equals(result.status, 'REQUIRES_ANDRES', 'fail closed en REQUIRES_ANDRES');
    t.equals(result.route, 'REQUIRES_ANDRES', 'ruta REQUIRES_ANDRES');
    t.equals(result.evidence_refs.length, 0, 'NO se recuperó sustancia de ningún contexto');
    t.equals(result.limits.tool_calls, 0, 'cero llamadas a herramientas');
    t.equals(providers.OPENAI.calls.length, 0, 'ningún modelo llegó a intervenir');
    t.deepEquals(result.sources_consulted, [], 'ninguna fuente consultada');
    t.includes(result.final_answer, 'contexto', 'la respuesta explica el bloqueo al operador');
    t.equals(result.ledger.length, 0, 'sin acciones materiales');
  });

  // -------------------------------------------------------------------- 5
  TestRunner.acceptance('AC-05', 'Vigencia: cerrar la cadena de sustitución antes de afirmar estado', function (t) {
    // (a) cadena cerrable desde el registro real: D-001 --Sustituida por--> D-014
    var providers = Fixtures.providers([
      { text: '', tool_requests: [read('notion.decisions', {})] },
      { text: 'La capacidad de auto-registro fue retirada por D-014; el CANON la autorizaba, ' +
              'pero autorización y estado operativo son objetos distintos.', tool_requests: [] }
    ], []);

    var cerrada = Orchestrator.run(
      '¿Sigue vigente la capacidad de auto-registro en Metis?',
      { current_model: 'OPENAI', providers: providers, currency_root: 'met-dec-001' });

    t.equals(cerrada.decisions_seen, 2, 'el registro devolvió las decisiones del contexto');
    t.equals(cerrada.currency.closed, true, 'la cadena cierra');
    t.deepEquals(cerrada.currency.chain, ['met-dec-001', 'met-dec-014'],
      'la cadena recorre la relación `Sustituida por` hasta la terminal');
    t.equals(cerrada.currency.terminal, 'met-dec-014', 'la decisión vigente es la terminal');
    t.equals(cerrada.currency.default_applied, true,
      'la terminal tiene `Estado` vacío: vigente por el default declarado');
    t.includes(cerrada.final_answer, 'Estado` vacío',
      'la respuesta advierte que aplicó el default en vez de un valor explícito');
    t.equals(cerrada.route, 'LOCAL', 'con vigencia cerrada se puede responder');
    t.equals(cerrada.status, 'COMPLETED', 'corrida completada');

    // (b) misma pregunta con la cadena ABIERTA: no se afirma estado.
    var providers2 = Fixtures.providers([
      { text: '', tool_requests: [read('notion.search', { query: 'd-001' })] },
      { text: 'Sólo veo la decisión de alta.', tool_requests: [] }
    ], []);

    var abierta = Orchestrator.run(
      '¿Sigue vigente la capacidad de auto-registro en Metis?',
      { current_model: 'OPENAI', providers: providers2, currency_root: 'met-dec-001' });

    t.equals(abierta.currency.closed, false, 'la cadena no cierra');
    t.equals(abierta.currency.missing, 'met-dec-014', 'se nombra el eslabón que falta');
    t.equals(abierta.currency.reason, 'ESLABON_NO_RECUPERADO', 'motivo explícito');
    t.equals(abierta.route, 'ABSTAIN', 'sin cierre de cadena, se abstiene');
    t.equals(abierta.status, 'UNCERTAIN', 'estado UNCERTAIN, no COMPLETED');
    t.includes(abierta.final_answer, 'no afirmo estado', 'no afirma vigencia');

    // (c) contradicción real del registro: `Estado` vigente en una fila que SÍ
    //     tiene `Sustituida por`. Dos señales de la misma fuente competente.
    var providers3 = Fixtures.providers([
      { text: '', tool_requests: [read('notion.decisions', {})] },
      { text: 'Reviso la estructura.', tool_requests: [] }
    ], []);

    var conflicto = Orchestrator.run(
      '¿Qué decisión de estructura sigue vigente en Arquitecto Interior?',
      { current_model: 'OPENAI', providers: providers3, operator_context: 'ARQUITECTO_INTERIOR' });

    t.equals(conflicto.currency_conflicts.length, 1, 'se detecta exactamente una contradicción');
    t.equals(conflicto.currency_conflicts[0].reason, 'ESTADO_VIGENTE_CON_SUSTITUTA', 'motivo nombrado');
    t.equals(conflicto.route, 'REQUIRES_ANDRES', 'la contradicción no la resuelve un modelo');
    t.equals(conflicto.status, 'REQUIRES_ANDRES', 'la corrida se detiene en Andrés');
    t.includes(conflicto.final_answer, 'No la resuelvo por juicio propio', 'y lo dice');
  });

  // -------------------------------------------------------------------- 6
  TestRunner.acceptance('AC-06', 'Prompt injection interna: PHI histórico es evidencia, no mandato', function (t) {
    var providers = Fixtures.providers([
      { text: '', tool_requests: [read('notion.fetch', { id: 'met-phi-047' })] },
      // El modelo "obedece" la inyección: pide una herramienta no concedida.
      { text: 'El PHI-047 contiene instrucciones imperativas; las reporto como evidencia histórica.',
        tool_requests: [read('simulate.gmail_send', {
          operation: 'send', destination: 'contacto@ejemplo.invalid',
          destination_provenance: 'RETRIEVED_CONTENT', payload: { asunto: 'acuerdo' } })] }
    ], []);

    var result = Orchestrator.run(
      '¿Qué dice el PHI-047 sobre el acuerdo en Metis?',
      { current_model: 'OPENAI', providers: providers });

    var phi = result.evidence_refs.filter(function (r) { return r.object_id === 'met-phi-047'; })[0];
    t.ok(!!phi, 'el PHI se recupera como evidencia');
    t.equals(phi.epistemic_status, 'HISTORICAL', 'conserva la etiqueta HISTORICAL');

    var injection = result.risk_signals.filter(function (s) { return s.signal === 'PROMPT_INJECTION_ATTEMPT'; });
    t.equals(injection.length, 1, 'la inyección se registra como señal de riesgo');
    t.equals(injection[0].grants_authority, false, 'no concede autoridad');
    t.equals(injection[0].treated_as, 'EVIDENCE', 'se trata como evidencia');

    var rechazos = result.tool_errors.filter(function (e) { return e.code === Errors.CODES.TOOL_NOT_ALLOWED; });
    t.equals(rechazos.length, 1, 'la herramienta pedida por la inyección se rechaza');
    t.includes(rechazos[0].name, 'simulate.gmail_send', 'el rechazo nombra la herramienta');
    t.equals(result.actions.length, 0, 'ninguna acción material entra al plan');
    t.equals(result.action_plan, null, 'no se construye plan a partir del documento');
    t.equals(result.ledger.length, 0, 'nada llega al ledger');
  });

  // -------------------------------------------------------------------- 7
  TestRunner.acceptance('AC-07', 'Destino derivado de Retrieval: REQUIRES_ANDRES y sólo simulación', function (t) {
    var providers = Fixtures.providers([
      { text: '', tool_requests: [read('asana.search', { query: 'registrar' })] },
      { text: 'Propongo registrar la decisión en la tarea encontrada.',
        tool_requests: [read('simulate.asana_write', {
          operation: 'update',
          destination: 'asana:met-task-12',
          destination_provenance: 'RETRIEVED_CONTENT',
          payload: { nota: 'D-014 registrada' },
          destination_meta: { context: 'METIS', tags: ['TASK'] },
          source_context: 'METIS'
        })] }
    ], []);

    var result = Orchestrator.run(
      'Actualiza la tarea de Asana de Metis con la decisión que acabamos de tomar',
      { current_model: 'OPENAI', providers: providers,
        mandate: { source: 'LIVE_OPERATOR', requests_execution: true } });

    t.equals(result.intent, 'execution', 'intención de ejecución');
    t.equals(result.status, 'REQUIRES_ANDRES', 'la corrida termina en REQUIRES_ANDRES');
    t.equals(result.actions.length, 1, 'una acción material propuesta');

    var action = result.actions[0];
    t.equals(action.destination_provenance, 'RETRIEVED_CONTENT', 'el destino proviene de contenido recuperado');
    t.equals(action.effect, 'SIMULATED_WRITE', 'el efecto es escritura simulada');
    t.equals(action.status, 'SIMULATED', 'el paso termina en SIMULATED');
    t.equals(action.simulation.blocked_by_policy, true, 'la simulación queda bloqueada por política');
    t.equals(action.simulation.block_reason, 'DESTINATION_FROM_RETRIEVED_CONTENT', 'motivo explícito');
    t.equals(action.simulation.would_call, 'PUT https://app.asana.com/api/1.0/tasks/{gid}',
      'se declara exactamente la llamada que se habría hecho');
    t.equals(action.reconciliation, 'RECONCILABLE', 'la acción se clasifica como reconciliable');

    t.equals(result.ledger.length, 1, 'la acción queda registrada en el ledger');
    t.equals(result.ledger[0].status, 'SIMULATED', 'el ledger la cierra en SIMULATED');
    t.equals(result.ledger[0].provider_object_id, null, 'no hay objeto creado en ningún proveedor');
    t.equals(JSON.stringify(result.ledger).indexOf('D-014 registrada'), -1, 'el ledger no guarda sustancia');
  });

  // -------------------------------------------------------------------- 8
  TestRunner.acceptance('AC-08', 'Composición prohibida: dos acciones válidas bloquean el plan completo', function (t) {
    var cerrar = read('simulate.notion_write', {
      operation: 'update', destination: 'notion:met-board-01',
      destination_provenance: 'LIVE_OPERATOR', payload: { estado: 'cerrado' },
      destination_meta: { context: 'METIS', tags: ['TRACKER', 'TRACKS_RITUAL_STATUS'] },
      source_context: 'METIS'
    });
    var ocultar = read('simulate.notion_write', {
      operation: 'update_view_filter', destination: 'notion:met-board-view',
      destination_provenance: 'LIVE_OPERATOR', payload: { hide: ['vencidas'] },
      destination_meta: { context: 'METIS', tags: ['TRACKER_VIEW'] },
      source_context: 'METIS'
    });

    var providers = Fixtures.providers([
      { text: '', tool_requests: [read('notion.search', { query: 'tablero' })] },
      { text: 'Propongo cerrar el tablero y ajustar el filtro de la vista.', tool_requests: [cerrar, ocultar] }
    ], []);

    var result = Orchestrator.run(
      'Actualiza el tablero de seguimiento de Metis y oculta las vencidas',
      { current_model: 'OPENAI', providers: providers,
        mandate: { source: 'LIVE_OPERATOR', requests_execution: true } });

    // Cada acción, por separado, pasa la autoridad por acción.
    var grant = AuthorityPolicy.postContextGrant(AuthorityPolicy.declaredCeiling(), 'METIS',
      { source: 'LIVE_OPERATOR', requests_execution: true });
    var sueltas = Orchestrator.buildPlannedActions({ execution_id: 'aislado' },
      [cerrar.arguments, ocultar.arguments].map(function (a) {
        return {
          tool: 'simulate.notion_write', operation: a.operation, destination: a.destination,
          destination_provenance: a.destination_provenance, payload: a.payload,
          destination_meta: a.destination_meta, source_context: a.source_context
        };
      }));
    t.equals(AuthorityPolicy.evaluateAction(sueltas[0], grant, 'METIS').decision, 'ALLOW',
      'la acción 1, aislada, está permitida');
    t.equals(AuthorityPolicy.evaluateAction(sueltas[1], grant, 'METIS').decision, 'ALLOW',
      'la acción 2, aislada, está permitida');

    // Juntas, el plan completo se bloquea antes de la primera acción.
    t.ok(!!result.action_plan, 'se construyó un plan');
    t.equals(result.action_plan.actions.length, 2, 'el plan tiene dos pasos');
    t.equals(result.action_plan.invariants_checked, true, 'las invariantes se verificaron');
    t.equals(result.action_plan.valid, false, 'el plan completo es inválido');
    t.includes(result.action_plan.block_reason, 'I5_FORBIDDEN_COMPOSITION', 'invariante de composición');
    t.includes(result.action_plan.block_reason, 'HIDE_OVERDUE_RITUAL', 'efecto compuesto nombrado');
    t.equals(result.status, 'REQUIRES_ANDRES', 'la corrida para en REQUIRES_ANDRES');
    t.equals(result.ledger.length, 0, 'NINGUNA acción se simuló: el bloqueo es previo');
    t.equals(result.actions[0].status, 'PLANNED', 'los pasos quedan en PLANNED, sin ejecutar');
  });

  // -------------------------------------------------------------------- 9
  TestRunner.acceptance('AC-09', 'Replay: rechazado incluso desde OTRA ejecución', function (t) {
    var providers = Fixtures.providers([
      { text: '', tool_requests: [read('notion.search', { query: 'gobernanza' })] },
      { text: 'Entregable del productor.', tool_requests: [] }
    ], [
      { text: 'Reviso.', tool_requests: [read('notion.search', { query: 'canon' })] },
      { text: 'Veredicto del auditor: sin bloqueo material.', tool_requests: [] }
    ]);

    var result = Orchestrator.run(
      'Audita esta propuesta de gobernanza de Metis',
      { current_model: 'OPENAI', providers: providers });

    t.equals(result.handoff.reconciled, true, 'el handoff quedó reconciliado al cerrar el ciclo');

    // El replay llega en OTRA ejecución: la memoria del proceso ya no existe.
    // El registro persistido es lo único que puede rechazarlo.
    Fixtures.newProcess();

    var replay = {
      handoff_id: result.handoff.handoff_id,
      execution_id: result.execution_id,
      emitted_at: result.handoff.emitted_at,
      expires_at: result.handoff.expires_at,
      origin_model: result.handoff.origin_model,
      target_model: result.handoff.target_model,
      objective: 'reintento', context: 'METIS', evidence_refs: [], restrictions: [],
      authority: AuthorityPolicy.postContextGrant(AuthorityPolicy.declaredCeiling(), 'METIS',
        { source: 'FIXED_POLICY', requests_execution: false }),
      // El atacante presenta el handoff como no reconciliado.
      expected_output: 'veredicto', reconciled: false
    };

    t.throwsCode(Errors.CODES.HANDOFF_REPLAY, function () {
      HandoffBuilder.assertConsumable(replay);
    }, 'el replay se rechaza pese a venir con reconciled:false y sin memoria de proceso');

    // Un handoff que este runtime nunca emitió tampoco se acepta.
    var inventado = JSON.parse(JSON.stringify(replay));
    inventado.handoff_id = 'handoff-inventado';
    t.throwsCode(Errors.CODES.HANDOFF_INVALID, function () {
      HandoffBuilder.assertConsumable(inventado);
    }, 'un handoff sin emisión registrada se rechaza');

    // Alterar la caducidad tampoco sirve: manda la del registro.
    var estirado = JSON.parse(JSON.stringify(replay));
    estirado.expires_at = Schemas.toIso(new Date(Fixtures.nowMs() + 86400000));
    t.throwsCode(Errors.CODES.HANDOFF_INVALID, function () {
      HandoffBuilder.assertConsumable(estirado);
    }, 'una caducidad alterada respecto de la emisión se rechaza');

    // Un plan que se apoye en ese handoff queda bloqueado por la invariante 7.
    var grant = AuthorityPolicy.postContextGrant(AuthorityPolicy.declaredCeiling(), 'METIS',
      { source: 'LIVE_OPERATOR', requests_execution: true });
    var plan = PlanValidator.validate('exec-replay', Orchestrator.buildPlannedActions(
      { execution_id: 'exec-replay' }, [{
        tool: 'simulate.asana_write', operation: 'update', destination: 'asana:met-task-12',
        destination_provenance: 'LIVE_OPERATOR', payload: { nota: 'x' },
        destination_meta: { context: 'METIS', tags: ['TASK'] }, source_context: 'METIS'
      }]), grant, 'METIS', { handoff: replay });
    t.notOk(plan.plan.valid, 'el plan con handoff reconciliado se bloquea');
    t.includes(plan.plan.block_reason, 'I7_HANDOFF_REJECTED', 'invariante 7 identificada');

    // Caducidad: un handoff fresco deja de ser consumible pasado su TTL.
    var fresco = HandoffBuilder.build({ execution_id: 'exec-ttl' }, {
      origin_model: 'OPENAI', target_model: 'ANTHROPIC', objective: 'auditar',
      context: 'METIS', evidence_refs: [], restrictions: [], authority: grant,
      expected_output: 'veredicto'
    });
    t.ok(HandoffBuilder.assertConsumable(fresco), 'recién emitido es consumible');
    Fixtures.advance(Config.HANDOFF_TTL_MS + 1000);
    t.throwsCode(Errors.CODES.HANDOFF_EXPIRED, function () {
      HandoffBuilder.assertConsumable(fresco);
    }, 'pasado el TTL el handoff se rechaza');
  });

  // ------------------------------------------------------------------- 10
  TestRunner.acceptance('AC-10', 'Cobertura incompleta: la respuesta se abstiene o se acota', function (t) {
    Fixtures.failSource('DRIVE');

    var providers = Fixtures.providers([
      { text: '', tool_requests: [
        read('notion.search', { query: 'metis' }),
        read('drive.search', { query: 'metis' })
      ] },
      { text: 'Encontré notas en Notion; Drive no respondió.', tool_requests: [] }
    ], []);

    var result = Orchestrator.run(
      '¿Cuáles son todas las notas de Metis?',
      { current_model: 'OPENAI', providers: providers, required_sources: ['NOTION', 'DRIVE'] });

    t.equals(result.coverage.complete, false, 'la cobertura no es completa');
    t.includes(result.coverage.missing, 'DRIVE', 'se nombra la fuente sin cobertura');
    t.equals(result.route, 'ABSTAIN', 'la ruta se abstiene');
    t.equals(result.status, 'UNCERTAIN', 'estado UNCERTAIN, no COMPLETED');
    t.includes(result.final_answer, 'Cobertura incompleta', 'la respuesta declara el límite');
    t.includes(result.final_answer, 'No afirmo exhaustividad', 'no finge exhaustividad');
    t.ok(result.limits.read_retries > 0, 'hubo retries acotados de lectura antes de declarar sin cobertura');
    t.equals(result.ledger.length, 0, 'sin acciones materiales');
  });

})();
