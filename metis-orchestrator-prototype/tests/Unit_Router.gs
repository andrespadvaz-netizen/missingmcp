/** Unit_Router.gs — spec §17 / Router. */
(function () {

  function base(overrides) {
    var input = {
      current_model: 'OPENAI',
      context_route: null,
      resolved_context: 'METIS',
      authority_conflict: false,
      currency_open: false,
      coverage: { complete: true, missing: [] },
      audit_completed: false,
      operator_model_instruction: null,
      requires_cross_audit: false,
      capability_gap: null,
      exclusive_tool: null,
      continuity: null
    };
    var keys = Object.keys(overrides || {});
    for (var i = 0; i < keys.length; i++) { input[keys[i]] = overrides[keys[i]]; }
    return input;
  }

  TestRunner.unit('Router', 'LOCAL por default', function (t) {
    var d = Router.decide(base());
    t.equals(d.route, 'LOCAL', 'ruta local');
    t.equals(d.material_cause, null, 'sin causa material');
    t.equals(d.model_roles.length, 1, 'un solo modelo interviene');
    t.equals(d.model_roles[0].role, 'LOCAL', 'rol LOCAL');
  });

  TestRunner.unit('Router', 'el modelo primario no obliga transferencia', function (t) {
    // METIS tiene ANTHROPIC como primario; la corrida empieza en OPENAI.
    t.equals(Config.primaryFor('METIS'), 'ANTHROPIC', 'primario de METIS es ANTHROPIC');
    var d = Router.decide(base({ current_model: 'OPENAI' }));
    t.equals(d.route, 'LOCAL', 'sigue local pese al primario distinto');
    t.deepEquals(Router.materialCauses(base()), [], 'no hay ninguna causa material');
  });

  TestRunner.unit('Router', 'continuidad sin artefacto nombrable no transfiere', function (t) {
    var sinArtefacto = base({ continuity: { artifact_id: null } });
    t.deepEquals(Router.materialCauses(sinArtefacto), [], 'continuidad sin artefacto no es causa');
    t.equals(Router.decide(sinArtefacto).route, 'LOCAL', 'permanece local');

    var conArtefacto = base({ continuity: { artifact_id: 'PHI-047' } });
    t.includes(Router.materialCauses(conArtefacto), 'MATERIAL_CONTINUITY', 'con artefacto sí es causa');
    t.equals(Router.decide(conArtefacto).route, 'ANTHROPIC', 'transfiere al primario del contexto');
  });

  TestRunner.unit('Router', 'transfiere sólo por causa material', function (t) {
    var audit = base({ requires_cross_audit: true });
    t.equals(Router.decide(audit).route, 'CROSS_AUDIT', 'auditoría obligatoria transfiere');
    t.equals(Router.decide(audit).target_model, 'ANTHROPIC', 'auditor es el otro modelo');

    var gap = base({ capability_gap: { needed: 'x', available_in: 'ANTHROPIC' } });
    t.equals(Router.decide(gap).route, 'ANTHROPIC', 'gap de capacidad transfiere');
    t.equals(Router.decide(gap).material_cause, 'CAPABILITY_GAP', 'causa registrada');

    var tool = base({ exclusive_tool: { tool: 'x', environment: 'ANTHROPIC' } });
    t.equals(Router.decide(tool).material_cause, 'EXCLUSIVE_TOOL', 'herramienta exclusiva transfiere');

    var operator = base({ operator_model_instruction: 'ANTHROPIC' });
    t.equals(Router.decide(operator).material_cause, 'OPERATOR_INSTRUCTION', 'instrucción del operador transfiere');
  });

  TestRunner.unit('Router', 'auditor no inicia nuevo ciclo', function (t) {
    t.equals(Router.auditorMayStartNewCycle(), false, 'el auditor nunca abre otro ciclo');
    var d = Router.decide(base({ audit_completed: true, requires_cross_audit: true }));
    t.equals(d.route, 'LOCAL', 'tras la auditoría el veredicto vuelve al originador');
    t.equals(d.reason, 'CYCLE_CLOSED_RETURN_TO_ORIGIN', 'motivo de cierre de ciclo');

    var blocking = Router.decide(base({ audit_completed: true, audit_blocks_materially: true }));
    t.equals(blocking.route, 'REQUIRES_ANDRES', 'si el auditor bloquea materialmente, para en Andrés');
  });

  TestRunner.unit('Router', 'bloqueos duros antes que cualquier transferencia', function (t) {
    t.equals(Router.decide(base({ context_route: 'REQUIRES_ANDRES', requires_cross_audit: true })).route,
      'REQUIRES_ANDRES', 'contexto ambiguo gana sobre auditoría');
    t.equals(Router.decide(base({ authority_conflict: true, requires_cross_audit: true })).route,
      'REQUIRES_ANDRES', 'conflicto de autoridad gana sobre auditoría');
    t.equals(Router.decide(base({ currency_open: true })).route, 'ABSTAIN', 'vigencia abierta se abstiene');
    t.equals(Router.decide(base({ coverage: { complete: false, missing: ['DRIVE'] } })).route,
      'ABSTAIN', 'cobertura incompleta se abstiene');
  });

  TestRunner.unit('ToolBroker', 'las capacidades del contrato coinciden con Config', function (t) {
    t.equals(ToolBroker.READ_TOOLS['notion.fetch'].capability, Config.CAPABILITIES.NOTION_READ, 'notion_read');
    t.equals(ToolBroker.READ_TOOLS['asana.get'].capability, Config.CAPABILITIES.ASANA_READ, 'asana_read');
    t.equals(ToolBroker.READ_TOOLS['drive.fetch'].capability, Config.CAPABILITIES.DRIVE_READ, 'drive_read');
    t.equals(ToolBroker.READ_TOOLS['calendar.read'].capability, Config.CAPABILITIES.CALENDAR_READ, 'calendar_read');
  });

})();
