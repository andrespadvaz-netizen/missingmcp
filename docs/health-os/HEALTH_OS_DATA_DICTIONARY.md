# Health OS: diccionario de datos

| Campo | Regla |
|---|---|
| `metric` | `heart_rate`, `hrv`, `sleep_duration`, `active_energy`, `distance`, `weight`/`body_mass` o `workout_duration` |
| `value` y `unit` | se normaliza a bpm, ms, min, kcal, km o kg según métrica |
| `observed_at` | ISO-8601 con offset; el día se calcula en la zona del operador |
| `source` | exclusivamente `APPLE_HEALTH` o `SPEEDIANCE` en ingestión nueva |
| `provenance_id` | ID original; participa en idempotencia |
| `semantic` | `measured`, `estimated`, `manual` o `derived` |
| `confidence` | opcional; se conserva sin inventar certeza |

Un `WorkoutSession` conserva ID, intervalo, actividad, origen, ejercicios y
métricas. Apple Health y Speediance se enlazan sólo con mismo tipo de actividad
y cinco minutos o más de solapamiento; ambos registros brutos se conservan.
