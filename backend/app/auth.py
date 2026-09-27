"""Single-admin password auth.

The admin password hash and a random signing secret live in the `settings` table.
On first start the password comes from ADMIN_PASSWORD (default "admin").
Login returns a signed, expiring token sent back as `Authorization: Bearer <token>`.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Header, HTTPException

from .db import get_conn

TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30
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


def ensure_admin_credentials() -> None:
    with get_conn() as conn:
        if _get_setting(conn, "admin_password_hash") is None:
            _set_setting(conn, "admin_password_hash", hash_password(os.environ.get("ADMIN_PASSWORD", "admin")))
        if _get_setting(conn, "token_secret") is None:
            _set_setting(conn, "token_secret", secrets.token_hex(32))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(payload: str, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())


def issue_token() -> str:
    with get_conn() as conn:
        secret = _get_setting(conn, "token_secret") or ""
    payload = _b64(json.dumps({"exp": int(time.time()) + TOKEN_TTL_SECONDS}).encode())
    return f"{payload}.{_sign(payload, secret)}"


def check_password(password: str) -> bool:
    with get_conn() as conn:
        stored = _get_setting(conn, "admin_password_hash")
    return bool(stored) and verify_password(password, stored)


def set_password(new_password: str) -> None:
    with get_conn() as conn:
        _set_setting(conn, "admin_password_hash", hash_password(new_password))
        _set_setting(conn, "token_secret", secrets.token_hex(32))


def _token_valid(token: str) -> bool:
    try:
        payload, sig = token.split(".", 1)
    except ValueError:
        return False
    with get_conn() as conn:
        secret = _get_setting(conn, "token_secret") or ""
    if not hmac.compare_digest(_sign(payload, secret), sig):
        return False
    try:
        return json.loads(_unb64(payload))["exp"] > time.time()
    except (ValueError, KeyError, TypeError):
        return False


def require_admin(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Admin login required")
    if not _token_valid(authorization.removeprefix("Bearer ").strip()):
        raise HTTPException(401, "Session expired, please log in again")
