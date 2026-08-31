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
    Config._setLimits({});
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
    Config._setPricing({});
    try {
      t.equals(OpenAIAdapter.estimateCost(usoDe('gpt-5', 1000, 1000), 'gpt-5'), null,
        'sin la propiedad de precios el costo es desconocido, no cero');
      t.equals(AnthropicAdapter.estimateCost(usoDe('claude-opus-5', 1000, 1000), 'claude-opus-5'), null,
        'lo mismo para el otro proveedor');
    } finally {
      Config._setPricing(null);
    }
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

  TestRunner.unit(
    'Puerta de gasto',
    'una credencial ausente es pre-dispatch: no ciega el contador ni bloquea al proveedor siguiente',
    function (t) {
      var intentosRed = { n: 0 };

      // Simula exactamente el punto de fallo de los adapters reales:
      // Config.secret(...) ocurre ANTES de UrlFetchApp.fetch(...).
      var sinCredencial = {
        name: 'OPENAI',
        complete: function () {
          throw Errors.missingCredential('OPENAI_API_KEY');
        },
        completeWithTools: function () {
          return this.complete();
        },
        normalizeResponse: function (raw) {
          return raw;
        },
        redactProviderError: function (e) {
          return e;
        }
      };

      // Segundo proveedor sano: debe poder ejecutarse con el MISMO runtime
      // después del MISSING_CREDENTIAL del primero.
      var siguiente = {
        name: 'ANTHROPIC',
        complete: function () {
          intentosRed.n++;
          return {
            text: 'ok',
            tool_requests: [],
            usage: {
              input_tokens: 100,
              output_tokens: 50,
              estimated_cost_usd: 0.25
            },
            provider_model: 'claude-opus-5',
            stop_reason: 'end_turn',
            provider_request_id: 'req-1'
          };
        },
        completeWithTools: function () {
          return this.complete();
        },
        normalizeResponse: function (raw) {
          return raw;
        },
        redactProviderError: function (e) {
          return e;
        }
      };

      Config._setLimits({
        MAX_RUN_BUDGET_USD: 1,
        MAX_DAILY_BUDGET_USD: 5,
        MAX_MONTHLY_BUDGET_USD: 25
      });

      try {
        var runtime = { cost_usd: 0, cost_known: true };
        var gastoAntes = Ledger.spend('DAILY');

        t.throwsCode(Errors.CODES.MISSING_CREDENTIAL, function () {
          ProviderAdapter.callBudgeted(
            sinCredencial,
            { system: 's', prompt: 'p' },
            null,
            runtime
          );
        }, 'la credencial ausente conserva su código tipado');

        t.equals(
          runtime.cost_known,
          true,
          'MISSING_CREDENTIAL es inequívocamente pre-dispatch y no ciega el contador'
        );

        t.equals(
          runtime.cost_usd,
          0,
          'el fallo previo al despacho no añade costo a la corrida'
        );

        t.ok(
          Math.abs(Ledger.spend('DAILY') - gastoAntes) < 1e-9,
          'el fallo previo al despacho tampoco registra gasto agregado'
        );

        ProviderAdapter.callBudgeted(
          siguiente,
          { system: 's', prompt: 'p' },
          null,
          runtime
        );

        t.equals(
          intentosRed.n,
          1,
          'el proveedor siguiente sí puede ejecutarse: la ventana no quedó falsamente cerrada'
        );

        t.equals(
          runtime.cost_usd,
          0.25,
          'el costo del proveedor siguiente se contabiliza normalmente'
        );

        t.ok(
          Math.abs((Ledger.spend('DAILY') - gastoAntes) - 0.25) < 1e-9,
          'el ledger sólo contiene el gasto real del proveedor que sí respondió'
        );
      } finally {
        Config._setLimits(Fixtures.TEST_BUDGETS);
      }
    }
  );

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

  TestRunner.unit('Puerta de gasto', 'una llamada que rebasa el techo detiene la corrida antes de cualquier llamada posterior', function (t) {
    // El nombre importa: no es un preflight capaz de predecir el costo. El
    // costo sólo se conoce DESPUÉS de recibir la respuesta, así que la llamada
    // que rebasa el techo sí ocurre y sí se paga. Lo que el techo garantiza es
    // que no haya ninguna posterior. Es una parada dura tras la llamada, no
    // una predicción.
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
      }, 'la segunda se paga, el acumulado llega a 1.8 y ahí se detiene');
      t.equals(p.llamadas.n, 2,
        'la segunda llamada SÍ ocurrió: el techo no la predijo, la contabilizó');
      t.throwsCode(Errors.CODES.LIMIT_EXCEEDED, function () {
        ProviderAdapter.callBudgeted(p, { system: 's', prompt: 'p' }, null, runtime);
      }, 'y la tercera ya no llega al proveedor');
      t.equals(p.llamadas.n, 2, 'el contador de llamadas no sube: eso es la parada dura');
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
    t.includes(String(smokeProveedoresReales), 'runtime.cost_known === false',
      'y corta la corrida cuando la puerta deja el contador ciego');
  });

  TestRunner.unit('Puerta de gasto', 'un fallo tras el intento de red ciega el contador', function (t) {
    // El caso clásico de resultado ambiguo tras el despacho: la petición pudo
    // llegar al proveedor, la inferencia ejecutarse y cobrarse, y la respuesta
    // perderse por timeout. No recibir el uso NO significa que el gasto no
    // existiera. Antes esto se trataba como 'fallo propio del proveedor' y la
    // corrida seguía con el siguiente.
    var p = proveedorFalso('FALSO', 'modelo-x', 0.001);
    p.complete = function () { throw Errors.providerError('FALSO', 'timeout'); };
    Config._setLimits({
      MAX_RUN_BUDGET_USD: 1, MAX_DAILY_BUDGET_USD: 5, MAX_MONTHLY_BUDGET_USD: 25
    });
    try {
      var runtime = { cost_usd: 0, cost_known: true };
      var lanzo = false;
      try {
        ProviderAdapter.callBudgeted(p, { system: 's', prompt: 'p' }, null, runtime);
      } catch (e) { lanzo = true; }
      t.ok(lanzo, 'el error se propaga, no se traga');
      t.equals(runtime.cost_known, false,
        'y el contador queda ciego: el costo de esa llamada es indeterminable');
    } finally {
      Config._setLimits(Fixtures.TEST_BUDGETS);
    }
  });

  TestRunner.unit('Puerta de gasto', 'un fallo previo al despacho NO ciega el contador', function (t) {
    // La contrapartida, y es igual de importante: una credencial ausente o un
    // nivel insuficiente ocurren antes de tocar la red. Sabemos con certeza que
    // no hubo gasto, así que cegar el contador ahí sería detener la corrida sin
    // motivo y convertir un problema de configuración en uno de contabilidad.
    var p = proveedorFalso('FALSO', 'modelo-x', 0.001);
    p.complete = function () { throw Errors.configError('credencial ausente'); };
    Config._setLimits({
      MAX_RUN_BUDGET_USD: 1, MAX_DAILY_BUDGET_USD: 5, MAX_MONTHLY_BUDGET_USD: 25
    });
    try {
      var runtime = { cost_usd: 0, cost_known: true };
      try {
        ProviderAdapter.callBudgeted(p, { system: 's', prompt: 'p' }, null, runtime);
      } catch (e) { /* esperado */ }
      t.equals(runtime.cost_known, true,
        'el contador sigue siendo fiable y el otro proveedor puede probarse');
    } finally {
      Config._setLimits(Fixtures.TEST_BUDGETS);
    }
  });

  TestRunner.unit('Puerta de gasto', 'una respuesta sin uso deja el contador ciego y corta', function (t) {
    // No se puede distinguir "costó cero" de "costó algo que no supe medir".
    // Antes esto sólo lo detectaba el ensayo, así que el orquestador no
    // heredaba la protección: la corrección vive en la puerta común.
    var p = proveedorFalso('FALSO', 'modelo-x', 0.001);
    p.complete = function () {
      return {
        text: 'ok', tool_requests: [], usage: null,
        provider_model: 'modelo-x', stop_reason: 'end_turn', provider_request_id: 'r1'
      };
    };
    Config._setLimits({
      MAX_RUN_BUDGET_USD: 1, MAX_DAILY_BUDGET_USD: 5, MAX_MONTHLY_BUDGET_USD: 25
    });
    try {
      var runtime = { cost_usd: 0, cost_known: true };
      t.throwsCode(Errors.CODES.PRICE_UNKNOWN, function () {
        ProviderAdapter.callBudgeted(p, { system: 's', prompt: 'p' }, null, runtime);
      }, 'una respuesta sin bloque de uso lanza en vez de devolverse como si fuera gratis');
      t.equals(runtime.cost_known, false, 'y el contador queda marcado como ciego');
    } finally {
      Config._setLimits(Fixtures.TEST_BUDGETS);
    }
  });

  TestRunner.unit('Puerta de gasto', 'alcanzar exactamente el techo impide otra llamada', function (t) {
    // El preflight usaba `>`, así que una corrida parada justo en el tope
    // permitía una llamada más. Los techos diario y mensual ya usaban `>=`.
    var p = proveedorFalso('FALSO', 'modelo-x', 0.1);
    Config._setLimits({
      MAX_RUN_BUDGET_USD: 1, MAX_DAILY_BUDGET_USD: 50, MAX_MONTHLY_BUDGET_USD: 100
    });
    try {
      t.throwsCode(Errors.CODES.LIMIT_EXCEEDED, function () {
        ProviderAdapter.callBudgeted(p, { system: 's', prompt: 'p' }, null,
          { cost_usd: 1.0, cost_known: true });
      }, 'exactamente en el techo, la llamada no sale');
      t.equals(p.llamadas.n, 0, 'y el proveedor no llegó a invocarse');
    } finally {
      Config._setLimits(Fixtures.TEST_BUDGETS);
    }
  });

  TestRunner.unit('Puerta de gasto', 'un costo desconocido corta la ventana de gasto', function (t) {
    // El defecto que esto cierra: el ensayo registraba el precio desconocido y
    // seguía con el proveedor siguiente. Perder el conocimiento del costo es
    // perder el contador contra el que se vigilan los techos; seguir gastando
    // sabiendo eso contradice el fail closed que la puerta promete.
    var texto = String(smokeProveedoresReales);
    t.includes(texto, 'PRICE_UNKNOWN', 'el ensayo distingue el costo desconocido');
    t.includes(texto, 'ventana de gasto cerrada tras el costo desconocido',
      'y anota explícitamente que corta');
    t.ok(/PRICE_UNKNOWN[\s\S]{0,1400}?break;/.test(texto),
      'el camino del costo desconocido termina en un corte, no en un continue');
    t.ok(/LIMIT_EXCEEDED[\s\S]{0,400}?break;/.test(texto),
      'un techo alcanzado también corta: vale para la corrida, no para un proveedor');
  });

  TestRunner.unit('Puerta de gasto', 'el ensayo admite repetir sólo un proveedor', function (t) {
    // Tras declarar un identificador nuevo hay que repetir SÓLO el proveedor
    // afectado: repetir el otro sería pagar otra vez una llamada que funcionó.
    var texto = String(smokeProveedoresReales);
    t.includes(texto, 'soloProveedor', 'la función acepta el filtro');
    t.includes(texto, 'proveedores.filter', 'y lo aplica sobre la lista');
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
    Config._setLimits({});
    Config._setPricing({});
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

  TestRunner.unit(
    'Smoke proveedores reales',
    'texto vacío no demuestra respuesta funcional',
    function (t) {
      var estado = smokeClasificaRespuestaProveedor({
        text: '   ',
        usage: { input_tokens: 30, output_tokens: 16, estimated_cost_usd: 0.00055 },
        provider_model: 'claude-opus-5'
      }, true);

      t.equals(
        estado,
        SMOKE_STATUS.NO_DEMOSTRADO,
        'whitespace-only no cuenta como output funcional'
      );
    }
  );

  TestRunner.unit(
    'Smoke proveedores reales',
    'texto no vacío sí demuestra respuesta funcional',
    function (t) {
      var estado = smokeClasificaRespuestaProveedor({
        text: 'aislamiento',
        usage: { input_tokens: 30, output_tokens: 16, estimated_cost_usd: 0.00055 },
        provider_model: 'claude-opus-5'
      }, true);

      t.equals(
        estado,
        SMOKE_STATUS.PASS,
        'una respuesta textual utilizable sí cierra el subgate funcional'
      );
    }
  );

}

/**
 * Registro del informe troceado. Va aparte de las suites de contenido porque
 * comprueba el mecanismo que las imprime, no lo que prueban.
 */
function registerUnitInformeTroceado() {

  /**
   * Informe sintético con el tamaño que se le pida. NO ejecuta la suite.
   *
   * La primera versión de esta prueba llamaba a `TestRunner.runAll()` dentro de
   * una prueba que forma parte de esa misma suite: la suite se ejecutaba a sí
   * misma, y cada nivel volvía a lanzarlo todo. Medido: 12,8 segundos con esa
   * prueba frente a 71 milisegundos sin ella, un factor de 180. En el arnés
   * local pasaba por "tarda un poco"; en Apps Script, con seis minutos de tope,
   * llevaba la corrida al corte por tiempo.
   *
   * La lección va más allá del arreglo: una prueba que mide una propiedad del
   * sistema NO necesita ejecutar el sistema. Necesita una entrada con esa
   * propiedad. Construirla es más barato, más determinista, y no puede
   * realimentarse.
   */
  function informeDe(numeroDeCasos, largoDelNombre) {
    function suite(titulo, n) {
      var results = [];
      for (var i = 0; i < n; i++) {
        results.push({
          name: new Array(largoDelNombre + 1).join('x') + '-' + i,
          suite: titulo + ' — bloque', id: null, ok: true, error: null,
          checks: [{ ok: true, msg: 'a' }, { ok: true, msg: 'b' }, { ok: true, msg: 'c' }]
        });
      }
      return {
        title: titulo, total: n, passed: n, failed: 0,
        assertions: n * 3, results: results, level: Config.LEVELS.LEVEL_0
      };
    }
    var unit = suite('Tests unitarios (spec §17)', numeroDeCasos);
    var acc = suite('Casos de aceptación (spec §16)', 10);
    return {
      reports: [unit, acc],
      total: unit.total + acc.total,
      passed: unit.passed + acc.passed,
      failed: 0,
      assertions: unit.assertions + acc.assertions
    };
  }

  var LIMITE = 8192;

  TestRunner.unit('Informe', 'el veredicto va en un trozo propio y corto', function (t) {
    // Google Cloud Logging corta cada entrada en 8.192 caracteres. La suite ya
    // lo rebasaba y el informe se truncaba a mitad del último caso de
    // aceptación, justo antes de los totales: la corrida terminaba bien y el
    // veredicto no se podía leer.
    var chunks = TestRunner.renderChunks(informeDe(120, 40));
    t.ok(chunks.length >= 2, 'el informe se emite en varios trozos, no en uno solo');
    var ultimo = chunks[chunks.length - 1];
    t.includes(ultimo, 'VEREDICTO GLOBAL', 'el veredicto es el último trozo');
    t.ok(ultimo.length < 200,
      'y es corto de verdad, ' + ultimo.length + ' caracteres: no puede quedar cortado');
  });

  TestRunner.unit('Informe', 'trocear por suite mantiene cada entrada bajo el límite', function (t) {
    // 120 casos con nombres de 40 caracteres son bastante más de lo que hay
    // hoy: la prueba mira hacia adelante, no al tamaño actual.
    var chunks = TestRunner.renderChunks(informeDe(120, 40));
    for (var i = 0; i < chunks.length; i++) {
      t.ok(chunks[i].length < LIMITE,
        'trozo ' + (i + 1) + ' de ' + chunks.length + ': ' + chunks[i].length + ' caracteres');
    }
  });

  TestRunner.unit('Informe', 'una sola cadena SÍ rebasaría el límite con ese tamaño', function (t) {
    // Comprobación en negativo: sin esto, la prueba anterior pasaría también
    // con un informe pequeño y no demostraría que el troceado sirve de algo.
    var completo = TestRunner.render(informeDe(120, 40));
    t.ok(completo.length > LIMITE,
      'el informe sin trocear mide ' + completo.length + ' caracteres, por encima del límite');
  });

  TestRunner.unit('Informe', 'medir el informe no ejecuta la suite', function (t) {
    // Guarda contra la reintroducción del defecto: si alguien vuelve a llamar a
    // runAll desde aquí, la suite se ejecuta a sí misma y la corrida se
    // multiplica por dos órdenes de magnitud.
    // Se miran los COMENTARIOS APARTE del código. El comentario de arriba
    // menciona el ejecutor a propósito, porque explica el defecto que se
    // cerró; si la prueba mirara el texto entero, ese comentario la haría
    // fallar y la única salida sería borrar la explicación. Pasó en el primer
    // intento, junto con otro caso de auto-referencia: las cadenas buscadas se
    // arman por partes porque, escritas literales, la prueba se encontraría a
    // sí misma.
    var codigo = String(registerUnitInformeTroceado)
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/\/\/[^\n]*/g, '');
    var prohibidos = ['run' + 'All(', 'run' + 'UnitTests(', 'run' + 'Acceptance('];
    for (var i = 0; i < prohibidos.length; i++) {
      t.ok(codigo.indexOf(prohibidos[i]) === -1,
        'el código de esta suite no relanza las pruebas con ' + prohibidos[i]);
    }
  });

}
