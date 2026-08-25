/**
 * DriveReadAdapter.gs — lectura REAL de sólo lectura de Google Drive.
 *
 * Usa la identidad ya autorizada del script con el scope `drive.readonly`
 * declarado en `appsscript.json`. No existe función de creación, edición,
 * movimiento ni borrado de archivos.
 */
var DriveReadAdapter = (function () {

  var MAX_TEXT_BYTES = 20000;

  var _backend = null;

  function useBackend(backend) { _backend = backend; }
  function resetBackend() { _backend = null; }

  function _assertLevel() {
    if (!Config.levelAllowsRealReads()) {
      throw Errors.levelViolation('Lectura real de Drive bloqueada en ' + Config.runLevel());
    }
  }

  function _realBackend() {
    return {
      search: function (query, options) {
        _assertLevel();
        var limit = (options && options.page_size) ? options.page_size : 10;
        // `searchFiles` con sintaxis de la API de Drive; sólo lectura.
        var escaped = String(query || '').replace(/'/g, "\\'");
        var iterator = DriveApp.searchFiles("title contains '" + escaped + "' and trashed = false");
        var out = [];
        while (iterator.hasNext() && out.length < limit) {
          out.push(_normalizeFile(iterator.next(), null));
        }
        return out;
      },
      fetch: function (id) {
        _assertLevel();
        var file = DriveApp.getFileById(id);
        var text = null;
        var mime = file.getMimeType();
        if (mime === 'text/plain' || mime === 'text/markdown' || mime === 'application/json') {
          text = file.getBlob().getDataAsString().slice(0, MAX_TEXT_BYTES);
        }
        return _normalizeFile(file, text);
      }
    };
  }

  function _normalizeFile(file, snippet) {
    return {
      source: 'DRIVE',
      id: file.getId(),
      title: file.getName(),
      context: null,
      kind: 'FILE',
      epistemic_status: RetrievalPolicy.epistemicFor('FILE'),
      snippet: snippet === undefined ? null : snippet,
      url: file.getUrl ? file.getUrl() : null
    };
  }

  function backend() { return _backend ? _backend : _realBackend(); }

  function search(query, options) { return backend().search(query, options || {}); }
  function fetch(id) { return backend().fetch(id); }

  return {
    useBackend: useBackend,
    resetBackend: resetBackend,
    search: search,
    fetch: fetch
  };
})();
