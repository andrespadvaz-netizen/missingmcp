# Baseline eras

Historical Garmin data remains readable as `ERA_GARMIN`. The first Apple Health
record starts `ERA_APPLE_TRANSITION`. Apple-derived analyses move to
`ERA_APPLE_BASELINE` after fourteen distinct local-calendar days of Apple Health
coverage; thirty days is the target for a stable baseline. The system never
silently compares Garmin and Apple values as one continuous series when their
algorithms or semantics differ.

## Métricas y cierre de octubre de 2026

La madurez de una métrica no se infiere de la cobertura global. Para el Brief,
una métrica requiere 28 días locales distintos. El 11-oct-2026 el operador
autorizó una extensión única y documentada de la ventana de sueño de 42 a 44
días: había 26 días dentro de los 42 y se incorporan sólo los dos días locales
inmediatamente anteriores. Esto madura un baseline histórico de sueño; no
configura umbrales médicos ni permite que el sistema emita `ACTIVA`.

Local dates use the operator's configured IANA timezone. Missing data remains
missing; it is never converted to zero.
