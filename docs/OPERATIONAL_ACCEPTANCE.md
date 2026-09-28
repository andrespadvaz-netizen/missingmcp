# Operational acceptance reopened — 2026-09-21

Candidate based on 8aabb521cc8eaca560ccd74193e3e2097131179c. The tag metis-orchestrator-baseline-v1.0 is not moved. No deployment or operational acceptance is asserted by this document.

## Reported incident and evidence limits

The operator reports two failed submissions from Claude: conversation context omitted from the first, and terminal reconciliation truncated after cross-audit in the second. The original 18-slide version was overwritten. The operator supplied its 42-slide successor, now privately preserved with SHA-256 as the new acceptance reference. Exact historical payloads and execution IDs remain unavailable; this is a new acceptance run, not an exact historical reproduction. Attribution to a specific client or deployed version remains provisional.

## Candidate changes

The advertised MCP input schema requires a context envelope, populated by the client from the current request, current conversation or visible project instructions. The gateway validates syntax, rejects disagreement with an explicit header, constructs the engine header and binds context/provenance to the encrypted request and idempotency fingerprint. Context is a client assertion, never new write authority; engine policy remains authoritative. Legacy payloads remain accepted for compatibility. Therefore the new tool schema must actually be refreshed and exercised in Claude: schema alone does not prove the client supplied correct context. No cross-conversation memory or inferred context from retrieved instructions is introduced.

Terminal reconciliation may continue after a known length stop, at most MAX_TERMINAL_CONTINUATIONS (default 2, allowed 0–3). Every continuation uses the same model, original request, producer and independent audit, via the existing budget/provenance/intervention gate and no tool contract. Existing global limits and monetary budgets are unchanged. If no intervention remains, the run fails closed. Unknown incomplete reasons, refusals, missing text and unexpected tools fail without recovery. OpenAI incomplete with explicit max_output_tokens reason is normalized as a length stop; other incomplete reasons remain nonrecoverable.

Each continuation must repeat the exact last 160 characters of the accumulated text before appending. Only that exact prefix is removed; mismatch, no progress, exhausted allowance or assembly above 100,000 characters fails closed. Stop reasons, request IDs and lengths are exposed in terminal_completion, without partial contents. This checks the text seam, not semantic correctness; real-client acceptance and independent review remain required. The previous arbitrary 450-word instruction is removed; the case must not be reduced to fit it.

## Bounded transport for the supplied artifact

The supplied deck contains 42 slides and five charts. Its slide text alone is 51,124 UTF-8 bytes, exceeding the former 16,000-byte ingress cap. The candidate now permits 128,000 UTF-8 bytes including the constructed context header, and 1 MiB JSON envelopes at the gateway and bridge. JSON Schema maxLength is a character bound; the server additionally enforces bytes. Unicode and escaped control-character boundary tests cover signed transport. Oversize requests are rejected before dispatch without truncation; signatures, encryption, idempotency, spend limits and write permissions are unchanged. Continuation telemetry is retained in the bridge result.

This does not add native PowerPoint ingestion or establish visual completeness. Private extraction currently preserves slide text, chart caches and cover/closing image labels. Table relationships and visual evidence must still be checked before real acceptance. The client must transmit the full relevant artifact, never substitute a short summary or claim a text-only audit covers unseen graphics.

## Required real acceptance

1. Preserve the supplied full deck and privately record its hash/version and all 42 slides (done); validate extraction coverage before submission.
2. In Claude, establish the project in an earlier turn and then request the complete audit without repeating the project.
3. Capture the actual tool arguments, engine context and original request to prove continuity.
4. Prove the full artifact enters the audit; no manual scope reduction.
5. Prove CROSS_AUDIT with provider receipts and complete terminal reconciliation.
6. Exercise length recovery or demonstrate bounded fail-closed with the configured policy; retain costs and stop reasons.
7. Confirm the full answer returns to Claude, without the operator routing between models.
8. Obtain independent review and operator ratification before claiming operational DoD.

Local tests and a draft PR are not operational acceptance. No credentials, business deck content or private conversation URLs belong in this repository.
