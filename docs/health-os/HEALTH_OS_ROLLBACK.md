# Rollback

The core is additive. To roll back an Apple producer, disable only its ingress
credential or adapter; preserve already accepted observations and their source
metadata. Do not delete Garmin history, Apple data, Speediance data, or linked
workouts. Re-enable the prior read-only historical view without reactivating the
legacy Garmin bridge.

Before a deployment, export the persistent store snapshot and record the
producer version. A rollback is proven by replaying the test fixtures and
confirming that duplicate events remain idempotent.
