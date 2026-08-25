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

  function _partitionOf(options) {
    var partition = options && options.partition ? options.partition : null;
    if (!partition || !partition.folder_ids || !partition.folder_ids.length) {
      throw Errors.sourcePartitionUndeclared(
        (options && options.context) ? options.context : 'DESCONOCIDO', 'DRIVE');
    }
    return partition;
  }

  function _realBackend() {
    return {
      /** La búsqueda se acota a las carpetas declaradas para el contexto. */
      search: function (query, options) {
        _assertLevel();
        var partition = _partitionOf(options);
        var limit = (options && options.page_size) ? options.page_size : 10;
        var escaped = String(query || '').replace(/'/g, "\\'");
        var out = [];
        for (var f = 0; f < partition.folder_ids.length && out.length < limit; f++) {
          var criteria = "title contains '" + escaped + "' and trashed = false and '" +
            partition.folder_ids[f] + "' in parents";
          var iterator = DriveApp.searchFiles(criteria);
          while (iterator.hasNext() && out.length < limit) {
            out.push(_normalizeFile(iterator.next(), null));
          }
        }
        return out;
      },

      fetch: function (id, options) {
        _assertLevel();
        var partition = _partitionOf(options);
        var file = DriveApp.getFileById(id);
        _assertInPartition(file, partition, options);
        var text = null;
        var mime = file.getMimeType();
        if (mime === 'text/plain' || mime === 'text/markdown' || mime === 'application/json') {
          text = file.getBlob().getDataAsString().slice(0, MAX_TEXT_BYTES);
        }
        return _normalizeFile(file, text);
      }
    };
  }

  /** El archivo debe colgar de alguna de las carpetas declaradas. */
  function _assertInPartition(file, partition, options) {
    var parents = file.getParents();
    while (parents.hasNext()) {
      if (partition.folder_ids.indexOf(parents.next().getId()) !== -1) { return true; }
    }
    throw Errors.partitionViolation(file.getId(),
      (options && options.context) ? options.context : null);
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
  function fetch(id, options) { return backend().fetch(id, options || {}); }

  return {
    useBackend: useBackend,
    resetBackend: resetBackend,
    search: search,
    fetch: fetch
  };
})();
