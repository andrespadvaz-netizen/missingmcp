# Health OS: contrato del Brief diario

La sección Health OS presenta sólo una decisión conservadora y la evidencia que
la sustenta. No replica el almacén de datos ni realiza diagnóstico.

## Orden y reglas de emisión

1. **Integridad:** mostrar última sincronización, conflictos o faltantes; una
   línea cuando todo esté sano.
2. **Recuperación:** sueño, FC reposo y HRV únicamente si tienen cobertura
   suficiente para la métrica concreta.
3. **Carga y fuerza:** actividad, entrenamiento y sesión lógica Apple/
   Speediance, sin doble conteo.
4. **Cambio relevante:** composición, movilidad, nutrición histórica, ruido o
   mindfulness sólo si cambia la decisión.
5. **Estado:** `INFORMATIVO`, `REVISAR_TECNICO` o `ABSTENERSE`, con
   factores, confianza, limitación y dato faltante.

El Brief se abstiene si la sincronización está atrasada, hay conflicto de
fuentes, falta baseline de sueño/FC reposo o sólo existe información
`model_interpreted`. Las observaciones clínicas sensibles se describen como
hechos de fuente y se escalan; nunca reciben interpretación diagnóstica.

La implementación expone este contrato como `POST /health-os/brief`, protegido
por la clave dedicada de lectura. Esta es la única fuente técnica del bloque
Health del Brief diario: el protocolo en Notion debe consumir este snapshot y no
una segunda lógica ni narrativa de ChatGPT. Su respuesta no contiene mediciones
crudas.

## Política Operativa v0.5 (approved by the operator, 2026-10-12)

- Allowed decisions: `INFORMATIVO`, `REVISAR_TECNICO`, `ABSTENERSE`, `URGENTE`.
- `URGENTE` is emitted only when the operator self-reports a symptom from the closed, bilingual
  list in section 6.0 of the policy. Never from metrics, never from model inference. The snapshot
  then carries `instruction` (stop the activity and call 911). The system notifies no one.
- Any other self-reported symptom yields `ABSTENERSE`.
- Every snapshot carries the fixed `legend` with the 911 reference; the Apps Script reader adds it
  even when Health OS is unavailable.
- Without a recorded Apple era start the decision is `ABSTENERSE`; baselines count only evidence
  observed after that start.
- Model-interpreted values and ChatGPT Health transcriptions are rejected at ingestion; historical
  ones are isolated and never change the decision.
- A stored row that cannot be decrypted is quarantined: `quality.pass` is false and the decision is
  `REVISAR_TECNICO`. It never causes an HTTP 500 or a restart loop.
- `/healthz` reports the deployed short commit (`RAILWAY_GIT_COMMIT_SHA`) for credential-free
  deployment verification.

## Sanitation and weekly trends (2026-10-12)

- `GET /health-os/quarantine` (read key): invalid stored rows counted by table and fixed reason
  category, earliest local Apple day per metric and the recorded Apple era start. No values.
- `POST /health-os/quarantine/apply` (ingest key), body `{"confirm": "QUARANTINE", "expected": N}`:
  moves the N invalid rows, still encrypted, into `health_os_quarantine` in one transaction. Nothing
  is deleted; a stale count is refused. Rows the core used to drop silently now count as invalid.
- `GET /health-os/trends` (read key): weekly descriptive aggregates (sleep, resting heart rate, HRV,
  respiratory rate, SpO2, workouts and strength sessions) against a provisional 28-day baseline
  (needs 14 valid days). App view only; the e-mail Brief keeps state, coverage, quality and legend.
  Steps and energy are excluded: iPhone and Watch intervals overlap and would double count.
