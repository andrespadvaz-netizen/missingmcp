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
    conforms: conforms
  };
})();
