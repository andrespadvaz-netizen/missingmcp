"""Synthetic checks for preparing a project policy without changing Asana."""
import importlib.util
import json
from pathlib import Path

import pytest

# Operator scripts are not an installed package. Load by path, as in
# test_scripts.py, so the pytest console entry point works without PYTHONPATH.
_path = Path(__file__).resolve().parents[1] / "scripts" / "asana_prepare_scope_policy.py"
_spec = importlib.util.spec_from_file_location("asana_prepare_scope_policy", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
make_candidate = _module.make_candidate
parse_additions = _module.parse_additions


PROFILE = json.dumps({"99": {"specialties": ["research"],
                             "contexts": ["SHOKKO"],
                             "audiences": ["project_members"],
                             "dot_authorized": True}})
ORIGINAL = {
    "workspace_gid": "100", "creator_gid": "55",
    "project_defaults": {
        "owner_gid": "44", "project_owner_gid": "44",
        "privacy_setting": "private", "audiences": ["project_members"],
        "sources": ["current_request"], "eligible_task_names": ["Research"],
        "routing": {"Research": "research"},
        "specialties": ["research"], "agent_gids": ["99"]},
    "projects": {"200": {"context": "SHOKKO"}}}
OBSERVED = {
    "200": {"gid": "200", "workspace": {"gid": "100"}, "archived": True},
    "201": {"gid": "201", "workspace": {"gid": "100"},
            "owner": {"gid": "46"}, "archived": False, "completed": False,
            "privacy_setting": "private"}}


def test_human_only_addition_overrides_agent_defaults_and_retires_archived():
    encoded = make_candidate(ORIGINAL, {"201": "ANDREA"}, ["200"],
                             OBSERVED, PROFILE)
    result = json.loads(encoded)
    assert set(result["projects"]) == {"201"}
    assert result["projects"]["201"] == {
        "context": "ANDREA", "owner_gid": "46",
        "project_owner_gid": "46", "privacy_setting": "private",
        "audiences": ["project_members"], "sources": ["current_request"],
        "eligible_task_names": [], "routing": {}, "specialties": [],
        "agent_gids": []}
    assert ORIGINAL["projects"] == {"200": {"context": "SHOKKO"}}


@pytest.mark.parametrize("change,reason", [
    ({"200": {"archived": False}}, "retirement_not_verified_closed"),
    ({"200": {"gid": "999"}}, "retirement_not_verified_closed"),
    ({"201": {"archived": True}}, "addition_not_verified_active"),
    ({"201": {"completed": True}}, "addition_not_verified_active"),
    ({"201": {"owner": None}}, "addition_owner_or_privacy_unverified"),
    ({"201": {"workspace": {"gid": "999"}}}, "addition_not_verified_active"),
])
def test_candidate_abstains_without_verified_project_state(change, reason):
    observed = {gid: value.copy() for gid, value in OBSERVED.items()}
    for gid, fields in change.items():
        observed[gid].update(fields)
    with pytest.raises(ValueError, match=reason):
        make_candidate(ORIGINAL, {"201": "ANDREA"}, ["200"],
                       observed, PROFILE)


def test_contexts_and_duplicate_additions_must_be_explicit():
    assert parse_additions(["SHOKKO:201,202", "ANDREA:203"]) == {
        "201": "SHOKKO", "202": "SHOKKO", "203": "ANDREA"}
    with pytest.raises(ValueError, match="invalid_or_duplicate_project_gid"):
        parse_additions(["SHOKKO:201", "ANDREA:201"])
    with pytest.raises(ValueError, match="invalid_project_context"):
        parse_additions(["UNKNOWN:201"])


def test_completed_project_can_be_retired_after_direct_verification():
    observed = {gid: value.copy() for gid, value in OBSERVED.items()}
    observed["200"] = {"gid": "200", "workspace": {"gid": "100"},
                       "archived": False, "completed": True}
    encoded = make_candidate(ORIGINAL, {"201": "ANDREA"}, ["200"],
                             observed, PROFILE)
    assert set(json.loads(encoded)["projects"]) == {"201"}
