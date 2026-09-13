/**
 * CalendarReadAdapter.gs — lectura REAL de sólo lectura de Google Calendar.
 *
 * Scope `calendar.readonly`. No existe función de creación, edición ni borrado
 * de eventos. Los calendarios legibles se declaran POR CONTEXTO en la partición
 * de fuentes (`METIS_SOURCE_PARTITIONS`); el calendario primario productivo es
 * superficie prohibida y no se declara en ninguna partición.
 */
var CalendarReadAdapter = (function () {

  var _backend = null;

  function useBackend(backend) { _backend = backend; }
  function resetBackend() { _backend = null; }

  function _assertLevel() {
    if (!Config.levelAllowsRealReads()) {
      throw Errors.levelViolation('Lectura real de Calendar bloqueada en ' + Config.runLevel());
    }
  }

  /**
   * Los calendarios legibles ya no son una lista global: se declaran POR
   * CONTEXTO en la partición. Un calendario de otro contexto no es alcanzable
   * desde esta corrida, y el calendario primario productivo no se declara nunca.
   */
  function readableCalendarIds(options) {
    var partition = options && options.partition ? options.partition : null;
    if (!partition || !partition.calendar_ids || !partition.calendar_ids.length) {
      throw Errors.sourcePartitionUndeclared(
        (options && options.context) ? options.context : 'DESCONOCIDO', 'CALENDAR');
    }
    return partition.calendar_ids.slice();
  }

  function _realBackend() {
    return {
      read: function (query, timeWindow, options) {
        _assertLevel();
        var ids = readableCalendarIds(options);
        var start = new Date(timeWindow.start);
        var end = new Date(timeWindow.end);
        var out = [];
        for (var i = 0; i < ids.length; i++) {
          var cal = CalendarApp.getCalendarById(ids[i]);
          if (!cal) { continue; }
          var events = cal.getEvents(start, end);
          for (var e = 0; e < events.length; e++) {
            var title = events[e].getTitle();
            if (query && ContextResolver.normalize(title).indexOf(ContextResolver.normalize(query)) === -1) {
              continue;
            }
            out.push(_normalizeEvent(events[e], ids[i]));
          }
        }
        return out;
      }
    };
  }

  function _normalizeEvent(event, calendarId) {
    return {
      source: 'CALENDAR',
      id: event.getId(),
      title: event.getTitle(),
      context: null,
      kind: 'EVENT',
      epistemic_status: RetrievalPolicy.epistemicFor('EVENT'),
      snippet: null,
      calendar_id: calendarId,
      starts_at: event.getStartTime().toISOString(),
      ends_at: event.getEndTime().toISOString()
    };
  }

  function backend() { return _backend ? _backend : _realBackend(); }

  function read(query, timeWindow, options) { return backend().read(query, timeWindow, options || {}); }

  return {
    useBackend: useBackend,
    resetBackend: resetBackend,
    readableCalendarIds: readableCalendarIds,
    read: read
  };
})();
