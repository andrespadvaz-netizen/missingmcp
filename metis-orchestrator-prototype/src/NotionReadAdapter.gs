/**
 * NotionReadAdapter.gs — lectura REAL de sólo lectura contra la API de Notion.
 *
 * Este adaptador NO expone ninguna operación de escritura: no existe función
 * que haga POST/PATCH sobre páginas, bloques o bases. `POST` aparece sólo en
 * `/query` y `/search`, que son operaciones de lectura de la API de Notion.
 *
 * Partición: toda consulta se acota EN ORIGEN a los data sources declarados
 * para el contexto resuelto, con filtro por la propiedad `Proyecto`. Un objeto
 * fuera de esa partición no se devuelve; si se pide por id, se rechaza.
 *
 * Nivel 0: el backend se inyecta con `useBackend(fixtureBackend)`.
 * Nivel 1/2: backend real vía UrlFetchApp. Sin credencial válida, se detiene.
 */
var NotionReadAdapter = (function () {

  /**
   * Esquema REAL de la database "Decisiones Tomadas"
   * (data source collection://1436a1fc-159a-4e99-a6d6-0313f5932560),
   * verificado contra el workspace el 2026-08-25. Los nombres son los que
   * existen; cambiarlos en Notion rompe esta lectura y debe reflejarse aquí.
   */
  var DECISION_PROPS = {
    TITLE: 'Decision',
    PROJECT: 'Proyecto',
    CONTEXT_TEXT: 'Contexto',
    STATE: 'Estado',
    SUPERSEDED_BY: 'Sustituida por',
    SUPERSEDES: 'Sustituye a',
    DATE: 'Fecha',
    DOC_URL: 'Doc vinculado',
    REVERSIBLE: 'Reversible',
    MODEL: 'Modelo donde se tomo'
  };

  /** Valores reales de la propiedad select `Estado`. */
  var DECISION_STATES = ['vigente', 'modificada', 'derogada'];

  var _backend = null;

  function useBackend(backend) { _backend = backend; }
  function resetBackend() { _backend = null; }

  function _assertLevel() {
    if (!Config.levelAllowsRealReads()) {
      throw Errors.levelViolation('Lectura real de Notion bloqueada en ' + Config.runLevel());
    }
  }

  function _partitionOf(options) {
    var partition = options && options.partition ? options.partition : null;
    if (!partition || !partition.data_sources || !partition.data_sources.length) {
      throw Errors.sourcePartitionUndeclared(
        (options && options.context) ? options.context : 'DESCONOCIDO', 'NOTION');
    }
    return partition;
  }

  function _request(url, method, body) {
    _assertLevel();
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

  /** Tope defensivo de páginas por consulta. Ver `collectPages`. */
  var MAX_PAGES = 20;

  /** Query de data source (API 2025-09-03). Es una LECTURA, aunque use POST. */
  function _queryDataSource(dataSourceId, filter, pageSize, startCursor) {
    var body = { page_size: pageSize ? pageSize : 25 };
    if (filter) { body.filter = filter; }
    if (startCursor) { body.start_cursor = startCursor; }
    return _request('https://api.notion.com/v1/data_sources/' +
      encodeURIComponent(dataSourceId) + '/query', 'post', body);
  }

  /**
   * Recorre TODAS las páginas de un query siguiendo `has_more`/`next_cursor`.
   *
   * Importa para la vigencia: una cadena de sustitución puede tener su eslabón
   * terminal en la segunda página. Quedarse con la primera haría que la cadena
   * no cerrara y el prototipo se abstuviera sin motivo real — un falso "no
   * puedo afirmar estado" es tan malo como afirmar de más.
   *
   * `fetchPage(cursor)` devuelve `{results, has_more, next_cursor}`.
   * Tope defensivo: `maxPages`. Alcanzarlo NO se silencia — lanza, porque
   * devolver un conjunto truncado como si fuera completo es exactamente el
   * modo de fallo "cobertura incompleta presentada como exhaustiva".
   */
  function collectPages(fetchPage, maxPages) {
    var cap = (maxPages === undefined || maxPages === null) ? MAX_PAGES : maxPages;
    var out = [];
    var cursor = null;
    var pages = 0;
    var seenCursors = {};

    while (pages < cap) {
      var res = fetchPage(cursor) || {};
      out = out.concat(res.results || []);
      pages++;
      if (!res.has_more) { return out; }
      var next = res.next_cursor;
      if (!next) { return out; }
      // Un cursor repetido sería un bucle infinito servido por el proveedor.
      if (seenCursors[next]) {
        throw Errors.readFailed('NOTION', 'cursor repetido en la paginación');
      }
      seenCursors[next] = true;
      cursor = next;
    }
    throw Errors.readFailed('NOTION',
      'la consulta excede ' + cap + ' páginas; el resultado estaría truncado');
  }

  /** Filtro por contexto: la propiedad `Proyecto` es la clave de partición. */
  function _projectFilter(partition) {
    if (!partition.project) { return null; }
    return { property: DECISION_PROPS.PROJECT, select: { equals: partition.project } };
  }

  function _andFilters(filters) {
    var present = filters.filter(function (f) { return !!f; });
    if (!present.length) { return null; }
    if (present.length === 1) { return present[0]; }
    return { and: present };
  }

  function _realBackend() {
    return {
      search: function (query, options) {
        _assertLevel();
        var partition = _partitionOf(options);
        var filter = _andFilters([
          _projectFilter(partition),
          query ? { property: DECISION_PROPS.TITLE, title: { contains: String(query) } } : null
        ]);
        var out = [];
        for (var i = 0; i < partition.data_sources.length; i++) {
          var res = _queryDataSource(partition.data_sources[i], filter,
            (options && options.page_size) ? options.page_size : 10);
          var results = res.results || [];
          for (var r = 0; r < results.length; r++) {
            out.push(_normalizePage(results[r], null, partition));
          }
        }
        return out;
      },

      fetch: function (id, options) {
        _assertLevel();
        var partition = _partitionOf(options);
        var page = _request('https://api.notion.com/v1/pages/' + encodeURIComponent(id), 'get', null);
        _assertInPartition(page, partition, options);
        var blocks = _request('https://api.notion.com/v1/blocks/' + encodeURIComponent(id) +
          '/children?page_size=50', 'get', null);
        return _normalizePage(page, _plainText(blocks.results || []), partition);
      },

      /**
       * Registro de decisiones del contexto: query de data source con filtro por
       * Proyecto. Devuelve cada decisión con su Estado y sus dos relaciones de
       * sustitución, que es lo que permite cerrar la cadena de vigencia.
       */
      decisions: function (options) {
        _assertLevel();
        var partition = _partitionOf(options);
        var dataSources = partition.decision_data_sources && partition.decision_data_sources.length
          ? partition.decision_data_sources
          : partition.data_sources;
        var filter = _projectFilter(partition);
        var pageSize = (options && options.page_size) ? options.page_size : 100;
        var out = [];
        for (var i = 0; i < dataSources.length; i++) {
          var dataSourceId = dataSources[i];
          // Paginación completa: la cadena de vigencia puede cruzar páginas.
          var results = collectPages(function (cursor) {
            return _queryDataSource(dataSourceId, filter, pageSize, cursor);
          }, options ? options.max_pages : null);
          for (var r = 0; r < results.length; r++) {
            out.push(_normalizePage(results[r], null, partition));
          }
        }
        return out;
      }
    };
  }

  /** Un objeto pedido por id debe pertenecer al contenedor del contexto. */
  function _assertInPartition(page, partition, options) {
    var parent = page.parent || {};
    var parentId = parent.data_source_id || parent.database_id || null;
    var context = (options && options.context) ? options.context : null;

    if (parentId && partition.data_sources.indexOf(parentId) === -1 &&
        (!partition.decision_data_sources || partition.decision_data_sources.indexOf(parentId) === -1)) {
      throw Errors.partitionViolation(page.id, context);
    }
    var project = _selectOf(page, DECISION_PROPS.PROJECT);
    if (partition.project && project && project !== partition.project) {
      throw Errors.partitionViolation(page.id, context);
    }
    return true;
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

  function _relationIds(page, propName) {
    var props = page.properties || {};
    var prop = props[propName];
    if (!prop || prop.type !== 'relation' || !prop.relation) { return []; }
    var ids = [];
    for (var i = 0; i < prop.relation.length; i++) {
      if (prop.relation[i] && prop.relation[i].id) { ids.push(prop.relation[i].id); }
    }
    return ids;
  }

  function _dateOf(page, propName) {
    var props = page.properties || {};
    var prop = props[propName];
    if (prop && prop.type === 'date' && prop.date) { return prop.date.start; }
    return null;
  }

  function _urlOf(page, propName) {
    var props = page.properties || {};
    var prop = props[propName];
    if (prop && prop.type === 'url') { return prop.url; }
    return null;
  }

  function _checkboxOf(page, propName) {
    var props = page.properties || {};
    var prop = props[propName];
    if (prop && prop.type === 'checkbox') { return prop.checkbox === true; }
    return null;
  }

  /**
   * ¿Esta página es una fila del registro de decisiones? Se decide por la
   * presencia de las propiedades estructurales, no por el título.
   */
  function isDecisionRow(page) {
    var props = page.properties || {};
    return !!(props[DECISION_PROPS.STATE] || props[DECISION_PROPS.SUPERSEDED_BY]);
  }

  /** Extrae la decisión estructurada de una fila del registro. */
  function toDecision(page) {
    var estado = _selectOf(page, DECISION_PROPS.STATE);
    return {
      id: page.id,
      title: _titleOf(page),
      project: _selectOf(page, DECISION_PROPS.PROJECT),
      // `Estado` está vacío en la mayoría de las filas reales; se conserva el
      // null tal cual y la regla de vigencia decide qué hacer con él.
      estado: (estado && DECISION_STATES.indexOf(estado) !== -1) ? estado : null,
      superseded_by: _relationIds(page, DECISION_PROPS.SUPERSEDED_BY),
      supersedes: _relationIds(page, DECISION_PROPS.SUPERSEDES),
      fecha: _dateOf(page, DECISION_PROPS.DATE),
      doc_url: _urlOf(page, DECISION_PROPS.DOC_URL),
      reversible: _checkboxOf(page, DECISION_PROPS.REVERSIBLE),
      model: _selectOf(page, DECISION_PROPS.MODEL)
    };
  }

  /** Clasifica la página para derivar su etiqueta epistémica (spec §9). */
  function classify(title, typeProp, page) {
    if (page && isDecisionRow(page)) { return 'DECISION_REGISTRY'; }
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

  function _normalizePage(page, snippet, partition) {
    var title = _titleOf(page);
    var project = _selectOf(page, DECISION_PROPS.PROJECT);
    var typeProp = _selectOf(page, 'Tipo');
    var kind = classify(title, typeProp, page);
    return {
      source: 'NOTION',
      id: page.id,
      title: title,
      context: project ? _contextForProject(project) : null,
      kind: kind,
      epistemic_status: RetrievalPolicy.epistemicFor(kind),
      snippet: snippet === undefined ? null : snippet,
      url: page.url ? page.url : null,
      decision: isDecisionRow(page) ? toDecision(page) : null
    };
  }

  /** Traduce el valor de `Proyecto` al nombre de contexto del prototipo. */
  function _contextForProject(project) {
    var names = Config.contextNames();
    for (var i = 0; i < names.length; i++) {
      if (Config.notionProjectFor(names[i]) === project) { return names[i]; }
    }
    return null;
  }

  function backend() { return _backend ? _backend : _realBackend(); }

  function search(query, options) { return backend().search(query, options || {}); }
  function fetch(id, options) { return backend().fetch(id, options || {}); }
  function decisions(options) { return backend().decisions(options || {}); }

  return {
    DECISION_PROPS: DECISION_PROPS,
    DECISION_STATES: DECISION_STATES,
    MAX_PAGES: MAX_PAGES,
    collectPages: collectPages,
    normalizePage: function (page, snippet, partition) { return _normalizePage(page, snippet, partition); },
    useBackend: useBackend,
    resetBackend: resetBackend,
    classify: classify,
    isDecisionRow: isDecisionRow,
    toDecision: toDecision,
    search: search,
    fetch: fetch,
    decisions: decisions
  };
})();
