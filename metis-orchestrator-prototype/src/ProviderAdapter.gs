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

    var normalized = toolContract
      ? provider.completeWithTools(request, toolContract)
      : provider.complete(request);
    assertNormalizedShape(normalized);

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
