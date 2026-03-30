import secrets
import base64
import hashlib
from datetime import datetime, timezone
from typing import Optional

import bcrypt

from app.core.db import get_db

PASSWORD_HASH_PREFIX = "v2$"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prehash_password(password: str) -> bytes:
    # Normalize to a fixed-size input so bcrypt never sees >72 bytes.
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(_prehash_password(password), bcrypt.gensalt())
    return f"{PASSWORD_HASH_PREFIX}{hashed.decode('utf-8')}"


def verify_password(password: str, password_hash: str) -> bool:
    if password_hash.startswith(PASSWORD_HASH_PREFIX):
        encoded = password_hash[len(PASSWORD_HASH_PREFIX) :].encode("utf-8")
        return bcrypt.checkpw(_prehash_password(password), encoded)

    if password_hash.startswith("$2"):
        password_bytes = password.encode("utf-8")
        try:
            return bcrypt.checkpw(password_bytes, password_hash.encode("utf-8"))
        except ValueError:
            # Support legacy behavior where bcrypt silently truncated long inputs.
            return bcrypt.checkpw(password_bytes[:72], password_hash.encode("utf-8"))

    return False


def create_session(user_id: int) -> str:
    token = secrets.token_hex(32)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, now_iso()),
        )
        conn.commit()
    return token


def get_user_from_token(token: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT users.id, users.email
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ?
            """,
            (token,),
        ).fetchone()
    if not row:
        return None
    return {"id": row["id"], "email": row["email"]}


def delete_session(token: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
