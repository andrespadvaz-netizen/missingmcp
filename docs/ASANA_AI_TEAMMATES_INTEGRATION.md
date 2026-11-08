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

## Direct Gateway connection design and activation boundary

Asana External MCP requires a registered confidential OAuth client and explicit
authorization and token URLs. Its callback is
`https://app.asana.com/-/integrations/oauth/callback`. The current Metis MCP
resource uses dynamic client registration, mandatory PKCE S256, and a shared
operator identity. It must not be handed to Asana as-is. A 404 from the current
production path means the route is absent at that deployment; it is not evidence
that historical records were deleted.

The proposed Asana resource needs its own OAuth client, per-person Gateway
identity, server-side context and tool allowlists, separate token namespace,
revocation, and encrypted receipts. It must expose no operator key, private Dot
conversation, or unrestricted Metis write tool. Asana's public instructions do
not establish whether its MCP OAuth client sends PKCE; that behavior needs to
be observed before choosing the authorization policy. Do not weaken the
existing clients' PKCE requirement to accommodate an unknown client.

The auto-review gate rejected adding startup provisioning of a persistent Asana
OAuth client without exact approval for credential and grant configuration.
No client was provisioned, no Asana app was created, and no Gateway production
settings were changed in this change set. Once the owner approves the concrete
identity, context/tool grants, callback and credential handling, implement the
separate resource and connect it through the Asana developer console. Asana
documents that app configuration beyond name/description cannot be edited after
saving, so final URLs and grants must be settled before that save.

References: [agents API](https://developers.asana.com/reference/getagentsforworkspace),
[agent profile](https://developers.asana.com/reference/getagent),
[task update](https://developers.asana.com/reference/updatetask),
[task stories](https://developers.asana.com/reference/getstoriesfortask),
[External MCP setup](https://help.asana.com/s/article/create-custom-mcp-apps-for-ai-teammates).
