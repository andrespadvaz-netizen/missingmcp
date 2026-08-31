/**
 * Unit_SimulatedWrite.gs — spec §17 / SimulatedWriteAdapter.
 *
 * Nota de ubicación: la spec §3 fija la lista de archivos de test, de modo que
 * las pruebas de contrato de `ProviderAdapter` y de los adaptadores de lectura
 * (que también demuestran "ninguna superficie de escritura real") viven aquí,
 * al final del archivo, en lugar de en un archivo nuevo.
 */
/**
 * Registro DIFERIDO: las pruebas de escritura simulada.
 *
 * NO es un IIFE. Apps Script concatena los .gs en un orden que no
 * controlamos, así que llamar a `TestRunner` en tiempo de carga rompe el
 * proyecto entero cuando este archivo se evalúa antes que TestRunner.gs
 * (una declaración `function` sí se hoistea; `var TestRunner = (...)()` no).
 * `TestRunner` invoca esta función desde los runners, ya con todo cargado.
 */
function registerUnitSimulatedWrite() {

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
      t.ok(
        /^METIS_[A-Z_]+$/.test(valor),
        nombres[i] + ' referencia un nombre simbólico, no un valor'
      );
    }

    t.equals(
      Config.SECRET_PROPERTY_NAMES.OPENAI_API_KEY,
      'METIS_OPENAI_API_KEY',
      'OpenAI apunta a la Script Property canónica y no contiene el secreto'
    );

    t.throwsCode(
      Errors.CODES.CONFIG,
      function () { Config.secret('CLAVE_INEXISTENTE'); },
      'una clave simbólica desconocida se rechaza'
    );
  });


  // --------------------------------------------------- presupuesto y precios
  TestRunner.unit('Presupuesto', 'sin precio configurado el costo es desconocido, no cero', function (t) {
    Config._setPricing({});
    try {
      var openai = OpenAIAdapter.create();
      t.equals(Config.priceFor('OPENAI', 'gpt-5'), null, 'no hay tabla de precios en el código');
      t.equals(openai.normalizeResponse({
        id: 'r', status: 'completed', output: [], model: 'gpt-5',
        usage: { input_tokens: 1000, output_tokens: 500 }
      }).usage.estimated_cost_usd, null, 'el costo sale null, no un número inventado');

      t.equals(AnthropicAdapter.estimateCost({ input_tokens: 1000, output_tokens: 500 }, 'claude-opus-5'), null,
        'lo mismo del lado de Anthropic');
    } finally {
      Config._setPricing(null);
    }
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
    Config._setLimits({});
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
    Config._setLimits({});
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

  });

  TestRunner.unit('Presupuesto', 'un techo sin declarar no se compara contra el gasto', function (t) {
    Config._setLimits({});
    Ledger.addSpend(9999);
    t.ok(Ledger.assertAggregateBudget(), 'sin techo no hay comparación posible');
    Config._setLimits(Fixtures.TEST_BUDGETS);
    t.throwsCode(Errors.CODES.LIMIT_EXCEEDED, function () {
      Ledger.assertAggregateBudget();
    }, 'declarado el techo, el gasto acumulado lo supera y detiene');
  });

  // ------------------------------------------------------------ smoke test
  //
  // Se ejecuta en Nivel 1 con los backends de fixture instalados: la guarda de
  // nivel pasa, pero no hay red — el arnés la bloquea, así que cualquier salida
  // externa lanzaría.

  function smokeEnLevel1(opciones) {
    Config._setRunLevel(Config.LEVELS.LEVEL_1);
    var r = smokeTestLevel1(opciones);
    Config._setRunLevel(Config.LEVELS.LEVEL_0);
    return r;
  }

  function estadoDe(report, etiqueta) {
    var c = report.checks.filter(function (x) { return x.check === etiqueta; })[0];
    return c ? c.status : null;
  }

  TestRunner.unit('Smoke', 'una fuente sin resultados es NO_DEMOSTRADO, nunca PASS', function (t) {
    // SHOKKO tiene partición de Asana/Drive/Calendar, pero el corpus sólo tiene
    // una nota de Notion: las demás fuentes devuelven cero.
    var r = smokeEnLevel1({ contexts: ['SHOKKO'] });

    t.equals(estadoDe(r, 'SHOKKO/notion.search'), 'PASS', 'la fuente con resultados sí valida');
    t.equals(estadoDe(r, 'SHOKKO/asana.search'), 'NO_DEMOSTRADO', 'cero resultados no es PASS');
    t.equals(estadoDe(r, 'SHOKKO/drive.search'), 'NO_DEMOSTRADO', 'ni en Drive');
    t.equals(estadoDe(r, 'SHOKKO/calendar.read'), 'NO_DEMOSTRADO', 'ni en Calendar');
    t.equals(r.status, 'NO_DEMOSTRADO', 'el veredicto global no es PASS');
    t.equals(r.ok, false, '`ok` sólo es true con PASS');
    t.ok(r.undemonstrated >= 3, 'se cuentan las fuentes sin demostrar');
    t.equals(r.models_invoked, 0, 'cero modelos');
    t.equals(r.writes_attempted, 0, 'cero escrituras');
  });

  TestRunner.unit('Smoke', 'un canario presente valida la fuente', function (t) {
    var r = smokeEnLevel1({
      contexts: ['METIS'],
      canaries: {
        METIS: {
          'notion.search':    { query: 'gate', expect_id: 'met-gate-01' },
          'notion.decisions': { min_results: 2 },
          'asana.search':     { query: 'registrar', expect_title_contains: 'gobernanza' },
          'drive.search':     { query: 'notas' },
          'calendar.read':    { min_results: 1 }
        }
      }
    });

    ['notion.search', 'notion.decisions', 'asana.search', 'drive.search', 'calendar.read']
      .forEach(function (tool) {
        t.equals(estadoDe(r, 'METIS/' + tool), 'PASS', tool + ' validada con canario');
      });
    t.equals(r.status, 'PASS', 'veredicto global PASS');
    t.equals(r.ok, true, 'y `ok` en true');
    t.equals(r.failed, 0, 'sin fallos');
    t.equals(r.undemonstrated, 0, 'sin fuentes por demostrar');
  });

  TestRunner.unit('Smoke', 'una fuente que responde pero sin el canario es FAIL', function (t) {
    var r = smokeEnLevel1({
      contexts: ['METIS'],
      // La fuente devuelve documentos, pero no el que el operador esperaba:
      // está leyendo, y no lo que se creía. Eso es peor que no leer.
      canaries: { METIS: { 'notion.search': { query: 'gate', expect_id: 'no-existe-en-metis' } } }
    });
    t.equals(estadoDe(r, 'METIS/notion.search'), 'FAIL', 'canario ausente es FAIL, no NO_DEMOSTRADO');
    t.includes(r.checks.filter(function (c) { return c.check === 'METIS/notion.search'; })[0].detail,
      'no-existe-en-metis', 'el motivo nombra el canario que faltó');
    t.equals(r.status, 'FAIL', 'el veredicto global es FAIL');

    var porTitulo = smokeEnLevel1({
      contexts: ['METIS'],
      canaries: { METIS: { 'notion.search': { query: 'gate', expect_title_contains: 'inexistente' } } }
    });
    t.equals(estadoDe(porTitulo, 'METIS/notion.search'), 'FAIL', 'el criterio por título también decide');
  });

  TestRunner.unit('Smoke', 'sin fuentes validadas el veredicto no puede ser PASS', function (t) {
    // VENTURE_QUEST no tiene ninguna partición declarada en los fixtures:
    // todo queda OMITIDA. Nada falla, pero tampoco se demostró nada.
    var r = smokeEnLevel1({ contexts: ['VENTURE_QUEST'] });
    t.equals(r.failed, 0, 'no falla nada');
    t.equals(r.validated, 0, 'pero no se validó ninguna fuente');
    t.ok(r.omitted >= 4, 'todas quedaron omitidas por falta de partición');
    t.equals(r.status, 'NO_DEMOSTRADO', 'un smoke que no leyó nada no es verde');
  });

  TestRunner.unit('Smoke', 'una lectura fallida y el nivel equivocado son FAIL', function (t) {
    Fixtures.failSource('DRIVE');
    var r = smokeEnLevel1({ contexts: ['METIS'] });
    t.equals(estadoDe(r, 'METIS/drive.search'), 'FAIL', 'una fuente que falla es FAIL, no NO_DEMOSTRADO');
    t.equals(r.status, 'FAIL', 'y arrastra el veredicto');
    Fixtures.clearFailures();

    // En Nivel 0 no se lee nada: la guarda de nivel corta antes.
    var nivel0 = smokeTestLevel1({ contexts: ['METIS'] });
    t.equals(estadoDe(nivel0, 'nivel'), 'FAIL', 'Nivel 0 detiene el smoke test');
    t.equals(nivel0.status, 'FAIL', 'veredicto FAIL');
    t.equals(nivel0.validated, 0, 'sin ninguna lectura');
  });

  TestRunner.unit('Smoke', 'el evaluador de canarios distingue los tres desenlaces', function (t) {
    var docs = [{ id: 'a', title: 'Gate Ciclo 1 a Ciclo 2' }, { id: 'b', title: 'Otra cosa' }];

    t.equals(smokeEvaluateCanary(null, []).status, 'NO_DEMOSTRADO', 'sin canario y sin resultados');
    t.equals(smokeEvaluateCanary(null, docs).status, 'PASS', 'sin canario, un resultado basta');
    t.equals(smokeEvaluateCanary({ min_results: 3 }, docs).status, 'NO_DEMOSTRADO', 'min_results no alcanzado');
    t.equals(smokeEvaluateCanary({ expect_id: 'a' }, docs).status, 'PASS', 'canario por id presente');
    t.equals(smokeEvaluateCanary({ expect_id: 'z' }, docs).status, 'FAIL', 'canario por id ausente');
    t.equals(smokeEvaluateCanary({ expect_title_contains: 'ciclo 1' }, docs).status, 'PASS',
      'el título se compara normalizado');
    t.equals(smokeEvaluateCanary({ expect_title_contains: 'zzz' }, docs).status, 'FAIL', 'título ausente');
    t.equals(smokeEvaluateCanary({ min_results: 0 }, []).status, 'PASS',
      'min_results 0 permite declarar explícitamente que se acepta vacío');
  });

}
