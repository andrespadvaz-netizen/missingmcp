"""Route audit must reveal profiles that cannot accept configured handoffs."""
import importlib.util
from pathlib import Path


_path = Path(__file__).resolve().parents[1] / "scripts" / "asana_route_scope_audit.py"
_spec = importlib.util.spec_from_file_location("asana_route_scope_audit", _path)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


def test_route_audit_distinguishes_human_only_and_unavailable_profile():
    scope = {
        "project_gid": "200", "context": "SHOKKO", "owner_gid": "44",
        "audiences": ["project_members"], "sources": ["current_request"],
        "eligible_task_names": ["Research"], "routing": {"Research": "growth"},
        "agent_gids": ["99"]}
    profiles = {"99": {"specialties": ["growth"],
                       "contexts": ["FINAL_FINAL"],
                       "audiences": ["project_members"],
                       "dot_authorized": True}}
    assert audit.audit_scope(scope, profiles) == ("UNROUTABLE", 0, 1)
    profiles["99"]["contexts"] = ["SHOKKO"]
    assert audit.audit_scope(scope, profiles) == ("COMPLETE", 1, 1)
    scope["eligible_task_names"].append("Launch")
    assert audit.audit_scope(scope, profiles) == ("PARTIAL", 1, 2)
    scope.update(agent_gids=[], eligible_task_names=[], routing={})
    assert audit.audit_scope(scope, profiles) == ("HUMAN_ONLY", 0, 0)
