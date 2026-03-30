from datetime import datetime, timezone

from app.core.db import get_db

PROVIDER_GEMINI = "gemini"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_user_api_key(user_id: int, api_key: str, provider: str = PROVIDER_GEMINI) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO api_keys (user_id, provider, api_key, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              provider = excluded.provider,
              api_key = excluded.api_key,
              updated_at = excluded.updated_at
            """,
            (user_id, provider, api_key, now_iso()),
        )
        conn.commit()


def get_user_api_key(user_id: int, provider: str = PROVIDER_GEMINI) -> str | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT api_key FROM api_keys WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        ).fetchone()
    if not row:
        return None
    return row["api_key"]


def delete_user_api_key(user_id: int, provider: str = PROVIDER_GEMINI) -> None:
    with get_db() as conn:
        conn.execute(
            "DELETE FROM api_keys WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        )
        conn.commit()
