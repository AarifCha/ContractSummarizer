"""
Lossless first-pass post-process: combine suspect sparse pages (many tiny chunks) into one JSON per page,
then renumber chunk_index and filenames so indices are contiguous.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

AUDIT_JSON_NAME = "first_pass_cleaner_audit.json"

# Filename tail after "{stem}_chunk_": "001.json" or "001_combined.json"
_CHUNK_TAIL_RE = re.compile(r"^(\d+)(?:_combined)?\.json$")
_CHUNK_FILE_RE = re.compile(r"^(.+)_chunk_(\d+)(?:_combined)?\.json$")


def _index_from_chunk_filename(path: Path) -> int | None:
    m = re.search(r"_chunk_(\d+)(?:_combined)?\.json$", path.name)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _chunk_json_paths_for_stem(output_dir: Path, stem: str) -> list[Path]:
    """
    List chunk JSON files for this PDF stem. Uses the same naming rule as extract_pdf_to_block_jsons
    ({stem}_chunk_<digits>.json) so we do not rely on regex stem capture (greedy .+ can mis-parse).
    """
    prefix = f"{stem}_chunk_"
    paths: list[Path] = []
    for fp in output_dir.glob("*.json"):
        if fp.name == AUDIT_JSON_NAME:
            continue
        if not fp.name.startswith(prefix):
            continue
        tail = fp.name[len(prefix) :]
        if _CHUNK_TAIL_RE.match(tail):
            paths.append(fp)
    return sorted(paths, key=lambda p: p.name)


def _safe_chunk_index(data: dict[str, Any], path: Path) -> int:
    v = data.get("chunk_index")
    try:
        if v is not None and v != "":
            return int(v)
    except (TypeError, ValueError):
        pass
    return _index_from_chunk_filename(path) or 0


def _word_count(text: str) -> int:
    return len((text or "").split())


def _combined_page_text(paths_data: list[tuple[Path, dict[str, Any]]]) -> str:
    parts: list[str] = []
    for _, data in paths_data:
        t = (data.get("text") or "").strip()
        if t:
            parts.append(t)
    return "\n\n".join(parts)


def _page_has_override(combined_text: str) -> bool:
    if not combined_text.strip():
        return False
    u = combined_text.upper()
    if "SCHEDULE OF VALUES" in u:
        return True
    if "IN WITNESS WHEREOF" in u:
        return True
    if "$$" in combined_text:
        return True
    if re.search(r"\bBY\s*:", combined_text, re.IGNORECASE):
        return True
    if re.search(r"\bTITLE\s*:", combined_text, re.IGNORECASE):
        return True
    if re.search(r"\bPRICING\b", u):
        return True
    return False


def _is_suspect_page(total_chunks: int, avg_words: float) -> bool:
    if total_chunks <= 0:
        return False
    if total_chunks > 6 and avg_words < 8:
        return True
    if total_chunks <= 3 and avg_words < 8:
        return True
    return False


def _discover_stem_and_chunk_files(
    output_dir: Path, stem_hint: str | None
) -> tuple[str | None, list[Path]]:
    """Return PDF stem and chunk JSON paths (excludes audit)."""
    if stem_hint:
        paths = _chunk_json_paths_for_stem(output_dir, stem_hint)
        if not paths:
            sample = [
                p.name
                for p in sorted(output_dir.glob("*.json"))[:8]
                if p.name != AUDIT_JSON_NAME
            ]
            logger.warning(
                "first_pass_cleaner: no files matching %r_chunk_*.json in %s; sample: %s",
                stem_hint,
                output_dir,
                sample,
            )
        return (stem_hint, paths) if paths else (None, [])

    chunk_files: list[tuple[str, Path]] = []
    for fp in output_dir.glob("*.json"):
        if fp.name == AUDIT_JSON_NAME:
            continue
        m = _CHUNK_FILE_RE.match(fp.name)
        if not m:
            continue
        fstem, _num = m.group(1), m.group(2)
        chunk_files.append((fstem, fp))
    if not chunk_files:
        return None, []
    stems = {s for s, _ in chunk_files}
    if len(stems) != 1:
        logger.warning(
            "first_pass_cleaner: expected one PDF stem in %s, found %s; using lexicographically first",
            output_dir,
            stems,
        )
    stem = sorted(stems)[0]
    paths = [p for s, p in chunk_files if s == stem]
    return stem, sorted(paths, key=lambda p: p.name)


def _load_chunk(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def combine_suspect_pages(output_dir: Path, stem_hint: str | None = None) -> dict[str, Any]:
    """
    Group chunk JSONs by primary page (page_numbers[0]). On suspect pages with 2+ chunks,
    merge into one file with filterCombined: True and remove the originals.
    Pass ``stem_hint`` (PDF filename stem) to only process that document's chunk files.
    Returns a small summary dict for auditing.
    """
    output_dir = output_dir.resolve()
    stem, chunk_paths = _discover_stem_and_chunk_files(output_dir, stem_hint)
    summary: dict[str, Any] = {
        "stem": stem,
        "pages_combined": [],
        "pages_skipped_single_chunk": [],
        "pages_skipped_override": [],
        "pages_skipped_not_suspect": [],
    }

    if not stem or not chunk_paths:
        _write_combine_audit(output_dir, summary)
        return summary

    pages: dict[int, list[tuple[Path, dict[str, Any]]]] = {}

    for fp in chunk_paths:
        data = _load_chunk(fp)
        if not data:
            continue
        pnums = data.get("page_numbers") or []
        if not pnums:
            continue
        try:
            first_page = int(pnums[0])
        except (TypeError, ValueError, IndexError):
            continue
        pages.setdefault(first_page, []).append((fp, data))

    for page_num in sorted(pages.keys()):
        items = pages[page_num]
        items.sort(key=lambda x: _safe_chunk_index(x[1], x[0]))

        total_chunks = len(items)
        if total_chunks < 2:
            summary["pages_skipped_single_chunk"].append(page_num)
            continue

        word_counts = [_word_count(str(x[1].get("text") or "")) for _, x in items]
        total_words = sum(word_counts)
        avg_words = total_words / max(total_chunks, 1)

        if not _is_suspect_page(total_chunks, avg_words):
            summary["pages_skipped_not_suspect"].append(
                {"page": page_num, "total_chunks": total_chunks, "avg_words": round(avg_words, 4)}
            )
            continue

        combined_text = _combined_page_text(items)
        if _page_has_override(combined_text):
            summary["pages_skipped_override"].append(page_num)
            continue

        _, first_data = items[0]
        min_idx = min(_safe_chunk_index(x[1], x[0]) for x in items)

        texts: list[str] = []
        all_pages: list[int] = []
        all_bboxes: list[list[float]] = []
        cross: list[str] = []
        seen_cross: set[str] = set()

        for _fp, ch in items:
            t = (ch.get("text") or "").strip()
            if t:
                texts.append(t)
            for pn in ch.get("page_numbers") or []:
                try:
                    pni = int(pn)
                    if pni not in all_pages:
                        all_pages.append(pni)
                except (TypeError, ValueError):
                    pass
            for bb in ch.get("bboxes") or []:
                if isinstance(bb, list) and len(bb) == 4:
                    all_bboxes.append([float(x) for x in bb])
            for ref in ch.get("cross_referenced_sections") or []:
                s = str(ref).strip()
                if s and s not in seen_cross:
                    seen_cross.add(s)
                    cross.append(s)

        merged: dict[str, Any] = {
            "chunk_index": min_idx,
            "headings": list(first_data.get("headings") or []),
            "text": "\n\n".join(texts),
            "page_numbers": all_pages,
            "bboxes": all_bboxes,
            "cross_referenced_sections": cross,
            "filterCombined": True,
        }

        out_name = f"{stem}_chunk_{min_idx:03d}_combined.json"
        out_path = output_dir / out_name

        for fp, _ in items:
            if fp != out_path:
                try:
                    fp.unlink()
                except OSError as exc:
                    logger.warning("first_pass_cleaner: could not remove %s: %s", fp, exc)

        out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["pages_combined"].append(
            {
                "page": page_num,
                "min_chunk_index": min_idx,
                "output": out_name,
                "total_chunks_merged": total_chunks,
                "avg_words": round(avg_words, 4),
            }
        )
        logger.info(
            "first_pass_cleaner: combined page %s → %s (%s chunks)",
            page_num,
            out_name,
            total_chunks,
        )

    _write_combine_audit(output_dir, summary)
    return summary


def _write_combine_audit(output_dir: Path, summary: dict[str, Any]) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "combine_suspect_pages",
        **summary,
    }
    p = output_dir / AUDIT_JSON_NAME
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("first_pass_cleaner: wrote audit %s", p)


def reindex_chunk_indices(output_dir: Path, stem: str) -> int:
    """
    Assign chunk_index = 1..N in document order (by current chunk_index), rename files to
    {stem}_chunk_{NNN}.json with no gaps.
    """
    output_dir = output_dir.resolve()
    files: list[tuple[Path, int]] = []
    for fp in _chunk_json_paths_for_stem(output_dir, stem):
        data = _load_chunk(fp)
        if not data:
            continue
        idx = _safe_chunk_index(data, fp)
        files.append((fp, idx))

    if not files:
        return 0

    files.sort(key=lambda x: (x[1], x[0].name))
    temps: list[tuple[Path, int]] = []
    for i, (fp, _old) in enumerate(files):
        tmp = output_dir / f"_reindex_tmp_{i:05d}.json"
        fp.rename(tmp)
        temps.append((tmp, i + 1))

    for tmp_path, new_idx in temps:
        try:
            data = json.loads(tmp_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            tmp_path.unlink(missing_ok=True)
            continue
        if not isinstance(data, dict):
            tmp_path.unlink(missing_ok=True)
            continue
        data["chunk_index"] = new_idx
        final = output_dir / f"{stem}_chunk_{new_idx:03d}.json"
        final.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.unlink(missing_ok=True)

    return len(temps)


__all__ = [
    "AUDIT_JSON_NAME",
    "combine_suspect_pages",
    "reindex_chunk_indices",
]
