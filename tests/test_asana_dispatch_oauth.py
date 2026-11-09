from urllib.parse import parse_qs, urlparse

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
