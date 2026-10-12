# Health OS Brief Runtime (Apps Script)

Versioned source of the Apps Script project "Metis Health OS Brief Runtime".
Deploy with `clasp push` from a checkout that has its own `.clasp.json`
(the script id is intentionally not committed).

- Reads only `METIS_HEALTH_OS_READ_KEY` and `METIS_HEALTH_OS_BASE_URL` from Script Properties.
- Accepts `INFORMATIVO`, `REVISAR_TECNICO`, `ABSTENERSE` and `URGENTE` (Política Operativa v0.5).
- Always returns the fixed 911 legend, including on failure.
- `verifyHealthOSBriefRuntime()` throws unless the live endpoint returned a valid snapshot;
  its receipt carries no health values.
- `writeHealthOSDailySnapshot()` writes one private JSON file (`Metis_HealthOS_Snapshot_Diario.json`)
  with two allowlisted blocks: `correo` (state, quality, coverage day counts, legend) and `vista_app`
  (weekly aggregates). The Metis Daily Briefing reads it through the Drive connector; Claude never
  holds a Health OS key. Scope `drive.file`: the script only touches the file it created.
- `installHealthOSSnapshotTrigger()` installs the daily trigger (05:00–06:00 America/Mexico_City).
