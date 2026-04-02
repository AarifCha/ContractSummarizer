from __future__ import annotations

import json
from pathlib import Path

ALL_SECTIONS_LABEL = "All Sections"


def section_queries_path(base_dir: Path) -> Path:
    return base_dir / "presetSummaryEmbbedings" / "SectionToQueries.json"


def load_section_queries(base_dir: Path) -> dict[str, list[str]]:
    path = section_queries_path(base_dir)
    if not path.is_file():
        raise FileNotFoundError(f"Section query config not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("SectionToQueries.json must be an object")

    out: dict[str, list[str]] = {}
    for section, entry in raw.items():
        if isinstance(entry, dict):
            qraw = entry.get("queries")
        elif isinstance(entry, list):
            qraw = entry
        else:
            qraw = None
        if not isinstance(qraw, list):
            continue
        queries = [str(q).strip() for q in qraw if str(q).strip()]
        if queries:
            out[str(section)] = queries
    return out
