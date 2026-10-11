# Security review

- Health OS has no Garmin login, password, or worker dependency.
- Producers must authenticate outside the domain core; raw credentials, tokens,
  and secret-bearing error text must not enter observations, fixtures, logs, or
  prompts.
- Every record includes source and producer record ID; source labels cannot
  confer authority by themselves.
- `model_interpreted` data is explanation-only and cannot enter baselines,
  trends, or readiness decisions.
- Notion/Brief receives an operational minimum, not a raw medical-data mirror.
- Wearables do not write canonical sources directly. A policy-bearing ingestion
  adapter validates producer identity, units, timezone-aware timestamps, and
  idempotence before persistence.
- Medical guardrails are invariants: no diagnosis, prescription, medication
  change, or recommendation to ignore acute symptoms.
