/**
 * Unit_ProveedoresReales.gs — las guardas que rodean a la única operación de
 * este prototipo que cuesta dinero.
 *
 * Ninguna de estas pruebas llama a un proveedor real. Todas comprueban que la
 * llamada se DETIENE antes de salir a la red cuando falta algo, y que el costo
 * informado es aritmética verificable y no un número opaco.
 *
 * Registro DIFERIDO, como el resto de los archivos de test.
 */
function registerUnitProveedoresReales() {

  /** Respuesta cruda mínima con el identificador de modelo que se le indique. */
  function usoDe(modelo, entrada, salida) {
    return { model: modelo, input_tokens: entrada, output_tokens: salida };
  }

  var PRECIOS = {
    OPENAI: { 'gpt-5': { input_per_1k: 0.00125, output_per_1k: 0.01 } },
    ANTHROPIC: { 'claude-opus-5': { input_per_1k: 0.005, output_per_1k: 0.025 } }
  };

  TestRunner.unit('Proveedores', 'en LEVEL_0 y LEVEL_1 no se invoca ningún modelo', function (t) {
    // El agujero que esto cierra: los cuatro adaptadores de lectura ya
    // lanzaban violación de nivel en LEVEL_0, pero los de proveedor no
    // comprobaban el nivel. Con la credencial cargada, el runtime "inerte" no
    // leía nada y aun así podía gastar dinero.
    var niveles = [Config.LEVELS.LEVEL_0, Config.LEVELS.LEVEL_1];
    for (var i = 0; i < niveles.length; i++) {
      Config._setRunLevel(niveles[i]);
      try {
        t.throwsCode(Errors.CODES.LEVEL_VIOLATION, function () {
          OpenAIAdapter.create().complete({ system: 's', prompt: 'p' });
        }, 'OpenAI bloqueado en ' + niveles[i]);
        t.throwsCode(Errors.CODES.LEVEL_VIOLATION, function () {
          AnthropicAdapter.create().complete({ system: 's', prompt: 'p' });
        }, 'Anthropic bloqueado en ' + niveles[i]);
      } finally {
        Config._setRunLevel(Config.LEVELS.LEVEL_0);
      }
    }
  });

  TestRunner.unit('Proveedores', 'el nivel se comprueba ANTES que los techos', function (t) {
    // Orden importante: si los techos se comprobaran primero, un operador en
    // LEVEL_0 sin presupuesto declarado recibiría un error de presupuesto y
    // creería que el problema es configurar dinero, cuando el problema es que
    // no debería estar llamando a nada.
    Config._setRunLevel(Config.LEVELS.LEVEL_0);
    Config._setLimits(null);
    try {
      t.throwsCode(Errors.CODES.LEVEL_VIOLATION, function () {
        OpenAIAdapter.create().complete({ system: 's', prompt: 'p' });
      }, 'sin nivel y sin techos, el error que gana es el de nivel');
    } finally {
      Config._setLimits(Fixtures.TEST_BUDGETS);
      Config._setRunLevel(Config.LEVELS.LEVEL_0);
    }
  });

  TestRunner.unit('Proveedores', 'un identificador sin precio deja el costo desconocido', function (t) {
    // El caso que se espera observar en la primera corrida real: el precio se
    // busca por el modelo que DEVUELVE la respuesta, no por el que se pidió, y
    // algunos proveedores responden con una instantánea fechada.
    Config._setPricing(PRECIOS);
    try {
      var conocido = OpenAIAdapter.estimateCost(usoDe('gpt-5', 1000, 1000), 'gpt-5');
      t.ok(typeof conocido === 'number', 'con el identificador declarado sí hay costo');

      var fechado = OpenAIAdapter.estimateCost(
        usoDe('gpt-5-2026-01-01', 1000, 1000), 'gpt-5-2026-01-01');
      t.equals(fechado, null,
        'una instantánea fechada NO hereda el precio del identificador base');

      var parecido = AnthropicAdapter.estimateCost(
        usoDe('claude-opus-5-fast', 1000, 1000), 'claude-opus-5-fast');
      t.equals(parecido, null,
        'un identificador parecido tampoco: no hay coincidencia por prefijo');
    } finally {
      Config._setPricing(null);
    }
  });

  TestRunner.unit('Proveedores', 'el costo informado se puede recalcular a mano', function (t) {
    // Criterio binario de cierre: el número que informa el orquestador tiene
    // que ser aritmética reproducible, no un valor opaco.
    Config._setPricing(PRECIOS);
    try {
      var entrada = 1500;
      var salida = 400;

      var abierto = OpenAIAdapter.estimateCost(usoDe('gpt-5', entrada, salida), 'gpt-5');
      var esperadoAbierto = (entrada / 1000) * 0.00125 + (salida / 1000) * 0.01;
      t.ok(Math.abs(abierto - esperadoAbierto) < 1e-9,
        'OpenAI: ' + abierto + ' coincide con el cálculo manual ' + esperadoAbierto);

      var antropico = AnthropicAdapter.estimateCost(
        usoDe('claude-opus-5', entrada, salida), 'claude-opus-5');
      var esperadoAntropico = (entrada / 1000) * 0.005 + (salida / 1000) * 0.025;
      t.ok(Math.abs(antropico - esperadoAntropico) < 1e-9,
        'Anthropic: ' + antropico + ' coincide con el cálculo manual ' + esperadoAntropico);

      t.ok(antropico > abierto,
        'la misma carga cuesta más en el modelo más caro, que es la comprobación ' +
        'de que cada proveedor usa SU tarifa y no la del otro');
    } finally {
      Config._setPricing(null);
    }
  });

  TestRunner.unit('Proveedores', 'sin precios declarados nada tiene costo', function (t) {
    Config._setPricing(null);
    t.equals(OpenAIAdapter.estimateCost(usoDe('gpt-5', 1000, 1000), 'gpt-5'), null,
      'sin la propiedad de precios el costo es desconocido, no cero');
    t.equals(AnthropicAdapter.estimateCost(usoDe('claude-opus-5', 1000, 1000), 'claude-opus-5'), null,
      'lo mismo para el otro proveedor');
  });

  // ------------------------------------------- la única puerta de gasto

  /** Proveedor simulado que informa uso y el identificador que se le indique. */
  function proveedorFalso(nombre, modeloDevuelto, costo) {
    var llamadas = { n: 0 };
    return {
      llamadas: llamadas,
      name: nombre,
      complete: function () {
        llamadas.n++;
        return {
          text: 'ok',
          tool_requests: [],
          usage: { input_tokens: 100, output_tokens: 50, estimated_cost_usd: costo },
          provider_model: modeloDevuelto,
          stop_reason: 'end_turn',
          provider_request_id: 'req-' + llamadas.n
        };
      },
      completeWithTools: function (r) { return this.complete(r); },
      normalizeResponse: function (raw) { return raw; },
      redactProviderError: function (e) { return e; }
    };
  }

  TestRunner.unit('Puerta de gasto', 'un presupuesto agotado impide la red ANTES de llamar', function (t) {
    // El defecto que esto cierra: el ensayo llamaba al adaptador directamente,
    // y el adaptador sólo comprueba que los techos ESTÉN DECLARADOS, no que no
    // se hayan excedido. El control real vivía en el orquestador, así que había
    // dos caminos de gasto con política distinta.
    var p = proveedorFalso('FALSO', 'modelo-x', 0.001);
    Config._setLimits({
      MAX_RUN_BUDGET_USD: 1, MAX_DAILY_BUDGET_USD: 5, MAX_MONTHLY_BUDGET_USD: 25
    });
    try {
      Ledger.addSpend(5.0); // agota el techo diario
      t.throwsCode(Errors.CODES.LIMIT_EXCEEDED, function () {
        ProviderAdapter.callBudgeted(p, { system: 's', prompt: 'p' }, null,
          { cost_usd: 0, cost_known: true });
      }, 'el techo diario agotado detiene la llamada');
      t.equals(p.llamadas.n, 0,
        'y el proveedor NO llegó a invocarse: el preflight ocurre antes de la red');
    } finally {
      Config._setLimits(Fixtures.TEST_BUDGETS);
    }
  });

  TestRunner.unit('Puerta de gasto', 'un costo conocido se contabiliza en el ledger', function (t) {
    var p = proveedorFalso('FALSO', 'modelo-x', 0.25);
    Config._setLimits({
      MAX_RUN_BUDGET_USD: 1, MAX_DAILY_BUDGET_USD: 5, MAX_MONTHLY_BUDGET_USD: 25
    });
    try {
      var antes = Ledger.spend('DAILY');
      var runtime = { cost_usd: 0, cost_known: true };
      ProviderAdapter.callBudgeted(p, { system: 's', prompt: 'p' }, null, runtime);
      t.equals(runtime.cost_usd, 0.25, 'el acumulador de la corrida sube');
      t.ok(Math.abs((Ledger.spend('DAILY') - antes) - 0.25) < 1e-9,
        'y el gasto queda registrado en el contador agregado, no sólo en memoria');
    } finally {
      Config._setLimits(Fixtures.TEST_BUDGETS);
    }
  });

  TestRunner.unit('Puerta de gasto', 'el tope por corrida detiene la SIGUIENTE llamada', function (t) {
    // Limitación inherente y declarada: el costo sólo se conoce después de la
    // respuesta, así que el tope no puede impedir que UNA llamada lo rebase.
    // Lo que sí debe hacer es impedir que haya una segunda.
    var p = proveedorFalso('FALSO', 'modelo-x', 0.9);
    Config._setLimits({
      MAX_RUN_BUDGET_USD: 1, MAX_DAILY_BUDGET_USD: 50, MAX_MONTHLY_BUDGET_USD: 100
    });
    try {
      var runtime = { cost_usd: 0, cost_known: true };
      ProviderAdapter.callBudgeted(p, { system: 's', prompt: 'p' }, null, runtime);
      t.equals(p.llamadas.n, 1, 'la primera llamada pasa: 0.9 no rebasa el tope de 1');
      t.throwsCode(Errors.CODES.LIMIT_EXCEEDED, function () {
        ProviderAdapter.callBudgeted(p, { system: 's', prompt: 'p' }, null, runtime);
      }, 'la segunda rebasa 1.8 y se detiene');
    } finally {
      Config._setLimits(Fixtures.TEST_BUDGETS);
    }
  });

  TestRunner.unit('Puerta de gasto', 'el identificador devuelto viaja en el error de precio', function (t) {
    // Sin esto, el fallo por costo desconocido no puede decir qué declarar
    // para resolverlo, y el ensayo habría gastado la sonda para nada.
    var p = proveedorFalso('FALSO', 'gpt-5-2026-01-01', null);
    Config._setLimits({
      MAX_RUN_BUDGET_USD: 1, MAX_DAILY_BUDGET_USD: 5, MAX_MONTHLY_BUDGET_USD: 25
    });
    try {
      var capturado = null;
      try {
        ProviderAdapter.callBudgeted(p, { system: 's', prompt: 'p' }, null,
          { cost_usd: 0, cost_known: true });
      } catch (e) { capturado = e; }
      t.ok(capturado && capturado.code === Errors.CODES.PRICE_UNKNOWN,
        'un costo nulo lanza precio desconocido');
      t.includes(String(capturado.message), 'gpt-5-2026-01-01',
        'y el mensaje nombra el identificador EXACTO que hay que declarar');
    } finally {
      Config._setLimits(Fixtures.TEST_BUDGETS);
    }
  });

  TestRunner.unit('Puerta de gasto', 'el contrato normalizado incluye el modelo devuelto', function (t) {
    var campos = ProviderAdapter.normalizedFields();
    t.ok(campos.indexOf('provider_model') !== -1,
      'provider_model forma parte del contrato, no se pierde por el camino');
    t.throwsCode(Errors.CODES.SCHEMA, function () {
      ProviderAdapter.assertNormalizedShape({
        text: '', tool_requests: [], usage: null,
        stop_reason: null, provider_request_id: null
      });
    }, 'una respuesta sin él queda fuera de contrato');
  });

  TestRunner.unit('Puerta de gasto', 'orquestador y ensayo usan la MISMA puerta', function (t) {
    // Si alguien reintroduce un segundo camino de gasto, las políticas pueden
    // divergir y uno de los dos acabará sin enforcement. Fue exactamente lo
    // que pasó con la primera versión del ensayo.
    t.includes(String(smokeProveedoresReales), 'ProviderAdapter.callBudgeted',
      'el ensayo pasa por la puerta común');
    t.notOk(/adapter\.complete\(/.test(String(smokeProveedoresReales)),
      'y no llama al adaptador directamente');
  });

  TestRunner.unit('Proveedores', 'el ensayo eleva en memoria y restituye con finally', function (t) {
    // Guarda contra una regresión silenciosa del propio ensayo. Si alguien le
    // quita el `finally`, o le quita la exigencia de partir de LEVEL_0, el
    // ensayo puede dejar el runtime elevado y esta prueba lo impide.
    var texto = String(smokeProveedoresReales);
    t.includes(texto, 'LEVELS.LEVEL_0)', 'el ensayo exige partir de nivel cero persistido');
    t.includes(texto, '_setRunLevel(Config.LEVELS.LEVEL_2)', 'eleva sólo en memoria');
    t.includes(texto, '} finally {', 'la ventana de gasto está protegida por finally');
    t.includes(texto, '_setRunLevel(Config.LEVELS.LEVEL_0)', 'y restituye el nivel cero');
    t.includes(texto, 'assertBudgetsConfigured', 'comprueba los techos antes de gastar');
    t.notOk(/ToolBroker\.invoke/.test(texto), 'el ensayo no invoca ninguna herramienta');
    t.notOk(/NotionReadAdapter|AsanaReadAdapter|DriveReadAdapter/.test(texto),
      'el ensayo no lee ninguna fuente: aísla el proveedor');
  });

  TestRunner.unit('Proveedores', 'en nivel cero no se consulta ni presupuesto ni credencial', function (t) {
    // El negativo que convierte LEVEL_0 en inerte ECONÓMICAMENTE y no sólo en
    // una etiqueta: sin techos declarados y sin credencial disponible, el error
    // que sale debe seguir siendo el de nivel. Si saliera cualquier otro,
    // significaría que algo se consultó antes de comprobar si se podía llamar.
    Config._setRunLevel(Config.LEVELS.LEVEL_0);
    Config._setLimits(null);
    Config._setPricing(null);
    try {
      t.throwsCode(Errors.CODES.LEVEL_VIOLATION, function () {
        OpenAIAdapter.create().complete({ system: 's', prompt: 'p' });
      }, 'OpenAI: gana el error de nivel, no el de presupuesto ni el de credencial');
      t.throwsCode(Errors.CODES.LEVEL_VIOLATION, function () {
        AnthropicAdapter.create().complete({ system: 's', prompt: 'p' });
      }, 'Anthropic: igual');
    } finally {
      Config._setLimits(Fixtures.TEST_BUDGETS);
      Config._setRunLevel(Config.LEVELS.LEVEL_0);
    }
  });

}
