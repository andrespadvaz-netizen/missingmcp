/**
 * AsanaReadAdapter.gs — lectura REAL de sólo lectura contra la API de Asana.
 *
 * Sólo `search` (typeahead) y `get` (tarea). No existe ninguna función que
 * cree, actualice, complete o borre tareas.
 */
var AsanaReadAdapter = (function () {

  var BASE = 'https://app.asana.com/api/1.0';

  var _backend = null;

  function useBackend(backend) { _backend = backend; }
  function resetBackend() { _backend = null; }

  function _assertLevel() {
    if (!Config.levelAllowsRealReads()) {
      throw Errors.levelViolation('Lectura real de Asana bloqueada en ' + Config.runLevel());
    }
  }

  function _request(path) {
    _assertLevel();
    var response = UrlFetchApp.fetch(BASE + path, {
      method: 'get',
      muteHttpExceptions: true,
      headers: { 'Authorization': 'Bearer ' + Config.secret('ASANA_API_KEY') }
    });
    var code = response.getResponseCode();
    if (code < 200 || code >= 300) {
      throw Errors.readFailed('ASANA', 'HTTP ' + code);
    }
    return JSON.parse(response.getContentText());
  }

  var FIELDS = 'name,notes,completed,due_on,permalink_url,projects.name,projects.gid';

  /** Tope defensivo de páginas por listado. Ver `collectPages`. */
  var MAX_PAGES = 20;

  /**
   * Recorre TODAS las páginas de un listado siguiendo `next_page.offset`.
   *
   * Asana devuelve como máximo 100 elementos por página. Un proyecto con más
   * tareas que eso deja fuera del primer lote a cualquier tarea antigua, y una
   * búsqueda que sólo mire la primera página informaría "no existe" sobre algo
   * que sí está. Igual que en Notion, alcanzar el tope LANZA en vez de devolver
   * un resultado truncado como si fuera completo.
   *
   * `fetchPage(offset)` devuelve `{data, next_page:{offset}}`.
   */
  function collectPages(fetchPage, maxPages) {
    var cap = (maxPages === undefined || maxPages === null) ? MAX_PAGES : maxPages;
    var out = [];
    var offset = null;
    var pages = 0;
    var seenOffsets = {};

    while (pages < cap) {
      var res = fetchPage(offset) || {};
      out = out.concat(res.data || []);
      pages++;
      var next = (res.next_page && res.next_page.offset) ? res.next_page.offset : null;
      if (!next) { return out; }
      if (seenOffsets[next]) {
        throw Errors.readFailed('ASANA', 'offset repetido en la paginación');
      }
      seenOffsets[next] = true;
      offset = next;
    }
    throw Errors.readFailed('ASANA',
      'el listado excede ' + cap + ' páginas; el resultado estaría truncado');
  }

  function _partitionOf(options) {
    var partition = options && options.partition ? options.partition : null;
    if (!partition || !partition.project_gids || !partition.project_gids.length) {
      throw Errors.sourcePartitionUndeclared(
        (options && options.context) ? options.context : 'DESCONOCIDO', 'ASANA');
    }
    return partition;
  }

  function _realBackend() {
    return {
      /**
       * Se listan las tareas de los proyectos declarados para el contexto. No se
       * usa el typeahead de workspace: buscaría en TODO el espacio y devolvería
       * tareas de otros contextos, que es exactamente lo que la partición
       * impide. El filtro por texto se aplica sobre ese conjunto ya acotado.
       */
      search: function (query, options) {
        _assertLevel();
        var partition = _partitionOf(options);
        var needle = ContextResolver.normalize(query || '');
        var limit = (options && options.page_size) ? options.page_size : 10;
        var out = [];

        for (var p = 0; p < partition.project_gids.length && out.length < limit; p++) {
          var projectGid = partition.project_gids[p];
          // Paginación completa del proyecto: la tarea buscada puede estar más
          // allá de las primeras 100.
          var data = collectPages(function (offset) {
            return _request('/projects/' + encodeURIComponent(projectGid) +
              '/tasks?limit=100&opt_fields=' + FIELDS +
              (offset ? '&offset=' + encodeURIComponent(offset) : ''));
          }, options ? options.max_pages : null);

          for (var i = 0; i < data.length && out.length < limit; i++) {
            var task = _normalizeTask(data[i], null, options ? options.context : null);
            if (!needle || ContextResolver.normalize(task.title || '').indexOf(needle) !== -1) {
              // Sólo sobre lo que va a admitirse. Ver `_assertNoForeignMembership`.
              _assertNoForeignMembership(data[i], options);
              out.push(task);
            }
          }
        }
        return out;
      },

      get: function (gid, options) {
        _assertLevel();
        var partition = _partitionOf(options);
        var res = _request('/tasks/' + encodeURIComponent(gid) + '?opt_fields=' + FIELDS);
        var data = res.data || {};
        _assertInPartition(data, partition, options);
        return _normalizeTask(data, data.notes ? data.notes : null, options ? options.context : null);
      }
    };
  }

  /** La tarea debe pertenecer a alguno de los proyectos declarados. */
  function _assertInPartition(task, partition, options) {
    var projects = task.projects || [];
    for (var i = 0; i < projects.length; i++) {
      if (partition.project_gids.indexOf(String(projects[i].gid)) !== -1) {
        return _assertNoForeignMembership(task, options);
      }
    }
    throw Errors.partitionViolation(task.gid ? String(task.gid) : 'desconocido',
      (options && options.context) ? options.context : null);
  }

  /**
   * Proyectos declarados para CUALQUIER contexto distinto del resuelto.
   *
   * Existe porque en Asana una tarea puede pertenecer a varios proyectos a la
   * vez. `_assertInPartition` se daba por satisfecha al encontrar UN proyecto
   * permitido, así que una tarea que estuviera a la vez en un proyecto de este
   * contexto y en uno de otro pasaba el filtro. Y en el camino de búsqueda el
   * contexto no se deriva del objeto sino que se estampa desde la sesión, de
   * modo que esa tarea salía etiquetada con el contexto de la corrida sin que
   * ninguna capa la cuestionara.
   */
  function _foreignProjectGids(context) {
    var out = {};
    var names = Config.contextNames();
    for (var i = 0; i < names.length; i++) {
      if (names[i] === context) { continue; }
      var foreign = Config.partitionFor(names[i], 'ASANA');
      if (!foreign || !foreign.project_gids) { continue; }
      for (var j = 0; j < foreign.project_gids.length; j++) {
        out[String(foreign.project_gids[j])] = names[i];
      }
    }
    return out;
  }

  /**
   * Pertenencia simultánea a proyectos de contextos incompatibles: se RECHAZA,
   * no se resuelve a favor del contexto de la corrida.
   *
   * La regla se aplica sobre las tareas que van a ADMITIRSE, no sobre todas las
   * del proyecto. Una tarea puente que no casa con la consulta no se devuelve,
   * así que no hay fuga que impedir; y abortar la sonda entera por su presencia
   * confundiría dos problemas distintos: que la partición contenga un puente, y
   * que esta lectura lo haya dejado pasar. Esta capa responde del segundo.
   */
  function _assertNoForeignMembership(task, options) {
    var context = (options && options.context) ? options.context : null;
    if (!context) { return true; }
    var foreign = _foreignProjectGids(context);
    var projects = task.projects || [];
    for (var i = 0; i < projects.length; i++) {
      var gid = String(projects[i].gid);
      if (foreign[gid]) {
        throw Errors.partitionViolation(
          task.gid ? String(task.gid) : 'desconocido', context);
      }
    }
    return true;
  }

  /**
   * El contexto del resultado es el CONTEXTO CANÓNICO YA RESUELTO de la corrida,
   * no una derivación del nombre del proyecto.
   *
   * Derivarlo del nombre era un defecto real: un proyecto llamado
   * "Metis — Sistema Operativo" se normalizaba a algo que no coincide con la
   * clave de contexto `METIS`, y el filtro de contaminación del ToolBroker
   * descartaba en silencio una tarea perfectamente legítima. La pertenencia ya
   * está demostrada antes de llegar aquí: la tarea vino de un proyecto declarado
   * en la partición del contexto, o `_assertInPartition` la rechazó.
   */
  function _normalizeTask(task, snippet, context) {
    return {
      source: 'ASANA',
      id: task.gid ? String(task.gid) : null,
      title: task.name ? task.name : null,
      context: context === undefined ? null : context,
      kind: 'TASK',
      epistemic_status: RetrievalPolicy.epistemicFor('TASK'),
      snippet: snippet === undefined ? null : snippet,
      url: task.permalink_url ? task.permalink_url : null,
      completed: task.completed === true,
      due_on: task.due_on ? task.due_on : null
    };
  }

  function backend() { return _backend ? _backend : _realBackend(); }

  function search(query, options) { return backend().search(query, options || {}); }
  function get(gid, options) { return backend().get(gid, options || {}); }

  return {
    MAX_PAGES: MAX_PAGES,
    collectPages: collectPages,
    useBackend: useBackend,
    resetBackend: resetBackend,
    // Expuesta para que el backend de Nivel 0 use la MISMA normalización que la
    // real: si el fixture normalizara por su cuenta, el test no probaría nada.
    normalizeTask: _normalizeTask,
    // Expuestas por el mismo motivo que `normalizeTask`: los tests inyectan un
    // backend de fixtures que reemplaza el backend real entero, así que una
    // guarda que sólo viva dentro del backend real no quedaría cubierta.
    foreignProjectGids: _foreignProjectGids,
    assertNoForeignMembership: _assertNoForeignMembership,
    search: search,
    get: get
  };
})();
