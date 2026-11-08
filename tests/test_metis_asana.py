import base64
import hashlib
import json
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from missingmcp.app import build_app
from missingmcp.config import load_config
from missingmcp.adapters.metis_asana import (
    MetisAsanaAdapter, ACCOUNT, PROBE_BUDGET_MICRO_USD,
    PROBE_MAX_CALLS, PROBE_MAX_OUTPUT_TOKENS, PROBE_TOTAL_BUDGET_MICRO_USD,
    probe_reservation_micro_usd,
)
from missingmcp.adapters.base import LoginError
from missingmcp import store


def config(tmp_path):
    return load_config({
        "GATEWAY_SECRET": "g" * 40, "DATA_DIR": str(tmp_path),
        "PUBLIC_URL": "https://gateway.example.com",
        "ASANA_MCP_CLIENT_ID": "asana-fixed-client-0001",
        "ASANA_MCP_CLIENT_SECRET": "c" * 40,
        "ASANA_MCP_ANDRES_KEY": "a" * 40,
        "METIS_OPENAI_API_KEY": "test-openai-key",
    })


def test_probe_worst_case_reservation_is_below_authorized_budget():
    assert probe_reservation_micro_usd("x" * 8192, "VENTURE_QUEST") < \
        PROBE_BUDGET_MICRO_USD


def test_static_confidential_oauth_requires_pkce_and_exposes_one_tool(tmp_path):
    cfg = config(tmp_path)
    client = TestClient(build_app(cfg), follow_redirects=False)
    callback = "https://app.asana.com/-/integrations/oauth/callback"
    metadata = client.get("/.well-known/oauth-authorization-server/metis-asana").json()
    assert "registration_endpoint" not in metadata
    assert client.post("/metis-asana/oauth/register", json={"redirect_uris": [callback]}).status_code == 403
    assert client.get("/metis-asana/oauth/authorize", params={
        "client_id": cfg.asana_mcp_client_id, "redirect_uri": callback,
        "state": "client-state"}).status_code == 400
    verifier = "asana-test-verifier-" + "x" * 43
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    auth = client.get("/metis-asana/oauth/authorize", params={
        "client_id": cfg.asana_mcp_client_id, "redirect_uri": callback, "state": "client-state",
        "code_challenge": challenge, "code_challenge_method": "S256"})
    assert auth.status_code == 200
    assert "metis_key" in auth.text
    assert client.get("/metis-asana/oauth/authorize", params={
        "client_id": cfg.asana_mcp_client_id, "redirect_uri": "https://evil.test/cb",
        "state": "client-state"}).status_code == 400
    csrf = auth.text.split('name="csrf" value="')[1].split('"')[0]
    finish = client.post("/metis-asana/oauth/authorize", data={
        "csrf": csrf, "client_id": cfg.asana_mcp_client_id,
        "redirect_uri": callback, "state": "client-state", "metis_key": "a" * 40,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    assert finish.status_code == 302
    code = parse_qs(urlparse(finish.headers["location"]).query)["code"][0]
    token = client.post("/metis-asana/oauth/token", data={
        "grant_type": "authorization_code", "client_id": cfg.asana_mcp_client_id,
        "client_secret": cfg.asana_mcp_client_secret, "redirect_uri": callback,
        "code": code, "code_verifier": verifier})
    assert token.status_code == 200
    assert token.json()["expires_in"] == 30 * 86400
    bearer = token.json()["access_token"]
    assert client.post("/metis-asana/oauth/token", data={
        "grant_type": "authorization_code", "client_id": cfg.asana_mcp_client_id,
        "client_secret": cfg.asana_mcp_client_secret, "redirect_uri": callback,
        "code": code}).status_code == 400
    listed = client.post("/metis-asana/mcp", headers={"Authorization": f"Bearer {bearer}"},
                         json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed.status_code == 200
    assert [t["name"] for t in listed.json()["result"]["tools"]] == ["metis_cross_audit"]
    assert client.post("/metis/mcp", headers={"Authorization": f"Bearer {bearer}"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code != 200


def test_asana_credential_is_independent(tmp_path):
    adapter = MetisAsanaAdapter(config(tmp_path))
    with pytest.raises(LoginError):
        adapter.start_login({"metis_key": "o" * 40})
    assert adapter.start_login({"metis_key": "a" * 40}).account_key == ACCOUNT


def test_legacy_reservations_migrate_once_into_monthly_ceiling(tmp_path):
    cfg = config(tmp_path)
    original = MetisAsanaAdapter(cfg)
    original.queue.db.execute(
        "INSERT INTO metis_asana_audits(owner,idem,fingerprint,state,created_at) "
        "VALUES (?,?,?,'DISPATCHED','2026-11-08 12:00:00')",
        (ACCOUNT, "legacy-key-000001", "f" * 64))
    original.queue.db.execute(
        "INSERT INTO metis_asana_probe_budget(owner,reserved_micro_usd,call_count) "
        "VALUES (?,?,?)", (ACCOUNT, 53_400, 1))
    original.queue.db.execute("DELETE FROM metis_asana_budget_migrations")
    reopened = MetisAsanaAdapter(cfg)
    restarted = MetisAsanaAdapter(cfg)
    for adapter in (reopened, restarted):
        row = adapter.queue.db.execute(
            "SELECT reserved_micro_usd,call_count FROM metis_asana_monthly_budget "
            "WHERE owner=? AND cycle='2026-11'", (ACCOUNT,)).fetchone()
        assert tuple(row) == (53_400, 1)


def test_static_client_rotation_revokes_only_asana_tokens(tmp_path):
    db = store.init_db(str(tmp_path / "gateway.db"))
    callback = "https://app.asana.com/-/integrations/oauth/callback"
    store.provision_static_client(db, "asana-fixed-client-0001", store.hash_token("old"),
                                  callback, "metis-asana")
    store.create_access_token(db, store.hash_token("asana-token"), "metis-asana",
                              ACCOUNT, "asana-fixed-client-0001", ttl=1000)
    store.create_access_token(db, store.hash_token("other-token"), "garmin",
                              "human", "other-client", ttl=1000)
    store.provision_static_client(db, "asana-fixed-client-0001", store.hash_token("new"),
                                  callback, "metis-asana")
    assert store.account_key_for_token_hash(db, store.hash_token("asana-token")) is None
    assert store.account_key_for_token_hash(db, store.hash_token("other-token")) is not None
    assert store.get_client(db, "asana-fixed-client-0001")["adapter"] == "metis-asana"
    with pytest.raises(ValueError, match="collides"):
        store.provision_static_client(db, "asana-fixed-client-0001", "hash", callback, "garmin")


@pytest.mark.asyncio
async def test_context_boundary_and_idempotent_receipt(tmp_path, monkeypatch):
    adapter = MetisAsanaAdapter(config(tmp_path))
    calls = []

    async def fake_run(origin, request, context, *, max_output_tokens):
        calls.append((origin, context, max_output_tokens))
        return {"status": "COMPLETED", "receipt_id": "receipt-asana-1",
                "context_project": context, "target_model_provider": "OPENAI",
                "elapsed_ms": 5, "cost_known": True, "cost_usd": 0.001,
                "final_answer": "reviewed"}

    monkeypatch.setattr(adapter.direct_audit, "run", fake_run)
    blob = adapter.start_login({"metis_key": "a" * 40}).blob

    async def call(context, key="stable-asana-key-0001"):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "metis_cross_audit", "arguments": {
                    "request": "Review evidence", "idempotency_key": key,
                    "context": {"project": context, "source": "current_request"}}}}
        _, _, payload = await adapter.handle(None, ACCOUNT, blob, json.dumps(body).encode())
        return json.loads(payload)["result"]

    assert (await call("PERSONAL"))["isError"] is True
    assert calls == []
    first = await call("SHOKKO")
    second = await call("SHOKKO")
    assert first == second
    assert calls == [("ASANA", "SHOKKO", PROBE_MAX_OUTPUT_TOKENS)]
    assert (await call("FINAL_FINAL"))["isError"] is True
    another = await call("FINAL_FINAL", "stable-asana-key-0002")
    assert another["isError"] is False
    assert (await call("VENTURE_QUEST", "stable-asana-key-0003"))["isError"] is False
    assert probe_reservation_micro_usd("Review evidence", "SHOKKO") < PROBE_BUDGET_MICRO_USD
    budget = adapter.queue.db.execute(
        "SELECT reserved_micro_usd,call_count FROM metis_asana_monthly_budget "
        "WHERE owner=?", (ACCOUNT,)).fetchone()
    assert budget["call_count"] == 3
    forwarded = ("Contexto: SHOKKO\nProcedencia del contexto declarada por el cliente: "
                 "current_request\n\nReview evidence")
    second_forwarded = forwarded.replace("SHOKKO", "FINAL_FINAL", 1)
    third_forwarded = forwarded.replace("SHOKKO", "VENTURE_QUEST", 1)
    assert budget["reserved_micro_usd"] == (probe_reservation_micro_usd(
        forwarded, "SHOKKO") + probe_reservation_micro_usd(
        second_forwarded, "FINAL_FINAL") + probe_reservation_micro_usd(
        third_forwarded, "VENTURE_QUEST"))
    assert budget["reserved_micro_usd"] < PROBE_TOTAL_BUDGET_MICRO_USD


@pytest.mark.asyncio
async def test_probe_budget_survives_restart_and_uncertain_result(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    adapter = MetisAsanaAdapter(cfg)
    blob = adapter.start_login({"metis_key": "a" * 40}).blob
    calls = []

    async def uncertain_run(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("provider outcome unknown")

    monkeypatch.setattr(adapter.direct_audit, "run", uncertain_run)

    async def call(target, key, request="Harmless evidence"):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "metis_cross_audit", "arguments": {
                    "request": request, "idempotency_key": key,
                    "context": {"project": "SHOKKO", "source": "current_request"}}}}
        _, _, payload = await target.handle(None, ACCOUNT, blob, json.dumps(body).encode())
        return json.loads(payload)["result"]

    with pytest.raises(RuntimeError, match="unknown"):
        await call(adapter, "stable-probe-key-0001")
    restarted = MetisAsanaAdapter(cfg)
    monkeypatch.setattr(restarted.direct_audit, "run", uncertain_run)
    assert (await call(restarted, "stable-probe-key-0001"))["isError"] is True
    for number in range(2, PROBE_MAX_CALLS + 1):
        with pytest.raises(RuntimeError, match="unknown"):
            await call(restarted, f"stable-probe-key-{number:04d}")
    assert (await call(restarted, "stable-probe-key-0021"))["isError"] is True
    assert calls == [1] * PROBE_MAX_CALLS
    row = restarted.queue.db.execute(
        "SELECT call_count,reserved_micro_usd FROM metis_asana_monthly_budget "
        "WHERE owner=?", (ACCOUNT,)).fetchone()
    assert row["call_count"] == PROBE_MAX_CALLS
    assert 0 < row["reserved_micro_usd"] < PROBE_TOTAL_BUDGET_MICRO_USD


@pytest.mark.asyncio
async def test_probe_rejects_oversize_and_unpriced_model_before_reservation(tmp_path):
    adapter = MetisAsanaAdapter(config(tmp_path))
    blob = adapter.start_login({"metis_key": "a" * 40}).blob

    async def call(request):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "metis_cross_audit", "arguments": {
                    "request": request, "idempotency_key": "stable-probe-key-0001",
                    "context": {"project": "SHOKKO", "source": "current_request"}}}}
        _, _, payload = await adapter.handle(None, ACCOUNT, blob, json.dumps(body).encode())
        return json.loads(payload)["result"]

    assert (await call("x" * 8193))["isError"] is True
    adapter.config = replace(adapter.config, metis_openai_model="unknown-model")
    assert (await call("short"))["isError"] is True
    assert adapter.queue.db.execute("SELECT count(*) FROM metis_asana_audits").fetchone()[0] == 0
