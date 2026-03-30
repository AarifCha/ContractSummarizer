"""
First pass: Docling converts the PDF in 100-page virtual batches (RAM-safe), then
HierarchicalChunker emits structured chunks with native headings; we add regex cross-references.
"""

from __future__ import annotations

import gc
import json
import logging
import re
from pathlib import Path
from typing import Any

import fitz

try:
    from docling.chunking import HierarchicalChunker
except ImportError:  # pragma: no cover
    from docling_core.transforms.chunker.hierarchical_chunker import HierarchicalChunker

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from app.ai.first_pass_cleaner import combine_suspect_pages, reindex_chunk_indices

logger = logging.getLogger(__name__)

REFERENCE_PATTERN = re.compile(
    r'(?:Article|Section|Exhibit|Schedule|Addendum)\s+["“”\'‘’]?\s*[A-Z0-9\.\-]+\s*["“”\'‘’]?',
    re.IGNORECASE,
)

_QUOTE_CHARS = frozenset('"“”\'‘’')


def _normalize_identifier(text: str) -> str:
    s = text.strip()
    while s and s[0] in _QUOTE_CHARS:
        s = s[1:].lstrip()
    while s and s[-1] in _QUOTE_CHARS:
        s = s[:-1].rstrip()
    s = re.sub(r"\s+", " ", s)
    return s.upper()


def _extract_cross_references(raw_text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in REFERENCE_PATTERN.finditer(raw_text):
        key = _normalize_identifier(m.group(0))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _pages_and_bboxes_from_doc_items(
    doc_items: Any, *, page_index_offset: int = 0
) -> tuple[list[int], list[list[float]]]:
    """
    Deduplicate global page numbers (first-seen order); collect bbox tuples from prov[0].
    ``page_index_offset`` maps batch-local page_no (restarts at 1 each batch) to global PDF pages:
    global = local + offset, offset = batch_start_page - 1.
    """
    page_numbers: list[int] = []
    seen_pages: set[int] = set()
    bboxes: list[list[float]] = []
    for item in doc_items or []:
        prov = getattr(item, "prov", None)
        if not prov:
            continue
        p0 = prov[0] if isinstance(prov, (list, tuple)) else prov
        local_pn = int(getattr(p0, "page_no", 1) or 1)
        global_pn = local_pn + page_index_offset
        if global_pn not in seen_pages:
            seen_pages.add(global_pn)
            page_numbers.append(global_pn)
        b = getattr(p0, "bbox", None)
        if b is None:
            continue
        if hasattr(b, "as_tuple"):
            bboxes.append(list(b.as_tuple()))
        elif all(hasattr(b, x) for x in ("l", "t", "r", "b")):
            bboxes.append([float(b.l), float(b.t), float(b.r), float(b.b)])
    return page_numbers, bboxes


_MIN_TEXT_CHARS = 5
# Split each PDF into this many Docling convert() passes (RAM vs. page indexing tradeoff).
_NUM_DOC_BATCHES = 10


def extract_pdf_to_block_jsons(pdf_path: str | Path, output_dir: Path) -> int:
    """
    Run Docling in ceil(total_pages/10) page batches (10 batches for a 100-page PDF → 10 pages each),
    chunk each batch with HierarchicalChunker, write one JSON per chunk, then combine suspect sparse
    pages and reindex chunk_index to a contiguous 1..N. Returns the final chunk JSON count on disk.
    """
    chunk_index = 0
    global_headings: list[str] = []

    path = Path(pdf_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")

    out = output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    stem = path.stem

    pdf_doc = fitz.open(path)
    total_pages = len(pdf_doc)
    pdf_doc.close()

    if total_pages == 0:
        return chunk_index

    batch_pages = max(1, (total_pages + _NUM_DOC_BATCHES - 1) // _NUM_DOC_BATCHES)

    for start_page in range(1, total_pages + 1, batch_pages):
        end_page = min(start_page + batch_pages - 1, total_pages)
        page_index_offset = start_page - 1

        pipeline_options = PdfPipelineOptions()
        pipeline_options.generate_parsed_pages = False
        pipeline_options.ocr_batch_size = 2
        pipeline_options.layout_batch_size = 2
        pipeline_options.table_batch_size = 2

        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        # Page range is a convert()-time argument, not a PdfPipelineOptions field.
        result = converter.convert(str(path), page_range=(start_page, end_page))
        doc = result.document

        chunker = HierarchicalChunker()
        for chunk in chunker.chunk(doc):
            raw_text = getattr(chunk, "text", None)
            if raw_text is None:
                continue
            raw_text = str(raw_text).strip()
            if not raw_text or len(raw_text) < _MIN_TEXT_CHARS:
                continue

            meta = getattr(chunk, "meta", None)
            doc_items: Any = []
            if meta is not None:
                hds = getattr(meta, "headings", None)
                if hds:
                    global_headings = [str(h) for h in hds]
                doc_items = getattr(meta, "doc_items", None) or []

            headings_out = list(global_headings)
            page_numbers, bboxes = _pages_and_bboxes_from_doc_items(
                doc_items, page_index_offset=page_index_offset
            )
            cross = _extract_cross_references(raw_text)

            chunk_index += 1
            payload = {
                "chunk_index": chunk_index,
                "headings": headings_out,
                "text": raw_text,
                "page_numbers": page_numbers,
                "bboxes": bboxes,
                "cross_referenced_sections": cross,
            }
            fp = out / f"{stem}_chunk_{chunk_index:03d}.json"
            fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        if hasattr(result, "input") and hasattr(result.input, "_backend") and hasattr(
            result.input._backend, "unload"
        ):
            result.input._backend.unload()

        del chunker
        del doc
        del result
        del converter
        gc.collect()

    logger.info(
        "first_pass: Docling wrote %s chunk JSON(s) for stem %r; running combine + reindex in %s",
        chunk_index,
        stem,
        out,
    )
    try:
        combine_suspect_pages(out, stem_hint=stem)
        n = reindex_chunk_indices(out, stem)
    except Exception:
        logger.exception("first_pass: combine/reindex failed after Docling (chunks on disk may be raw)")
        raise
    logger.info("first_pass: post-process done, %s chunk file(s) after reindex", n)
    return n


__all__ = ["REFERENCE_PATTERN", "extract_pdf_to_block_jsons"]
