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
