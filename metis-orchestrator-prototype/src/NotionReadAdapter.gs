/**
 * NotionReadAdapter.gs — lectura REAL de sólo lectura contra la API de Notion.
 *
 * Este adaptador NO expone ninguna operación de escritura: no existe función
 * que haga POST/PATCH sobre páginas, bloques o bases. Sólo `search` y `fetch`.
 *
 * Nivel 0: el backend se inyecta con `useBackend(fixtureBackend)`.
 * Nivel 1/2: backend real vía UrlFetchApp. Sin credencial válida, se detiene.
 */
var NotionReadAdapter = (function () {

  var _backend = null;

  function useBackend(backend) { _backend = backend; }
  function resetBackend() { _backend = null; }

  function _realBackend() {
    return {
      search: function (query, options) {
        var body = {
          query: String(query || ''),
          page_size: (options && options.page_size) ? options.page_size : 10
        };
        var res = _request('https://api.notion.com/v1/search', 'post', body);
        var results = res.results || [];
        var out = [];
        for (var i = 0; i < results.length; i++) {
          out.push(_normalizePage(results[i], null));
        }
        return out;
      },
      fetch: function (id) {
        var page = _request('https://api.notion.com/v1/pages/' + encodeURIComponent(id), 'get', null);
        var blocks = _request('https://api.notion.com/v1/blocks/' + encodeURIComponent(id) + '/children?page_size=50', 'get', null);
        return _normalizePage(page, _plainText(blocks.results || []));
      }
    };
  }

  function _request(url, method, body) {
    if (!Config.levelAllowsRealReads()) {
      throw Errors.levelViolation('Lectura real de Notion bloqueada en ' + Config.runLevel());
    }
    var options = {
      method: method,
      muteHttpExceptions: true,
      headers: {
        // El valor del secreto nunca se registra ni se devuelve.
        'Authorization': 'Bearer ' + Config.secret('NOTION_API_KEY'),
        'Notion-Version': Config.NOTION_VERSION
      }
    };
    if (body) {
      options.contentType = 'application/json';
      options.payload = JSON.stringify(body);
    }
    var response = UrlFetchApp.fetch(url, options);
    var code = response.getResponseCode();
    if (code < 200 || code >= 300) {
      // Redacción antes de cualquier exposición (spec §11, §14).
      throw Errors.readFailed('NOTION', 'HTTP ' + code);
    }
    return JSON.parse(response.getContentText());
  }

  function _plainText(blocks) {
    var parts = [];
    for (var i = 0; i < blocks.length; i++) {
      var block = blocks[i];
      var type = block.type;
      var payload = block[type];
      if (payload && payload.rich_text) {
        for (var r = 0; r < payload.rich_text.length; r++) {
          parts.push(payload.rich_text[r].plain_text);
        }
      }
    }
    return parts.join('\n');
  }

  function _titleOf(page) {
    var props = page.properties || {};
    var keys = Object.keys(props);
    for (var i = 0; i < keys.length; i++) {
      var prop = props[keys[i]];
      if (prop && prop.type === 'title' && prop.title && prop.title.length) {
        return prop.title[0].plain_text;
      }
    }
    return null;
  }

  function _selectOf(page, propName) {
    var props = page.properties || {};
    var prop = props[propName];
    if (prop && prop.type === 'select' && prop.select) { return prop.select.name; }
    return null;
  }

  /** Clasifica la página para derivar su etiqueta epistémica (spec §9). */
  function classify(title, typeProp) {
    var t = ContextResolver.normalize(title || '');
    var declared = ContextResolver.normalize(typeProp || '');
    if (declared.indexOf('decision') !== -1) { return 'DECISION_REGISTRY'; }
    if (declared.indexOf('phi') !== -1 || declared.indexOf('handoff') !== -1) { return 'PHI'; }
    if (declared.indexOf('propuesta') !== -1 || declared.indexOf('borrador') !== -1) { return 'PROPOSAL'; }
    if (t.indexOf('canon-core') !== -1) { return 'CANON_CORE'; }
    if (t.indexOf('canon') !== -1) { return 'CANON'; }
    if (t.indexOf('decisiones tomadas') !== -1) { return 'DECISION_REGISTRY'; }
    if (t.indexOf('phi') !== -1 || t.indexOf('handoff') !== -1) { return 'PHI'; }
    if (t.indexOf('chat log') !== -1) { return 'CHAT_LOG'; }
    return 'NOTE';
  }

  function _normalizePage(page, snippet) {
    var title = _titleOf(page);
    var contextProp = _selectOf(page, 'Contexto');
    var typeProp = _selectOf(page, 'Tipo');
    var kind = classify(title, typeProp);
    return {
      source: 'NOTION',
      id: page.id,
      title: title,
      context: contextProp ? contextProp.toUpperCase().replace(/[^A-Z_]/g, '_') : null,
      kind: kind,
      epistemic_status: RetrievalPolicy.epistemicFor(kind),
      snippet: snippet === undefined ? null : snippet,
      url: page.url ? page.url : null
    };
  }

  function backend() { return _backend ? _backend : _realBackend(); }

  function search(query, options) { return backend().search(query, options || {}); }
  function fetch(id) { return backend().fetch(id); }

  return {
    useBackend: useBackend,
    resetBackend: resetBackend,
    classify: classify,
    search: search,
    fetch: fetch
  };
})();
