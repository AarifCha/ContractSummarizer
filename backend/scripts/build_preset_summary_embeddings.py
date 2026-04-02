#!/usr/bin/env python3
"""
One-off: embed preset contract-summary query strings from SectionToQueries.json via the
local Jina embeddings model and write presetSummaryEmbbedings/embedDict.json.

  cd .../ContractSummarizer/backend
  python scripts/build_preset_summary_embeddings.py

  # optional: python scripts/build_preset_summary_embeddings.py --model jinaai/jina-embeddings-v3
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL = "jinaai/jina-embeddings-v3"
QUERY_TASK_NAME = "retrieval.query"


def _backend_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_unique_phrases_from_section_queries(
    section_path: Path,
) -> tuple[list[str], list[str]]:
    """
    Read SectionToQueries.json; return (ordered_unique_phrases, section_titles_in_order).

    Each query string is embedded once; duplicates across sections are skipped after first occurrence.
    """
    raw: dict[str, Any] = json.loads(section_path.read_text(encoding="utf-8"))
    titles: list[str] = []
    seen: set[str] = set()
    phrases: list[str] = []
    for title, entry in raw.items():
        titles.append(title)
        if isinstance(entry, list):
            qraw = entry
        elif isinstance(entry, dict):
            qraw = entry.get("queries")
            if not isinstance(qraw, list):
                raise SystemExit(f"Section {title!r}: missing or invalid 'queries' list")
        else:
            raise SystemExit(f"Section {title!r}: expected dict or legacy list")
        for q in qraw:
            s = str(q).strip()
            if not s:
                continue
            if s in seen:
                continue
            seen.add(s)
            phrases.append(s)
    return phrases, titles


def _embed_all(texts: list[str], model_name: str) -> list[list[float]]:
    model = SentenceTransformer(model_name, trust_remote_code=True)
    out: list[list[float]] = []
    for text in texts:
        content = (text or "").strip() or "[empty]"
        try:
            emb = model.encode(
                content,
                task=QUERY_TASK_NAME,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except TypeError:
            emb = model.encode(content, convert_to_numpy=True, show_progress_bar=False)

        # SentenceTransformer may return 1-D vector or shape (1, d).
        row = emb[0] if getattr(emb, "ndim", 1) > 1 else emb
        out.append([float(x) for x in row])
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=EMBEDDING_MODEL)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    section_path = _backend_root() / "presetSummaryEmbbedings" / "SectionToQueries.json"
    if not section_path.is_file():
        raise SystemExit(f"Missing {section_path}")

    phrases, section_titles = _load_unique_phrases_from_section_queries(section_path)
    if not phrases:
        raise SystemExit("No query strings found in SectionToQueries.json")

    out_dir = _backend_root() / "presetSummaryEmbbedings"
    out_path = out_dir / "embedDict.json"

    if args.dry_run:
        print(f"Sections: {len(section_titles)}  Unique phrases: {len(phrases)} -> {out_path}")
        return

    vectors = _embed_all(phrases, args.model)
    embed_dict = dict(zip(phrases, vectors, strict=True))

    payload = {
        "embedding_model": args.model,
        "embedding_task": QUERY_TASK_NAME,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phrase_count": len(phrases),
        "source": "SectionToQueries.json",
        "sections": section_titles,
        "embedDict": embed_dict,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(embed_dict)} vectors to {out_path}")


if __name__ == "__main__":
    main()
