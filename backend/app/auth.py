"""Role auth: `household` and `admin` log in with a password; `connector` is the MCP server
(Claude) and authenticates with an OAuth access token issued by oauth.py.

Password hashes and a random signing secret live in the `settings` table. On first start
the passwords come from ADMIN_PASSWORD and HOUSEHOLD_PASSWORD (no defaults).
Login returns a signed, expiring token carrying the role, sent back as `Authorization: Bearer <token>`.
Household tokens are long-lived and renewed on every request (sliding window); admin tokens are short.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import HTTPException, Request

from . import oauth
from .db import get_conn

ADMIN = "admin"
HOUSEHOLD = "household"
CONNECTOR = "connector"
ROLES = (ADMIN, HOUSEHOLD)  # password roles
ALL_ROLES = (ADMIN, HOUSEHOLD, CONNECTOR)
TOKEN_TTL = {ADMIN: 60 * 60 * 24 * 30, HOUSEHOLD: 60 * 60 * 24 * 365}
PASSWORD_KEY = {ADMIN: "admin_password_hash", HOUSEHOLD: "household_password_hash"}
VERSION_KEY = {ADMIN: "admin_token_version", HOUSEHOLD: "household_token_version"}
PBKDF2_ITERATIONS = 200_000


def _get_setting(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_password(password, bytes.fromhex(salt_hex)), stored)


PASSWORD_ENV = {ADMIN: "ADMIN_PASSWORD", HOUSEHOLD: "HOUSEHOLD_PASSWORD"}


LEGACY_DEFAULT_PASSWORD = {ADMIN: "admin", HOUSEHOLD: "home"}


class MissingPasswordError(RuntimeError):
    pass


def _needs_seed(conn, role: str) -> bool:
    stored = _get_setting(conn, PASSWORD_KEY[role])
    return stored is None or verify_password(LEGACY_DEFAULT_PASSWORD[role], stored)


def ensure_admin_credentials() -> None:
    """Seed each role's password from its env var on first start. There is no default:
    a role with no usable stored password and no env var aborts startup. A password left
    at a legacy built-in default counts as unset."""
    with get_conn() as conn:
        to_seed = [role for role in ROLES if _needs_seed(conn, role)]
        missing = [PASSWORD_ENV[role] for role in to_seed if not os.environ.get(PASSWORD_ENV[role])]
        if missing:
            raise MissingPasswordError(
                f"Refusing to start: no password configured for {', '.join(missing)}. "
                "Set the environment variable(s) and restart."
            )
        for role in to_seed:
            _set_setting(conn, PASSWORD_KEY[role], hash_password(os.environ[PASSWORD_ENV[role]]))
        if _get_setting(conn, "token_secret") is None:
            _set_setting(conn, "token_secret", secrets.token_hex(32))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(payload: str, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())


def issue_token(role: str) -> str:
    assert role in ROLES
    with get_conn() as conn:
        secret = _get_setting(conn, "token_secret") or ""
        version = _get_setting(conn, VERSION_KEY[role]) or "0"
    payload = _b64(json.dumps({"role": role, "v": version, "exp": int(time.time()) + TOKEN_TTL[role]}).encode())
    return f"{payload}.{_sign(payload, secret)}"


def check_password(role: str, password: str) -> bool:
    with get_conn() as conn:
        stored = _get_setting(conn, PASSWORD_KEY[role])
    return bool(stored) and verify_password(password, stored)


def role_for_password(password: str) -> str | None:
    """Admin is checked first so the two passwords can never collide into the weaker role."""
    for role in ROLES:
        if check_password(role, password):
            return role
    return None


def set_password(role: str, new_password: str) -> None:
    """Changing a role's password invalidates every existing session of that role."""
    with get_conn() as conn:
        _set_setting(conn, PASSWORD_KEY[role], hash_password(new_password))
        _set_setting(conn, VERSION_KEY[role], secrets.token_hex(8))


def token_role(token: str) -> str | None:
    """Role carried by a valid, unexpired token; None otherwise."""
    try:
        payload, sig = token.split(".", 1)
    except ValueError:
        return None
    with get_conn() as conn:
        secret = _get_setting(conn, "token_secret") or ""
        versions = {role: _get_setting(conn, VERSION_KEY[role]) or "0" for role in ROLES}
    if not hmac.compare_digest(_sign(payload, secret), sig):
        return None
    try:
        data = json.loads(_unb64(payload))
        if data["exp"] > time.time() and data["role"] in ROLES and data["v"] == versions[data["role"]]:
            return data["role"]
    except (ValueError, KeyError, TypeError):
        pass
    return None


def authenticate(authorization: str | None) -> str:
    """Return the role for a bearer header or raise 401. Password-session tokens first,
    then OAuth access tokens (connector role)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Login required")
    token = authorization.removeprefix("Bearer ").strip()
    role = token_role(token)
    if role is None and oauth.access_token_valid(token):
        role = CONNECTOR
    if role is None:
        raise HTTPException(401, "Session expired, please log in again")
    return role


# Route-level role declarations. The middleware in main.py already guarantees a valid session
# (request.state.role); these only decide *which* role may use the route.


def need_household(request: Request) -> None:
    """Any logged-in role (household, admin or connector)."""
    if request.state.role not in ALL_ROLES:
        raise HTTPException(401, "Login required")


def need_admin(request: Request) -> None:
    if request.state.role != ADMIN:
        raise HTTPException(403, "Admin access required")


ROLE_DEPENDENCIES = (need_household, need_admin)
