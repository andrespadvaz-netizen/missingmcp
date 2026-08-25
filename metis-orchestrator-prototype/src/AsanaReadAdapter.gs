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
          var res = _request('/projects/' + encodeURIComponent(partition.project_gids[p]) +
            '/tasks?limit=100&opt_fields=' + FIELDS);
          var data = res.data || [];
          for (var i = 0; i < data.length && out.length < limit; i++) {
            var task = _normalizeTask(data[i], null, options ? options.context : null);
            if (!needle || ContextResolver.normalize(task.title || '').indexOf(needle) !== -1) {
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
      if (partition.project_gids.indexOf(String(projects[i].gid)) !== -1) { return true; }
    }
    throw Errors.partitionViolation(task.gid ? String(task.gid) : 'desconocido',
      (options && options.context) ? options.context : null);
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
    useBackend: useBackend,
    resetBackend: resetBackend,
    // Expuesta para que el backend de Nivel 0 use la MISMA normalización que la
    // real: si el fixture normalizara por su cuenta, el test no probaría nada.
    normalizeTask: _normalizeTask,
    search: search,
    get: get
  };
})();
