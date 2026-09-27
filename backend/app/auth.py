"""Named user accounts. Each person logs in with their own name + password and gets a signed,
expiring token carrying their user id and role (`admin` or `member`). `connector` is the MCP
server (Claude) and authenticates with an OAuth access token issued by oauth.py.

Members get long sessions renewed on every request (sliding window); admin tokens are short.
Changing a user's password bumps that user's token version, signing out only their devices.

Bootstrap: when the users table is empty, HOME_USERS ("Courtney:admin,Magnus:admin,Susan,Vanessa")
is created. Each new user's password comes from PASSWORD_<NAME>, else the legacy stored
admin/household password (upgrading an existing install), else ADMIN_PASSWORD/HOUSEHOLD_PASSWORD.
There is no default: a user with no password source aborts startup naming the variable.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time

from fastapi import HTTPException, Request

from . import oauth
from .db import get_conn

ADMIN = "admin"
MEMBER = "member"
CONNECTOR = "connector"
USER_ROLES = (ADMIN, MEMBER)
ALL_ROLES = (ADMIN, MEMBER, CONNECTOR)
TOKEN_TTL = {ADMIN: 60 * 60 * 24 * 30, MEMBER: 60 * 60 * 24 * 365}
PBKDF2_ITERATIONS = 200_000

DEFAULT_HOME_USERS = "Courtney:admin,Magnus:admin,Susan,Vanessa"
LEGACY_PASSWORD_KEY = {ADMIN: "admin_password_hash", MEMBER: "household_password_hash"}
LEGACY_PASSWORD_ENV = {ADMIN: "ADMIN_PASSWORD", MEMBER: "HOUSEHOLD_PASSWORD"}
LEGACY_DEFAULT_PASSWORD = {ADMIN: "admin", MEMBER: "home"}


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


class MissingPasswordError(RuntimeError):
    pass


def password_env_var(name: str) -> str:
    return "PASSWORD_" + re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")


def _parse_home_users(spec: str) -> list[tuple[str, str]]:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, role = part.partition(":")
        out.append((name.strip(), ADMIN if role.strip().lower() == ADMIN else MEMBER))
    return out


def _legacy_hash(conn, role: str) -> str | None:
    stored = _get_setting(conn, LEGACY_PASSWORD_KEY[role])
    if stored and not verify_password(LEGACY_DEFAULT_PASSWORD[role], stored):
        return stored
    return None


def ensure_users() -> None:
    """Create the household's accounts on first start (or any HOME_USERS name that is missing)."""
    with get_conn() as conn:
        if _get_setting(conn, "token_secret") is None:
            _set_setting(conn, "token_secret", secrets.token_hex(32))
        spec = os.environ.get("HOME_USERS")
        have_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
        if have_users and not spec:
            return
        wanted = _parse_home_users(spec or DEFAULT_HOME_USERS)
        existing = {r["name"].lower() for r in conn.execute("SELECT name FROM users")}
        to_create = [(n, r) for n, r in wanted if n.lower() not in existing]
        plan, missing = [], []
        for name, role in to_create:
            env_pw = os.environ.get(password_env_var(name))
            legacy_env = os.environ.get(LEGACY_PASSWORD_ENV[role])
            if env_pw:
                plan.append((name, role, hash_password(env_pw)))
            elif _legacy_hash(conn, role):
                plan.append((name, role, _legacy_hash(conn, role)))
            elif legacy_env:
                plan.append((name, role, hash_password(legacy_env)))
            else:
                missing.append(password_env_var(name))
        if missing:
            raise MissingPasswordError(
                f"Refusing to start: no password configured for {', '.join(missing)}. "
                "Set the environment variable(s) and restart."
            )
        for name, role, pw_hash in plan:
            conn.execute(
                "INSERT INTO users (name, role, password_hash, token_version, created_at) VALUES (?, ?, ?, ?, ?)",
                (name, role, pw_hash, "0", time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())),
            )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(payload: str, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())


def public_user(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "role": row["role"], "active": bool(row["active"]), "email": row["email"]}


def get_user(conn, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def find_user_by_name(conn, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE lower(name) = lower(?)", (name.strip(),)).fetchone()


def issue_token(user: sqlite3.Row | dict) -> str:
    with get_conn() as conn:
        secret = _get_setting(conn, "token_secret") or ""
    payload = _b64(
        json.dumps(
            {"u": user["id"], "r": user["role"], "v": user["token_version"], "exp": int(time.time()) + TOKEN_TTL[user["role"]]}
        ).encode()
    )
    return f"{payload}.{_sign(payload, secret)}"


def login(name: str, password: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        user = find_user_by_name(conn, name)
    if user and user["active"] and verify_password(password, user["password_hash"]):
        return user
    return None


def admin_for_password(password: str) -> sqlite3.Row | None:
    """The admin whose password this is (used by the connector consent page)."""
    with get_conn() as conn:
        admins = conn.execute("SELECT * FROM users WHERE role = ? AND active = 1", (ADMIN,)).fetchall()
    return next((a for a in admins if verify_password(password, a["password_hash"])), None)


def check_user_password(user_id: int, password: str) -> bool:
    with get_conn() as conn:
        user = get_user(conn, user_id)
    return bool(user) and verify_password(password, user["password_hash"])


def set_user_password(user_id: int, new_password: str) -> None:
    """Changing a password signs out every device of that user, and nobody else."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, token_version = ? WHERE id = ?",
            (hash_password(new_password), secrets.token_hex(8), user_id),
        )


def token_user(token: str) -> sqlite3.Row | None:
    """The user behind a valid, unexpired session token; None otherwise."""
    try:
        payload, sig = token.split(".", 1)
    except ValueError:
        return None
    with get_conn() as conn:
        secret = _get_setting(conn, "token_secret") or ""
        if not hmac.compare_digest(_sign(payload, secret), sig):
            return None
        try:
            data = json.loads(_unb64(payload))
            if data["exp"] <= time.time() or data["r"] not in USER_ROLES:
                return None
            user = get_user(conn, int(data["u"]))
        except (ValueError, KeyError, TypeError):
            return None
    if user and user["active"] and user["role"] == data["r"] and user["token_version"] == data["v"]:
        return user
    return None


def authenticate(authorization: str | None) -> tuple[str, sqlite3.Row | None]:
    """(role, user) for a bearer header or raise 401. User-session tokens first, then OAuth
    access tokens (connector role, no user)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Login required")
    token = authorization.removeprefix("Bearer ").strip()
    user = token_user(token)
    if user is not None:
        return user["role"], user
    if oauth.access_token_valid(token):
        return CONNECTOR, None
    raise HTTPException(401, "Session expired, please log in again")


# Route-level role declarations. The middleware in main.py already guarantees a valid session
# (request.state.role); these only decide *which* role may use the route.


def need_member(request: Request) -> None:
    """Any logged-in role (member, admin or connector)."""
    if request.state.role not in ALL_ROLES:
        raise HTTPException(401, "Login required")


def need_admin(request: Request) -> None:
    if request.state.role != ADMIN:
        raise HTTPException(403, "Admin access required")


def need_manager(request: Request) -> None:
    """Admin or the connector (Claude): may change rooms, but a member may not."""
    if request.state.role not in (ADMIN, CONNECTOR):
        raise HTTPException(403, "Only an admin (or Claude) can change rooms")


ROLE_DEPENDENCIES = (need_member, need_admin, need_manager)
