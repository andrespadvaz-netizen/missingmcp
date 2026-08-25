/**
 * Router.gs — política de routing determinista (spec §8).
 *
 * Default: **resolver localmente**. Se transfiere SÓLO por una causa material:
 *   - capacidad técnica no disponible localmente;
 *   - auditoría cruzada obligatoria;
 *   - continuidad material verificable y no reconstruible por Retrieval
 *     (exige artefacto nombrable);
 *   - herramienta exclusiva del otro entorno;
 *   - instrucción expresa del operador.
 *
 * El modelo primario por contexto orienta continuidad pero NO obliga handoff.
 * Un auditor no inicia otro ciclo dentro de la misma corrida.
 */
var Router = (function () {

  var MATERIAL_CAUSES = {
    CAPABILITY_GAP: 'CAPABILITY_GAP',
    MANDATORY_CROSS_AUDIT: 'MANDATORY_CROSS_AUDIT',
    MATERIAL_CONTINUITY: 'MATERIAL_CONTINUITY',
    EXCLUSIVE_TOOL: 'EXCLUSIVE_TOOL',
    OPERATOR_INSTRUCTION: 'OPERATOR_INSTRUCTION'
  };

  function other(model) { return model === 'OPENAI' ? 'ANTHROPIC' : 'OPENAI'; }

  /**
   * Enumera las causas materiales presentes. Sin causa material no hay handoff.
   */
  function materialCauses(input) {
    var causes = [];
    if (input.operator_model_instruction) { causes.push(MATERIAL_CAUSES.OPERATOR_INSTRUCTION); }
    if (input.requires_cross_audit) { causes.push(MATERIAL_CAUSES.MANDATORY_CROSS_AUDIT); }
    if (input.capability_gap && input.capability_gap.available_in) { causes.push(MATERIAL_CAUSES.CAPABILITY_GAP); }
    if (input.exclusive_tool && input.exclusive_tool.environment) { causes.push(MATERIAL_CAUSES.EXCLUSIVE_TOOL); }
    // Continuidad material sólo cuenta si puede nombrarse un artefacto verificable.
    if (input.continuity && input.continuity.artifact_id) { causes.push(MATERIAL_CAUSES.MATERIAL_CONTINUITY); }
    return causes;
  }

  /** El auditor nunca abre un ciclo nuevo dentro de la misma corrida (spec §8). */
  function auditorMayStartNewCycle() { return false; }

  function _decision(route, targetModel, reason, cause, roles) {
    return {
      route: route,
      target_model: targetModel === undefined ? null : targetModel,
      reason: reason,
      material_cause: cause === undefined ? null : cause,
      model_roles: roles || []
    };
  }

  /**
   * @param {object} input
   * @param {string} input.current_model             'OPENAI' | 'ANTHROPIC'
   * @param {string|null} input.resolved_context
   * @param {string|null} input.context_route        ruta impuesta por ContextResolver
   * @param {boolean} input.authority_conflict
   * @param {boolean} input.currency_conflict   contradicción Estado vs relación
   * @param {boolean} input.currency_open       cadena de sustitución sin cerrar
   * @param {{complete:boolean}|null} input.coverage
   * @param {boolean} input.audit_completed
   * @param {boolean} input.audit_blocks_materially
   * @param {string|null} input.operator_model_instruction
   * @param {boolean} input.requires_cross_audit
   * @param {{needed:string, available_in:string}|null} input.capability_gap
   * @param {{tool:string, environment:string}|null} input.exclusive_tool
   * @param {{artifact_id:string|null}|null} input.continuity
   */
  function decide(input) {
    var current = input.current_model;

    // 1. El contexto manda: si no se resolvió, no se enruta trabajo.
    if (input.context_route === 'REQUIRES_ANDRES') {
      return _decision('REQUIRES_ANDRES', null, 'CONTEXT_AMBIGUOUS');
    }
    if (input.context_route === 'ABSTAIN') {
      return _decision('ABSTAIN', null, 'CONTEXT_UNRESOLVED');
    }

    // 2. Conflicto real de autoridad/norma: no lo resuelve un modelo.
    if (input.authority_conflict) {
      return _decision('REQUIRES_ANDRES', null, 'AUTHORITY_CONFLICT');
    }

    // 3. Contradicción dentro del registro de decisiones (p. ej. `Estado`
    //    vigente en una fila que sí tiene `Sustituida por`). Dos señales de la
    //    misma fuente competente que se contradicen: no lo resuelve un modelo.
    if (input.currency_conflict) {
      return _decision('REQUIRES_ANDRES', null, 'CURRENCY_CONFLICT');
    }

    // 4. Vigencia no cerrable: no se afirma estado.
    if (input.currency_open) {
      return _decision('ABSTAIN', null, 'CURRENCY_CHAIN_OPEN');
    }

    // 5. Cobertura insuficiente: abstenerse o acotar, nunca fingir exhaustividad.
    if (input.coverage && input.coverage.complete === false) {
      return _decision('ABSTAIN', null, 'COVERAGE_INCOMPLETE');
    }

    // 6. Cierre de ciclo: tras una auditoría el veredicto vuelve al originador.
    if (input.audit_completed) {
      if (input.audit_blocks_materially) {
        return _decision('REQUIRES_ANDRES', null, 'AUDIT_BLOCKS_MATERIALLY');
      }
      return _decision('LOCAL', null, 'CYCLE_CLOSED_RETURN_TO_ORIGIN', null,
        [{ model: current, role: 'LOCAL' }]);
    }

    // 7. Instrucción expresa del operador.
    if (input.operator_model_instruction) {
      var wanted = input.operator_model_instruction;
      if (wanted === current) {
        return _decision('LOCAL', null, 'OPERATOR_INSTRUCTION_IS_CURRENT_MODEL',
          MATERIAL_CAUSES.OPERATOR_INSTRUCTION, [{ model: current, role: 'LOCAL' }]);
      }
      return _decision(wanted, wanted, 'OPERATOR_INSTRUCTION', MATERIAL_CAUSES.OPERATOR_INSTRUCTION,
        [{ model: current, role: 'PRODUCER' }, { model: wanted, role: 'PRODUCER' }]);
    }

    // 8. Auditoría cruzada obligatoria: productor + auditor independiente.
    if (input.requires_cross_audit) {
      var auditor = other(current);
      return _decision('CROSS_AUDIT', auditor, 'MANDATORY_CROSS_AUDIT', MATERIAL_CAUSES.MANDATORY_CROSS_AUDIT,
        [{ model: current, role: 'PRODUCER' }, { model: auditor, role: 'AUDITOR' }]);
    }

    // 9. Gap de capacidad técnica.
    if (input.capability_gap && input.capability_gap.available_in && input.capability_gap.available_in !== current) {
      var capTarget = input.capability_gap.available_in;
      return _decision(capTarget, capTarget, 'CAPABILITY_GAP', MATERIAL_CAUSES.CAPABILITY_GAP,
        [{ model: current, role: 'PRODUCER' }, { model: capTarget, role: 'PRODUCER' }]);
    }

    // 10. Herramienta exclusiva del otro entorno.
    if (input.exclusive_tool && input.exclusive_tool.environment && input.exclusive_tool.environment !== current) {
      var toolTarget = input.exclusive_tool.environment;
      return _decision(toolTarget, toolTarget, 'EXCLUSIVE_TOOL', MATERIAL_CAUSES.EXCLUSIVE_TOOL,
        [{ model: current, role: 'PRODUCER' }, { model: toolTarget, role: 'PRODUCER' }]);
    }

    // 11. Continuidad material: sólo con artefacto verificable nombrable.
    if (input.continuity && input.continuity.artifact_id) {
      var primary = Config.primaryFor(input.resolved_context);
      if (primary && primary !== current) {
        return _decision(primary, primary, 'MATERIAL_CONTINUITY', MATERIAL_CAUSES.MATERIAL_CONTINUITY,
          [{ model: current, role: 'PRODUCER' }, { model: primary, role: 'PRODUCER' }]);
      }
    }

    // 12. Default: resolver localmente. El primario NO obliga transferencia.
    return _decision('LOCAL', null, 'LOCAL_BY_DEFAULT', null, [{ model: current, role: 'LOCAL' }]);
  }

  return {
    MATERIAL_CAUSES: MATERIAL_CAUSES,
    other: other,
    materialCauses: materialCauses,
    auditorMayStartNewCycle: auditorMayStartNewCycle,
    decide: decide
  };
})();
