/**
 * ToolBroker.gs — contrato común de herramientas, independiente del proveedor
 * (spec §10).
 *
 * Lecturas permitidas: notion.search, notion.fetch, asana.search, asana.get,
 * calendar.read, drive.search, drive.fetch.
 * Escrituras: expuestas ÚNICAMENTE como `simulate.*`.
 *
 * Guardas aplicadas en cada invocación:
 *   - herramienta dentro del AuthorityGrant vigente;
 *   - tope de tool calls por corrida (parada dura);
 *   - alcance de contexto: sin contexto resuelto no se sirve sustancia;
 *   - retries acotados de lectura; agotados, la fuente queda SIN COBERTURA;
 *   - todo texto recuperado se etiqueta como evidencia y se le buscan señales
 *     de prompt injection (nunca se obedece).
 *
 * Una herramienta `simulate.*` NO ejecuta nada al ser invocada: encola la acción
 * propuesta para que el PLAN COMPLETO se valide antes de cualquier simulación
 * (spec §7). Así el modelo no puede saltarse PlanValidator.
 */
var ToolBroker = (function () {

  /**
   * Capacidades escritas como literal (no `Config.CAPABILITIES.X`) para no
   * depender del orden de carga de archivos en Apps Script. `Unit_Router.gs`
   * verifica que sigan coincidiendo con `Config.CAPABILITIES`.
   */
  var READ_TOOLS = {
    'notion.search':  { capability: 'notion_read',   source: 'NOTION',   substance: false },
    'notion.fetch':   { capability: 'notion_read',   source: 'NOTION',   substance: true },
    'asana.search':   { capability: 'asana_read',    source: 'ASANA',    substance: false },
    'asana.get':      { capability: 'asana_read',    source: 'ASANA',    substance: true },
    'calendar.read':  { capability: 'calendar_read', source: 'CALENDAR', substance: false },
    'drive.search':   { capability: 'drive_read',    source: 'DRIVE',    substance: false },
    'drive.fetch':    { capability: 'drive_read',    source: 'DRIVE',    substance: true }
  };

  var DESCRIPTIONS = {
    'notion.search': 'Busca páginas en Notion. Devuelve punteros (id, título, contexto).',
    'notion.fetch': 'Recupera una página de Notion por id, con su texto plano.',
    'asana.search': 'Busca tareas en Asana. Devuelve punteros.',
    'asana.get': 'Recupera una tarea de Asana por gid.',
    'calendar.read': 'Lee eventos de calendarios declarados en una ventana temporal.',
    'drive.search': 'Busca archivos en Drive por título. Devuelve punteros.',
    'drive.fetch': 'Recupera el contenido de texto de un archivo de Drive por id.',
    'simulate.notion_write': 'Propone una escritura en Notion. SIMULADA: no ejecuta nada.',
    'simulate.asana_write': 'Propone una escritura en Asana. SIMULADA: no ejecuta nada.',
    'simulate.calendar_write': 'Propone una escritura en Calendar. SIMULADA: no ejecuta nada.',
    'simulate.drive_write': 'Propone una escritura en Drive. SIMULADA: no ejecuta nada.',
    'simulate.gmail_send': 'Propone un envío por Gmail. SIMULADA: no ejecuta nada.'
  };

  /** Contrato expuesto al modelo. Sólo nombres abstractos, jamás secretos. */
  function contract(grant) {
    var tools = [];
    for (var i = 0; i < grant.allowed_tools.length; i++) {
      var name = grant.allowed_tools[i];
      var isRead = !!READ_TOOLS[name];
      tools.push({
        name: name,
        kind: isRead ? 'READ' : 'SIMULATED_WRITE',
        capability: isRead ? READ_TOOLS[name].capability : 'simulated_write',
        description: DESCRIPTIONS[name] ? DESCRIPTIONS[name] : '',
        input_schema: isRead
          ? { type: 'object', properties: { query: { type: 'string' }, id: { type: 'string' },
                                            context: { type: 'string' }, time_window: { type: 'object' } } }
          : { type: 'object', properties: { operation: { type: 'string' }, destination: { type: 'string' },
                                            destination_provenance: { type: 'string' }, payload: { type: 'object' } },
              required: ['operation'] }
      });
    }
    return tools;
  }

  function newSession(execution, scope, grant) {
    return {
      execution_id: execution.execution_id,
      scope: scope,
      grant: grant,
      tool_calls: 0,
      evidence_refs: [],
      documents: [],
      proposed_actions: [],
      risk_signals: [],
      tool_errors: [],
      source_status: {},
      read_retries: 0
    };
  }

  function _markSource(session, source, covered, reason) {
    session.source_status[source] = { covered: covered, reason: covered ? null : reason };
  }

  function _withRetries(session, source, fn) {
    var attempts = 0;
    var lastError = null;
    while (attempts <= Config.LIMITS.MAX_READ_RETRIES) {
      try {
        var result = fn();
        _markSource(session, source, true, null);
        return { ok: true, value: result };
      } catch (e) {
        if (!Errors.is(e, Errors.CODES.READ_FAILED)) { throw e; }
        lastError = e;
        attempts++;
        session.read_retries++;
      }
    }
    _markSource(session, source, false, 'READ_FAILED_AFTER_RETRIES');
    return { ok: false, error: Errors.redactText(lastError ? lastError.message : 'lectura fallida') };
  }

  function _recordDocs(session, docs, wantsSubstance) {
    var refs = [];
    for (var i = 0; i < docs.length; i++) {
      var doc = docs[i];

      // Cero contaminación entre contextos: un resultado que pertenece a otro
      // contexto se descarta antes de entrar a la sesión, aunque la fuente lo
      // haya devuelto.
      if (session.scope.resolved && doc.context && doc.context !== session.scope.resolved) {
        session.risk_signals.push({
          object_id: doc.id,
          source: doc.source,
          signal: 'CROSS_CONTEXT_RESULT_DISCARDED',
          treated_as: 'DISCARDED',
          grants_authority: false,
          matches: 0
        });
        continue;
      }

      var ref = RetrievalPolicy.toEvidenceRef(doc);
      refs.push(ref);
      session.evidence_refs.push(ref);

      var stored = {
        source: doc.source, id: doc.id, title: doc.title, context: doc.context,
        kind: doc.kind, epistemic_status: ref.epistemic_status,
        snippet: wantsSubstance ? (doc.snippet === undefined ? null : doc.snippet) : null
      };
      session.documents.push(stored);

      if (wantsSubstance && doc.snippet) {
        var injection = RetrievalPolicy.detectInjection(doc.snippet);
        if (injection.is_injection_attempt) {
          session.risk_signals.push({
            object_id: doc.id,
            source: doc.source,
            signal: 'PROMPT_INJECTION_ATTEMPT',
            treated_as: 'EVIDENCE',
            grants_authority: false,
            matches: injection.signals.length
          });
        }
      }
    }
    return refs;
  }

  /**
   * Invoca una herramienta del contrato.
   * @return {object} resultado normalizado, sin secretos.
   */
  function invoke(session, toolName, args) {
    var params = args || {};

    if (session.grant.allowed_tools.indexOf(toolName) === -1) {
      throw Errors.toolNotAllowed(toolName);
    }
    if (session.tool_calls >= Config.LIMITS.MAX_TOOL_CALLS) {
      throw Errors.limitExceeded('MAX_TOOL_CALLS', session.tool_calls);
    }
    session.tool_calls++;

    // --- herramientas de escritura: encolan acción propuesta, no ejecutan.
    if (SimulatedWriteAdapter.isSimulationTool(toolName)) {
      var proposed = {
        tool: toolName,
        operation: params.operation,
        destination: (params.destination === undefined ? null : params.destination),
        destination_provenance: params.destination_provenance ? params.destination_provenance : 'RETRIEVED_CONTENT',
        payload: params.payload ? params.payload : {},
        destination_meta: params.destination_meta ? params.destination_meta : {},
        source_context: params.source_context ? params.source_context : null
      };
      session.proposed_actions.push(proposed);
      return {
        simulated: true,
        would_call: SimulatedWriteAdapter.wouldCall(toolName, params.operation),
        would_target: proposed.destination,
        effect_summary: 'acción encolada para validación de plan completo',
        blocked_by_policy: true,
        block_reason: 'PENDING_PLAN_VALIDATION'
      };
    }

    var spec = READ_TOOLS[toolName];
    if (!spec) { throw Errors.toolNotAllowed(toolName); }

    ContextResolver.assertReadAllowed(session.scope, params.context ? params.context : null, spec.substance);

    var outcome = _withRetries(session, spec.source, function () {
      switch (toolName) {
        case 'notion.search': return NotionReadAdapter.search(params.query, params);
        case 'notion.fetch':  return [NotionReadAdapter.fetch(params.id)];
        case 'asana.search':  return AsanaReadAdapter.search(params.query, params);
        case 'asana.get':     return [AsanaReadAdapter.get(params.id)];
        case 'calendar.read': return CalendarReadAdapter.read(params.query, params.time_window);
        case 'drive.search':  return DriveReadAdapter.search(params.query, params);
        case 'drive.fetch':   return [DriveReadAdapter.fetch(params.id)];
        default: throw Errors.toolNotAllowed(toolName);
      }
    });

    if (!outcome.ok) {
      return { ok: false, source: spec.source, coverage: false, error: outcome.error, documents: [] };
    }

    var docs = outcome.value || [];
    _recordDocs(session, docs, spec.substance);

    return {
      ok: true,
      source: spec.source,
      coverage: true,
      documents: docs.map(function (d) {
        return {
          id: d.id, title: d.title, context: d.context, kind: d.kind,
          epistemic_status: d.epistemic_status,
          snippet: spec.substance ? (d.snippet === undefined ? null : d.snippet) : null
        };
      })
    };
  }

  return {
    READ_TOOLS: READ_TOOLS,
    DESCRIPTIONS: DESCRIPTIONS,
    contract: contract,
    newSession: newSession,
    invoke: invoke
  };
})();
