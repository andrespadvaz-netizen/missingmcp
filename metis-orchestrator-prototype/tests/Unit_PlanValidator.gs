/** Unit_PlanValidator.gs — spec §17 / PlanValidator. */
/**
 * Registro DIFERIDO: las pruebas de PlanValidator.
 *
 * NO es un IIFE. Apps Script concatena los .gs en un orden que no
 * controlamos, así que llamar a `TestRunner` en tiempo de carga rompe el
 * proyecto entero cuando este archivo se evalúa antes que TestRunner.gs
 * (una declaración `function` sí se hoistea; `var TestRunner = (...)()` no).
 * `TestRunner` invoca esta función desde los runners, ya con todo cargado.
 */
function registerUnitPlanValidator() {

  var EXECUTION = { execution_id: 'exec-plan-test' };

  function grantFor(context) {
    return AuthorityPolicy.postContextGrant(
      AuthorityPolicy.declaredCeiling(), context || 'METIS',
      { source: 'LIVE_OPERATOR', requests_execution: true });
  }

  function actionsFrom(list) {
    return Orchestrator.buildPlannedActions(EXECUTION, list);
  }

  var SAFE_ACTION = {
    tool: 'simulate.asana_write',
    operation: 'update',
    destination: 'asana:and-task-77',
    destination_provenance: 'LIVE_OPERATOR',
    payload: { nota: 'decisión registrada' },
    destination_meta: { context: 'METIS', tags: ['TASK'], productive: false },
    source_context: 'METIS'
  };

  TestRunner.unit('PlanValidator', 'valida invariantes sobre plan completo', function (t) {
    var res = PlanValidator.validate(EXECUTION.execution_id, actionsFrom([SAFE_ACTION]),
      grantFor('METIS'), 'METIS', {});
    t.ok(res.plan.invariants_checked, 'declara invariantes verificadas');
    t.ok(res.plan.valid, 'plan seguro es válido');
    t.equals(res.plan.block_reason, null, 'sin motivo de bloqueo');
    t.equals(res.violations.length, 0, 'sin violaciones');
    t.equals(res.plan.actions.length, 1, 'el plan conserva sus pasos');

    var canon = PlanValidator.validate(EXECUTION.execution_id, actionsFrom([{
      tool: 'simulate.notion_write', operation: 'update', destination: 'notion:canon',
      destination_provenance: 'LIVE_OPERATOR', payload: { texto: 'x' },
      destination_meta: { context: 'METIS', tags: ['CANON'] }, source_context: 'METIS'
    }]), grantFor('METIS'), 'METIS', {});
    t.notOk(canon.plan.valid, 'tocar CANON invalida el plan');
    t.includes(canon.plan.block_reason, 'I4_FORBIDDEN_SURFACE', 'invariante 4 identificada');

    var fecha = PlanValidator.validate(EXECUTION.execution_id, actionsFrom([{
      tool: 'simulate.notion_write', operation: 'update', destination: 'notion:met-board-01',
      destination_provenance: 'LIVE_OPERATOR', payload: { evaluation_date: '2027-01-01' },
      destination_meta: { context: 'METIS', tags: [] }, source_context: 'METIS'
    }]), grantFor('METIS'), 'METIS', {});
    t.notOk(fecha.plan.valid, 'mover una fecha de evaluación invalida el plan');
    t.includes(fecha.plan.block_reason, 'I3_NO_DATE_MUTATION', 'invariante 3 identificada');

    var cruzado = PlanValidator.validate(EXECUTION.execution_id, actionsFrom([{
      tool: 'simulate.notion_write', operation: 'append', destination: 'notion:and-01',
      destination_provenance: 'LIVE_OPERATOR', payload: { texto: 'x' },
      destination_meta: { context: 'ANDREA', tags: [] }, source_context: 'METIS'
    }]), grantFor('METIS'), 'METIS', {});
    t.notOk(cruzado.plan.valid, 'depositar sustancia en otro contexto invalida el plan');
    t.includes(cruzado.plan.block_reason, 'I2_NO_CROSS_CONTEXT', 'invariante 2 identificada');
  });

  TestRunner.unit('PlanValidator', 'detecta composición prohibida', function (t) {
    var cerrarTracker = {
      tool: 'simulate.notion_write', operation: 'update', destination: 'notion:met-board-01',
      destination_provenance: 'LIVE_OPERATOR', payload: { estado: 'cerrado' },
      destination_meta: { context: 'METIS', tags: ['TRACKER', 'TRACKS_RITUAL_STATUS'] },
      source_context: 'METIS'
    };
    var ocultarVencidas = {
      tool: 'simulate.notion_write', operation: 'update_view_filter', destination: 'notion:met-board-view',
      destination_provenance: 'LIVE_OPERATOR', payload: { hide: ['vencidas'] },
      destination_meta: { context: 'METIS', tags: ['TRACKER_VIEW'] },
      source_context: 'METIS'
    };
    var grant = grantFor('METIS');

    // Cada acción, por separado, es admisible.
    var solo1 = PlanValidator.validate(EXECUTION.execution_id, actionsFrom([cerrarTracker]), grant, 'METIS', {});
    var solo2 = PlanValidator.validate(EXECUTION.execution_id, actionsFrom([ocultarVencidas]), grant, 'METIS', {});
    t.ok(solo1.plan.valid, 'cerrar el tablero, por sí solo, es válido');
    t.ok(solo2.plan.valid, 'ocultar lo vencido, por sí solo, es válido');

    // Juntas producen un efecto prohibido.
    var juntas = PlanValidator.validate(EXECUTION.execution_id,
      actionsFrom([cerrarTracker, ocultarVencidas]), grant, 'METIS', {});
    t.includes(juntas.composite_effects, 'HIDE_OVERDUE_RITUAL', 'se deriva el efecto compuesto');
    t.notOk(juntas.plan.valid, 'el plan COMPLETO queda bloqueado');
    t.includes(juntas.plan.block_reason, 'I5_FORBIDDEN_COMPOSITION', 'invariante 5 identificada');
  });

  TestRunner.unit('PlanValidator', 'impide cualquier escritura real', function (t) {
    var grant = grantFor('METIS');
    var real = actionsFrom([SAFE_ACTION]);
    real[0].step.tool = 'notion.write_real';
    var res = PlanValidator.validate(EXECUTION.execution_id, real, grant, 'METIS', {});
    t.notOk(res.plan.valid, 'una herramienta de escritura real invalida el plan');
    t.includes(res.plan.block_reason, 'I1_NO_REAL_WRITE', 'invariante 1 identificada');

    var badGrant = grantFor('METIS');
    badGrant.writes_are_simulated_only = false;
    var res2 = PlanValidator.validate(EXECUTION.execution_id, actionsFrom([SAFE_ACTION]), badGrant, 'METIS', {});
    t.notOk(res2.plan.valid, 'un grant sin writes_are_simulated_only invalida el plan');

    var todas = actionsFrom([SAFE_ACTION]);
    t.equals(todas[0].step.effect, 'SIMULATED_WRITE', 'toda acción material nace como SIMULATED_WRITE');
  });

  TestRunner.unit('PlanValidator', 'invariantes 6 y 7: segundo ciclo y handoff replay', function (t) {
    var grant = grantFor('METIS');
    var res6 = PlanValidator.validate(EXECUTION.execution_id, actionsFrom([SAFE_ACTION]), grant, 'METIS',
      { audit_completed: true, requests_new_audit: true });
    t.notOk(res6.plan.valid, 'una segunda auditoría en la misma corrida bloquea');
    t.includes(res6.plan.block_reason, 'I6_NO_SECOND_AUDIT', 'invariante 6 identificada');

    var handoff = HandoffBuilder.build({ execution_id: EXECUTION.execution_id }, {
      origin_model: 'OPENAI', target_model: 'ANTHROPIC',
      objective: 'auditar', context: 'METIS', evidence_refs: [],
      restrictions: [], authority: grant, expected_output: 'veredicto'
    });
    HandoffBuilder.reconcile(handoff);
    var res7 = PlanValidator.validate(EXECUTION.execution_id, actionsFrom([SAFE_ACTION]), grant, 'METIS',
      { handoff: handoff });
    t.notOk(res7.plan.valid, 'un handoff ya reconciliado bloquea el plan');
    t.includes(res7.plan.block_reason, 'I7_HANDOFF_REJECTED', 'invariante 7 identificada');
  });

}
