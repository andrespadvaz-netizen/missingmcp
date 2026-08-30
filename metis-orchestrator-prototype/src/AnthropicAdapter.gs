/**
 * AnthropicAdapter.gs — invoca la Messages API y traduce al MISMO contrato
 * interno que `OpenAIAdapter` (spec §11).
 *
 * Secretos: leídos de Script Properties en el momento de la llamada. Ningún
 * proveedor conoce el secreto del otro.
 */
var AnthropicAdapter = (function () {

  /**
   * Los precios NO viven en el código: cambian y un número obsoleto produce un
   * contador de costo falso, que es peor que no tenerlo. Se declaran en la
   * Script Property `METIS_PRICING`. Sin precio para el modelo usado, el costo
   * queda DESCONOCIDO (`null`) y el orquestador falla cerrado.
   */

  var Adapter = class AnthropicAdapterImpl {

    get name() { return 'ANTHROPIC'; }

    toolsFor(toolContract) {
      return (toolContract || []).map(function (t) {
        return {
          name: t.name.replace(/\./g, '__'),
          description: t.description,
          input_schema: t.input_schema
        };
      });
    }

    buildRequest(request, toolContract) {
      var body = {
        model: request.model ? request.model : Config.PROVIDERS.ANTHROPIC.model,
        max_tokens: request.max_output_tokens ? request.max_output_tokens : Config.PROVIDERS.ANTHROPIC.max_tokens,
        system: request.system,
        messages: [{ role: 'user', content: [{ type: 'text', text: request.prompt }] }]
      };
      if (toolContract && toolContract.length) { body.tools = this.toolsFor(toolContract); }
      return body;
    }

    complete(request) {
      return this.completeWithTools(request, null);
    }

    completeWithTools(request, toolContract) {
      // Antes que nada: el nivel. Una llamada a un modelo es la única operación
      // de este prototipo que cuesta dinero, así que no puede ocurrir con el
      // runtime en estado inerte aunque la credencial esté cargada. Los
      // adaptadores de lectura ya fallaban así; éste no lo hacía, y eso
      // convertía LEVEL_0 en "no lee" en vez de "no hace nada externo".
      if (!Config.levelAllowsModelCalls()) {
        throw Errors.levelViolation(
          'Invocación real de Anthropic bloqueada en ' + Config.runLevel() +
          '. Los modelos sólo se llaman en LEVEL_2.');
      }
      // Después: los techos deben existir. Vale para toda llamada real.
      Config.assertBudgetsConfigured();
      var body = this.buildRequest(request, toolContract);
      // Ver OpenAIAdapter: la credencial se resuelve fuera del try para no
      // enmascarar un error de configuración como fallo de proveedor.
      var apiKey = Config.secret('ANTHROPIC_API_KEY');
      var response;
      try {
        response = UrlFetchApp.fetch(Config.PROVIDERS.ANTHROPIC.endpoint, {
          method: 'post',
          contentType: 'application/json',
          muteHttpExceptions: true,
          headers: {
            'x-api-key': apiKey,
            'anthropic-version': Config.PROVIDERS.ANTHROPIC.version_header
          },
          payload: JSON.stringify(body)
        });
      } catch (e) {
        var redacted = this.redactProviderError(e);
        throw Errors.providerError('ANTHROPIC', null, redacted.message);
      }
      var code = response.getResponseCode();
      if (code < 200 || code >= 300) {
        throw Errors.providerError('ANTHROPIC', code, 'respuesta no exitosa');
      }
      return this.normalizeResponse(JSON.parse(response.getContentText()));
    }

    normalizeResponse(raw) {
      var text = '';
      var toolRequests = [];
      var content = (raw && raw.content) ? raw.content : [];
      for (var i = 0; i < content.length; i++) {
        var block = content[i];
        if (block.type === 'text') {
          text += block.text;
        } else if (block.type === 'tool_use') {
          toolRequests.push({
            id: block.id ? block.id : null,
            name: String(block.name).replace(/__/g, '.'),
            arguments: block.input ? block.input : {}
          });
        }
      }
      var usage = null;
      if (raw && raw.usage) {
        usage = {
          input_tokens: raw.usage.input_tokens ? raw.usage.input_tokens : 0,
          output_tokens: raw.usage.output_tokens ? raw.usage.output_tokens : 0,
          // `null` = precio no configurado. El orquestador lo trata como
          // costo desconocido y detiene la corrida, no como cero.
          estimated_cost_usd: estimateCost(raw.usage, raw.model ? raw.model : null)
        };
      }
      var normalized = {
        text: text,
        tool_requests: toolRequests,
        usage: usage,
        stop_reason: (raw && raw.stop_reason) ? raw.stop_reason : null,
        provider_request_id: (raw && raw.id) ? raw.id : null
      };
      ProviderAdapter.assertNormalizedShape(normalized);
      return normalized;
    }

    redactProviderError(error, status) {
      return Errors.redactProviderError('ANTHROPIC', error, status === undefined ? null : status);
    }
  };

  function estimateCost(usage, model) {
    var price = Config.priceFor('ANTHROPIC', model);
    if (!price) { return null; }
    var inTok = usage.input_tokens ? usage.input_tokens : 0;
    var outTok = usage.output_tokens ? usage.output_tokens : 0;
    return (inTok / 1000) * price.input_per_1k + (outTok / 1000) * price.output_per_1k;
  }

  return {
    estimateCost: estimateCost,
    create: function () { return new Adapter(); },
    Impl: Adapter
  };
})();
