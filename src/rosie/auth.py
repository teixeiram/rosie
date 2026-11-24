"""Accounts, password login, cookie sessions and role checks.

Roles are enforced here on the server: `require_admin` returns 403 for anyone
but an admin, whatever the UI shows. Passwords are hashed with scrypt; session
tokens are random and only their SHA-256 is stored.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

COOKIE = "sid"
ROLES = ("admin", "member")
# Login throttling: this many failures per username in the window locks it.
MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60

_SCRYPT = {"n": 2**15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def public(self) -> dict:
        return {"username": self.username, "display_name": self.display_name, "role": self.role}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(dk.hex(), dk_hex)


# A real hash to compare against when the username doesn't exist, so a wrong
# username takes as long as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def _row_user(row: sqlite3.Row) -> User:
    return User(row["id"], row["username"], row["display_name"], row["role"])


def create_user(
    conn: sqlite3.Connection, username: str, display_name: str, role: str, password: str
) -> User:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    cur = conn.execute(
        "INSERT INTO users (username, display_name, role, password_hash, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (username.strip(), display_name.strip(), role, hash_password(password), int(time.time())),
    )
    return User(cur.lastrowid, username.strip(), display_name.strip(), role)


def set_password(conn: sqlite3.Connection, username: str, password: str) -> None:
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    cur = conn.execute(
        "UPDATE users SET password_hash = ? WHERE username = ?", (hash_password(password), username)
    )
    if cur.rowcount != 1:
        raise ValueError(f"no user {username!r}")
    # Changing a password signs out every device.
    conn.execute(
        "DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)",
        (username,),
    )


class LoginLocked(Exception):
    pass


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> User | None:
    now = int(time.time())
    conn.execute("DELETE FROM login_failures WHERE at < ?", (now - FAILURE_WINDOW,))
    failures = conn.execute(
        "SELECT COUNT(*) FROM login_failures WHERE username = ?", (username,)
    ).fetchone()[0]
    if failures >= MAX_FAILURES:
        raise LoginLocked()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    ok = check_password(password, row["password_hash"] if row else _DUMMY_HASH)
    if row is None or not ok:
        conn.execute(
            "INSERT INTO login_failures (username, at) VALUES (?, ?)", (username[:64], now)
        )
        return None
    conn.execute("DELETE FROM login_failures WHERE username = ?", (username,))
    return _row_user(row)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(conn: sqlite3.Connection, user: User, days: int) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (_token_hash(token), user.id, now, now + days * 86400),
    )
    return token


def end_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def user_for_token(conn: sqlite3.Connection, token: str) -> User | None:
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash = ? AND s.expires_at > ?",
        (_token_hash(token), int(time.time())),
    ).fetchone()
    return _row_user(row) if row else None


# --- FastAPI dependencies ---------------------------------------------------


def current_user(request: Request) -> User:
    token = request.cookies.get(COOKIE)
    user = user_for_token(request.app.state.db, token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only for the admin account")
    return user
