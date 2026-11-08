# Asana AI Teammates and Metis Gateway

## Current implementation

`missingmcp.metis.asana_agents.AsanaAgents` supports agent discovery, agent
profiles, task reads, story reads, creation of a dedicated agent task linked to
an original human-owned task, and assignment of an existing dedicated task.
`scripts/asana_agents.py` exposes those operations
to an operator. The Asana API is in Early Access and requires
`Asana-Enable: ai_teammate_actors`. Agent GIDs are only used as assignees on
task create/update, as documented by Asana.

Configure the operator process with `ASANA_ACCESS_TOKEN`,
`ASANA_WORKSPACE_GID`, `ASANA_DISPATCH_LEDGER` (a path on a durable volume),
and `ASANA_AGENT_PROJECTS_JSON`, a JSON object mapping
allowed project GIDs to exact Metis context IDs. Keep the token in a secret
manager, not this repository or shell history. Example configuration using
placeholders:

```json
{"<shokko-project-gid>": "SHOKKO", "<final-final-project-gid>": "FINAL_FINAL"}
```

Invoke `uv run python scripts/asana_agents.py discover`; use `agent --agent-gid
<gid>`, `task --task-gid <gid>`, `stories --task-gid <gid>`, or `assign
--task-gid <dedicated-task-gid> --agent-gid <gid> --project-gid <gid>`.
To create a dedicated task, use `create --source-task-gid <gid>
--project-gid <gid> --agent-gid <gid> --title <title> --request <brief>
--idempotency-key <stable-key>`. The CLI cannot create without a durable ledger.
Keep the same key for an identical request. If creation is uncertain, reconcile
it manually before choosing a new key; retries never create a second task.
Assignment requires an unassigned task in an allowed project. It never moves
or overwrites a human-owned original. After assignment it verifies the task.
If transport fails during the write, it reads the task and only reports success
if the requested assignee appears; it does not repeat the PUT blindly.

Stories include stable GIDs and timestamps for de-duplication. A story or task
completion is evidence of Asana activity, not proof that the agent's answer is
correct. The caller must review the output and attachments before treating the
work as complete. Task descriptions and comments are visible to the task's
audience, so the operator should check the audience before placing sensitive
context in a task.

## Direct Gateway connection

Asana External MCP requires a registered confidential OAuth client and explicit
authorization and token URLs. Its callback is
`https://app.asana.com/-/integrations/oauth/callback`. The current Metis MCP
resource uses dynamic client registration, mandatory PKCE S256, and a shared
operator identity. It must not be handed to Asana as-is. A 404 from the current
production path means the route is absent at that deployment; it is not evidence
that historical records were deleted.

The `metis-asana` resource has its own pinned confidential OAuth client and
an individual Gateway identity for Andrés. It exposes only
`metis_cross_audit` for `SHOKKO`, `FINAL_FINAL`, and `VENTURE_QUEST`. It does
not expose the operator key, private Dot conversations, or Metis writes.
Every audit needs an idempotency key. The Gateway reserves that key before the
provider call; uncertain calls are not automatically repeated. Receipts are
encrypted. The initial production check reserves one call for Andrés for the
lifetime of the durable database. It accepts at most 8,192 UTF-8 bytes of
forwarded request text and caps GPT-5 output, including reasoning, at 4,096
tokens. A conservative reservation is made in the same transaction as the
idempotency record before dispatch. Its maximum is below USD 0.10 at the
published GPT-5 token rates. Failed or uncertain calls do not release the
reservation; a distinct idempotency key cannot trigger another call. This
one-call guard must be deliberately redesigned and approved before routine
audits are enabled. Asana request usage is separate from Gateway model cost.

Asana's live OAuth request included PKCE S256. The Asana resource and all
existing Gateway clients require S256 for new authorization codes and token
exchange. The conditional no-PKCE exception was removed after observing the
live request. Exact callback matching, one-use code, client secret and
individual login remain in place. Tokens last 30 days
and can be revoked with `scripts/revoke.py --account
metis-asana:asana-andres-1211117380475084`.

The owner approved the access scope in [ASANA_MCP_ACCESS_SCOPE.md](ASANA_MCP_ACCESS_SCOPE.md).
The code enables the resource only when all three `ASANA_MCP_*` values are
present. To configure Asana External MCP, enter the verified production URLs:
`/metis-asana/mcp`, `/metis-asana/oauth/authorize`, and
`/metis-asana/oauth/token`, plus the pinned client ID and secret. Use Asana's
exact callback `https://app.asana.com/-/integrations/oauth/callback`.
Asana documents that connection settings cannot be edited after saving, so
check the deployed origin before that action. Each authorized person connects
individually; this resource currently accepts only Andrés's key.

References: [agents API](https://developers.asana.com/reference/getagentsforworkspace),
[agent profile](https://developers.asana.com/reference/getagent),
[task update](https://developers.asana.com/reference/updatetask),
[task stories](https://developers.asana.com/reference/getstoriesfortask),
[External MCP setup](https://help.asana.com/s/article/create-custom-mcp-apps-for-ai-teammates).

## AI Teammate creation boundary

The Venture Quest workspace had no configured AI Teammates when the app was
connected. Asana's blank-teammate form asks for a name, description, behavior
guide, work access and first tasks. Do not select a template with broad writing
skills or add a private project by default. An initial evidence-audit teammate
should have no added work access, no first tasks, and only the Metis Gateway
app. Its behavior guide should require an explicit request from Andrés, one
declared context, only evidence supplied for that request, and no Asana edits,
comments, messages or external actions. Creating the teammate or assigning
work requires a separate decision about audience and Asana AI usage.

No added work access is **not** zero Asana visibility: Asana says a newly
created AI Teammate can see public tasks and projects in its domain. It can
access private work only when added as a member or collaborator. Asana also
stores task-linked memories after interactions, and comments are visible to
the task audience. Check the workspace's public data and each task audience
before creation or assignment. The Gateway's read-only tool restriction does
not remove the teammate's native Asana permissions. See [Asana access control](https://help.asana.com/s/article/understanding-access-control-for-ai-teammates).

Asana counts completed AI Teammate and Dash requests against a shared account
allowance, with additional usage depending on the plan. The separate Client
Management 14-day trial has not been activated. See [Asana pricing](https://asana.com/pricing)
and [Client Management pricing](https://help.asana.com/s/article/asana-client-management-pricing-and-plans).
