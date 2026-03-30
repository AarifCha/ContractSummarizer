#!/usr/bin/env python3
"""
One-off: embed preset contract-summary query strings from SectionToQueries.json (Gemini embedding
model, same as graphDBGen) and write presetSummaryEmbbedings/embedDict.json.

  cd .../ContractSummarizer/backend
  export GEMINI_API_KEY=...   # or GOOGLE_API_KEY
  python scripts/build_preset_summary_embeddings.py

  # or: python scripts/build_preset_summary_embeddings.py --db-user-id 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EMBEDDING_MODEL = "models/gemini-embedding-2-preview"


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


def _resolve_api_key(args: argparse.Namespace) -> str:
    if args.db_user_id is not None:
        sys.path.insert(0, str(_backend_root()))
        from app.core.api_keys import get_user_api_key

        key = get_user_api_key(int(args.db_user_id))
        if not key:
            raise SystemExit(
                f"No Gemini API key in DB for user_id={args.db_user_id}. "
                "Use the app or GEMINI_API_KEY."
            )
        return key
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise SystemExit(
            "Set GEMINI_API_KEY or GOOGLE_API_KEY, or use --db-user-id <id>."
        )
    return key


def _embed_all(texts: list[str], api_key: str) -> list[list[float]]:
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    out: list[list[float]] = []
    for text in texts:
        content = (text or "").strip() or "[empty]"
        response = genai.embed_content(model=EMBEDDING_MODEL, content=content)
        emb = response.get("embedding")
        if emb is None:
            raise RuntimeError(f"No embedding for {content[:80]!r}")
        out.append([float(x) for x in emb])
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db-user-id", type=int, default=None)
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

    vectors = _embed_all(phrases, _resolve_api_key(args))
    embed_dict = dict(zip(phrases, vectors, strict=True))

    payload = {
        "embedding_model": EMBEDDING_MODEL,
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
