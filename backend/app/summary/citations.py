from __future__ import annotations

import re
from collections import defaultdict

CITATION_RE = re.compile(r"\[chunk[-_]\s*([^\]]+)\]")


def parse_cited_chunk_indices(summary_text: str) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for match in CITATION_RE.finditer(summary_text or ""):
        raw_group = (match.group(1) or "").strip()
        if not raw_group:
            continue
        for part in raw_group.split(","):
            token = part.strip()
            if token.startswith("chunk_"):
                token = token.removeprefix("chunk_").strip()
            elif token.startswith("chunk-"):
                token = token.removeprefix("chunk-").strip()
            if not token:
                continue
            try:
                idx = int(token)
            except ValueError:
                continue
            if idx in seen:
                continue
            seen.add(idx)
            ids.append(idx)
    return ids


def build_citation_map(summary_text: str, by_chunk_index: dict[int, dict]) -> dict[str, dict]:
    citation_map: dict[str, dict] = {}
    occ = 0
    for match in CITATION_RE.finditer(summary_text or ""):
        raw_group = (match.group(1) or "").strip()
        chunk_indices: list[int] = []
        seen: set[int] = set()
        for part in raw_group.split(","):
            token = part.strip()
            if token.startswith("chunk_"):
                token = token.removeprefix("chunk_").strip()
            elif token.startswith("chunk-"):
                token = token.removeprefix("chunk-").strip()
            try:
                idx = int(token)
            except (TypeError, ValueError):
                continue
            if idx in seen:
                continue
            seen.add(idx)
            chunk_indices.append(idx)
        key = f"c{occ}"
        occ += 1
        citation_map[key] = {
            "raw": match.group(0),
            "chunk_indices": chunk_indices,
            "chunk_ids": [str((by_chunk_index.get(idx) or {}).get("chunk_id") or "") for idx in chunk_indices],
        }
    return citation_map


def highlights_by_page(rows: list[dict]) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = defaultdict(list)
    for row in rows:
        pages = row.get("page_numbers")
        bboxes = row.get("bboxes")
        if not isinstance(pages, list) or not pages:
            continue
        if not isinstance(bboxes, list) or not bboxes:
            continue
        try:
            page0 = int(pages[0])
        except (TypeError, ValueError):
            continue
        page_key = str(page0)
        for bbox in bboxes:
            if isinstance(bbox, list) and len(bbox) >= 4:
                try:
                    out[page_key].append([float(v) for v in bbox[:4]])
                except (TypeError, ValueError):
                    continue
    return dict(out)
