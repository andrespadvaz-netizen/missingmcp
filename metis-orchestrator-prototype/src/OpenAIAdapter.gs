/**
 * OpenAIAdapter.gs — invoca la Responses API y traduce al contrato interno.
 *
 * Secretos: se leen de Script Properties en el momento de la llamada
 * (`Config.secret('OPENAI_API_KEY')`) y nunca se guardan en la instancia, en el
 * prompt, en el handoff, en el ledger ni en un log.
 */
var OpenAIAdapter = (function () {

  /** Referencia de costo aproximado, sólo para el contador agregado (spec §13). */
  var PRICE_PER_1K = { input: 0.0025, output: 0.010 };

  var Adapter = class OpenAIAdapterImpl {

    get name() { return 'OPENAI'; }

    /** Traduce el contrato común de herramientas al formato del proveedor. */
    toolsFor(toolContract) {
      return (toolContract || []).map(function (t) {
        return {
          type: 'function',
          name: t.name.replace(/\./g, '__'),
          description: t.description,
          parameters: t.input_schema
        };
      });
    }

    buildRequest(request, toolContract) {
      var body = {
        model: request.model ? request.model : Config.PROVIDERS.OPENAI.model,
        input: [
          { role: 'system', content: [{ type: 'input_text', text: request.system }] },
          { role: 'user', content: [{ type: 'input_text', text: request.prompt }] }
        ]
      };
      if (request.max_output_tokens) { body.max_output_tokens = request.max_output_tokens; }
      if (toolContract && toolContract.length) { body.tools = this.toolsFor(toolContract); }
      return body;
    }

    complete(request) {
      return this.completeWithTools(request, null);
    }

    completeWithTools(request, toolContract) {
      var body = this.buildRequest(request, toolContract);
      var response;
      try {
        response = UrlFetchApp.fetch(Config.PROVIDERS.OPENAI.endpoint, {
          method: 'post',
          contentType: 'application/json',
          muteHttpExceptions: true,
          headers: { 'Authorization': 'Bearer ' + Config.secret('OPENAI_API_KEY') },
          payload: JSON.stringify(body)
        });
      } catch (e) {
        var redacted = this.redactProviderError(e);
        throw Errors.providerError('OPENAI', null, redacted.message);
      }
      var code = response.getResponseCode();
      if (code < 200 || code >= 300) {
        // El body crudo NUNCA se propaga: sólo status.
        throw Errors.providerError('OPENAI', code, 'respuesta no exitosa');
      }
      return this.normalizeResponse(JSON.parse(response.getContentText()));
    }

    normalizeResponse(raw) {
      var text = '';
      var toolRequests = [];
      var output = (raw && raw.output) ? raw.output : [];
      for (var i = 0; i < output.length; i++) {
        var item = output[i];
        if (item.type === 'message' && item.content) {
          for (var c = 0; c < item.content.length; c++) {
            if (item.content[c].type === 'output_text') { text += item.content[c].text; }
          }
        } else if (item.type === 'function_call') {
          var args = {};
          try { args = JSON.parse(item.arguments); } catch (e) { args = { _unparsed: true }; }
          toolRequests.push({
            id: item.call_id ? item.call_id : null,
            name: String(item.name).replace(/__/g, '.'),
            arguments: args
          });
        }
      }
      var usage = null;
      if (raw && raw.usage) {
        usage = {
          input_tokens: raw.usage.input_tokens ? raw.usage.input_tokens : 0,
          output_tokens: raw.usage.output_tokens ? raw.usage.output_tokens : 0,
          estimated_cost_usd: estimateCost(raw.usage)
        };
      }
      var normalized = {
        text: text,
        tool_requests: toolRequests,
        usage: usage,
        stop_reason: (raw && raw.status) ? raw.status : null,
        provider_request_id: (raw && raw.id) ? raw.id : null
      };
      ProviderAdapter.assertNormalizedShape(normalized);
      return normalized;
    }

    /** Nunca devuelve headers, payload ni credenciales. */
    redactProviderError(error, status) {
      return Errors.redactProviderError('OPENAI', error, status === undefined ? null : status);
    }
  };

  function estimateCost(usage) {
    var inTok = usage.input_tokens ? usage.input_tokens : 0;
    var outTok = usage.output_tokens ? usage.output_tokens : 0;
    return (inTok / 1000) * PRICE_PER_1K.input + (outTok / 1000) * PRICE_PER_1K.output;
  }

  return {
    PRICE_PER_1K: PRICE_PER_1K,
    create: function () { return new Adapter(); },
    Impl: Adapter
  };
})();
