import secrets
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.api.pdfs.constants import BASE_DIR, UPLOAD_DIR
from app.api.pdfs.schemas import SectionSearchRequest
from app.api.pdfs.storage_paths import is_supported_storage_path
from app.core.db import get_db
from app.core.deps import current_user
from app.preprocessing.chunk_vector_index import search_chunks_in_chromadb
from app.qa.local_usefulness_classifier import filter_retrieved_chunks
from app.summary.all_sections_task import run_all_sections_summary_task
from app.summary.chunk_data import load_all_chunks_for_stem, page_then_chunk_sort_key
from app.summary.citations import build_citation_map, highlights_by_page, parse_cited_chunk_indices
from app.summary.final_summary_cache import load_cached_final_summary
from app.summary.gemini_contract_summary import FLASH_LITE_MODEL
from app.summary.section_queries import ALL_SECTIONS_LABEL, load_section_queries
from app.summary.task_state import task_get, task_put, usage_summary_for_task

router = APIRouter()

_DEFAULT_USAGE = {
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


@router.get("/section-options")
def section_options(user=Depends(current_user)):
    del user
    try:
        sections = load_section_queries(Path(BASE_DIR))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"section_options_error: {exc}") from exc
    return {"sections": [ALL_SECTIONS_LABEL, *list(sections.keys())]}


@router.post("/{pdf_id}/section-search")
def section_search(
    pdf_id: int,
    payload: SectionSearchRequest,
    background_tasks: BackgroundTasks,
    user=Depends(current_user),
):
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT stored_name, original_name, processing_status
            FROM pdfs
            WHERE id = ? AND user_id = ?
            """,
            (pdf_id, user["id"]),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    if not is_supported_storage_path(row["stored_name"], user):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Legacy storage format is unsupported")
    if row["processing_status"] != "done":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="PDF processing is not complete yet",
        )

    try:
        section_map = load_section_queries(Path(BASE_DIR))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"section_search_error: {exc}") from exc

    section_name = payload.section.strip()
    pdf_path = Path(UPLOAD_DIR) / row["stored_name"]
    upload_folder = pdf_path.parent
    first_pass_dir = upload_folder / "first_pass_data"
    if not first_pass_dir.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chunk data not found for this PDF")

    stem = Path(row["original_name"]).stem

    if section_name == ALL_SECTIONS_LABEL:
        try:
            all_rows = load_all_chunks_for_stem(first_pass_dir, stem)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"section_search_error: {exc}") from exc

        cached_summary = load_cached_final_summary(upload_folder, stem)
        if cached_summary:
            by_chunk_index: dict[int, dict] = {}
            for row_item in all_rows:
                try:
                    idx = int(row_item.get("chunk_index"))
                except (TypeError, ValueError):
                    continue
                by_chunk_index[idx] = row_item
            cited_chunk_indices = parse_cited_chunk_indices(cached_summary)
            cited_rows = [by_chunk_index[idx] for idx in cited_chunk_indices if idx in by_chunk_index]
            task_id = secrets.token_hex(16)
            task_put(
                task_id,
                {
                    "task_id": task_id,
                    "pdf_id": pdf_id,
                    "user_id": user["id"],
                    "section": section_name,
                    "status": "done",
                    "phase": "done",
                    "total_windows": 0,
                    "completed_windows": 0,
                    "raw_summary_text": "",
                    "summary_text": cached_summary,
                    "highlight_chunks": cited_rows,
                    "highlights_by_page": highlights_by_page(cited_rows),
                    "results": cited_rows,
                    "error": None,
                    "summary_model": FLASH_LITE_MODEL,
                    "flash_lite_status": "done",
                    "citation_map": build_citation_map(cached_summary, by_chunk_index),
                    "stem": stem,
                    "upload_folder": str(upload_folder),
                    "api_usage_summary": usage_summary_for_task({"task_id": task_id, "upload_folder": str(upload_folder)}),
                },
            )
            cached_task = task_get(task_id) or {}
            return {
                "mode": "all_sections_summary",
                "section": section_name,
                "queries": [],
                "task_id": task_id,
                "status": "done",
                "phase": "done",
                "flash_lite_status": "done",
                "total_windows": 0,
                "completed_windows": 0,
                "results": cited_rows,
                "highlight_chunks": cited_rows,
                "highlights_by_page": highlights_by_page(cited_rows),
                "raw_summary_text": "",
                "summary_text": cached_summary,
                "summary_model": FLASH_LITE_MODEL,
                "citation_map": build_citation_map(cached_summary, by_chunk_index),
                "api_usage_summary": cached_task.get("api_usage_summary", _DEFAULT_USAGE),
            }

        task_id = secrets.token_hex(16)
        task_put(
            task_id,
            {
                "task_id": task_id,
                "pdf_id": pdf_id,
                "user_id": user["id"],
                "section": section_name,
                "status": "queued",
                "phase": "queued",
                "total_windows": 0,
                "completed_windows": 0,
                "raw_summary_text": "",
                "summary_text": "",
                "highlight_chunks": [],
                "highlights_by_page": {},
                "results": [],
                "error": None,
                "summary_model": FLASH_LITE_MODEL,
                "flash_lite_status": "pending",
                "citation_map": {},
                "stem": stem,
                "upload_folder": str(upload_folder),
                "api_usage_summary": usage_summary_for_task({"task_id": task_id, "upload_folder": str(upload_folder)}),
            },
        )
        background_tasks.add_task(run_all_sections_summary_task, task_id, user["id"], all_rows)

        return {
            "mode": "all_sections_summary",
            "section": section_name,
            "queries": [],
            "task_id": task_id,
            "status": "queued",
            "phase": "queued",
            "total_windows": 0,
            "completed_windows": 0,
            "results": [],
            "highlight_chunks": [],
            "highlights_by_page": {},
            "raw_summary_text": "",
            "summary_text": "",
            "summary_model": FLASH_LITE_MODEL,
            "flash_lite_status": "pending",
            "citation_map": {},
            "api_usage_summary": (task_get(task_id) or {}).get("api_usage_summary", _DEFAULT_USAGE),
        }

    queries = section_map.get(section_name)
    if not queries:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown section: {section_name}")
    sections_to_run = [section_name]

    merged: list[dict] = []
    try:
        for section in sections_to_run:
            section_queries = section_map.get(section, [])
            for query in section_queries:
                stage1 = search_chunks_in_chromadb(
                    first_pass_dir,
                    stem,
                    query,
                    top_k=payload.top_k_per_query,
                )
                stage2 = filter_retrieved_chunks(query, stage1)
                for row_item in stage2:
                    copied = dict(row_item)
                    copied["origin_sections"] = [section]
                    merged.append(copied)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"section_search_error: {exc}") from exc

    merged.sort(key=lambda r: str(r.get("chunk_id") or ""))
    dedup: dict[str, dict] = {}
    for row_item in merged:
        cid = str(row_item.get("chunk_id") or "").strip()
        if not cid:
            continue
        if cid not in dedup:
            dedup[cid] = row_item
            continue
        existing = dedup[cid]
        existing_origins = existing.get("origin_sections")
        if not isinstance(existing_origins, list):
            existing_origins = []
        new_origins = row_item.get("origin_sections")
        if not isinstance(new_origins, list):
            new_origins = []
        seen = {str(x) for x in existing_origins}
        for origin in new_origins:
            s = str(origin)
            if s not in seen:
                existing_origins.append(s)
                seen.add(s)
        existing["origin_sections"] = existing_origins

    results = sorted(dedup.values(), key=page_then_chunk_sort_key)
    return {"section": section_name, "queries": queries, "results": results}


@router.get("/section-search-status/{task_id}")
def section_search_status(task_id: str, user=Depends(current_user)):
    task = task_get(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.get("user_id") != user["id"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    task["api_usage_summary"] = usage_summary_for_task(task)
    return task
