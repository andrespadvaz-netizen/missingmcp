# Long-running acceptance: incident and required correction

Status: acceptance remains OPEN. This document is a design requirement, not an implemented continuation mechanism.

## Observed

- Engine 15 / bridge 19 deployed with 16,384 output tokens per call and the authorized USD 2 run ceiling. Daily/monthly ceilings unchanged.
- The full 42-slide request was accepted through Claude, with context carried from the existing conversation.
- Apps Script terminated doPost after 368.99 seconds. Gateway reported ENGINE_INTERRUPTED, unknown total cost, and paused further spend.
- A persisted monthly budget counter contained `NaN`. Existing numeric comparisons did not reject it. The candidate now rejects invalid counters before a provider call and validates both counters before either update. It preserves the corrupt value for accounting review.
- The daily counter contains partial accounted spend; this is NOT proof of the interrupted run's total cost or a substitute for provider receipts.

Google documents a six-minute execution limit: https://developers.google.com/apps-script/guides/services/quotas

## Required execution design

1. Preserve a single immutable execution ID, request fingerprint, context, policy/version binding and cost ceiling across continuations. Preserve the complete request; do not split the business audit into smaller scopes.
2. Persist a state machine at model-turn boundaries: producer turns, auditor turns, reconciliation and output validation. Each bridge invocation may initiate at most one paid model call. A checkpoint must carry session evidence, tool outcomes, exact prompts, counters, handoff state and partial completion state.
3. Journal a call intent before dispatch and its provider request ID, usage, normalized response and accounting receipt immediately after receipt. A crash in the dispatch/result window stays uncertain and must not trigger another paid call automatically.
4. Commit result and accounting idempotently. Returning or resuming a committed step must not add spend again. Handle interrupted journal writes, concurrent claims and expired leases explicitly.
5. Keep public get_execution read-only. The existing worker advances durable pending steps through an authenticated bridge action; the client continues polling the same ID.
6. Do not replay completed reads/writes to reconstruct state. Persist tool outcomes and retain existing context/authority checks, handoff replay protection, total intervention caps and productive write validation.
7. A single model call can itself exceed the platform limit. Persist pre-dispatch uncertainty and test this case. If provider receipts demonstrate that risk for this workload, move long provider execution to the existing Gateway worker with explicit credential/access review, rather than claiming that turn splitting solves it.
8. Keep the last committed result on interruption, with accurate stage and known/unknown cost. Do not label a partial producer or auditor complete.

## Required evidence before unpausing

- Provider usage for the interrupted window, attributable to this execution; reconcile the partial ledger and monthly counter without resetting either to zero.
- Crash tests before dispatch, after provider response, during accounting, between turns and during final result publication; no duplicate calls or charges.
- Tests for context and policy mismatch, corrupt checkpoints, handoff expiry/replay, recovery limits, concurrent claims and both budget periods.
- Repeat the same complete frozen request through Claude, obtain complete independent audit and reconciliation, and return the actual final answer plus receipt.

The local budget-counter correction alone does not resolve the execution timeout and does not close DoD.
