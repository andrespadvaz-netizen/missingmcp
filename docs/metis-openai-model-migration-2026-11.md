# Metis Gateway: OpenAI model migration candidate (2026-11-11)

## Scope and decision

This candidate is based on remote `codex/asana-ai-teammates-coordination`
at `e7763be08a4ccdbc1c132ebdbad10360260c288f`. The Gateway direct audit
uses `POST /v1/responses`, free text, `reasoning.effort=low`, and a maximum of
8,192 output tokens (4,096 for the Asana probe). It does not use Chat
Completions, temperature, top-p, tools, or Structured Outputs on this path.

The candidate replacement is `gpt-6.1-sol`: Responses and low reasoning are
documented as supported, its published Standard output rate matches GPT-5,
and its published input rate is lower than OpenAI's listed replacement
`gpt-5.6-sol`. This is a cost and API compatibility choice, **not** a verified
quality equivalence. GPT-5 and its 2025-08-07 snapshot are scheduled for API
shutdown on 2026-12-11. Do not use either for rollback after that date.

## Costs and controls

Published Standard USD per million text tokens (input / cached read / output):

| Model | Input | Cached read | Output | 12,000 input + 8,192 output, uncached |
| --- | ---: | ---: | ---: | ---: |
| GPT-5 | 1.25 | 0.125 | 10 | $0.09692 |
| GPT-5.6 Sol | 4 | 0.40 | 20 | $0.21184 |
| GPT-6.1 Sol | 2 | 0.10 | 10 | $0.10592 |

GPT-5.6 Sol's listed price is promotional through 2026-11-30, so its later
price must be checked before any rollout. GPT-6.1 Sol has a $2.50/M default
30-minute cache write rate. The direct audit selects explicit cache mode with
no breakpoints, which the prompt-caching documentation says avoids cache
writes. It does not request Fast, regional, or long-context processing. The
Asana probe caps request text at 8,192 UTF-8 bytes, output at 4,096 tokens,
and uses a conservative 1,024-token framing allowance. Its worst-case
reservation for 8,192 ASCII bytes in VENTURE_QUEST is $0.060726, below the
$0.065 per-call ceiling. **Deployment approval must explicitly cover raising
the per-call reserve ceiling from $0.055 to $0.065.** The monthly reserved
ceiling stays $1.10 and the
separate 20-call limit stays in place. Because each call reserves its maximum
rather than actual usage, the monthly ceiling may stop the probe before 20
calls (at most 18 worst-case calls at $0.060726 each). Reservations are not
refunded after uncertain dispatches.

The direct audit's `cost_usd` uses the published uncached Standard rate and
requires the returned model to match `gpt-6.1-sol`; a different returned model
has unknown cost. The model page lists this alias without a dated snapshot;
the canary must confirm the actual `response.model` value. This calculation
is an estimate, not provider billing.
Availability, actual invoice treatment, response quality, and any account
specific processing uplift have not been verified. The unrestricted Metis
direct-audit route has an output cap and timeout but no new monetary budget in
this patch; review its existing Gateway controls before enabling broader use.

Sources: [deprecations](https://developers.openai.com/api/docs/deprecations#2026-06-11-gpt-5-and-o3-model-deprecations),
[pricing](https://developers.openai.com/api/docs/pricing),
[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
[prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).

## Other reference

`metis-orchestrator-prototype/src/Config.gs` also defaults to GPT-5. Its
README calls the Apps Script prototype unactivated. Publication of that
version was not established here. Before activating it, update its model,
review `METIS_PRICING` for the **actual** model ID, mock its Responses tool
calling and terminal statuses, and test its budget guards independently.
No Apps Script project or Script Property is changed by this patch.

## Validation and rollout

1. Run `PYTHONPATH=src python -m pytest tests/test_metis.py tests/test_metis_asana.py -q`
   in an isolated checkout. Mock tests cover request body, completed and
   incomplete responses, refusal, wrong returned model, HTTP 429, and probe
   budgets. Run the full suite and deployment gates before release.
2. Verify that deployment has no `METIS_OPENAI_MODEL` override. Any value
   other than `gpt-6.1-sol` now fails audit preflight before dispatch. Recheck official
   price and model availability at rollout time, without exposing key values.
3. With separate authorization for paid validation, run a small bounded
   canary on representative audit examples. Compare terminal status, refusal,
   visible answer quality, usage, latency, and provider invoice against the
   GPT-5 baseline. Set a maximum sample count and cost first. Do not infer
   quality from these mock tests.
4. Prepare a draft PR from an isolated branch, review it, and deploy via a
   controlled release. The currently deployed Railway service follows the
   remote `codex/asana-ai-teammates-coordination` branch; pushing that branch
   can trigger a deployment without CI. Do not push this candidate there.
5. Watch failed and incomplete audits, `cost_known=false`, output-token
   exhaustion, latency, and monthly reserve exhaustion after rollout.

If the canary fails before 2026-12-11, revert the application release to the
previous known-good artifact and check the configured model. After that date,
do **not** restore GPT-5: pause OpenAI-backed audits until another supported,
priced model has passed the same checks. Restoring a model string alone cannot
undo an already dispatched charge or a durable reservation.
