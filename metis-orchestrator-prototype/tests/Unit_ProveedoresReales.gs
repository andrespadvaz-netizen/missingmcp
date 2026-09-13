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

  [OpenAIAdapter, AnthropicAdapter].forEach(function (factory) {
    TestRunner.unit('Puerta de gasto', factory.create().name + ': CONFIG tras respuesta bloquea el siguiente intento', function (t) {
      var savedFetch = UrlFetchApp;
      var savedProperties = PropertiesService;
      var calls = 0;
      var provider = factory.create();
      var runtime = { cost_usd: 0, cost_known: true };
      Config._setRunLevel(Config.LEVELS.LEVEL_2);
      Config._setPricing(null);
      try {
        // Sustituye servicios completos: ninguna propiedad ni credencial real.
        PropertiesService = { getScriptProperties: function () {
          return { getProperty: function (key) {
            return key === 'METIS_PRICING' ? '{invalid' : 'fixture-only';
          } };
        } };
        UrlFetchApp = { fetch: function () {
          calls++;
          return {
            getResponseCode: function () { return 200; },
            getContentText: function () { return JSON.stringify({
              model: 'fixture-model', usage: { input_tokens: 10, output_tokens: 5 }
            }); }
          };
        } };
        t.throwsCode(Errors.CODES.CONFIG, function () {
          ProviderAdapter.callBudgeted(provider, { system: 's', prompt: 'p' }, null, runtime);
        }, 'la tarifa mal formada conserva el diagnóstico de configuración');
        t.equals(calls, 1, 'la respuesta simulada ya llegó');
        t.equals(runtime.cost_known, false, 'el costo posterior a la respuesta es desconocido');
        t.throwsCode(Errors.CODES.PRICE_UNKNOWN, function () {
          ProviderAdapter.callBudgeted(provider, { system: 's', prompt: 'p' }, null, runtime);
        }, 'el siguiente intento queda bloqueado');
        t.equals(calls, 1, 'no se despacha una segunda petición');
      } finally {
        UrlFetchApp = savedFetch;
        PropertiesService = savedProperties;
        Config._setRunLevel(Config.LEVELS.LEVEL_0);
        Config._setPricing(null);
      }
    });
  });

  /** Respuesta cruda mínima con el identificador de modelo que se le indique. */
  function usoDe(modelo, entrada, salida) {
    return { model: modelo, input_tokens: entrada, output_tokens: salida };
  }

  var PRECIOS = {
    OPENAI: { 'gpt-5': { input_per_1k: 0.00125, output_per_1k: 0.01 } },
    ANTHROPIC: { 'claude-opus-5': { input_per_1k: 0.005, output_per_1k: 0.025 } }
  };

  TestRunner.unit('Proveedores', 'en LEVEL_0 y LEVEL_1 no se invoca ningún modelo', function (t) {
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
    var p = proveedorFalso('FALSO', 'modelo-x', 0.001);
    Config._setLimits({
      MAX_RUN_BUDGET_USD: 1, MAX_DAILY_BUDGET_USD: 5, MAX_MONTHLY_BUDGET_USD: 25
    });
    try {
      Ledger.addSpend(5.0);
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
    t.includes(String(smokeProveedoresReales), 'ProviderAdapter.callBudgeted',
      'el ensayo pasa por la puerta común');
    t.notOk(/adapter\.complete\(/.test(String(smokeProveedoresReales)),
      'y no llama al adaptador directamente');
    t.includes(String(smokeProveedoresReales), 'runtime.cost_known === false',
      'y corta la corrida cuando la puerta deja el contador ciego');
  });

  TestRunner.unit('Puerta de gasto', 'un fallo tras el intento de red ciega el contador', function (t) {
    var p = proveedorFalso('FALSO', 'modelo-x', 0.001);
    p.complete = function () { throw Errors.providerError('FALSO', null, 'timeout'); };
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
    var texto = String(smokeProveedoresReales);
    t.includes(texto, 'soloProveedor', 'la función acepta el filtro');
    t.includes(texto, 'proveedores.filter', 'y lo aplica sobre la lista');
  });

  TestRunner.unit('Proveedores', 'el ensayo eleva en memoria y restituye con finally', function (t) {
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

  // ------------------------------- elevación de nivel (ensayo real)
  // Criterio DoD 2: "el log observa explícitamente la elevación temporal al
  // nivel de llamada a proveedor y el regreso al estado inerte, con regresión
  // que falle si la línea no aparece". Ejecutada de verdad contra
  // smokeProveedoresReales() con MAX_RUN_BUDGET_USD:0 para forzar que
  // ProviderAdapter.callBudgeted lance LIMIT_EXCEEDED en su preflight —
  // ANTES de provider.complete() — sin llamar red ni gastar. La prueba
  // "un presupuesto agotado impide la red ANTES de llamar" (arriba en este
  // mismo archivo) ya demuestra, con un proveedor falso contador, que ese
  // mismo preflight detiene el despacho antes del complete(); esta prueba
  // demuestra que el MISMO camino se activa desde el ensayo real.

  TestRunner.unit(
    'Elevación de nivel (ensayo real)',
    'con techo de corrida en cero, smokeProveedoresReales eleva a LEVEL_2, corta antes del despacho por preflight presupuestario, y restituye LEVEL_0 — todo ejecutado, sin llamar red',
    function (t) {
      Config._setLimits({
        MAX_RUN_BUDGET_USD: 0,
        MAX_DAILY_BUDGET_USD: 5,
        MAX_MONTHLY_BUDGET_USD: 25
      });
      try {
        t.equals(Config.runLevel(), Config.LEVELS.LEVEL_0,
          'parte de LEVEL_0, el estado persistido y exigido por la función');

        var reporte = smokeProveedoresReales('OPENAI');

        var elevado = reporte.checks.filter(function (c) { return c.check === 'nivel_elevado'; })[0];
        t.ok(!!elevado, 'el reporte contiene el check de elevación');
        t.includes(elevado.detail, 'LEVEL_2', 'la elevación registrada es a LEVEL_2');

        var restituido = reporte.checks.filter(function (c) { return c.check === 'nivel_al_terminar'; })[0];
        t.ok(!!restituido, 'el reporte contiene el check de restitución');
        t.includes(restituido.detail, 'LEVEL_0', 'la restitución registrada es a LEVEL_0');

        var despacho = reporte.checks.filter(function (c) { return c.check === 'OPENAI/complete'; })[0];
        t.ok(!!despacho, 'el intento de despacho quedó registrado');
        t.equals(despacho.status, SMOKE_STATUS.FAIL, 'el intento termina en FAIL, no en PASS silencioso');
        t.includes(despacho.detail, 'TECHO ALCANZADO',
          'el fallo es por techo de corrida alcanzado, no por credencial ni red');

        t.equals(Config.runLevel(), Config.LEVELS.LEVEL_0,
          'el nivel real, leído después de la llamada, quedó en LEVEL_0 — no sólo el texto del log lo dice');

        t.equals(reporte.status, SMOKE_STATUS.FAIL,
          'el veredicto agregado de la sonda es FAIL: un techo alcanzado no puede maquillarse como PASS');
      } finally {
        Config._setLimits(Fixtures.TEST_BUDGETS);
        Config._setRunLevel(Config.LEVELS.LEVEL_0);
      }
    }
  );

  // ------------------------------------ despacho único (vía Orchestrator)
  // Criterio 9 del Definition of Done: "cada intervención admite como máximo
  // un despacho de proveedor". Reencuadrado tras auditoría cruzada con
  // ChatGPT (2026-09-06): el objeto del criterio es la intervención de
  // modelo (_modelTurn en Orchestrator.gs), no el bucle de
  // smokeProveedoresReales(). Estas pruebas ejecutan el camino real —
  // Orchestrator.run() con proveedores inyectados por options.providers —
  // en vez de inspeccionar texto.

  /** Proveedor que cumple ProviderAdapter y falla EN el despacho, no antes. */
  function proveedorQueFallaTrasDespacho(nombre) {
    var llamadas = { n: 0 };
    return {
      llamadas: llamadas,
      name: nombre,
      complete: function () {
        llamadas.n++;
        throw Errors.providerError(nombre, null, 'timeout tras el despacho (fixture)');
      },
      completeWithTools: function (r) { return this.complete(r); },
      normalizeResponse: function (raw) { return raw; },
      redactProviderError: function (e) { return e; }
    };
  }

  TestRunner.unit(
    'Despacho único (orquestador)',
    'operator_context resuelve el contexto sin heurística de texto',
    function (t) {
      Fixtures.resetAll();
      var resuelto = ContextResolver.resolve(
        'Prueba controlada de una intervención de modelo.',
        { operator_context: 'METIS' }
      );
      t.equals(resuelto.resolved_context, 'METIS',
        'el atajo operator_context resuelve METIS sin depender de señales de texto');
    }
  );

  TestRunner.unit(
    'Despacho único (orquestador)',
    'un fallo en la primera intervención produce exactamente un despacho, termina FAILED y no invoca al proveedor secundario',
    function (t) {
      Fixtures.resetAll();
      try {
        var principal = proveedorQueFallaTrasDespacho('OPENAI');
        var secundario = Fixtures.scriptedProvider('ANTHROPIC', []);

        var resultado = Orchestrator.run(
          'Prueba controlada de una intervención de modelo.',
          {
            current_model: 'OPENAI',
            operator_context: 'METIS',
            intent: 'analysis',
            providers: { OPENAI: principal, ANTHROPIC: secundario }
          }
        );

        t.equals(resultado.resolved_context, 'METIS',
          'guarda de encuadre: el contexto resolvió correctamente antes de evaluar despacho');
        t.equals(principal.llamadas.n, 1,
          'el proveedor de la intervención se despachó exactamente una vez');
        t.equals(secundario.calls.length, 0,
          'el proveedor secundario NUNCA se invocó: sin respaldo ni segundo despacho implícito');
        t.equals(resultado.status, 'FAILED',
          'la corrida termina fail-closed tras el fallo posterior al despacho');
        t.equals(resultado.models.length, 0,
          'runtime.models sólo se llena DESPUÉS de una llamada exitosa; esta lanzó, así que queda vacío');

        // Regresión 1 del criterio "salida de degradación estructurada"
        // (diseño auditado, 2026-09-06): extiende esta prueba en vez de
        // duplicarla, según lo pedido por ChatGPT y Andrés.
        t.ok(!!resultado.degradation, 'la degradación queda estructurada, no solo en blocks');
        t.equals(resultado.degradation.code, Errors.CODES.PROVIDER,
          'la degradación registra el código real del error de proveedor');
        t.equals(resultado.degradation.provider, 'OPENAI',
          'identifica el proveedor activo en el momento del fallo');
        t.equals(resultado.degradation.stage, 'PROVIDER_DISPATCH',
          'la etapa registrada es el despacho, no una etapa anterior heredada');
        t.equals(resultado.degradation.cost_status, 'UNKNOWN',
          'el costo queda marcado desconocido: el fallo fue posterior al intento de red');
        t.equals(resultado.degradation.next_action.applicable, false,
          'no hay continuación automática definida');
        t.equals(resultado.degradation.next_action.action, null,
          'y no se inventa una acción');
      } finally {
        Config._setLimits(Fixtures.TEST_BUDGETS);
      }
    }
  );

  TestRunner.unit(
    'Despacho único (orquestador)',
    'una intervención exitosa sin herramientas solicitadas produce exactamente un despacho',
    function (t) {
      Fixtures.resetAll();
      try {
        var principal = Fixtures.scriptedProvider('OPENAI',
          [{ text: 'respuesta de prueba', tool_requests: [] }]);
        var secundario = Fixtures.scriptedProvider('ANTHROPIC', []);

        var resultado = Orchestrator.run(
          'Prueba controlada de una intervención de modelo.',
          {
            current_model: 'OPENAI',
            operator_context: 'METIS',
            intent: 'analysis',
            providers: { OPENAI: principal, ANTHROPIC: secundario }
          }
        );

        t.equals(resultado.resolved_context, 'METIS', 'guarda de encuadre: contexto resuelto');
        t.equals(principal.calls.length, 1,
          'un solo turno: sin tool_requests no hay segundo turno de producción');
        t.equals(resultado.producer_turns, 1, 'el ciclo del productor registra un solo turno');
        t.equals(resultado.models.length, 1, 'una intervención registrada, un despacho');
        t.equals(secundario.calls.length, 0,
          'el proveedor secundario no participó en este camino');

        // Regresión 4 del criterio "salida de degradación estructurada":
        // una corrida exitosa no debe dejar residuo de degradación.
        t.equals(resultado.degradation, null,
          'una corrida exitosa no deja residuo de degradación');
      } finally {
        Config._setLimits(Fixtures.TEST_BUDGETS);
      }
    }
  );


  // ------------------------- identidad de origen y routing material
  // Criterio reconciliado tras auditoría cruzada con ChatGPT y aprobación de
  // Andrés en nivel canónico (6-sep-2026): la identidad del modelo de origen
  // (current_model) nunca se infiere por default; debe declararse
  // explícitamente por el llamador, y su ausencia falla cerrado. Una vez
  // declarada, gobierna la primera intervención local. El contexto, por sí
  // solo, no obliga cambio de modelo ni handoff (ver Router.gs). La tabla
  // exhaustiva de primarios por contexto se prueba en Unit_Router.gs, no
  // aquí: independencia probatoria explícita respecto de este criterio.

  TestRunner.unit(
    'Identidad de origen y routing material',
    'sin current_model declarado, Orchestrator.run() falla cerrado y NUNCA asume OPENAI por default',
    function (t) {
      Fixtures.resetAll();
      var providers = Fixtures.providers([], []);
      t.throwsCode(Errors.CODES.CONFIG, function () {
        Orchestrator.run('Prueba controlada de una intervención de modelo.', {
          operator_context: 'METIS',
          intent: 'analysis',
          providers: providers
          // current_model deliberadamente ausente.
        });
      }, 'la ausencia de current_model lanza CONFIG en vez de defaultear a OPENAI');
      t.equals(providers.OPENAI.calls.length, 0,
        'y ningún proveedor llegó a invocarse: el fallo es previo a cualquier despacho');
      t.equals(providers.ANTHROPIC.calls.length, 0, 'tampoco el otro');
    }
  );

  TestRunner.unit(
    'Identidad de origen y routing material',
    'el primario contextual no sustituye al current_model declarado en la primera intervención local',
    function (t) {
      // Corrección de ChatGPT (auditoría cruzada, 6-sep-2026): declarar
      // current_model igual al primario del contexto (ambos ANTHROPIC en
      // METIS) no discrimina si la declaración explícita gobierna o si algún
      // comportamiento futuro fuerza el primario en silencio — las dos
      // explicaciones producen el mismo resultado observable. Esta versión
      // enfrenta deliberadamente current_model contra un primario distinto,
      // reproduciendo la colisión original de la ronda: METIS -> primario
      // ANTHROPIC, origen declarado OPENAI -> la primera intervención debe
      // ser OPENAI, sin handoff sólo por ser METIS.
      Fixtures.resetAll();
      try {
        var declarado = Fixtures.scriptedProvider('OPENAI',
          [{ text: 'respuesta de prueba', tool_requests: [] }]);
        var primarioContextual = Fixtures.scriptedProvider('ANTHROPIC', []);

        t.equals(Config.primaryFor('METIS'), 'ANTHROPIC',
          'guarda de encuadre: METIS tiene ANTHROPIC como primario');

        var resultado = Orchestrator.run(
          'Prueba controlada de una intervención de modelo.',
          {
            current_model: 'OPENAI',
            operator_context: 'METIS',
            intent: 'analysis',
            providers: { OPENAI: declarado, ANTHROPIC: primarioContextual }
          }
        );

        t.equals(resultado.resolved_context, 'METIS', 'el contexto resuelve a METIS');
        t.equals(declarado.calls.length, 1,
          'la primera intervención va al current_model declarado');
        t.equals(primarioContextual.calls.length, 0,
          'el primario contextual no fuerza transferencia por sí solo');
        t.equals(resultado.models[0].model, 'OPENAI',
          'la intervención registrada conserva la identidad de origen declarada');
      } finally {
        Config._setLimits(Fixtures.TEST_BUDGETS);
      }
    }
  );

  // ------------------- salida de degradación estructurada
  // Criterio DoD reconciliado (diseño auditado por ChatGPT, aprobado por
  // Andrés con correcciones de instrumentación, 2026-09-06): proveedor,
  // etapa, estado del costo, siguiente acción o declaración explícita de
  // que no aplica ninguna. Cuatro regresiones en total, cada una aislando
  // una ubicación real distinta del pipeline: la 1 y la 4 extienden pruebas
  // existentes de "Despacho único (orquestador)", arriba (fallo tras
  // despacho / corrida exitosa); la 2 y la 3 viven en esta suite (fallo de
  // resolución de proveedor / fallo duro dentro de ToolBroker).

  /**
   * Proveedor que EXISTE pero no cumple ProviderAdapter (le falta un método
   * requerido) — a propósito, para que la prueba de PROVIDER_RESOLUTION no
   * dependa de la mera ausencia del objeto, sino que pueda demostrar, con un
   * contador real, que `complete()` —el método que `ProviderAdapter.callBudgeted`
   * invocaría si el despacho ocurriera— nunca se llamó. Prueba directa, no
   * inferida, de que la resolución falló ANTES del despacho.
   */
  function proveedorNoConforme(nombre) {
    var llamadas = { n: 0 };
    return {
      llamadas: llamadas,
      name: nombre,
      complete: function () {
        llamadas.n++;
        return { text: 'nunca debería llegar aquí', tool_requests: [], usage: null,
                 provider_model: null, stop_reason: null, provider_request_id: null };
      }
      // Deliberadamente ausentes: completeWithTools, normalizeResponse,
      // redactProviderError. ProviderAdapter.conforms() debe rechazarlo.
    };
  }

  TestRunner.unit(
    'Salida de degradación estructurada',
    'un fallo en la resolución del proveedor (adaptador no conforme) se degrada con stage PROVIDER_RESOLUTION, costo conocido, y el despacho NUNCA se alcanza',
    function (t) {
      Fixtures.resetAll();
      try {
        var noConforme = proveedorNoConforme('OPENAI');
        t.equals(ProviderAdapter.conforms(noConforme), false,
          'guarda de encuadre: el doble efectivamente no cumple el contrato, no es un descuido');

        var resultado = Orchestrator.run(
          'Prueba controlada de una intervención de modelo.',
          {
            current_model: 'OPENAI',
            operator_context: 'METIS',
            intent: 'analysis',
            providers: { OPENAI: noConforme }
          }
        );

        t.equals(resultado.status, 'FAILED', 'la corrida termina fail-closed');
        t.ok(!!resultado.degradation, 'la degradación queda estructurada');
        t.equals(resultado.degradation.code, Errors.CODES.CONFIG,
          'código real del fallo: adaptador no conforme');
        t.equals(resultado.degradation.provider, 'OPENAI',
          'identifica el proveedor que se intentaba resolver');
        t.equals(resultado.degradation.stage, 'PROVIDER_RESOLUTION',
          'la etapa es resolución, distinta de despacho: discrimina PROVIDER_RESOLUTION de PROVIDER_DISPATCH');
        t.equals(noConforme.llamadas.n, 0,
          'PRUEBA DIRECTA: complete() nunca se invocó — ProviderAdapter.callBudgeted() nunca fue alcanzado, no es una inferencia por ausencia de objeto');
        t.equals(resultado.degradation.cost_status, 'KNOWN',
          'ningún despacho ocurrió: el costo sigue siendo conocido (cero)');
        t.equals(resultado.degradation.next_action.applicable, false,
          'sin continuación automática definida');
        t.equals(resultado.degradation.next_action.action, null, 'y no se inventa una acción');
      } finally {
        Config._setLimits(Fixtures.TEST_BUDGETS);
      }
    }
  );

  // Regresión 3, diseñada por ChatGPT tras leer ToolBroker.gs (Fase B,
  // 2026-09-06): LIMIT_EXCEEDED por MAX_TOOL_CALLS es el único camino
  // realista para forzar un fallo duro dentro de invoke() sin fabricar un
  // CONTEXT_AMBIGUOUS artificial (el contexto ya está resuelto para cuando
  // se llega a _executeToolRequests). La herramienta pedida (notion.search)
  // es una de lectura real y autorizada — no inventada — según
  // AuthorityPolicy.preRetrievalGrant().
  TestRunner.unit(
    'Salida de degradación estructurada',
    'un límite duro dentro de ToolBroker.invoke se degrada como TOOL_EXECUTION sin atribuir falsamente el fallo al proveedor',
    function (t) {
      Fixtures.resetAll();

      Config._setLimits({
        MAX_RUN_BUDGET_USD: 1,
        MAX_DAILY_BUDGET_USD: 5,
        MAX_MONTHLY_BUDGET_USD: 25,
        MAX_TOOL_CALLS: 0
      });

      try {
        var principal = Fixtures.scriptedProvider('OPENAI', [{
          text: '',
          tool_requests: [{
            name: 'notion.search',
            arguments: { query: 'fixture' }
          }]
        }]);

        var secundario = Fixtures.scriptedProvider('ANTHROPIC', []);

        var resultado = Orchestrator.run(
          'Prueba controlada de fallo duro durante ejecución de herramienta.',
          {
            current_model: 'OPENAI',
            operator_context: 'METIS',
            intent: 'analysis',
            providers: {
              OPENAI: principal,
              ANTHROPIC: secundario
            }
          }
        );

        t.equals(resultado.status, 'FAILED',
          'la guarda dura de herramienta detiene la corrida');

        t.ok(!!resultado.degradation,
          'el fallo queda representado en la degradación estructurada');

        t.equals(
          resultado.degradation.code,
          Errors.CODES.LIMIT_EXCEEDED,
          'se conserva el código tipado del límite'
        );

        t.equals(
          resultado.degradation.stage,
          'TOOL_EXECUTION',
          'la etapa corresponde al lugar real del fallo, distinta de PROVIDER_DISPATCH'
        );

        t.equals(
          resultado.degradation.provider,
          null,
          'el fallo pertenece a ToolBroker, no se atribuye falsamente al proveedor que ya despachó con éxito'
        );

        t.equals(
          resultado.degradation.cost_status,
          'KNOWN',
          'la intervención de proveedor terminó correctamente antes del fallo de herramienta'
        );

        t.equals(
          resultado.degradation.next_action.applicable,
          false,
          'no se activa recuperación automática'
        );

        t.equals(
          resultado.degradation.next_action.action,
          null,
          'no se inventa una acción siguiente'
        );

        t.equals(
          principal.calls.length,
          1,
          'el proveedor produjo exactamente la solicitud de herramienta, un solo turno'
        );

        t.equals(
          secundario.calls.length,
          0,
          'el fallo de herramienta no provoca fallback ni segundo proveedor'
        );

        t.equals(
          resultado.limits.tool_calls,
          0,
          'la parada ocurrió antes de incrementar el contador de herramientas'
        );
      } finally {
        Config._setLimits(Fixtures.TEST_BUDGETS);
      }
    }
  );

  // ------------------- procedencia: seleccionado vs. invocado
  // Criterio 9 del Definition of Done (texto exacto, 5-sep-2026): "Toda
  // intervención que invoque un proveedor conserva en el resultado la
  // identidad del proveedor efectivamente invocado, distinguida de proveedor
  // seleccionado y no invocado." Diseño auditado por ChatGPT tras revisar
  // OpenAIAdapter.gs y AnthropicAdapter.gs (2026-09-12): tres niveles
  // independientes — selected_provider (siempre), invoked_provider (sólo con
  // evidencia POSITIVA de despacho real, vía e.dispatchAttempted o respuesta
  // normalizada válida), provider_model (sólo si hubo respuesta). La verdad
  // nace en la frontera de red de cada adaptador (UrlFetchApp.fetch), nunca
  // se infiere desde códigos de error, y runtime.models NO se toca — vive en
  // runtime.provider_provenance, una estructura separada, para no alterar la
  // semántica ya usada por pruebas anteriores de esta ronda.
  //
  // Corrección de código que hizo posible esta prueba: ambos adaptadores
  // reales tenían un hueco real en el primer catch que rodea a
  // UrlFetchApp.fetch() — no marcaban e.dispatchAttempted cuando fetch()
  // mismo lanzaba (timeout, corte de red), a diferencia del segundo catch
  // (fallos posteriores a obtener response), que sí lo hacía. Corregido en
  // OpenAIAdapter.gs y AnthropicAdapter.gs.

  /**
   * Proveedor que cumple ProviderAdapter y falla EN el intento real de red,
   * con dispatchAttempted ya seteado — igual que hacen ahora los adaptadores
   * reales tras la corrección del hueco. Distinto de
   * proveedorQueFallaTrasDespacho (arriba, suite "Despacho único"): aquél no
   * seteaba dispatchAttempted porque en ese momento el criterio de
   * procedencia no existía todavía.
   */
  function proveedorConFalloDeRed(nombre) {
    var llamadas = { n: 0 };
    return {
      llamadas: llamadas,
      name: nombre,
      complete: function () {
        llamadas.n++;
        var err = Errors.providerError(nombre, null, 'timeout de red (fixture)');
        err.dispatchAttempted = true;
        throw err;
      },
      completeWithTools: function (r) { return this.complete(r); },
      normalizeResponse: function (raw) { return raw; },
      redactProviderError: function (e) { return e; }
    };
  }

  TestRunner.unit(
    'Procedencia (proveedor seleccionado vs. invocado)',
    'un fallo antes de tocar la red dentro del adaptador registra al proveedor como seleccionado pero NUNCA invocado',
    function (t) {
      Fixtures.resetAll();
      try {
        var noConforme = proveedorNoConforme('OPENAI');
        var resultado = Orchestrator.run(
          'Prueba controlada de una intervención de modelo.',
          {
            current_model: 'OPENAI',
            operator_context: 'METIS',
            intent: 'analysis',
            providers: { OPENAI: noConforme }
          }
        );

        t.equals(resultado.provider_provenance.length, 1, 'un solo intento de procedencia registrado');
        var intento = resultado.provider_provenance[0];
        t.equals(intento.selected_provider, 'OPENAI', 'el proveedor seleccionado queda registrado');
        t.equals(intento.invoked_provider, null,
          'nunca se marca como invocado: el fallo fue de resolución, antes de cualquier despacho real');
        t.equals(intento.provider_model, null, 'sin despacho, no hay modelo concreto que registrar');
        t.equals(intento.role, 'LOCAL', 'el rol de la intervención queda registrado');
      } finally {
        Config._setLimits(Fixtures.TEST_BUDGETS);
      }
    }
  );

  TestRunner.unit(
    'Procedencia (proveedor seleccionado vs. invocado)',
    'un fallo de red tras el intento real de despacho (dispatchAttempted) registra al proveedor como efectivamente invocado, sin modelo concreto',
    function (t) {
      Fixtures.resetAll();
      try {
        var conFallo = proveedorConFalloDeRed('OPENAI');
        var resultado = Orchestrator.run(
          'Prueba controlada de una intervención de modelo.',
          {
            current_model: 'OPENAI',
            operator_context: 'METIS',
            intent: 'analysis',
            providers: { OPENAI: conFallo }
          }
        );

        t.equals(resultado.status, 'FAILED', 'la corrida termina fail-closed');
        t.equals(resultado.provider_provenance.length, 1, 'un solo intento registrado');
        var intento = resultado.provider_provenance[0];
        t.equals(intento.selected_provider, 'OPENAI', 'proveedor seleccionado');
        t.equals(intento.invoked_provider, 'OPENAI',
          'el despacho SÍ se intentó de verdad: dispatchAttempted lo demuestra, no la ausencia del objeto');
        t.equals(intento.provider_model, null,
          'sin respuesta normalizada, no hay modelo concreto que registrar');
        t.equals(conFallo.llamadas.n, 1, 'guarda de encuadre: el doble sí fue invocado exactamente una vez');
      } finally {
        Config._setLimits(Fixtures.TEST_BUDGETS);
      }
    }
  );

  TestRunner.unit(
    'Procedencia (proveedor seleccionado vs. invocado)',
    'una respuesta válida registra el proveedor invocado y el identificador de modelo concreto devuelto',
    function (t) {
      Fixtures.resetAll();
      try {
        var principal = Fixtures.scriptedProvider('OPENAI',
          [{ text: 'respuesta de prueba', tool_requests: [], model: 'gpt-5-2026-09-01' }]);
        var secundario = Fixtures.scriptedProvider('ANTHROPIC', []);

        var resultado = Orchestrator.run(
          'Prueba controlada de una intervención de modelo.',
          {
            current_model: 'OPENAI',
            operator_context: 'METIS',
            intent: 'analysis',
            providers: { OPENAI: principal, ANTHROPIC: secundario }
          }
        );

        t.equals(resultado.provider_provenance.length, 1, 'un solo intento registrado');
        var intento = resultado.provider_provenance[0];
        t.equals(intento.selected_provider, 'OPENAI', 'proveedor seleccionado');
        t.equals(intento.invoked_provider, 'OPENAI', 'proveedor efectivamente invocado');
        t.equals(intento.provider_model, 'gpt-5-2026-09-01',
          'se conserva el identificador exacto devuelto por el proveedor, no el solicitado');
        t.equals(secundario.calls.length, 0, 'el proveedor secundario no participó en este camino');
      } finally {
        Config._setLimits(Fixtures.TEST_BUDGETS);
      }
    }
  );

  TestRunner.unit(
    'Procedencia (proveedor seleccionado vs. invocado)',
    'un fallo de precio posterior a una respuesta válida NO borra la procedencia ya conocida: proveedor invocado y modelo concreto se conservan',
    function (t) {
      Fixtures.resetAll();
      try {
        var principal = Fixtures.scriptedProvider('OPENAI', [{
          text: 'respuesta de prueba',
          tool_requests: [],
          model: 'gpt-5-2026-09-01',
          usage: { input_tokens: 10, output_tokens: 5, estimated_cost_usd: null }
        }]);
        var secundario = Fixtures.scriptedProvider('ANTHROPIC', []);

        var resultado = Orchestrator.run(
          'Prueba controlada de una intervención de modelo.',
          {
            current_model: 'OPENAI',
            operator_context: 'METIS',
            intent: 'analysis',
            providers: { OPENAI: principal, ANTHROPIC: secundario }
          }
        );

        t.equals(resultado.status, 'FAILED', 'la corrida falla cerrado por precio desconocido');
        t.ok(!!resultado.degradation, 'la degradación queda estructurada');
        t.equals(resultado.degradation.cost_status, 'UNKNOWN',
          'el costo queda desconocido tras el fallo de precio');

        t.equals(resultado.provider_provenance.length, 1, 'un solo intento registrado');
        var intento = resultado.provider_provenance[0];
        t.equals(intento.selected_provider, 'OPENAI', 'proveedor seleccionado');
        t.equals(intento.invoked_provider, 'OPENAI',
          'CRÍTICO: el proveedor SÍ fue invocado y respondió, aunque el accounting posterior haya fallado');
        t.equals(intento.provider_model, 'gpt-5-2026-09-01',
          'CRÍTICO: el modelo concreto devuelto se conserva aunque la corrida termine en FAILED — es precisamente la evidencia que justificaría declarar el precio para ese identificador');
      } finally {
        Config._setLimits(Fixtures.TEST_BUDGETS);
      }
    }
  );

  // Ajuste 1 de ChatGPT (auditoría cruzada, 2026-09-12): las cuatro pruebas de
  // arriba demuestran que ProviderAdapter respeta e.dispatchAttempted cuando
  // existe, pero NINGUNA prueba que los adaptadores REALES lo produzcan en el
  // hueco que se acaba de corregir (primer catch alrededor de
  // UrlFetchApp.fetch()). Sin esta prueba, si alguien revirtiera esa línea
  // por accidente, toda la suite de procedencia seguiría en verde. Mismo
  // patrón de mock de UrlFetchApp/PropertiesService que ya usa la primera
  // prueba de este archivo ("CONFIG tras respuesta bloquea el siguiente
  // intento"); no se inventa infraestructura nueva.
  [OpenAIAdapter, AnthropicAdapter].forEach(function (factory) {
    TestRunner.unit(
      'Procedencia (frontera real de red)',
      factory.create().name + ': si UrlFetchApp.fetch lanza, el error conserva dispatchAttempted=true',
      function (t) {
        var savedFetch = UrlFetchApp;
        var savedProperties = PropertiesService;
        var calls = 0;

        Config._setRunLevel(Config.LEVELS.LEVEL_2);
        Config._setLimits({
          MAX_RUN_BUDGET_USD: 1,
          MAX_DAILY_BUDGET_USD: 5,
          MAX_MONTHLY_BUDGET_USD: 25
        });

        try {
          PropertiesService = {
            getScriptProperties: function () {
              return {
                getProperty: function () { return 'fixture-only'; }
              };
            }
          };

          UrlFetchApp = {
            fetch: function () {
              calls++;
              throw new Error('timeout simulado');
            }
          };

          var capturado = null;
          try {
            factory.create().complete({ system: 's', prompt: 'p' });
          } catch (e) {
            capturado = e;
          }

          t.equals(calls, 1, 'se alcanzó exactamente una vez la frontera de red');
          t.ok(!!capturado, 'el error del fetch se propagó');
          t.equals(capturado.code, Errors.CODES.PROVIDER,
            'el fallo se normaliza como error de proveedor');
          t.equals(capturado.dispatchAttempted, true,
            'CRÍTICO: conserva evidencia positiva de que el despacho externo fue intentado');
        } finally {
          UrlFetchApp = savedFetch;
          PropertiesService = savedProperties;
          Config._setRunLevel(Config.LEVELS.LEVEL_0);
          Config._setLimits(Fixtures.TEST_BUDGETS);
        }
      }
    );
  });

  // Ajuste 2 de ChatGPT: el criterio dice "TODA intervención". Las cuatro
  // pruebas anteriores verifican únicamente corridas de una sola
  // intervención. Se demuestra la propiedad general —una intervención, un
  // registro; dos intervenciones, dos registros independientes y
  // ordenados— sin necesidad de CROSS_AUDIT ni de leer HandoffBuilder.gs:
  // basta que el primer turno del productor pida una herramienta real
  // (notion.search, ya autorizada en METIS), lo que fuerza un SEGUNDO
  // _modelTurn() del mismo proveedor dentro de _modelCycle().
  TestRunner.unit(
    'Procedencia (proveedor seleccionado vs. invocado)',
    'dos intervenciones del mismo proveedor producen dos registros de procedencia independientes, cada uno con su propio modelo concreto',
    function (t) {
      Fixtures.resetAll();
      try {
        var principal = Fixtures.scriptedProvider('OPENAI', [
          { text: '', tool_requests: [{ name: 'notion.search', arguments: { query: 'fixture' } }],
            model: 'gpt-5-turno-1' },
          { text: 'respuesta final', tool_requests: [], model: 'gpt-5-turno-2' }
        ]);
        var secundario = Fixtures.scriptedProvider('ANTHROPIC', []);

        var resultado = Orchestrator.run(
          'Prueba controlada de una intervención de modelo.',
          {
            current_model: 'OPENAI',
            operator_context: 'METIS',
            intent: 'analysis',
            providers: { OPENAI: principal, ANTHROPIC: secundario }
          }
        );

        t.equals(principal.calls.length, 2, 'guarda de encuadre: el productor sí tuvo dos turnos');
        t.equals(resultado.provider_provenance.length, 2,
          'dos intervenciones producen dos registros, no uno compartido ni sobrescrito');
        t.notOk(resultado.provider_provenance[0] === resultado.provider_provenance[1],
          'son dos objetos distintos, no la misma referencia repetida');

        var primero = resultado.provider_provenance[0];
        var segundo = resultado.provider_provenance[1];
        t.equals(primero.selected_provider, 'OPENAI', 'primer registro: proveedor seleccionado');
        t.equals(primero.invoked_provider, 'OPENAI', 'primer registro: proveedor invocado');
        t.equals(primero.provider_model, 'gpt-5-turno-1', 'primer registro: modelo concreto del primer turno');
        t.equals(segundo.selected_provider, 'OPENAI', 'segundo registro: proveedor seleccionado');
        t.equals(segundo.invoked_provider, 'OPENAI', 'segundo registro: proveedor invocado');
        t.equals(segundo.provider_model, 'gpt-5-turno-2', 'segundo registro: modelo concreto del segundo turno');
        t.equals(secundario.calls.length, 0, 'el proveedor secundario no participó en este camino');
      } finally {
        Config._setLimits(Fixtures.TEST_BUDGETS);
      }
    }
  );

  // Corrección de ChatGPT (auditoría cruzada, 2026-09-12): un proveedor
  // CONFORME (con los cuatro métodos requeridos) que retorna una forma que
  // NO cumple el contrato normalizado. provider.complete() SÍ retornó —el
  // despacho ocurrió— pero assertNormalizedShape() debe rechazar la forma.
  // Antes de la corrección de ProviderAdapter.gs, este caso habría dejado
  // invoked_provider en null pese a que el proveedor sí respondió; ahora
  // debe quedar en OPENAI, con provider_model en null porque la forma nunca
  // llegó a validarse como confiable.
  function proveedorFormaInvalida(nombre) {
    return {
      name: nombre,
      complete: function () {
        return {
          text: 'respuesta',
          tool_requests: 'esto debería ser un array', // forma inválida a propósito
          usage: { input_tokens: 10, output_tokens: 5, estimated_cost_usd: 0.001 },
          provider_model: 'modelo-que-nunca-se-confirma',
          stop_reason: 'end_turn',
          provider_request_id: 'req-1'
        };
      },
      completeWithTools: function (r) { return this.complete(r); },
      normalizeResponse: function (raw) { return raw; },
      redactProviderError: function (e) { return e; }
    };
  }

  TestRunner.unit(
    'Procedencia (proveedor seleccionado vs. invocado)',
    'una forma inválida devuelta por un proveedor conforme registra invoked_provider (el despacho SÍ ocurrió) pero NUNCA confía en provider_model',
    function (t) {
      Fixtures.resetAll();
      try {
        var formaInvalida = proveedorFormaInvalida('OPENAI');
        var resultado = Orchestrator.run(
          'Prueba controlada de una intervención de modelo.',
          {
            current_model: 'OPENAI',
            operator_context: 'METIS',
            intent: 'analysis',
            providers: { OPENAI: formaInvalida }
          }
        );

        t.equals(resultado.status, 'FAILED', 'la corrida falla cerrado por forma inválida');
        t.ok(!!resultado.degradation, 'la degradación queda estructurada');
        t.equals(resultado.degradation.code, Errors.CODES.SCHEMA,
          'el código real es de esquema, no de proveedor ni de configuración');

        t.equals(resultado.provider_provenance.length, 1, 'un solo intento registrado');
        var intento = resultado.provider_provenance[0];
        t.equals(intento.selected_provider, 'OPENAI', 'proveedor seleccionado');
        t.equals(intento.invoked_provider, 'OPENAI',
          'CRÍTICO: el despacho SÍ ocurrió (complete() retornó) aunque la forma fuera inválida');
        t.equals(intento.provider_model, null,
          'CRÍTICO: nunca se confía en provider_model de una forma que no pasó la validación de contrato');
      } finally {
        Config._setLimits(Fixtures.TEST_BUDGETS);
      }
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
    var chunks = TestRunner.renderChunks(informeDe(120, 40));
    t.ok(chunks.length >= 2, 'el informe se emite en varios trozos, no en uno solo');
    var ultimo = chunks[chunks.length - 1];
    t.includes(ultimo, 'VEREDICTO GLOBAL', 'el veredicto es el último trozo');
    t.ok(ultimo.length < 200,
      'y es corto de verdad, ' + ultimo.length + ' caracteres: no puede quedar cortado');
  });

  TestRunner.unit('Informe', 'trocear por suite mantiene cada entrada bajo el límite', function (t) {
    var chunks = TestRunner.renderChunks(informeDe(120, 40));
    for (var i = 0; i < chunks.length; i++) {
      t.ok(chunks[i].length < LIMITE,
        'trozo ' + (i + 1) + ' de ' + chunks.length + ': ' + chunks[i].length + ' caracteres');
    }
  });

  TestRunner.unit('Informe', 'una sola cadena SÍ rebasaría el límite con ese tamaño', function (t) {
    var completo = TestRunner.render(informeDe(120, 40));
    t.ok(completo.length > LIMITE,
      'el informe sin trocear mide ' + completo.length + ' caracteres, por encima del límite');
  });

  TestRunner.unit(
    'Informe',
    'múltiples suites distintas dentro de un mismo reporte físico producen chunks por suite, cada uno bajo el límite',
    function (t) {
      // Reproduce la estructura REAL de runAll(): muchas suites distintas
      // (r.suite) conviven dentro de UN solo reporte físico (unitReport).
      // La prueba "trocear por suite..." de arriba usa un informe sintético
      // de una sola suite repetida y no ejercía este caso: pasaba aunque el
      // troceado real agrupara todas las suites en un solo chunk, que es
      // exactamente el defecto que produjo el truncamiento de Apps Script
      // el 2026-09-06 (Logging output too large. Truncating output.) y se
      // llevó de encima la evidencia de la suite "Despacho único
      // (orquestador)".
      function suiteDe(nombre, n, largoDelNombre) {
        var results = [];
        for (var i = 0; i < n; i++) {
          results.push({
            suite: nombre,
            name: new Array(largoDelNombre + 1).join('x') + '-' + i,
            id: null, ok: true, error: null,
            checks: [{ ok: true, msg: 'a' }, { ok: true, msg: 'b' }, { ok: true, msg: 'c' }]
          });
        }
        return results;
      }
      // Cuatro suites de 40 casos cada una: 160 resultados en UN solo
      // reporte físico. Combinados en un solo chunk, esto rebasa con
      // holgura el límite de 8.192 caracteres; cada suite por separado, no.
      var resultadosCombinados = []
        .concat(suiteDe('Suite A', 40, 40))
        .concat(suiteDe('Suite B', 40, 40))
        .concat(suiteDe('Suite C', 40, 40))
        .concat(suiteDe('Suite D', 40, 40));
      var reporteUnico = {
        title: 'Tests unitarios (spec §17)',
        total: resultadosCombinados.length, passed: resultadosCombinados.length, failed: 0,
        assertions: resultadosCombinados.length * 3,
        results: resultadosCombinados, level: Config.LEVELS.LEVEL_0
      };
      var reporteCompleto = {
        title: 'Suite completa',
        total: reporteUnico.total, passed: reporteUnico.passed, failed: 0,
        assertions: reporteUnico.assertions,
        reports: [reporteUnico]
      };

      var chunks = TestRunner.renderChunks(reporteCompleto);
      for (var i = 0; i < chunks.length; i++) {
        t.ok(chunks[i].length < LIMITE,
          'chunk ' + (i + 1) + ' de ' + chunks.length + ': ' + chunks[i].length + ' caracteres');
      }
      ['Suite A', 'Suite B', 'Suite C', 'Suite D'].forEach(function (nombreSuite) {
        var encabezado = '=== Tests unitarios (spec §17) — ' + nombreSuite + ' ===';
        t.includes(chunks.join('\n---\n'), encabezado,
          'existe un encabezado de chunk propio para ' + nombreSuite);
      });
    }
  );

  TestRunner.unit('Informe', 'medir el informe no ejecuta la suite', function (t) {
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
