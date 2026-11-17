"""A proposed rollout must preserve scope and use distinct context agents."""
import importlib.util
import json
from pathlib import Path

import pytest


_path = Path(__file__).resolve().parents[1] / "scripts" / "asana_prepare_agent_rollout.py"
_spec = importlib.util.spec_from_file_location("asana_prepare_agent_rollout", _path)
rollout = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rollout)


def policy_and_profiles():
    policy = {"workspace_gid": "100", "creator_gid": "55", "project_defaults": {
        "owner_gid": "44", "project_owner_gid": "44", "privacy_setting": "private",
        "audiences": ["project_members"], "sources": ["current_request"],
        "eligible_task_names": [], "routing": {}, "specialties": [], "agent_gids": []},
        "projects": {str(gid): {"context": "ANDREA"} for gid in range(200, 229)}}
    profiles = {}
    for gid, context in (("101", "SHOKKO"), ("102", "FINAL_FINAL"),
                         ("103", "VENTURE_QUEST")):
        profiles[gid] = {"specialties": ["valid"], "contexts": [context],
                         "audiences": ["project_members"], "dot_authorized": True}
    for project_gid, agent_gid, context in (("200", "101", "SHOKKO"),
                                           ("201", "102", "FINAL_FINAL"),
                                           ("202", "103", "VENTURE_QUEST")):
        policy["projects"][project_gid] = {
            "context": context, "eligible_task_names": ["Valid", "Unavailable"],
            "routing": {"Valid": "valid", "Unavailable": "unavailable"},
            "specialties": ["valid", "unavailable"], "agent_gids": [agent_gid]}
    policy["projects"]["203"] = {"context": "FINAL_FINAL",
                                  "privacy_setting": "public_to_workspace",
                                  "audiences": ["workspace"]}
    policy["projects"]["204"] = {"context": "SHOKKO"}
    policy["projects"]["205"] = {"context": "FINAL_FINAL"}
    policy["projects"]["206"] = {"context": "METIS"}
    policy["projects"]["207"] = {"context": "ARQUITECTO_INTERIOR"}
    policy["projects"]["208"] = {"context": "METIS/VAL"}
    return policy, profiles


def test_rollout_candidate_keeps_owner_and_uses_public_agent_only_for_public_scope():
    policy, profiles = policy_and_profiles()
    agents = {context: gid for context, gid in zip(
        rollout.NEW_CONTEXTS, ("301", "302", "303", "304", "305"))}
    candidate_raw, profiles_raw = rollout.make_candidate(policy, profiles, "", agents)
    candidate, new_profiles = json.loads(candidate_raw), json.loads(profiles_raw)
    assert len(candidate["projects"]) == 29
    assert len(new_profiles) == len(profiles) + 5
    assert candidate["projects"]["200"]["eligible_task_names"] == ["Valid"]
    assert candidate["projects"]["204"]["agent_gids"] == ["101"]
    assert candidate["projects"]["205"]["agent_gids"] == ["102"]
    assert candidate["projects"]["203"]["agent_gids"] == ["305"]
    assert candidate["projects"]["203"]["audiences"] == ["workspace"]
    assert candidate["projects"]["206"]["agent_gids"] == ["302"]
    assert candidate["projects"]["208"]["agent_gids"] == ["304"]
    assert all(entry.get("owner_gid", "44") == "44"
               for entry in candidate["projects"].values())
    assert policy["projects"]["204"] == {"context": "SHOKKO"}


def test_rollout_rejects_duplicate_or_missing_agents():
    with pytest.raises(ValueError, match="five_distinct_context_agents_required"):
        rollout.parse_agents(["ANDREA:301"])
    with pytest.raises(ValueError, match="five_distinct_context_agents_required"):
        rollout.parse_agents([
            "ANDREA:301", "METIS:301", "ARQUITECTO_INTERIOR:303",
            "METIS/VAL:304", "FINAL_FINAL_PUBLIC:305"])
