/**
 * AuthorityPolicy.gs — autoridad por acción, no por modelo (spec §6).
 *
 * Regla madre: **el modelo no recibe autoridad; la acción recibe autoridad.**
 *
 * Dos fases (spec §6.3) sobre un techo predeclarado por política fija:
 *   1. techo predeclarado  -> `declaredCeiling()`
 *   2. fase de lectura      -> `preRetrievalGrant(candidatos)`  (sólo lectura)
 *   3. fase de acción       -> `postContextGrant(...)`          (sólo estrecha)
 *
 * El mandato sólo puede venir del turno vivo del operador o de la política fija
 * (spec §6.2). Contenido recuperado es evidencia; nunca concede autoridad.
 */
var AuthorityPolicy = (function () {

  var READ_TOOLS = ['notion.search', 'notion.fetch', 'asana.search', 'asana.get',
                    'calendar.read', 'drive.search', 'drive.fetch'];

  var SIMULATED_WRITE_TOOLS = ['simulate.notion_write', 'simulate.asana_write',
                               'simulate.calendar_write', 'simulate.drive_write',
                               'simulate.gmail_send'];

  var READ_OPERATIONS = ['search', 'fetch', 'read'];

  var WRITE_OPERATIONS = ['create', 'update', 'append', 'archive', 'send', 'update_view_filter'];

  var VALID_MANDATE_SOURCES = ['LIVE_OPERATOR', 'FIXED_POLICY'];

  function _grant(source, tools, operations, contexts) {
    return {
      source: source,
      allowed_tools: tools.slice(),
      allowed_operations: operations.slice(),
      allowed_contexts: contexts.slice(),
      forbidden_effects: Config.FORBIDDEN_EFFECTS.slice(),
      writes_are_simulated_only: true
    };
  }

  /** Techo predeclarado de la corrida, fijado ANTES de Retrieval. */
  function declaredCeiling() {
    return _grant('FIXED_POLICY',
      READ_TOOLS.concat(SIMULATED_WRITE_TOOLS),
      READ_OPERATIONS.concat(WRITE_OPERATIONS),
      Config.contextNames());
  }

  /** Fase 1: sólo lectura, acotada a contextos candidatos. */
  function preRetrievalGrant(candidateContexts) {
    var contexts = (candidateContexts && candidateContexts.length) ? candidateContexts : Config.contextNames();
    return _grant('FIXED_POLICY', READ_TOOLS, READ_OPERATIONS, contexts);
  }

  /**
   * Fase 2: whitelist de acción tras resolver contexto. Sólo puede estrechar el
   * techo predeclarado. `mandate` describe de dónde viene la autorización.
   */
  function postContextGrant(ceiling, resolvedContext, mandate) {
    assertMandateSource(mandate);
    var tools = READ_TOOLS.slice();
    var operations = READ_OPERATIONS.slice();

    // Las herramientas de escritura simulada sólo se habilitan si el mandato
    // vivo del operador (o una regla fija) pide una ejecución.
    if (mandate.requests_execution === true) {
      tools = tools.concat(SIMULATED_WRITE_TOOLS);
      operations = operations.concat(WRITE_OPERATIONS);
    }

    var grant = _grant(
      mandate.source,
      tools,
      operations,
      resolvedContext ? [resolvedContext] : []
    );

    // Un mandato puede añadir efectos prohibidos, nunca quitarlos.
    if (mandate.extra_forbidden_effects) {
      grant.forbidden_effects = grant.forbidden_effects.concat(mandate.extra_forbidden_effects);
    }

    assertNarrowing(ceiling, grant);
    return grant;
  }

  function _isSubset(inner, outer) {
    for (var i = 0; i < inner.length; i++) {
      if (outer.indexOf(inner[i]) === -1) { return false; }
    }
    return true;
  }

  /** `next` no puede superar `previous` en ninguna dimensión. */
  function isNarrowing(previous, next) {
    if (!_isSubset(next.allowed_tools, previous.allowed_tools)) { return false; }
    if (!_isSubset(next.allowed_operations, previous.allowed_operations)) { return false; }
    if (!_isSubset(next.allowed_contexts, previous.allowed_contexts)) { return false; }
    // Los efectos prohibidos sólo pueden crecer.
    if (!_isSubset(previous.forbidden_effects, next.forbidden_effects)) { return false; }
    if (next.writes_are_simulated_only !== true) { return false; }
    return true;
  }

  function assertNarrowing(previous, next) {
    if (!isNarrowing(previous, next)) {
      throw Errors.make(Errors.CODES.AUTHORITY_WIDENED,
        'La autoridad posterior supera el techo previo', {
          previous_tools: previous.allowed_tools.length,
          next_tools: next.allowed_tools.length
        });
    }
    return true;
  }

  /** Spec §6.2: el mandato NUNCA proviene de contenido recuperado. */
  function assertMandateSource(mandate) {
    if (!mandate || VALID_MANDATE_SOURCES.indexOf(mandate.source) === -1) {
      throw Errors.authorityDenied('Mandato inválido: sólo turno vivo del operador o política fija', {
        source: (mandate && mandate.source) ? mandate.source : null
      });
    }
    return true;
  }

  /**
   * Contenido recuperado tratado como mandato: siempre falso.
   * Existe como función para que el test lo pueda ejercer explícitamente.
   */
  function mandateFromRetrievedContent(retrievedText) {
    return {
      grants_authority: false,
      treated_as: 'EVIDENCE',
      reason: 'RETRIEVED_CONTENT_IS_NEVER_MANDATE',
      length: String(retrievedText === null || retrievedText === undefined ? '' : retrievedText).length
    };
  }

  function _touchesForbiddenSurface(destinationMeta) {
    if (!destinationMeta) { return null; }
    var surfaces = [].concat(destinationMeta.surface || [], destinationMeta.tags || []);
    for (var i = 0; i < surfaces.length; i++) {
      if (Config.FORBIDDEN_SURFACES.indexOf(surfaces[i]) !== -1) { return surfaces[i]; }
    }
    return null;
  }

  function _touchesProtectedDateField(payload) {
    if (!payload || typeof payload !== 'object') { return null; }
    var keys = Object.keys(payload);
    for (var i = 0; i < keys.length; i++) {
      if (Config.PROTECTED_DATE_FIELDS.indexOf(keys[i]) !== -1) { return keys[i]; }
    }
    return null;
  }

  /**
   * Evalúa una acción planificada contra el grant vigente.
   * @param {{step:object, payload:object, destination_meta:object}} planned
   * @return {{decision:'ALLOW'|'REQUIRES_ANDRES'|'DENY', reason:string}}
   */
  function evaluateAction(planned, grant, resolvedContext) {
    var step = planned.step;

    if (step.effect !== 'READ' && step.effect !== 'SIMULATED_WRITE') {
      return { decision: 'DENY', reason: 'REAL_EXTERNAL_WRITE' };
    }
    if (grant.allowed_tools.indexOf(step.tool) === -1) {
      return { decision: 'DENY', reason: 'TOOL_NOT_ALLOWED' };
    }
    if (grant.allowed_operations.indexOf(step.operation) === -1) {
      return { decision: 'DENY', reason: 'OPERATION_NOT_ALLOWED' };
    }

    var surface = _touchesForbiddenSurface(planned.destination_meta);
    if (surface) {
      return { decision: 'DENY', reason: 'FORBIDDEN_SURFACE:' + surface };
    }

    var dateField = _touchesProtectedDateField(planned.payload);
    if (dateField) {
      return { decision: 'DENY', reason: 'PROTECTED_DATE_FIELD:' + dateField };
    }

    var destContext = planned.destination_meta ? planned.destination_meta.context : null;
    if (destContext && resolvedContext && destContext !== resolvedContext) {
      return { decision: 'DENY', reason: 'CROSS_CONTEXT_DESTINATION' };
    }
    if (destContext && grant.allowed_contexts.indexOf(destContext) === -1) {
      return { decision: 'DENY', reason: 'CONTEXT_NOT_ALLOWED' };
    }

    // Spec §6.4: destino derivado exclusivamente de contenido recuperado degrada
    // cualquier escritura a REQUIRES_ANDRES (aquí: bloqueo simulado).
    if (step.effect === 'SIMULATED_WRITE' && step.destination_provenance === 'RETRIEVED_CONTENT') {
      return { decision: 'REQUIRES_ANDRES', reason: 'DESTINATION_FROM_RETRIEVED_CONTENT' };
    }

    return { decision: 'ALLOW', reason: 'WITHIN_GRANT' };
  }

  return {
    READ_TOOLS: READ_TOOLS,
    SIMULATED_WRITE_TOOLS: SIMULATED_WRITE_TOOLS,
    READ_OPERATIONS: READ_OPERATIONS,
    WRITE_OPERATIONS: WRITE_OPERATIONS,
    declaredCeiling: declaredCeiling,
    preRetrievalGrant: preRetrievalGrant,
    postContextGrant: postContextGrant,
    isNarrowing: isNarrowing,
    assertNarrowing: assertNarrowing,
    assertMandateSource: assertMandateSource,
    mandateFromRetrievedContent: mandateFromRetrievedContent,
    evaluateAction: evaluateAction
  };
})();
