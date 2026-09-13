/**
 * PlanValidator.gs — validación del PLAN COMPLETO antes de la primera acción
 * (spec §7).
 *
 * Invariantes mínimas:
 *  1. Ningún plan puede producir escritura real.
 *  2. Ningún objeto recibe contenido cuyo contexto de origen difiera del resuelto.
 *  3. Ninguna fecha de evaluación, caducidad o disparador de ritual se modifica.
 *  4. Ninguna acción toca CANON, Decisiones Tomadas, Chat Log, Handoffs PHI,
 *     Gmail, calendario primario ni instancia real de ritual.
 *  5. Ninguna secuencia de acciones permitidas produce un efecto prohibido.
 *  6. Un auditor no inicia una nueva auditoría dentro de la misma corrida.
 *  7. Handoff expirado o reconciliado se rechaza.
 *
 * Una violación bloquea el PLAN COMPLETO, no sólo el paso infractor.
 */
var PlanValidator = (function () {

  var CLOSURE_MARKERS = ['cerrado', 'closed', 'completado', 'done', 'archivado', 'archived'];
  var OVERDUE_MARKERS = ['vencida', 'vencidas', 'overdue', 'atrasada', 'atrasadas'];

  function _violation(code, detail) {
    return { invariant: code, detail: detail };
  }

  function _isSimulatedTool(tool) {
    return String(tool).indexOf('simulate.') === 0;
  }

  function _payloadMentions(payload, markers) {
    if (!payload || typeof payload !== 'object') { return false; }
    var text = ContextResolver.normalize(Schemas.canonicalize(payload));
    for (var i = 0; i < markers.length; i++) {
      if (text.indexOf(ContextResolver.normalize(markers[i])) !== -1) { return true; }
    }
    return false;
  }

  function _tags(planned) {
    var meta = planned.destination_meta || {};
    return [].concat(meta.surface || [], meta.tags || []);
  }

  // ---------------------------------------------- invariante 5: composición
  /**
   * Deriva efectos GLOBALES del plan. Cada acción puede ser individualmente
   * permitida y aun así componer un efecto prohibido.
   */
  function deriveCompositeEffects(plannedActions, resolvedContext) {
    var effects = [];
    var i;

    // (a) escritura real: ninguna herramienta de escritura fuera de `simulate.*`
    for (i = 0; i < plannedActions.length; i++) {
      var s = plannedActions[i].step;
      if (s.effect === 'SIMULATED_WRITE' && !_isSimulatedTool(s.tool)) {
        effects.push('REAL_EXTERNAL_WRITE');
        break;
      }
    }

    // (b) transferencia de sustancia entre contextos
    var contexts = {};
    for (i = 0; i < plannedActions.length; i++) {
      var p = plannedActions[i];
      if (p.source_context) { contexts[p.source_context] = true; }
      if (p.destination_meta && p.destination_meta.context) { contexts[p.destination_meta.context] = true; }
    }
    if (resolvedContext) { delete contexts[resolvedContext]; }
    if (Object.keys(contexts).length > 0) {
      effects.push('CROSS_CONTEXT_SUBSTANCE_TRANSFER');
    }

    // (c) ocultamiento de instancias vencidas de ritual por composición:
    //     cerrar/archivar un objeto que rastrea estado de ritual + ocultar
    //     lo vencido en su vista. Cada paso, por separado, es admisible.
    var closesRitualTracker = false;
    var hidesOverdue = false;
    for (i = 0; i < plannedActions.length; i++) {
      var pa = plannedActions[i];
      var tags = _tags(pa);
      var op = pa.step.operation;
      if (tags.indexOf('TRACKS_RITUAL_STATUS') !== -1 &&
          (op === 'update' || op === 'archive') &&
          (_payloadMentions(pa.payload, CLOSURE_MARKERS) || op === 'archive')) {
        closesRitualTracker = true;
      }
      if (op === 'update_view_filter' && _payloadMentions(pa.payload, OVERDUE_MARKERS)) {
        hidesOverdue = true;
      }
    }
    if (closesRitualTracker && hidesOverdue) {
      effects.push('HIDE_OVERDUE_RITUAL');
    }

    // (d) desplazamiento de fechas protegidas sin mandato vivo del operador
    for (i = 0; i < plannedActions.length; i++) {
      var keys = plannedActions[i].payload ? Object.keys(plannedActions[i].payload) : [];
      for (var k = 0; k < keys.length; k++) {
        if (Config.PROTECTED_DATE_FIELDS.indexOf(keys[k]) !== -1 &&
            plannedActions[i].step.destination_provenance !== 'LIVE_OPERATOR') {
          effects.push('SCHEDULE_SHIFT_WITHOUT_OPERATOR');
          k = keys.length; i = plannedActions.length;
        }
      }
    }

    // (e) mutación de fuente productiva
    for (i = 0; i < plannedActions.length; i++) {
      var meta = plannedActions[i].destination_meta || {};
      if (meta.productive === true && plannedActions[i].step.effect === 'SIMULATED_WRITE') {
        effects.push('MUTATE_PRODUCTIVE_SOURCE');
        break;
      }
    }

    return effects;
  }

  /**
   * Valida el plan completo.
   * @param {string} executionId
   * @param {Array<{step:object, payload:object, destination_meta:object, source_context:string|null}>} plannedActions
   * @param {object} grant AuthorityGrant vigente
   * @param {string|null} resolvedContext
   * @param {object} runState {audit_completed, requests_new_audit, handoff}
   * @return {{plan:object, violations:Array, per_action:Array}}
   */
  function validate(executionId, plannedActions, grant, resolvedContext, runState) {
    var state = runState || {};
    var violations = [];
    var perAction = [];
    var i;

    // --- invariante 1: ninguna escritura real
    for (i = 0; i < plannedActions.length; i++) {
      var step = plannedActions[i].step;
      if (step.effect !== 'READ' && step.effect !== 'SIMULATED_WRITE') {
        violations.push(_violation('I1_NO_REAL_WRITE', 'efecto no permitido: ' + step.effect));
      }
      if (step.effect === 'SIMULATED_WRITE' && !_isSimulatedTool(step.tool)) {
        violations.push(_violation('I1_NO_REAL_WRITE', 'herramienta de escritura no simulada: ' + step.tool));
      }
      if (step.effect === 'READ' && _isSimulatedTool(step.tool)) {
        violations.push(_violation('I1_NO_REAL_WRITE', 'herramienta de simulación usada como lectura: ' + step.tool));
      }
    }
    if (grant && grant.writes_are_simulated_only !== true) {
      violations.push(_violation('I1_NO_REAL_WRITE', 'grant sin writes_are_simulated_only'));
    }

    // --- invariante 2: sin contaminación de contexto en el destino
    for (i = 0; i < plannedActions.length; i++) {
      var pa = plannedActions[i];
      var destCtx = pa.destination_meta ? pa.destination_meta.context : null;
      if (pa.step.effect !== 'SIMULATED_WRITE') { continue; }
      if (resolvedContext && destCtx && destCtx !== resolvedContext) {
        violations.push(_violation('I2_NO_CROSS_CONTEXT', 'destino en contexto ' + destCtx));
      }
      if (resolvedContext && pa.source_context && pa.source_context !== resolvedContext) {
        violations.push(_violation('I2_NO_CROSS_CONTEXT', 'sustancia originada en ' + pa.source_context));
      }
    }

    // --- invariante 3: fechas protegidas intocables
    for (i = 0; i < plannedActions.length; i++) {
      var payload = plannedActions[i].payload || {};
      var pkeys = Object.keys(payload);
      for (var pk = 0; pk < pkeys.length; pk++) {
        if (Config.PROTECTED_DATE_FIELDS.indexOf(pkeys[pk]) !== -1) {
          violations.push(_violation('I3_NO_DATE_MUTATION', 'campo protegido: ' + pkeys[pk]));
        }
      }
    }

    // --- invariante 4: superficies prohibidas
    for (i = 0; i < plannedActions.length; i++) {
      var tags = _tags(plannedActions[i]);
      for (var t = 0; t < tags.length; t++) {
        if (Config.FORBIDDEN_SURFACES.indexOf(tags[t]) !== -1) {
          violations.push(_violation('I4_FORBIDDEN_SURFACE', tags[t]));
        }
      }
    }

    // --- autoridad por acción (alimenta el veredicto y el registro de bloqueos)
    var requiresAndres = false;
    for (i = 0; i < plannedActions.length; i++) {
      var decision = AuthorityPolicy.evaluateAction(plannedActions[i], grant, resolvedContext);
      perAction.push({ ordinal: plannedActions[i].step.ordinal, decision: decision });
      if (decision.decision === 'DENY') {
        violations.push(_violation('AUTHORITY_DENIED', 'ordinal ' + plannedActions[i].step.ordinal + ': ' + decision.reason));
      }
      if (decision.decision === 'REQUIRES_ANDRES') { requiresAndres = true; }
    }

    // --- invariante 5: composición prohibida
    var composite = deriveCompositeEffects(plannedActions, resolvedContext);
    var forbidden = (grant && grant.forbidden_effects) ? grant.forbidden_effects : Config.FORBIDDEN_EFFECTS;
    for (i = 0; i < composite.length; i++) {
      if (forbidden.indexOf(composite[i]) !== -1) {
        violations.push(_violation('I5_FORBIDDEN_COMPOSITION', composite[i]));
      }
    }

    // --- invariante 6: el auditor no abre otro ciclo
    var requestsNewAudit = state.requests_new_audit === true;
    for (i = 0; i < plannedActions.length; i++) {
      if (plannedActions[i].step.operation === 'audit') { requestsNewAudit = true; }
    }
    if (state.audit_completed === true && requestsNewAudit && !Router.auditorMayStartNewCycle()) {
      violations.push(_violation('I6_NO_SECOND_AUDIT', 'el auditor no inicia otra auditoría en la misma corrida'));
    }

    // --- invariante 7: handoff caducado o reconciliado
    if (state.handoff) {
      try {
        HandoffBuilder.assertConsumable(state.handoff);
      } catch (e) {
        violations.push(_violation('I7_HANDOFF_REJECTED', e.code ? e.code : 'HANDOFF_INVALID'));
      }
    }

    var valid = violations.length === 0 && !requiresAndres;
    var blockReason = null;
    if (violations.length) {
      blockReason = violations[0].invariant + ': ' + violations[0].detail;
    } else if (requiresAndres) {
      blockReason = 'REQUIRES_ANDRES: procedencia de destino no autoriza escritura';
    }

    var plan = {
      execution_id: executionId,
      actions: plannedActions.map(function (p) { return p.step; }),
      invariants_checked: true,
      valid: valid,
      block_reason: blockReason
    };
    Schemas.assertValid('ActionPlan', plan);

    return {
      plan: plan,
      violations: violations,
      per_action: perAction,
      composite_effects: composite,
      requires_andres: requiresAndres
    };
  }

  return {
    deriveCompositeEffects: deriveCompositeEffects,
    validate: validate
  };
})();
