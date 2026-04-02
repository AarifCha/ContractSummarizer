from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from app.summary.telemetry import summarize_usage_for_task

ALL_SECTION_TASKS: dict[str, dict[str, Any]] = {}
ALL_SECTION_TASKS_LOCK = threading.Lock()


def task_put(task_id: str, state: dict[str, Any]) -> None:
    with ALL_SECTION_TASKS_LOCK:
        ALL_SECTION_TASKS[task_id] = state


def task_update(task_id: str, **patch: Any) -> None:
    with ALL_SECTION_TASKS_LOCK:
        current = ALL_SECTION_TASKS.get(task_id)
        if current is None:
            return
        current.update(patch)


def task_get(task_id: str) -> dict[str, Any] | None:
    with ALL_SECTION_TASKS_LOCK:
        current = ALL_SECTION_TASKS.get(task_id)
        if current is None:
            return None
        return dict(current)


def usage_summary_for_task(task: dict[str, Any]) -> dict[str, Any]:
    upload_folder_raw = task.get("upload_folder")
    task_id = task.get("task_id")
    if not isinstance(upload_folder_raw, str) or not upload_folder_raw.strip():
        return {
            "per_model": {},
            "totals": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "input_cost": 0.0,
                "output_cost": 0.0,
                "total_cost": 0.0,
            },
        }
    if not isinstance(task_id, str) or not task_id.strip():
        return {
            "per_model": {},
            "totals": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "input_cost": 0.0,
                "output_cost": 0.0,
                "total_cost": 0.0,
            },
        }

    return summarize_usage_for_task(Path(upload_folder_raw), task_id)


def extract_retry_delay_seconds(exc: Exception) -> float | None:
    """
    Best-effort extraction of retry delay from Gemini quota/rate-limit errors.
    Handles phrases like:
      - 'Please retry in 27.545489003s'
      - 'retry_delay { seconds: 27 }'
    """
    msg = str(exc)
    m = re.search(r"retry in\s+([0-9]+(?:\.[0-9]+)?)s", msg, flags=re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    m = re.search(r"retry_delay\s*\{\s*seconds:\s*([0-9]+)", msg, flags=re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None
