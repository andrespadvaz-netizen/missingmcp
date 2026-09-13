/**
 * ProviderAdapter.gs — interfaz común de proveedor (spec §11).
 *
 * `OpenAIAdapter` y `AnthropicAdapter` deben producir EXACTAMENTE el mismo
 * objeto normalizado:
 *
 *   { text, tool_requests, usage, stop_reason, provider_request_id }
 *
 * Ningún adaptador almacena secretos en su instancia: la credencial se lee de
 * Script Properties en el momento de la llamada y nunca sale del adaptador.
 *
 * Nota de runtime: las implementaciones concretas NO usan `extends` para no
 * depender del orden de carga de archivos en Apps Script. La conformidad se
 * verifica con `ProviderAdapter.conforms(instancia)`.
 */
var ProviderAdapter = (function () {

  var REQUIRED_METHODS = ['complete', 'completeWithTools', 'normalizeResponse', 'redactProviderError'];

  var Base = class ProviderAdapterBase {
    complete(request) { throw Errors.configError('complete() no implementado'); }
    completeWithTools(request, toolContract) { throw Errors.configError('completeWithTools() no implementado'); }
    normalizeResponse(raw) { throw Errors.configError('normalizeResponse() no implementado'); }
    redactProviderError(error) { throw Errors.configError('redactProviderError() no implementado'); }
  };

  /** Forma canónica de la respuesta normalizada. */
  function emptyNormalized() {
    return {
      text: '',
      tool_requests: [],
      usage: null,
      stop_reason: null,
      // Identificador de modelo que DEVOLVIÓ el proveedor, que no siempre es el
      // que se pidió: algunos responden con una instantánea fechada. Va en el
      // contrato porque es el dato con el que se busca el precio, y sin él, el
      // fallo por costo desconocido no puede decir qué declarar para
      // resolverlo. `null` si el proveedor no lo informa.
      provider_model: null,
      provider_request_id: null
    };
  }

  function normalizedFields() {
    return Object.keys(emptyNormalized()).sort();
  }

  /** Verifica que un objeto normalizado tenga el contrato exacto. */
  function assertNormalizedShape(obj) {
    var expected = normalizedFields();
    var actual = Object.keys(obj).sort();
    if (expected.join(',') !== actual.join(',')) {
      throw Errors.schemaError('Respuesta normalizada fuera de contrato', {
        expected: expected, actual: actual
      });
    }
    if (Object.prototype.toString.call(obj.tool_requests) !== '[object Array]') {
      throw Errors.schemaError('tool_requests debe ser array');
    }
    if (typeof obj.text !== 'string') {
      throw Errors.schemaError('text debe ser string');
    }
    return true;
  }

  /**
   * ÚNICA PUERTA DE GASTO. Toda llamada pagada del prototipo pasa por aquí.
   *
   * Existía un segundo camino: el ensayo de proveedores llamaba directamente
   * al adaptador, que sólo comprueba que los techos ESTÉN DECLARADOS, no que
   * no se hayan excedido. El control real vivía en el orquestador, así que el
   * ensayo tenía techos configurados y ningún enforcement: el gasto no se
   * agregaba al ledger, y ni el tope por corrida ni el diario ni el mensual se
   * comprobaban. Con dieciséis tokens de salida el riesgo era ínfimo, pero eso
   * no permite decir que la puerta presupuestaria funciona.
   *
   * Secuencia, y el orden importa:
   *   techos declarados → presupuesto agregado → tope de la corrida sobre lo
   *   ya acumulado → proveedor → precio conocido → contabilizar → tope otra vez.
   *
   * El tope aparece dos veces a propósito. Antes de llamar comprueba lo ya
   * gastado, y después comprueba el total con la llamada incluida. El primero
   * es lo que convierte el techo en parada dura; el segundo es lo que lo hace
   * exacto.
   *
   * EL NIVEL NO SE COMPRUEBA AQUÍ, a propósito. Vive en los dos adaptadores
   * reales, que son los únicos que tocan la red y por tanto los únicos que
   * pueden gastar. Ponerlo también en esta puerta cerraría el paso a los
   * proveedores simulados, que corren en LEVEL_0 por diseño y no cuestan
   * dinero: rompería toda la batería de casos de aceptación sin cerrar ningún
   * agujero, porque el gasto ocurre una capa más adentro y allí ya está
   * bloqueado.
   *
   * Limitación inherente y declarada: el costo real sólo se conoce DESPUÉS de
   * la respuesta, así que el tope por corrida no puede impedir que una única
   * llamada lo rebase; impide la siguiente y detiene la corrida. Por eso el
   * techo de tokens de salida importa tanto en el primer ensayo.
   *
   * `runtime` es el acumulador del llamador: { cost_usd, cost_known }.
   *
   * PROCEDENCIA (auditoría cruzada, 2026-09-12): si `runtime.active_provider_attempt`
   * existe (lo fija `Orchestrator._modelTurn()` antes de llamar aquí), esta
   * función es la ÚNICA que escribe `invoked_provider` y `provider_model` en
   * ese intento — nunca el orquestador, y nunca antes de tener evidencia
   * positiva de despacho real. Dos puntos de escritura, ambos posteriores a
   * cruzar la frontera de red del adaptador:
   *   (a) dentro del catch, sólo si `e.dispatchAttempted === true` — cubre
   *       fallos posteriores al intento real (timeout, HTTP no exitoso,
   *       parseo, forma inválida);
   *   (b) inmediatamente después de que `provider.complete()`/`completeWithTools()`
   *       RETORNA — antes incluso de `assertNormalizedShape()` — porque el
   *       proveedor ya respondió en ese punto, sin importar si la forma es
   *       válida. `provider_model`, en cambio, sólo se confía DESPUÉS de que
   *       la forma pasa esa validación: antes de eso podría ser el campo de
   *       un objeto que ni siquiera cumple el contrato normalizado.
   */
  function callBudgeted(provider, request, toolContract, runtime) {
    Config.assertBudgetsConfigured();
    Ledger.assertAggregateBudget();

    // Preflight del tope de la corrida sobre lo YA acumulado. No puede predecir
    // el costo de la llamada que viene —eso sólo se sabe al recibirla— pero sí
    // puede negarse cuando el techo ya está rebasado. Sin esto, una corrida que
    // ya lo hubiera superado seguía pagando una llamada más por cada intento
    // antes de detenerse, que es lo contrario de una parada dura.
    var limitesPrevios = Config.limits();
    // `>=`, no `>`. Alcanzar exactamente el techo es haberlo alcanzado: con `>`
    // una corrida parada justo en el tope permitía una llamada más. Los techos
    // diario y mensual ya usaban `>=`, así que el de corrida era además
    // inconsistente con sus propios hermanos.
    if (typeof limitesPrevios.MAX_RUN_BUDGET_USD === 'number' &&
        runtime.cost_usd >= limitesPrevios.MAX_RUN_BUDGET_USD) {
      throw Errors.limitExceeded('MAX_RUN_BUDGET_USD', runtime.cost_usd);
    }
    if (runtime.cost_known === false) {
      // Contador ciego: no se sigue gastando. Vale también si el llamador
      // ignoró el error anterior y volvió a intentarlo.
      throw Errors.priceUnknown('CORRIDA', 'el costo acumulado dejó de ser conocido');
    }

    // Guardas previas de los adaptadores. El código solo NO demuestra la fase:
    // CONFIG también puede surgir al leer precios después de recibir respuesta.
    // La evidencia de despacho de los adaptadores reales prevalece sobre esta lista.
    var PRE_DISPATCH = [
      Errors.CODES.LEVEL_VIOLATION,
      Errors.CODES.LIMIT_EXCEEDED,
      Errors.CODES.BUDGET_UNCONFIGURED,
      Errors.CODES.CONFIG,
      Errors.CODES.MISSING_CREDENTIAL
    ];

    var normalized;
    try {
      normalized = toolContract
        ? provider.completeWithTools(request, toolContract)
        : provider.complete(request);
    } catch (e) {
      // Resultado ambiguo tras el intento de red. La petición pudo llegar al
      // proveedor, la inferencia pudo ejecutarse y cobrarse, y la respuesta
      // perderse por timeout o corte. No recibir el uso NO significa que el
      // gasto no existiera, exactamente igual que un bloque de uso ausente.
      //
      // Política conservadora deliberada para esta fase: cualquier error que no
      // sea inequívocamente previo al despacho deja el contador ciego y detiene
      // la corrida. Distinguir un 401 de un timeout es sofisticación que hoy no
      // hace falta y que, mal hecha, reabre el agujero.
      if (e.dispatchAttempted === true || PRE_DISPATCH.indexOf(e.code) === -1) {
        runtime.cost_known = false;
      }
      // Procedencia: sólo con evidencia POSITIVA de despacho (dispatchAttempted),
      // nunca por exclusión de PRE_DISPATCH — esa lista es sobre el contador de
      // costo, no sobre la verdad de si la red se tocó.
      if (e.dispatchAttempted === true && runtime.active_provider_attempt) {
        runtime.active_provider_attempt.invoked_provider = provider.name;
      }
      throw e;
    }

    // Procedencia (corrección de ChatGPT, 2026-09-12): el proveedor YA
    // RETORNÓ en este punto — la intervención ocurrió, independientemente de
    // si la forma que devolvió es válida. Marcarlo AQUÍ, antes de
    // assertNormalizedShape(), en vez de después, cierra una ventana real: si
    // un proveedor conforme devuelve una forma inválida, assertNormalizedShape
    // lanza SCHEMA sin `dispatchAttempted` (nadie se lo pone, es un error
    // propio de este módulo, no del adaptador), y el catch de abajo nunca
    // habría marcado `invoked_provider` pese a que el despacho sí ocurrió.
    if (runtime.active_provider_attempt) {
      runtime.active_provider_attempt.invoked_provider = provider.name;
    }

    try {
      assertNormalizedShape(normalized);
    } catch (e) {
      // El error de forma nunca es un código PRE_DISPATCH (no está en esa
      // lista), así que bajo la misma política de arriba el costo queda
      // ciego incondicionalmente: el proveedor ya respondió y no podemos
      // confiar en nada de lo que dijo, incluida su forma de uso.
      runtime.cost_known = false;
      throw e;
    }

    // Sólo AHORA, tras validar la forma, confiamos en provider_model — antes
    // de este punto podría ser el campo de un objeto que ni siquiera cumple
    // el contrato normalizado.
    if (runtime.active_provider_attempt) {
      runtime.active_provider_attempt.provider_model = normalized.provider_model;
    }

    // Una respuesta sin bloque de uso deja el contador ciego igual que un
    // precio desconocido: no se puede distinguir "costó cero" de "costó algo
    // que no supe medir". Si esto sólo se tratara en el ensayo, el orquestador
    // no heredaría la protección, así que va aquí.
    if (!normalized.usage) {
      runtime.cost_known = false;
      throw Errors.priceUnknown(
        provider.name ? provider.name : 'PROVEEDOR',
        'el proveedor respondió SIN bloque de uso: no hay tokens que contar y ' +
        'el costo de esta llamada es indeterminable');
    }

    if (normalized.usage) {
      var cost = normalized.usage.estimated_cost_usd;
      if (typeof cost === 'number') {
        runtime.cost_usd += cost;
        Ledger.addSpend(cost);
        var limits = Config.limits();
        if (typeof limits.MAX_RUN_BUDGET_USD === 'number' &&
            runtime.cost_usd > limits.MAX_RUN_BUDGET_USD) {
          throw Errors.limitExceeded('MAX_RUN_BUDGET_USD', runtime.cost_usd);
        }
      } else {
        // Fail closed: no se sigue gastando contra un contador ciego. El
        // identificador devuelto viaja en el error para que el operador sepa
        // exactamente qué declarar en METIS_PRICING.
        runtime.cost_known = false;
        throw Errors.priceUnknown(
          provider.name ? provider.name : 'PROVEEDOR',
          normalized.provider_model ? normalized.provider_model : 'identificador no informado');
      }
    }
    return normalized;
  }

  function conforms(instance) {
    if (!instance) { return false; }
    for (var i = 0; i < REQUIRED_METHODS.length; i++) {
      if (typeof instance[REQUIRED_METHODS[i]] !== 'function') { return false; }
    }
    return true;
  }

  return {
    Base: Base,
    REQUIRED_METHODS: REQUIRED_METHODS,
    emptyNormalized: emptyNormalized,
    normalizedFields: normalizedFields,
    assertNormalizedShape: assertNormalizedShape,
    callBudgeted: callBudgeted,
    conforms: conforms
  };
})();
