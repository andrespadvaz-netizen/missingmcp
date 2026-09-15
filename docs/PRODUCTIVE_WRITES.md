# Productive writes v2

Candidate implementation. Deployment and real-provider acceptance are separate
release gates; passing local tests is not evidence that production writes work.
The immutable v1 baseline tags and the existing deployed library version remain
independent of this branch.

## Behavior

The engine offers `productive.inspect` and `productive.propose` only at LEVEL_3
with an enabled operator-owned policy. Inspection creates an opaque, expiring
snapshot. Proposing performs no external mutation. The complete plan is checked
before it can leave the engine; a required audit receives the actual plan and
has read-only authority.

The Gateway transactionally encrypts every intent in SQLite before dispatching
an action. It reports APPLYING until every provider result has been read back.
Each dispatch has a monotonic bridge receipt. Recovery requests status only;
it never replays a mutation. An uncertain write pauses subsequent writes and
leaves read-only executions available. A partial plan lists the successful,
uncertain, and skipped actions separately. It does not attempt rollback by
deleting objects.

Same-request retries must retain the exact request and idempotency key.
Do not reset sequences, delete receipts, or resume uncertain writes without
checking the provider object and recording the reconciliation.

## Supported routine operations

| Provider | Create | Update |
|---|---|---|
| Notion | Plain page under an explicitly registered page | Page title or one unformatted paragraph |
| Asana | Task in one explicitly registered project | Title, notes, due date, completed flag; protected ritual tasks excluded |
| Drive | Native Google document with initial text in a registered folder | One unique exact text replacement with requiredRevisionId |

No delete, archive, send, sharing, permissions, Calendar mutation, CANON mutation,
arbitrary HTTP request, or general Notion database/property mutation is exposed.
Rich documents and unsupported object types fail visibly. Multiple distinct
creations in the same destination are supported. Duplicate creations and
conflicting updates in one plan are rejected or deduplicated before dispatch.

Notion and Asana do not provide the same revision precondition used for Google
Docs. Their adapters compare the inspected revision immediately before updating
and verify afterwards. A concurrent external edit in that interval can still
cause a conflict; failed verification is UNCERTAIN, not success.

## Context policy

`METIS_PRODUCTIVE_POLICY` lives in the engine's Script Properties, never in a
prompt, public repository, or model-editable destination. Example (synthetic):

```json
{
  "version": 1,
  "enabled": true,
  "authorization_ref": "operator-approved-record",
  "revision": "reviewed-registry-1",
  "protected_ids": ["protected-canonical-object"],
  "contexts": {
    "PROJECT/SUBPROJECT": {
      "primary": "OPENAI",
      "notion_project": "Subproject",
      "signals": ["unique subproject name"]
    }
  },
  "destinations": {
    "subproject_notes": {
      "provider": "NOTION",
      "context": "PROJECT/SUBPROJECT",
      "root_id": "verified-container-id",
      "kind": "routine",
      "operations": ["create", "update"]
    }
  },
  "read_partitions": {}
}
```

Every destination belongs to one exact context. The nearest registered ancestor
wins: a parent cannot enter a registered child context. Ancestor IDs and protected
IDs are checked against live provider data. Future contexts can be added to this
registry without changing the code, but must have verified roots; unknown contexts
do not inherit permissions. LEVEL_3 derives Asana/Drive reads from those exact
roots. Additional read-only sources require explicit `read_partitions`; the legacy
broad registry is not inherited.

For unambiguous selection, the client carries the established context on the
first line: `Contexto: PROJECT/SUBPROJECT`. It carries only relevant facts from
the current conversation, not other projects or the full chat.

## Deployment gates

1. Run Python tests, bridge tests, productive engine tests, the legacy engine
   suite, and static guards. CI runs on Linux; three legacy file-mode/path tests
   assume POSIX semantics and fail on Windows.
2. Preserve the current library/deployment IDs and their existing versions as
   rollback references. Upload reviewed engine files and create a new immutable
   library version. This candidate bridge expects version 6; verify that the
   actual version is 6 before publishing it.
3. Configure verified context roots, protected objects, and authorization record.
   Keep `GATEWAY_PRODUCTIVE_ENABLED` absent/false while staging.
4. Authorize the new Drive scope for the deploying Google identity. The API
   scope can access Drive broadly; the actuator constrains every operation to
   registered routine destinations. Calendar remains read-only. No Gmail or
   trigger scope is requested.
5. Upload Bridge, WriteBridge, Diagnostics, ConnectionCopy and the pinned manifest;
   deploy the bridge version. Deploy the Gateway code on its persistent volume.
   Do not run two Gateway workers against the same bridge sequence.
6. Enable the bridge flag only after the policy is installed. Refresh/reconnect
   clients. Claude uses `/metis/mcp`; ChatGPT uses `/metis-chatgpt/mcp`. OAuth
   tokens are resource-bound; origin is server-declared, not a request argument.
7. From both clients, create and update clearly identified acceptance objects,
   verify their provider links, and repeat the same idempotency key. Record the
   actual deployment versions and receipts. Keep test objects unless deletion
   is separately authorized. Do not declare broad activation based on one client.

Client consent/confirmation behavior remains under that client's controls.
Routine write authority does not disable platform security prompts.

## Verification

```text
python -m pytest tests/test_metis.py tests/test_metis_writes.py -q
node metis-gateway-bridge/test_bridge.cjs
node metis-orchestrator-prototype/tools/test_productive.cjs
node metis-orchestrator-prototype/tools/run_local.js all
node metis-orchestrator-prototype/tools/static_guards.js
```

Provider references: [Notion blocks](https://developers.notion.com/reference/block),
[Asana task updates](https://developers.asana.com/reference/updatetask),
[Drive multipart uploads](https://developers.google.com/workspace/drive/api/guides/manage-uploads),
[Google Docs write control](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate).
