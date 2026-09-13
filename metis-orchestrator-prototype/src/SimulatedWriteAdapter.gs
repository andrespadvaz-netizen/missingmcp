/**
 * SimulatedWriteAdapter.gs — adaptadores de escritura EXCLUSIVAMENTE simulados
 * (spec §10, §14, §17).
 *
 * Propiedades verificables:
 *   - este archivo NO referencia UrlFetchApp, DriveApp, CalendarApp, GmailApp
 *     ni ningún cliente HTTP: es imposible que emita una mutación externa;
 *   - produce la representación EXACTA de lo que habría ocurrido: método y
 *     endpoint reales que se habrían invocado, destino y resumen del efecto;
 *   - exige ledger disponible: sin ledger, la acción material se bloquea y la
 *     corrida falla cerrada (spec §5, §14).
 *
 * Máquina de estados por acción simulada:
 *   INTENT_RECORDED -> SENT -> SIMULATED
 * ("SENT" = entregada al adaptador de simulación; nunca sale de Apps Script.)
 */
var SimulatedWriteAdapter = (function () {

  /** Traducción tool+operation -> llamada real que se habría hecho. */
  var WOULD_CALL = {
    'simulate.notion_write': {
      create: 'POST https://api.notion.com/v1/pages',
      update: 'PATCH https://api.notion.com/v1/pages/{id}',
      append: 'PATCH https://api.notion.com/v1/blocks/{id}/children',
      archive: 'PATCH https://api.notion.com/v1/pages/{id} {archived:true}',
      update_view_filter: 'PATCH https://api.notion.com/v1/databases/{id} (filtro de vista)'
    },
    'simulate.asana_write': {
      create: 'POST https://app.asana.com/api/1.0/tasks',
      update: 'PUT https://app.asana.com/api/1.0/tasks/{gid}',
      append: 'POST https://app.asana.com/api/1.0/tasks/{gid}/stories',
      archive: 'PUT https://app.asana.com/api/1.0/tasks/{gid} {completed:true}'
    },
    'simulate.calendar_write': {
      create: 'CalendarApp.getCalendarById({id}).createEvent(...)',
      update: 'CalendarApp.getEventById({id}).setTime(...)',
      archive: 'CalendarApp.getEventById({id}).deleteEvent()'
    },
    'simulate.drive_write': {
      create: 'DriveApp.getFolderById({id}).createFile(...)',
      update: 'DriveApp.getFileById({id}).setContent(...)',
      archive: 'DriveApp.getFileById({id}).setTrashed(true)'
    },
    'simulate.gmail_send': {
      send: 'GmailApp.sendEmail(destinatario, asunto, cuerpo)'
    }
  };

  function isSimulationTool(tool) {
    return Object.prototype.hasOwnProperty.call(WOULD_CALL, tool);
  }

  function wouldCall(tool, operation) {
    var byTool = WOULD_CALL[tool];
    if (!byTool) { return null; }
    return byTool[operation] ? byTool[operation] : null;
  }

  /** Resumen del efecto: campos tocados, no valores de negocio en el ledger. */
  function effectSummary(planned) {
    var step = planned.step;
    var fields = planned.payload ? Object.keys(planned.payload).sort() : [];
    var dest = step.destination === null ? 'sin destino' : step.destination;
    return step.operation + ' sobre ' + dest +
           ' (campos: ' + (fields.length ? fields.join(', ') : 'ninguno') + ')' +
           ' [procedencia destino: ' + step.destination_provenance + ']';
  }

  /**
   * Simula una acción material. Nunca ejecuta efecto externo.
   * @param {{step:object, payload:object, destination_meta:object}} planned
   * @param {{policy_decision:object, model_role:string}} ctx
   * @return {{simulated:boolean, would_call:string, would_target:string|null,
   *           effect_summary:string, blocked_by_policy:boolean, block_reason:string|null}}
   */
  function simulate(planned, ctx) {
    var step = planned.step;
    var context = ctx || {};

    if (!isSimulationTool(step.tool)) {
      throw Errors.realWriteAttempt(step.tool);
    }
    if (step.effect !== 'SIMULATED_WRITE') {
      throw Errors.realWriteAttempt(step.tool + '/' + step.effect);
    }

    // Fail closed sin ledger (spec §5, §14).
    if (!Ledger.available()) {
      throw Errors.ledgerUnavailable('acción material simulada bloqueada');
    }

    var decision = context.policy_decision || { decision: 'ALLOW', reason: 'WITHIN_GRANT' };
    var blocked = decision.decision !== 'ALLOW';

    Ledger.recordIntent({
      execution_id: step.action_id.split(':')[0],
      ordinal: step.ordinal,
      payload_hash: step.payload_hash,
      tool: step.tool,
      operation: step.operation,
      destination_hash: Schemas.destinationHash(step.destination),
      model_role: context.model_role ? context.model_role : 'LOCAL'
    });
    Ledger.transition(step.action_id.split(':')[0], step.ordinal, 'SENT');
    Ledger.transition(step.action_id.split(':')[0], step.ordinal, 'SIMULATED');

    step.status = 'SIMULATED';

    return {
      simulated: true,
      would_call: wouldCall(step.tool, step.operation),
      would_target: step.destination === undefined ? null : step.destination,
      effect_summary: effectSummary(planned),
      blocked_by_policy: blocked,
      block_reason: blocked ? decision.reason : null
    };
  }

  /** Representación de un bloqueo sin ejecución posible (plan inválido). */
  function blockedResult(planned, reason) {
    return {
      simulated: true,
      would_call: wouldCall(planned.step.tool, planned.step.operation),
      would_target: planned.step.destination === undefined ? null : planned.step.destination,
      effect_summary: effectSummary(planned),
      blocked_by_policy: true,
      block_reason: reason
    };
  }

  return {
    WOULD_CALL: WOULD_CALL,
    isSimulationTool: isSimulationTool,
    wouldCall: wouldCall,
    effectSummary: effectSummary,
    simulate: simulate,
    blockedResult: blockedResult
  };
})();
