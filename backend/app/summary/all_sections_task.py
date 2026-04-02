from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any

from google import genai

from app.core.api_keys import get_user_api_key
from app.summary.all_sections_constants import (
    ALL_SECTIONS_PROMPT,
    ALL_SECTIONS_WINDOW_MODEL,
    WINDOW_CONCURRENCY,
    WINDOW_FETCH_MAX_ATTEMPTS,
)
from app.summary.citations import (
    build_citation_map,
    highlights_by_page,
    parse_cited_chunk_indices,
)
from app.summary.final_summary_cache import persist_final_summary
from app.summary.gemini_contract_summary import FLASH_LITE_MODEL, synthesize_final_summary_with_meta
from app.summary.page_windows import build_page_windows, format_window_text
from app.summary.task_state import (
    extract_retry_delay_seconds,
    task_get,
    task_update,
    usage_summary_for_task,
)
from app.summary.telemetry import (
    extract_usage_and_finish_reason,
    log_successful_call,
)


async def _fetch_window_summary(
    window_index: int,
    window_start: int,
    window_end: int,
    window_rows: list[dict],
    prompt: str,
    semaphore: asyncio.Semaphore,
    client: Any,
) -> dict[str, Any]:
    last_exc: Exception | None = None
    async with semaphore:
        for attempt in range(WINDOW_FETCH_MAX_ATTEMPTS):
            try:
                response = await client.aio.models.generate_content(
                    model=ALL_SECTIONS_WINDOW_MODEL,
                    contents=prompt,
                )
                text = str(getattr(response, "text", "") or "").strip()
                return {
                    "ok": True,
                    "window_index": window_index,
                    "window_start": window_start,
                    "window_end": window_end,
                    "window_rows": window_rows,
                    "text": text,
                    "raw_response": response,
                }
            except Exception as exc:
                last_exc = exc
                if attempt == WINDOW_FETCH_MAX_ATTEMPTS - 1:
                    break
                exp_wait_s = min(45.0, 1.0 * (2**attempt))
                quota_wait_s = extract_retry_delay_seconds(exc)
                base_wait_s = max(exp_wait_s, quota_wait_s or 0.0)
                wait_s = base_wait_s + random.uniform(0.2, 1.0)
                await asyncio.sleep(wait_s)
    return {
        "ok": False,
        "window_index": window_index,
        "window_start": window_start,
        "window_end": window_end,
        "window_rows": window_rows,
        "text": "",
        "error": str(last_exc) if last_exc else "unknown",
        "raw_response": None,
    }


async def _run_all_windows_async(
    client: Any,
    window_items: list[tuple[int, int, int, list[dict], str]],
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(WINDOW_CONCURRENCY)
    tasks = [
        _fetch_window_summary(wi, ws, we, wr, prompt, semaphore, client)
        for wi, ws, we, wr, prompt in window_items
    ]
    return await asyncio.gather(*tasks)


def run_all_sections_summary_task(task_id: str, user_id: int, all_rows: list[dict]) -> None:
    try:
        state = task_get(task_id) or {}
        stem = str(state.get("stem") or "")
        pdf_id = int(state.get("pdf_id") or 0)
        upload_folder_raw = str(state.get("upload_folder") or "")
        if not stem or not upload_folder_raw:
            raise RuntimeError("Task metadata missing for all-sections summary")
        upload_folder = Path(upload_folder_raw)

        api_key = get_user_api_key(user_id)
        if not api_key:
            raise RuntimeError("No Gemini API key found for this user")

        windows = build_page_windows(all_rows)
        total = len(windows)
        task_update(
            task_id,
            total_windows=total,
            completed_windows=0,
            status="running",
            phase="gemma_windows",
            flash_lite_status="pending",
        )
        by_chunk_index: dict[int, dict] = {}
        for row in all_rows:
            try:
                idx = int(row.get("chunk_index"))
            except (TypeError, ValueError):
                continue
            by_chunk_index[idx] = row
        output_blocks: list[str] = []
        raw_summary_text = ""

        window_items: list[tuple[int, int, int, list[dict], str]] = []
        for window_index, (window_start, window_end, window_rows) in enumerate(windows, start=1):
            prompt = ALL_SECTIONS_PROMPT.format(
                concatenated_5_page_text_with_chunk_ids=format_window_text(window_rows)
            )
            window_items.append((window_index, window_start, window_end, window_rows, prompt))

        client = genai.Client(api_key=api_key)
        try:
            window_results = asyncio.run(_run_all_windows_async(client, window_items))
        finally:
            closer = getattr(client, "close", None)
            if callable(closer):
                closer()

        for window_index, res in enumerate(sorted(window_results, key=lambda r: int(r["window_index"])), start=1):
            if not res.get("ok"):
                raw_summary_text = "\n\n".join(output_blocks).strip()
                cited_chunk_indices = parse_cited_chunk_indices(raw_summary_text)
                cited_rows = [by_chunk_index[idx] for idx in cited_chunk_indices if idx in by_chunk_index]
                hbp = highlights_by_page(cited_rows)
                task_update(
                    task_id,
                    status="running",
                    error=f"Window {res.get('window_index')}: {res.get('error', 'failed')}",
                    completed_windows=window_index,
                    raw_summary_text=raw_summary_text,
                    summary_text=raw_summary_text,
                    highlight_chunks=cited_rows,
                    highlights_by_page=hbp,
                    results=cited_rows,
                    phase="gemma_windows",
                    flash_lite_status="pending",
                    api_usage_summary=usage_summary_for_task(task_get(task_id) or {}),
                )
                continue

            resp = res.get("raw_response")
            text = str(res.get("text") or "").strip()
            prompt_tokens, completion_tokens, total_tokens, finish_reason = extract_usage_and_finish_reason(
                resp, response_text=text
            )
            log_successful_call(
                upload_folder=upload_folder,
                task_id=task_id,
                pdf_id=pdf_id,
                user_id=user_id,
                model_id=ALL_SECTIONS_WINDOW_MODEL,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                finish_reason=finish_reason,
            )
            if text:
                output_blocks.append(text)
            raw_summary_text = "\n\n".join(output_blocks).strip()
            cited_chunk_indices = parse_cited_chunk_indices(raw_summary_text)
            cited_rows = [by_chunk_index[idx] for idx in cited_chunk_indices if idx in by_chunk_index]
            hbp = highlights_by_page(cited_rows)
            task_update(
                task_id,
                completed_windows=window_index,
                raw_summary_text=raw_summary_text,
                summary_text=raw_summary_text,
                highlight_chunks=cited_rows,
                highlights_by_page=hbp,
                results=cited_rows,
                phase="gemma_windows",
                flash_lite_status="pending",
                api_usage_summary=usage_summary_for_task(task_get(task_id) or {}),
            )

        task_update(task_id, phase="flash_lite", flash_lite_status="running")
        final_summary, flash_resp = synthesize_final_summary_with_meta(raw_summary_text, api_key=api_key)
        flash_prompt_tokens, flash_completion_tokens, flash_total_tokens, flash_finish_reason = extract_usage_and_finish_reason(
            flash_resp, response_text=final_summary
        )
        log_successful_call(
            upload_folder=upload_folder,
            task_id=task_id,
            pdf_id=pdf_id,
            user_id=user_id,
            model_id=FLASH_LITE_MODEL,
            prompt_tokens=flash_prompt_tokens,
            completion_tokens=flash_completion_tokens,
            total_tokens=flash_total_tokens,
            finish_reason=flash_finish_reason,
        )
        persist_final_summary(upload_folder, stem, final_summary)
        cited_chunk_indices = parse_cited_chunk_indices(final_summary)
        cited_rows = [by_chunk_index[idx] for idx in cited_chunk_indices if idx in by_chunk_index]
        hbp = highlights_by_page(cited_rows)
        citation_map = build_citation_map(final_summary, by_chunk_index)
        task_update(
            task_id,
            status="done",
            phase="done",
            flash_lite_status="done",
            summary_model=FLASH_LITE_MODEL,
            summary_text=final_summary,
            highlight_chunks=cited_rows,
            highlights_by_page=hbp,
            results=cited_rows,
            citation_map=citation_map,
            api_usage_summary=usage_summary_for_task(task_get(task_id) or {}),
        )
    except Exception as exc:
        task_update(task_id, status="failed", phase="failed", flash_lite_status="failed", error=f"{exc}")
