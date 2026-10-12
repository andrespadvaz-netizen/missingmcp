/**
 * Daily Health OS snapshot for the Metis Daily Briefing (Política Operativa v0.5, section 7).
 *
 * The Brief is a Claude scheduled task that cannot call Apps Script and must
 * never hold a Health OS key.  This writer runs on a daily time trigger inside
 * the runtime (which holds only the read key) and leaves one private JSON file
 * in the owner's Drive.  The Brief reads that file through the Drive connector.
 *
 * Two blocks with separate allowlists, exactly as section 7 requires:
 *   correo     state, quality, coverage (day counts), what is missing, legend.
 *   vista_app  weekly aggregates only (HealthOSTrendsAdapter allowlist).
 * Never single readings, timestamps of readings, provenance or credentials.
 *
 * The file is created by this script, so the narrow drive.file scope is enough.
 * Its id is kept in Script Properties; sharing is never changed (private).
 */
var HealthOSSnapshotWriter = (function () {
  var FILE_ID_PROPERTY = 'METIS_HEALTH_OS_SNAPSHOT_FILE_ID';
  var FILE_NAME = 'Metis_HealthOS_Snapshot_Diario.json';
  var SCHEMA = 'metis-health-os-snapshot/v1';
  var POLICY = 'Política Operativa de Health OS 0.5, aprobada por el titular el 2026-10-11';
  var TRIGGER_HANDLER = 'writeHealthOSDailySnapshot';
  var COVERAGE_METRICS = ['sleep_duration', 'resting_heart_rate'];

  function coverage(value) {
    if (Object.prototype.toString.call(value) !== '[object Array]') { return []; }
    return value.filter(function (row) {
      return row && COVERAGE_METRICS.indexOf(row.metric) >= 0 &&
        typeof row.valid_days === 'number' && typeof row.required_days === 'number';
    }).map(function (row) {
      return { metric: row.metric, valid_days: row.valid_days, required_days: row.required_days };
    });
  }

  function emailBlock(brief) {
    if (!brief || brief.available !== true || !brief.snapshot) {
      return { available: false, estado: null, motivo: (brief && brief.reason) || 'Health OS no respondió',
        calidad: null, cobertura: [], faltante: [], instruccion: null,
        leyenda: (brief && brief.legend) || null };
    }
    var s = brief.snapshot;
    return {
      available: true,
      estado: s.decision,
      motivo: null,
      calidad: { pass: s.quality.pass === true, stale: s.quality.stale === true },
      cobertura: coverage(s.coverage),
      faltante: s.missing || [],
      instruccion: s.decision === 'URGENTE' ? s.instruction : null,
      leyenda: brief.legend
    };
  }

  function build(now) {
    var brief = HealthOSBriefAdapter.read([]);
    var trends = HealthOSTrendsAdapter.read();
    return {
      schema: SCHEMA,
      generated_at: Utilities.formatDate(now, 'America/Mexico_City', "yyyy-MM-dd'T'HH:mm:ssXXX"),
      politica: POLICY,
      correo: emailBlock(brief),
      vista_app: { available: trends.available === true, motivo: trends.reason || null, semanas: trends.weeks || [] },
      nota: 'Sin síntomas autorreportados: esta corrida es automática y no puede activar URGENTE. ' +
        'Ante cualquier señal de alarma, llama al 911 sin esperar al sistema.'
    };
  }

  function file() {
    var props = PropertiesService.getScriptProperties();
    var id = props.getProperty(FILE_ID_PROPERTY);
    if (id) {
      try { return DriveApp.getFileById(id); } catch (error) { /* deleted: recreate below */ }
    }
    var created = DriveApp.createFile(FILE_NAME, '{}', MimeType.PLAIN_TEXT);
    props.setProperty(FILE_ID_PROPERTY, created.getId());
    return created;
  }

  function write() {
    var snapshot = build(new Date());
    var target = file();
    target.setContent(JSON.stringify(snapshot, null, 2));
    // Receipt without health values: safe to show in an execution log.
    var receipt = {
      file_id: target.getId(), generated_at: snapshot.generated_at,
      correo_available: snapshot.correo.available, estado: snapshot.correo.estado,
      semanas: snapshot.vista_app.semanas.length,
      legend_present: typeof snapshot.correo.leyenda === 'string' && snapshot.correo.leyenda.indexOf('911') >= 0
    };
    console.log(JSON.stringify(receipt));
    return receipt;
  }

  function installTrigger() {
    ScriptApp.getProjectTriggers().forEach(function (t) {
      if (t.getHandlerFunction() === TRIGGER_HANDLER) { ScriptApp.deleteTrigger(t); }
    });
    // 05:00–06:00 America/Mexico_City (manifest time zone): before the 07:05 Brief.
    ScriptApp.newTrigger(TRIGGER_HANDLER).timeBased().everyDays(1).atHour(5).create();
    return { installed: true, handler: TRIGGER_HANDLER, window: '05:00-06:00 America/Mexico_City' };
  }

  return { write: write, installTrigger: installTrigger };
})();

/** Daily time-trigger entry point. */
function writeHealthOSDailySnapshot() { return HealthOSSnapshotWriter.write(); }

/** Run once by the operator after deploying; idempotent (replaces its own trigger). */
function installHealthOSSnapshotTrigger() { return HealthOSSnapshotWriter.installTrigger(); }
