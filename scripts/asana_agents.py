"""Operator CLI for Asana AI Teammate coordination. Never prints credentials."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from missingmcp.metis.asana_agents import AsanaAgents, AsanaError


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["discover", "agent", "task", "stories", "assign", "create"])
    parser.add_argument("--agent-gid")
    parser.add_argument("--task-gid")
    parser.add_argument("--project-gid")
    parser.add_argument("--source-task-gid")
    parser.add_argument("--title")
    parser.add_argument("--request")
    parser.add_argument("--idempotency-key")
    args = parser.parse_args()
    try:
        allowed = json.loads(os.environ["ASANA_AGENT_PROJECTS_JSON"])
        client = AsanaAgents(os.environ["ASANA_ACCESS_TOKEN"],
                             os.environ["ASANA_WORKSPACE_GID"], allowed,
                             ledger_path=os.environ.get("ASANA_DISPATCH_LEDGER"))
        if args.action == "discover":
            value = await client.discover()
        elif args.action == "agent":
            value = await client.agent(args.agent_gid)
        elif args.action == "task":
            value = await client.task(args.task_gid)
        elif args.action == "stories":
            value = await client.stories(args.task_gid)
        elif args.action == "assign":
            value = await client.assign_dedicated_task(
                args.task_gid, args.agent_gid, args.project_gid)
        else:
            value = await client.create_dedicated_task(
                source_task_gid=args.source_task_gid, project_gid=args.project_gid,
                agent_gid=args.agent_gid, title=args.title, request=args.request,
                idempotency_key=args.idempotency_key)
        print(json.dumps(value, ensure_ascii=False, indent=2))
    except (AsanaError, ValueError, KeyError) as exc:
        # Never print the bearer or raw upstream response, including on errors.
        print(f"Asana coordination failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
