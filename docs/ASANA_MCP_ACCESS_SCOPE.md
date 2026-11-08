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

## OAuth compatibility and risk

Existing Gateway clients require PKCE S256. Keep that rule unchanged. Asana's
public External MCP guide does not say whether its client sends PKCE. The new
resource should require PKCE when sent. If Asana omits it, accept a confidential
authorization-code flow **only for the pinned Asana client** with exact redirect
matching, a client secret at token exchange, CSRF state, one-use short-lived
codes, per-person login and rate limits. Do not grant a no-PKCE exception to
dynamic clients or existing resources. The remaining risk is interception of
an authorization code in the user's browser/redirect path; HTTPS, the exact
callback and confidential token exchange limit that risk.

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
approval does not authorize model writes or access for other people.
