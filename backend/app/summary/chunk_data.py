from __future__ import annotations

import json
import re
from pathlib import Path


def chunk_order_sort_key(row: dict) -> tuple[int, str]:
    try:
        chunk_index = int(row.get("chunk_index", 10**9))
    except (TypeError, ValueError):
        chunk_index = 10**9
    chunk_id = str(row.get("chunk_id") or "")
    return (chunk_index, chunk_id)


def page_then_chunk_sort_key(row: dict) -> tuple[int, int]:
    pages = row.get("page_numbers")
    if isinstance(pages, list) and pages:
        try:
            page0 = int(pages[0])
        except (TypeError, ValueError):
            page0 = 10**9
    else:
        page0 = 10**9
    try:
        chunk_index = int(row.get("chunk_index", 10**9))
    except (TypeError, ValueError):
        chunk_index = 10**9
    return (page0, chunk_index)


def load_all_chunks_for_stem(first_pass_dir: Path, stem: str) -> list[dict]:
    def _normalize_stem_token(value: str) -> str:
        cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value))
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        return cleaned

    paths = sorted(first_pass_dir.glob(f"{stem}_chunk_*.json"), key=lambda p: p.name)
    if not paths:
        target = _normalize_stem_token(stem)
        fallback: list[Path] = []
        for p in first_pass_dir.glob("*_chunk_*.json"):
            m = re.match(r"^(.*)_chunk_(\d+)\.json$", p.name)
            if not m:
                continue
            file_stem = m.group(1)
            if _normalize_stem_token(file_stem) == target:
                fallback.append(p)
        paths = sorted(fallback, key=lambda p: p.name)
    rows: list[dict] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        try:
            chunk_index = int(payload.get("chunk_index"))
        except (TypeError, ValueError):
            m = re.search(r"_chunk_(\d+)\.json$", path.name)
            chunk_index = int(m.group(1)) if m else 0
        page_numbers = payload.get("page_numbers") if isinstance(payload.get("page_numbers"), list) else []
        bboxes = payload.get("bboxes") if isinstance(payload.get("bboxes"), list) else []
        headings = payload.get("headings") if isinstance(payload.get("headings"), list) else []
        text = str(payload.get("text") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "rank": None,
                "chunk_id": f"{stem}:{chunk_index:06d}",
                "score": None,
                "text": text,
                "page_numbers": page_numbers,
                "bboxes": bboxes,
                "headings": headings,
                "chunk_index": chunk_index,
                "source_file": path.name,
                "origin_sections": [],
            }
        )
    rows.sort(key=chunk_order_sort_key)
    return rows
