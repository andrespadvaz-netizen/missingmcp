"""Individual outbound Asana OAuth grant, stored on the Gateway's encrypted volume.

This grant is only for Andrés. A separate setup key starts the one-use PKCE
flow; neither OAuth tokens nor client secrets are exposed by these methods.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import time
from urllib.parse import urlencode

import httpx

from .. import store

AUTH_URL = "https://app.asana.com/-/oauth_authorize"
TOKEN_URL = "https://app.asana.com/-/oauth_token"
OWNER_GID = "1211117380475084"
SCOPES = ("projects:read", "stories:read", "tasks:read", "tasks:write")
CALLBACK_PATH = "/asana-dispatch/oauth/callback"


class DispatchAuthError(RuntimeError):
    pass


class AsanaDispatchOAuth:
    def __init__(self, config, *, transport: httpx.AsyncBaseTransport | None = None,
                 clock=time.time):
        if not all((config.asana_dispatch_client_id,
                    config.asana_dispatch_client_secret,
                    config.asana_dispatch_setup_key)):
            raise ValueError("dispatch_oauth_not_configured")
        self.config = config
        self.transport = transport
        self.clock = clock
        self.db = sqlite3.connect(config.db_path, isolation_level=None,
                                  timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS asana_dispatch_oauth_states (
            state_hash TEXT PRIMARY KEY, verifier_enc TEXT NOT NULL,
            created_at INTEGER NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS asana_dispatch_oauth_tokens (
            owner_gid TEXT PRIMARY KEY, access_enc TEXT NOT NULL,
            refresh_enc TEXT NOT NULL, expires_at INTEGER NOT NULL,
            refresh_state TEXT NOT NULL DEFAULT 'READY')""")
        columns = {row[1] for row in self.db.execute(
            "PRAGMA table_info(asana_dispatch_oauth_tokens)")}
        if "refresh_state" not in columns:
            self.db.execute("ALTER TABLE asana_dispatch_oauth_tokens ADD COLUMN "
                            "refresh_state TEXT NOT NULL DEFAULT 'READY'")
            # Earlier code could retry a refresh after receiving a malformed
            # response. An expiring legacy row may already have been used.
            self.db.execute("UPDATE asana_dispatch_oauth_tokens SET "
                            "refresh_state='UNCERTAIN' WHERE expires_at<=?",
                            (int(self.clock()) + 120,))

    @property
    def redirect_uri(self):
        # The registered URI is pinned to the independently verified Railway
        # service domain, rather than a user-supplied redirect parameter.
        return "https://missingmcp-production.up.railway.app" + CALLBACK_PATH

    def begin(self, setup_key: str) -> str:
        if not isinstance(setup_key, str) or not hmac.compare_digest(
                setup_key, self.config.asana_dispatch_setup_key):
            raise DispatchAuthError("setup_forbidden")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.db.execute("DELETE FROM asana_dispatch_oauth_states WHERE created_at<?",
                        (int(self.clock()) - 600,))
        self.db.execute("INSERT INTO asana_dispatch_oauth_states VALUES (?,?,?)",
                        (store.hash_token(state),
                         store.encrypt(self.config.gateway_secret, verifier),
                         int(self.clock())))
        return AUTH_URL + "?" + urlencode({
            "client_id": self.config.asana_dispatch_client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code", "state": state,
            "code_challenge_method": "S256", "code_challenge": challenge,
            "scope": ("default" if self.config.asana_dispatch_full_permissions
                      else " ".join(SCOPES)),
        })

    def _consume_state(self, state: str) -> str:
        if not isinstance(state, str) or len(state) > 128:
            raise DispatchAuthError("invalid_state")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT verifier_enc,created_at FROM asana_dispatch_oauth_states "
                "WHERE state_hash=?", (store.hash_token(state),)).fetchone()
            self.db.execute("DELETE FROM asana_dispatch_oauth_states WHERE state_hash=?",
                            (store.hash_token(state),))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        if row is None or row["created_at"] < int(self.clock()) - 600:
            raise DispatchAuthError("invalid_state")
        return store.decrypt(self.config.gateway_secret, row["verifier_enc"])

    async def _token(self, data: dict) -> dict:
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
                response = await client.post(TOKEN_URL, data=data)
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DispatchAuthError("token_exchange_failed") from exc
        if not isinstance(value, dict):
            raise DispatchAuthError("invalid_token_response")
        return value

    @staticmethod
    def _parse_token(value: dict, *, require_owner: bool,
                     prior_refresh: str | None = None) -> tuple[str, str, int]:
        access, refresh, ttl = (value.get("access_token"),
                                value.get("refresh_token"), value.get("expires_in"))
        # RFC 6749 section 6 permits a refresh response without a replacement
        # refresh token. In that case the existing token remains in force.
        # A supplied but malformed replacement is never silently ignored.
        if refresh is None and prior_refresh is not None:
            refresh = prior_refresh
        token_type = value.get("token_type", "bearer")
        owner = value.get("data")
        if (not isinstance(access, str) or len(access) < 16 or
                not isinstance(refresh, str) or len(refresh) < 16 or
                not isinstance(ttl, int) or isinstance(ttl, bool) or not 0 < ttl <= 86400 or
                not isinstance(token_type, str) or token_type.lower() != "bearer"):
            raise DispatchAuthError("invalid_token_response")
        if require_owner and (not isinstance(owner, dict) or
                              str(owner.get("gid")) != OWNER_GID):
            raise DispatchAuthError("owner_mismatch")
        return access, refresh, ttl

    async def complete(self, state: str, code: str) -> None:
        verifier = self._consume_state(state)
        if not isinstance(code, str) or not 1 <= len(code) <= 2048:
            raise DispatchAuthError("invalid_code")
        value = await self._token({
            "grant_type": "authorization_code",
            "client_id": self.config.asana_dispatch_client_id,
            "client_secret": self.config.asana_dispatch_client_secret,
            "redirect_uri": self.redirect_uri, "code": code,
            "code_verifier": verifier,
        })
        access, refresh, ttl = self._parse_token(value, require_owner=True)
        self.db.execute("INSERT INTO asana_dispatch_oauth_tokens"
                        "(owner_gid,access_enc,refresh_enc,expires_at,refresh_state) "
                        "VALUES (?,?,?,?,'READY') "
                        "ON CONFLICT(owner_gid) DO UPDATE SET access_enc=excluded.access_enc, "
                        "refresh_enc=excluded.refresh_enc, expires_at=excluded.expires_at, "
                        "refresh_state='READY'",
                        (OWNER_GID, store.encrypt(self.config.gateway_secret, access),
                         store.encrypt(self.config.gateway_secret, refresh),
                         int(self.clock()) + ttl))

    async def access_token(self) -> str:
        """Reserve a refresh durably before the network call; never retry uncertainty."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM asana_dispatch_oauth_tokens WHERE owner_gid=?",
                                  (OWNER_GID,)).fetchone()
            if row is None:
                raise DispatchAuthError("dispatch_not_connected")
            if row["refresh_state"] != "READY":
                raise DispatchAuthError("dispatch_refresh_requires_reauth")
            if row["expires_at"] > int(self.clock()) + 120:
                access = store.decrypt(self.config.gateway_secret, row["access_enc"])
                self.db.execute("COMMIT")
                return access
            else:
                refresh = store.decrypt(self.config.gateway_secret, row["refresh_enc"])
                self.db.execute("UPDATE asana_dispatch_oauth_tokens SET "
                                "refresh_state='UNCERTAIN' WHERE owner_gid=?", (OWNER_GID,))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        # A network failure, malformed response or process crash leaves the
        # durable barrier in place. Only a new approved OAuth grant clears it.
        value = await self._token({
            "grant_type": "refresh_token",
            "client_id": self.config.asana_dispatch_client_id,
            "client_secret": self.config.asana_dispatch_client_secret,
            "refresh_token": refresh,
        })
        access, new_refresh, ttl = self._parse_token(
            value, require_owner=False, prior_refresh=refresh)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("UPDATE asana_dispatch_oauth_tokens SET "
                            "access_enc=?,refresh_enc=?,expires_at=?,refresh_state='READY' "
                            "WHERE owner_gid=? AND refresh_state='UNCERTAIN'",
                            (store.encrypt(self.config.gateway_secret, access),
                             store.encrypt(self.config.gateway_secret, new_refresh),
                             int(self.clock()) + ttl, OWNER_GID))
            if not self.db.execute("SELECT changes()").fetchone()[0]:
                raise DispatchAuthError("dispatch_refresh_superseded")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return access
