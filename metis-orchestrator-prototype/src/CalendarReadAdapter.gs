/**
 * CalendarReadAdapter.gs — lectura REAL de sólo lectura de Google Calendar.
 *
 * Scope `calendar.readonly`. No existe función de creación, edición ni borrado
 * de eventos. Los calendarios legibles se declaran en Script Properties
 * (`METIS_CALENDAR_READ_IDS`, separados por coma); el calendario primario
 * productivo es superficie prohibida y no se enumera por default.
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

  function readableCalendarIds() {
    var raw = Config.setting('CALENDAR_READ_IDS');
    if (!raw) { return []; }
    return String(raw).split(',').map(function (s) { return s.trim(); }).filter(function (s) { return !!s; });
  }

  function _realBackend() {
    return {
      read: function (query, timeWindow) {
        _assertLevel();
        var ids = readableCalendarIds();
        if (!ids.length) { throw Errors.configError('Sin calendarios declarados en METIS_CALENDAR_READ_IDS'); }
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

  function read(query, timeWindow) { return backend().read(query, timeWindow); }

  return {
    useBackend: useBackend,
    resetBackend: resetBackend,
    readableCalendarIds: readableCalendarIds,
    read: read
  };
})();
