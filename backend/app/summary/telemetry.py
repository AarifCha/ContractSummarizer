from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _ensure_api_calls_db(upload_folder: Path) -> Path:
    api_calls_dir = upload_folder / "api_calls"
    api_calls_dir.mkdir(parents=True, exist_ok=True)
    db_path = api_calls_dir / "calls.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_calls (
              call_id INTEGER PRIMARY KEY AUTOINCREMENT,
              task_id TEXT NOT NULL,
              pdf_id INTEGER NOT NULL,
              user_id INTEGER NOT NULL,
              model_id TEXT NOT NULL,
              prompt_tokens INTEGER NOT NULL,
              completion_tokens INTEGER NOT NULL,
              total_tokens INTEGER NOT NULL,
              estimated_cost REAL NOT NULL,
              finish_reason TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_calls_task_id ON api_calls(task_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_calls_task_model ON api_calls(task_id, model_id)"
        )
        conn.commit()
    return db_path


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_usage_field(usage: Any, *keys: str) -> Any:
    if usage is None:
        return None
    for key in keys:
        if isinstance(usage, dict) and key in usage:
            return usage.get(key)
        if hasattr(usage, key):
            return getattr(usage, key)
    return None


def _usage_metadata_to_dict(usage: Any) -> dict[str, Any]:
    """Best-effort: Google protobuf usage_metadata -> plain dict."""
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    try:
        from google.protobuf.json_format import MessageToDict

        if hasattr(usage, "DESCRIPTOR"):
            raw = MessageToDict(usage, preserving_proto_field_name=True)
            return raw if isinstance(raw, dict) else {}
    except Exception:
        pass
    out: dict[str, Any] = {}
    if hasattr(usage, "ListFields"):
        try:
            for desc, val in usage.ListFields():
                out[desc.name] = val
        except Exception:
            pass
    return out


def _rough_tokens_from_text(text: str) -> int:
    """When the API omits completion counts, approximate from UTF-8 text length (~4 chars/token)."""
    if not text or not text.strip():
        return 0
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def extract_usage_and_finish_reason(
    resp: Any,
    *,
    response_text: str | None = None,
) -> tuple[int, int, int, str | None]:
    """
    Read token counts from the provider response object (google-generativeai).

    Tries, in order:
    - OpenAI-style: resp.usage.{prompt_tokens, completion_tokens, total_tokens}
    - Google: usage_metadata (object or protobuf) with prompt_token_count, candidates_token_count, total_token_count
    - Alternate field names some models use
    - If completion is still 0 but response_text is non-empty, approximate output tokens from text length
      (API sometimes omits candidates_token_count for Gemma).
    """
    # OpenAI-compatible
    usage_oa = getattr(resp, "usage", None)
    prompt_oa = _as_int(_read_usage_field(usage_oa, "prompt_tokens"), 0)
    completion_oa = _as_int(_read_usage_field(usage_oa, "completion_tokens"), 0)
    total_oa = _as_int(_read_usage_field(usage_oa, "total_tokens"), prompt_oa + completion_oa)

    # Google usage_metadata (dict or proto)
    um = _usage_metadata_to_dict(getattr(resp, "usage_metadata", None))
    prompt_g = _as_int(
        um.get("prompt_token_count")
        or um.get("promptTokenCount")
        or _read_usage_field(getattr(resp, "usage_metadata", None), "prompt_token_count"),
        0,
    )
    completion_g = _as_int(
        um.get("candidates_token_count")
        or um.get("candidatesTokenCount")
        or um.get("output_token_count")
        or um.get("outputTokenCount")
        or _read_usage_field(getattr(resp, "usage_metadata", None), "candidates_token_count"),
        0,
    )
    total_g = _as_int(
        um.get("total_token_count")
        or um.get("totalTokenCount")
        or _read_usage_field(getattr(resp, "usage_metadata", None), "total_token_count"),
        prompt_g + completion_g,
    )

    cached = _as_int(um.get("cached_content_token_count") or um.get("cachedContentTokenCount"), 0)
    thoughts = _as_int(um.get("thoughts_token_count") or um.get("thoughtsTokenCount"), 0)

    if prompt_oa or completion_oa or total_oa:
        prompt_tokens = prompt_oa
        completion_tokens = completion_oa
        total_tokens = total_oa
    else:
        prompt_tokens = prompt_g
        completion_tokens = completion_g
        total_tokens = total_g

    if completion_tokens <= 0 and total_tokens > prompt_tokens + cached + thoughts:
        completion_tokens = total_tokens - prompt_tokens - cached - thoughts
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens

    text = response_text if response_text is not None else str(getattr(resp, "text", "") or "")
    if completion_tokens <= 0 and text.strip():
        completion_tokens = _rough_tokens_from_text(text)
        if total_tokens < prompt_tokens + completion_tokens:
            total_tokens = prompt_tokens + completion_tokens

    finish_reason: str | None = None
    candidates = getattr(resp, "candidates", None)
    if isinstance(candidates, list) and candidates:
        candidate0 = candidates[0]
        finish_reason_raw = getattr(candidate0, "finish_reason", None)
        if finish_reason_raw is not None:
            finish_reason = str(finish_reason_raw)
    if finish_reason is None:
        choices = getattr(resp, "choices", None)
        if isinstance(choices, list) and choices:
            c0 = choices[0]
            if isinstance(c0, dict):
                fr = c0.get("finish_reason")
            else:
                fr = getattr(c0, "finish_reason", None)
            if fr is not None:
                finish_reason = str(fr)

    return (
        max(0, prompt_tokens),
        max(0, completion_tokens),
        max(0, total_tokens),
        finish_reason,
    )


def log_successful_call(
    *,
    upload_folder: Path,
    task_id: str,
    pdf_id: int,
    user_id: int,
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    finish_reason: str | None,
) -> int:
    db_path = _ensure_api_calls_db(upload_folder)
    created_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO api_calls (
              task_id,
              pdf_id,
              user_id,
              model_id,
              prompt_tokens,
              completion_tokens,
              total_tokens,
              estimated_cost,
              finish_reason,
              created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                int(pdf_id),
                int(user_id),
                str(model_id),
                max(0, int(prompt_tokens)),
                max(0, int(completion_tokens)),
                max(0, int(total_tokens)),
                0.0,
                (str(finish_reason) if finish_reason is not None else None),
                created_at,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def summarize_usage_for_task(upload_folder: Path, task_id: str) -> dict[str, Any]:
    db_path = _ensure_api_calls_db(upload_folder)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
              model_id,
              SUM(prompt_tokens) AS total_prompt_tokens,
              SUM(completion_tokens) AS total_completion_tokens,
              SUM(total_tokens) AS total_tokens
            FROM api_calls
            WHERE task_id = ?
            GROUP BY model_id
            ORDER BY model_id ASC
            """,
            (task_id,),
        ).fetchall()
    per_model: dict[str, Any] = {}
    grand_input_tokens = 0
    grand_output_tokens = 0
    grand_total_tokens = 0

    for row in rows:
        model_id = str(row["model_id"])
        prompt_tokens = _as_int(row["total_prompt_tokens"], 0)
        completion_tokens = _as_int(row["total_completion_tokens"], 0)
        total_tokens = _as_int(row["total_tokens"], prompt_tokens + completion_tokens)
        per_model[model_id] = {
            "model_id": model_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "input_cost": 0.0,
            "output_cost": 0.0,
            "total_cost": 0.0,
        }
        grand_input_tokens += prompt_tokens
        grand_output_tokens += completion_tokens
        grand_total_tokens += total_tokens

    return {
        "per_model": per_model,
        "totals": {
            "prompt_tokens": grand_input_tokens,
            "completion_tokens": grand_output_tokens,
            "total_tokens": grand_total_tokens,
            "input_cost": 0.0,
            "output_cost": 0.0,
            "total_cost": 0.0,
        },
    }
