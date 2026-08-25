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

  function _realBackend() {
    return {
      search: function (query, options) {
        _assertLevel();
        var workspace = Config.setting('ASANA_WORKSPACE_GID');
        if (!workspace) { throw Errors.configError('Falta ASANA_WORKSPACE_GID en Script Properties'); }
        var path = '/workspaces/' + encodeURIComponent(workspace) +
                   '/typeahead?resource_type=task&count=' + ((options && options.page_size) ? options.page_size : 10) +
                   '&query=' + encodeURIComponent(String(query || ''));
        var res = _request(path);
        var out = [];
        var data = res.data || [];
        for (var i = 0; i < data.length; i++) {
          out.push(_normalizeTask(data[i], null));
        }
        return out;
      },
      get: function (gid) {
        _assertLevel();
        var res = _request('/tasks/' + encodeURIComponent(gid) +
          '?opt_fields=name,notes,completed,due_on,permalink_url,projects.name');
        return _normalizeTask(res.data || {}, (res.data && res.data.notes) ? res.data.notes : null);
      }
    };
  }

  function _normalizeTask(task, snippet) {
    var projects = task.projects || [];
    var projectName = projects.length ? projects[0].name : null;
    return {
      source: 'ASANA',
      id: task.gid ? String(task.gid) : null,
      title: task.name ? task.name : null,
      context: projectName ? ContextResolver.normalize(projectName).toUpperCase().replace(/[^A-Z_]/g, '_') : null,
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
  function get(gid) { return backend().get(gid); }

  return {
    useBackend: useBackend,
    resetBackend: resetBackend,
    search: search,
    get: get
  };
})();
