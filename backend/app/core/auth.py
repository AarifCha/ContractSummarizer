import secrets
from datetime import datetime, timezone
from typing import Optional

from passlib.context import CryptContext

from app.core.db import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


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
