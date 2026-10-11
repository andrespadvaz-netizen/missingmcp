# Deduplication policy

An observation is idempotent by source, producer record ID, metric, and exact
timestamp. Replaying it produces no second observation.

An Apple Health workout and Speediance workout are linked only when both have
the same activity type, their starts are at most fifteen minutes apart, and the
overlap is at least half of the shorter session. Timestamp proximity alone is
insufficient. Linked sessions count once operationally while retaining both raw
records and their provenance.
