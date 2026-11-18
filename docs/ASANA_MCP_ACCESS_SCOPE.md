# Exact access request: Asana External MCP → Metis Gateway

This document defines the access Andrés approved. The document itself does
not create a credential, OAuth grant, AI Teammate, or production deployment.

## Parties and resources

- **Asana organization:** Venture Quest, workspace GID `1211117380475097`.
- **Initial connecting person:** Andrés Padilla Vázquez, Asana user GID
  `1211117380475084`. Any other person needs a separate Gateway identity and
  grant; there is no shared operator account.
- **OAuth client:** Asana External MCP, confidential authorization-code client,
  exact redirect URI `https://app.asana.com/-/integrations/oauth/callback`.
  Client ID and secret are unique to Asana, generated and stored as deployment
  secrets, and never copied into a task, comment, source file or chat.
- **MCP resource:** a new `/metis-asana/mcp` endpoint, separate from `/metis/mcp`
  and `/metis-chatgpt/mcp`. Tokens are bound to this resource, the Asana client,
  and an individual Gateway account. Existing tokens cannot call it.
- **Authorization and token URLs:** `/metis-asana/oauth/authorize` and
  `/metis-asana/oauth/token` on the Gateway's verified production origin. The
  final HTTPS origin must be checked before saving the Asana app, because Asana
  does not let admins edit connection settings after saving.
- **Duration:** 30-day access tokens, no refresh token initially. Expiry or
  revocation requires a new interactive authorization. Revocation deletes that
  account's Asana-resource tokens without affecting other Gateway adapters.

## Permissions and data flow

- **Tool exposed:** `metis_cross_audit` only. It sends the text and evidence
  explicitly supplied in the tool call to the configured model API and returns
  a terminal answer plus an encrypted receipt. It cannot read private Dot
  conversations, browse Gateway account data, create executions, or write to
  Asana, Drive, Gmail, Calendar or Metis projects.
- **Contexts:** the initial Andrés account may request `SHOKKO`,
  `FINAL_FINAL`, and `VENTURE_QUEST`. Each call declares exactly one of those
  IDs. The Gateway rejects any other ID before a model call. No context is
  inferred from natural-language text. If an AI Teammate should be restricted
  to only one context despite Andrés having access to all three, it requires a
  separate OAuth resource/client per context; Asana applies a person's app
  connection across eligible AI Teammates and Dash.
- **Source data:** only text explicitly sent in a call. Project labels enforce
  routing but cannot prove that prose contains no material from another
  project. The invoking person's Asana permissions and the task audience remain
  separate controls. This endpoint has no automatic access to Metis memory.
- **Receipts:** account, context, status, model, cost, timestamps and provider
  request ID are recorded. Request and answer text remain encrypted. No secret
  or content is logged or sent to general analytics.
- **Retries:** every model call requires a caller-supplied idempotency key. A
  durable reservation is written before dispatch. Repeated keys return the
  completed receipt or an `outcome_uncertain` state; they never silently make
  another paid model request. Any uncertain outcome needs operator
  reconciliation.
- **Authorized spending guard:** two Asana audits total for this account,
  including the failed 401 attempt and at most one new call. Each call has a
  USD 0.10 reservation ceiling; the cumulative ceiling is USD 0.20 across the
  lifetime of the durable Gateway database. The forwarded request is limited
  to 8,192 UTF-8 bytes and GPT-5 output to 4,096 tokens. Before dispatch, an
  SQLite transaction reserves a conservative token-cost ceiling below USD
  0.10 per call. A failed or uncertain call keeps its reservation. The guard
  rejects a different configured model and does not reset on restart. A third
  call requires an explicit later change to this budget policy. The Asana AI
  request allowance is billed separately and must be checked in Asana.

## OAuth compatibility and risk

The live Asana External MCP OAuth request included PKCE S256. All Gateway
clients, including the pinned Asana client, now require PKCE S256 for new
authorization codes and token exchange. The previously approved conditional
no-PKCE fallback was not needed and has been removed. The fixed callback,
confidential client secret, one-use short-lived codes and per-person login
remain in place.

Saving and distributing the app lets an AI Teammate use this resource on behalf
of each person who authorizes it. Asana states that a person's app connection
can also be available to Dash and other current or future AI Teammates with the
app added. Asana controls alone therefore do not isolate a context; the Gateway
must enforce the allowlist.

## Approved scope

Andrés approved this exact grant: create the one Asana confidential OAuth
client and `/metis-asana/mcp` resource; connect Andrés's individual Gateway
identity; expose only the read-only cross-audit tool for the three named
contexts; store the secrets in the deployment secret manager; allow 30-day
access tokens with per-account revocation and no refresh; and use the
confidential-client no-PKCE exception only if Asana's client omits PKCE. The
live request established that Asana sends S256, so the exception is not used.
The approval does not authorize model writes or access for other people.
