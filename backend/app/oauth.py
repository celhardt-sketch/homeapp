"""OAuth 2.1 authorization server for the MCP connector, stored in SQLite.

FastMCP/the MCP SDK implement the protocol (metadata, dynamic client registration, PKCE,
token endpoint, revocation); this module only supplies storage and the consent step.
`authorize()` parks the request and redirects the user to the app's approval page, where
the admin password is required (main.py). Approval mints the authorization code.

Every access token issued here maps to the `connector` role. Revoking the connector deletes
all of its tokens, which does not touch household or admin sessions.
"""

import json
import os
import secrets
import time
from datetime import datetime, timezone

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl

from fastmcp.server.auth.auth import ClientRegistrationOptions, OAuthProvider, RevocationOptions

from .db import get_conn

SCOPE = "home"
CODE_TTL = 5 * 60
ACCESS_TTL = 60 * 60
PENDING_TTL = 15 * 60
APPROVE_PATH = "/connect/approve"


def public_base_url() -> str:
    """Where this server is reachable from the internet (OAuth issuer + resource URL)."""
    url = os.environ.get("PUBLIC_URL")
    if not url and os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
        url = "https://" + os.environ["RAILWAY_PUBLIC_DOMAIN"]
    return (url or "http://localhost:8000").rstrip("/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def access_token_valid(token: str) -> bool:
    """Used by the /api middleware so the connector's OAuth token also works on the REST API."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT expires_at FROM oauth_tokens WHERE token = ? AND kind = 'access'", (token,)
        ).fetchone()
        if not row or (row["expires_at"] is not None and row["expires_at"] < time.time()):
            return False
        conn.execute("UPDATE oauth_tokens SET last_used_at = ? WHERE token = ?", (_now(), token))
        return True


def connector_status() -> dict:
    with get_conn() as conn:
        clients = conn.execute("SELECT client_id, data, created_at FROM oauth_clients").fetchall()
        tokens = conn.execute(
            "SELECT client_id, MAX(last_used_at) AS last_used_at, COUNT(*) AS n FROM oauth_tokens "
            "WHERE kind = 'refresh' OR expires_at > ? GROUP BY client_id",
            (time.time(),),
        ).fetchall()
        by_client = {t["client_id"]: t for t in tokens}
        return {
            "connected": any(by_client.values()),
            "mcp_url": public_base_url() + "/mcp",
            "clients": [
                {
                    "client_id": c["client_id"],
                    "name": json.loads(c["data"]).get("client_name") or "Unnamed client",
                    "created_at": c["created_at"],
                    "active": c["client_id"] in by_client,
                    "last_used_at": by_client[c["client_id"]]["last_used_at"] if c["client_id"] in by_client else None,
                }
                for c in clients
            ],
        }


def revoke_all() -> int:
    """Cut off every connector token and registered client. Household/admin sessions are untouched."""
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM oauth_tokens").fetchone()[0]
        for table in ("oauth_tokens", "oauth_codes", "oauth_pending", "oauth_clients"):
            conn.execute(f"DELETE FROM {table}")
        return n


# ---------- consent step (called from the approval page in main.py) ----------


def pending_request(pending_id: str) -> dict | None:
    with get_conn() as conn:
        conn.execute("DELETE FROM oauth_pending WHERE expires_at < ?", (time.time(),))
        row = conn.execute("SELECT * FROM oauth_pending WHERE id = ?", (pending_id,)).fetchone()
        if not row:
            return None
        client = conn.execute("SELECT data FROM oauth_clients WHERE client_id = ?", (row["client_id"],)).fetchone()
    data = json.loads(row["data"])
    data["client_name"] = (json.loads(client["data"]).get("client_name") if client else None) or row["client_id"]
    return data


def approve(pending_id: str) -> str | None:
    """Consume the pending request, mint the authorization code and return the redirect URL."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM oauth_pending WHERE id = ? AND expires_at >= ?", (pending_id, time.time())
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM oauth_pending WHERE id = ?", (pending_id,))
        p = json.loads(row["data"])
        code = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=code,
            client_id=row["client_id"],
            redirect_uri=AnyHttpUrl(p["redirect_uri"]),
            redirect_uri_provided_explicitly=p["redirect_uri_provided_explicitly"],
            scopes=p["scopes"],
            expires_at=time.time() + CODE_TTL,
            code_challenge=p["code_challenge"],
            resource=p.get("resource"),
        )
        conn.execute(
            "INSERT INTO oauth_codes (code, client_id, data, expires_at) VALUES (?, ?, ?, ?)",
            (code, row["client_id"], auth_code.model_dump_json(), auth_code.expires_at),
        )
    return construct_redirect_uri(p["redirect_uri"], code=code, state=p.get("state"))


def deny(pending_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT data FROM oauth_pending WHERE id = ?", (pending_id,)).fetchone()
        conn.execute("DELETE FROM oauth_pending WHERE id = ?", (pending_id,))
    if not row:
        return None
    p = json.loads(row["data"])
    return construct_redirect_uri(p["redirect_uri"], error="access_denied", state=p.get("state"))


# ---------- provider ----------


class SqliteOAuthProvider(OAuthProvider):
    def __init__(self, base_url: str):
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[SCOPE],
        )

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with get_conn() as conn:
            row = conn.execute("SELECT data FROM oauth_clients WHERE client_id = ?", (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(row["data"]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise ValueError("client_id is required")
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO oauth_clients (client_id, data, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(client_id) DO UPDATE SET data = excluded.data",
                (client_info.client_id, client_info.model_dump_json(), _now()),
            )

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Park the request and send the user to the approval page (admin password required)."""
        pending_id = secrets.token_urlsafe(24)
        data = {
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "scopes": params.scopes or [SCOPE],
            "code_challenge": params.code_challenge,
            "state": params.state,
            "resource": params.resource,
        }
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO oauth_pending (id, client_id, data, expires_at) VALUES (?, ?, ?, ?)",
                (pending_id, client.client_id, json.dumps(data), time.time() + PENDING_TTL),
            )
        return f"{public_base_url()}{APPROVE_PATH}?req={pending_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT data FROM oauth_codes WHERE code = ? AND client_id = ? AND expires_at >= ?",
                (authorization_code, client.client_id, time.time()),
            ).fetchone()
        return AuthorizationCode.model_validate_json(row["data"]) if row else None

    def _issue(self, conn, client_id: str, scopes: list[str]) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = _now()
        conn.execute(
            "INSERT INTO oauth_tokens (token, kind, client_id, scopes, pair, expires_at, created_at) "
            "VALUES (?, 'access', ?, ?, ?, ?, ?)",
            (access, client_id, " ".join(scopes), refresh, time.time() + ACCESS_TTL, now),
        )
        conn.execute(
            "INSERT INTO oauth_tokens (token, kind, client_id, scopes, pair, expires_at, created_at) "
            "VALUES (?, 'refresh', ?, ?, ?, NULL, ?)",
            (refresh, client_id, " ".join(scopes), access, now),
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            refresh_token=refresh,
            scope=" ".join(scopes),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        with get_conn() as conn:
            deleted = conn.execute("DELETE FROM oauth_codes WHERE code = ?", (authorization_code.code,)).rowcount
            if not deleted:
                raise TokenError("invalid_grant", "Authorization code not found or already used.")
            return self._issue(conn, client.client_id, authorization_code.scopes)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM oauth_tokens WHERE token = ? AND kind = 'refresh' AND client_id = ?",
                (refresh_token, client.client_id),
            ).fetchone()
        if not row:
            return None
        return RefreshToken(token=row["token"], client_id=row["client_id"], scopes=row["scopes"].split(), expires_at=None)

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        if not set(scopes).issubset(refresh_token.scopes):
            raise TokenError("invalid_scope", "Requested scopes exceed those originally granted.")
        with get_conn() as conn:
            self._revoke(conn, refresh_token.token)
            return self._issue(conn, client.client_id, scopes or refresh_token.scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM oauth_tokens WHERE token = ? AND kind = 'access' AND expires_at >= ?",
                (token, time.time()),
            ).fetchone()
            if not row:
                return None
            conn.execute("UPDATE oauth_tokens SET last_used_at = ? WHERE token = ?", (_now(), token))
        return AccessToken(
            token=row["token"],
            client_id=row["client_id"],
            scopes=row["scopes"].split(),
            expires_at=int(row["expires_at"]),
        )

    @staticmethod
    def _revoke(conn, token: str) -> None:
        conn.execute("DELETE FROM oauth_tokens WHERE token = ? OR pair = ?", (token, token))

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with get_conn() as conn:
            self._revoke(conn, token.token)
