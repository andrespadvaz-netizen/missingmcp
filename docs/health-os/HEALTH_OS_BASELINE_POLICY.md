# Baseline eras

Historical Garmin data remains readable as `ERA_GARMIN`. The first Apple Health
record starts `ERA_APPLE_TRANSITION`. Apple-derived analyses move to
`ERA_APPLE_BASELINE` after fourteen distinct local-calendar days of Apple Health
coverage; thirty days is the target for a stable baseline. The system never
silently compares Garmin and Apple values as one continuous series when their
algorithms or semantics differ.

Local dates use the operator's configured IANA timezone. Missing data remains
missing; it is never converted to zero.
