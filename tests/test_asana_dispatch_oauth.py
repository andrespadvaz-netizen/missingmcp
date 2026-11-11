from urllib.parse import parse_qs, urlparse
import asyncio
import sqlite3

import httpx
import pytest
from starlette.testclient import TestClient

from missingmcp.app import build_app
from missingmcp.config import load_config
from missingmcp.metis.asana_dispatch_oauth import (
    AsanaDispatchOAuth, DispatchAuthError, OWNER_GID, SCOPES,
)


def config(tmp_path):
    return load_config({
        "GATEWAY_SECRET": "g" * 40, "DATA_DIR": str(tmp_path),
        "PUBLIC_URL": "https://gateway.example.com",
        "ASANA_DISPATCH_CLIENT_ID": "1220338382227997",
        "ASANA_DISPATCH_CLIENT_SECRET": "c" * 40,
        "ASANA_DISPATCH_SETUP_KEY": "s" * 40,
    })


def test_asana_dispatch_full_permissions_requests_default_scope(tmp_path):
    from dataclasses import replace

    oauth = AsanaDispatchOAuth(replace(config(tmp_path),
                                      asana_dispatch_full_permissions=True))
    query = parse_qs(urlparse(oauth.begin("s" * 40)).query)
    assert query["scope"] == ["default"]


@pytest.mark.asyncio
async def test_asana_dispatch_pkce_owner_and_refresh(tmp_path):
    clock = [1000]
    calls = []

    def upstream(request):
        form = parse_qs(request.content.decode())
        calls.append(form)
        if form["grant_type"] == ["authorization_code"]:
            return httpx.Response(200, json={
                "access_token": "access-" + "a" * 32,
                "refresh_token": "refresh-" + "r" * 32,
                "expires_in": 3600, "token_type": "bearer",
                "data": {"gid": OWNER_GID},
            })
        return httpx.Response(200, json={
            "access_token": "access-" + "b" * 32,
            "refresh_token": "refresh-" + "n" * 32,
            "expires_in": 3600, "token_type": "bearer",
        })

    oauth = AsanaDispatchOAuth(config(tmp_path), transport=httpx.MockTransport(upstream),
                               clock=lambda: clock[0])
    with pytest.raises(DispatchAuthError, match="setup_forbidden"):
        oauth.begin("wrong")
    url = oauth.begin("s" * 40)
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == [" ".join(SCOPES)]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [oauth.redirect_uri]
    await oauth.complete(query["state"][0], "one-use-code")
    assert len(calls[0]["code_verifier"][0]) >= 43
    with pytest.raises(DispatchAuthError, match="invalid_state"):
        await oauth.complete(query["state"][0], "one-use-code")
    assert await oauth.access_token() == "access-" + "a" * 32
    raw = oauth.db.execute("SELECT access_enc,refresh_enc FROM asana_dispatch_oauth_tokens").fetchone()
    assert "access-" not in raw["access_enc"] and "refresh-" not in raw["refresh_enc"]
    clock[0] = 4490
    assert await oauth.access_token() == "access-" + "b" * 32
    assert calls[-1]["refresh_token"] == ["refresh-" + "r" * 32]


@pytest.mark.asyncio
async def test_invalid_refresh_is_barred_until_new_grant(tmp_path):
    clock = [1000]
    refreshes = []

    def upstream(request):
        form = parse_qs(request.content.decode())
        if form["grant_type"] == ["authorization_code"]:
            return httpx.Response(200, json={
                "access_token": "access-" + "a" * 32,
                "refresh_token": "refresh-" + "r" * 32,
                "expires_in": 3600, "token_type": "bearer",
                "data": {"gid": OWNER_GID}})
        refreshes.append(1)
        return httpx.Response(200, json={"access_token": "access-" + "b" * 32})

    oauth = AsanaDispatchOAuth(config(tmp_path), transport=httpx.MockTransport(upstream),
                               clock=lambda: clock[0])
    state = parse_qs(urlparse(oauth.begin("s" * 40)).query)["state"][0]
    await oauth.complete(state, "new-code")
    clock[0] = 4490
    with pytest.raises(DispatchAuthError, match="invalid_token_response"):
        await oauth.access_token()
    assert oauth.db.execute("SELECT refresh_state FROM asana_dispatch_oauth_tokens").fetchone()[0] == "UNCERTAIN"
    restarted = AsanaDispatchOAuth(config(tmp_path), transport=httpx.MockTransport(upstream),
                                   clock=lambda: clock[0])
    with pytest.raises(DispatchAuthError, match="dispatch_refresh_requires_reauth"):
        await restarted.access_token()
    assert len(refreshes) == 1
    state = parse_qs(urlparse(restarted.begin("s" * 40)).query)["state"][0]
    await restarted.complete(state, "replacement-code")
    assert await restarted.access_token() == "access-" + "a" * 32


@pytest.mark.asyncio
async def test_concurrent_refresh_has_one_network_call(tmp_path):
    clock = [1000]
    entered, release = asyncio.Event(), asyncio.Event()
    refreshes = []

    async def upstream(request):
        form = parse_qs(request.content.decode())
        if form["grant_type"] == ["authorization_code"]:
            return httpx.Response(200, json={
                "access_token": "access-" + "a" * 32,
                "refresh_token": "refresh-" + "r" * 32,
                "expires_in": 3600, "token_type": "bearer",
                "data": {"gid": OWNER_GID}})
        refreshes.append(1)
        entered.set()
        await release.wait()
        return httpx.Response(200, json={
            "access_token": "access-" + "b" * 32,
            "refresh_token": "refresh-" + "n" * 32,
            "expires_in": 3600, "token_type": "bearer"})

    transport = httpx.MockTransport(upstream)
    first = AsanaDispatchOAuth(config(tmp_path), transport=transport,
                               clock=lambda: clock[0])
    state = parse_qs(urlparse(first.begin("s" * 40)).query)["state"][0]
    await first.complete(state, "new-code")
    clock[0] = 4490
    second = AsanaDispatchOAuth(config(tmp_path), transport=transport,
                                clock=lambda: clock[0])
    task = asyncio.create_task(first.access_token())
    await entered.wait()
    with pytest.raises(DispatchAuthError, match="dispatch_refresh_requires_reauth"):
        await second.access_token()
    release.set()
    assert await task == "access-" + "b" * 32
    assert len(refreshes) == 1


@pytest.mark.asyncio
async def test_expiring_legacy_token_requires_reauthorization(tmp_path):
    path = config(tmp_path).db_path
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE asana_dispatch_oauth_tokens ("
               "owner_gid TEXT PRIMARY KEY, access_enc TEXT NOT NULL, "
               "refresh_enc TEXT NOT NULL, expires_at INTEGER NOT NULL)")
    db.execute("INSERT INTO asana_dispatch_oauth_tokens VALUES (?,?,?,?)",
               (OWNER_GID, "encrypted-access", "encrypted-refresh", 1000))
    db.commit()
    db.close()
    oauth = AsanaDispatchOAuth(config(tmp_path), clock=lambda: 2000)
    assert oauth.db.execute("SELECT refresh_state FROM asana_dispatch_oauth_tokens").fetchone()[0] == "UNCERTAIN"
    with pytest.raises(DispatchAuthError, match="dispatch_refresh_requires_reauth"):
        await oauth.access_token()


@pytest.mark.asyncio
async def test_asana_dispatch_rejects_wrong_owner_without_storing_grant(tmp_path):
    def upstream(_):
        return httpx.Response(200, json={
            "access_token": "access-" + "a" * 32,
            "refresh_token": "refresh-" + "r" * 32,
            "expires_in": 3600, "token_type": "bearer",
            "data": {"gid": "999"},
        })

    oauth = AsanaDispatchOAuth(config(tmp_path), transport=httpx.MockTransport(upstream))
    state = parse_qs(urlparse(oauth.begin("s" * 40)).query)["state"][0]
    with pytest.raises(DispatchAuthError, match="owner_mismatch"):
        await oauth.complete(state, "code")
    assert oauth.db.execute("SELECT count(*) FROM asana_dispatch_oauth_tokens").fetchone()[0] == 0


def test_dispatch_setup_requires_key_and_browser_bound_state(tmp_path, monkeypatch):
    async def fake_token(self, _):
        return {"access_token": "access-" + "a" * 32,
                "refresh_token": "refresh-" + "r" * 32,
                "expires_in": 3600, "token_type": "bearer",
                "data": {"gid": OWNER_GID}}

    monkeypatch.setattr(AsanaDispatchOAuth, "_token", fake_token)
    client = TestClient(build_app(config(tmp_path)), base_url="https://testserver",
                        follow_redirects=False)
    assert client.post("/asana-dispatch/oauth/start", data={"setup_key": "bad"}).status_code == 403
    start = client.post("/asana-dispatch/oauth/start", data={"setup_key": "s" * 40})
    assert start.status_code == 303
    assert "httponly" in start.headers["set-cookie"].lower()
    assert "secure" in start.headers["set-cookie"].lower()
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = "/asana-dispatch/oauth/callback?state=" + state + "&code=example"
    assert TestClient(build_app(config(tmp_path)), base_url="https://testserver").get(callback).status_code == 400
    assert client.get(callback).status_code == 200
    assert client.get(callback).status_code == 400
