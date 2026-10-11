# Health OS architecture

The Health OS runtime is independent of Garmin. ChatGPT Health is the primary
human-facing Apple Health query surface, but its narrative output is not a
canonical data transport. The runtime accepts authenticated normalized raw
records only from an authorized Apple Health adapter and from Speediance.

```
Apple Watch → Apple Health → authorized raw-data adapter → Health OS core
                              ↑
                    ChatGPT Health (query/explanation only)
Speediance → authorized producer ───────────────────┘
```

Apple Health owns physiological readings and ordinary activity. Speediance owns
strength detail. A five-minute temporal overlap plus matching activity type is
required before an Apple strength workout and a Speediance session become one
logical workout. They remain separate raw records with their own provenance.

The core emits only ACTIVA, AJUSTA, DETEN, ABSTENERSE or ESCALAR with factors,
confidence and missing evidence. It never diagnoses, prescribes, changes
medication, alters a calendar, or modifies a training plan.

## Integration boundary

Apple Health has no server-side API. A future iPhone HealthKit producer must
authenticate to the chosen ingestion endpoint and submit only data the user
authorized. Until that producer is connected, no record may be represented as
real Apple Health data.
