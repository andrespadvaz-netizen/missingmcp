# Health OS Brief Runtime (Apps Script)

Versioned source of the Apps Script project "Metis Health OS Brief Runtime".
Deploy with `clasp push` from a checkout that has its own `.clasp.json`
(the script id is intentionally not committed).

- Reads only `METIS_HEALTH_OS_READ_KEY` and `METIS_HEALTH_OS_BASE_URL` from Script Properties.
- Accepts `INFORMATIVO`, `REVISAR_TECNICO`, `ABSTENERSE` and `URGENTE` (Política Operativa v0.5).
- Always returns the fixed 911 legend, including on failure.
- `verifyHealthOSBriefRuntime()` throws unless the live endpoint returned a valid snapshot;
  its receipt carries no health values.
