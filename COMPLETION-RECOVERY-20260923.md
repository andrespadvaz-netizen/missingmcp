## Follow-up: full 42-slide acceptance exposed an all-stage completion defect

The real run resolved its context and submitted the full artifact, but the auditor returned a length stop and was nevertheless treated as finished. Reconciliation then returned a length stop with no visible text. The run failed, cost was known, and no business writes occurred. Operational DoD remains OPEN.

The candidate now validates completion in producer and auditor cycles as well as reconciliation, rejects truncated tool-bearing responses before any tool executes, and preserves stage-level usage, stop reasons and provider request IDs. A nonempty length stop can continue with an exact seam. An empty length stop can restart once with twice the observed output allowance (maximum 16,384), without changing provider/model/effort or original scope. Every recovery is tool-free, bounded by the existing continuation/intervention counts, and preflighted against run/day/month spend using a conservative input-byte envelope plus output allowance. This preflight is not a distributed reservation and is not a new guarantee for ordinary provider calls, which retain their existing post-call budget enforcement.

Unknown stops, unknown usage/prices, repeated empty output, invalid seams and budget exhaustion fail explicitly. No budget ceiling is increased. The full case still requires successful production acceptance; synthetic tests do not close it.

Sources consulted: https://developers.openai.com/api/docs/guides/reasoning and https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost (23 September 2026). Both describe output allowances shared with reasoning and possible cutoff before visible text; the specific failed run's hidden reasoning consumption was not observed.

Validation:176 engine cases/941 assertions;19 terminal/recovery tests;26 productive tests;20 bridge tests;59 targeted Python tests;9 static guards passed locally.
