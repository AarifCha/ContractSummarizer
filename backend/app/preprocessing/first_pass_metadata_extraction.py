"""
First pass: Docling converts the PDF in 100-page virtual batches (RAM-safe), then
HierarchicalChunker emits structured chunks with native headings.
"""

from __future__ import annotations

import gc
import json
import logging
from pathlib import Path
from typing import Any, Callable

import fitz

try:
    from docling.chunking import HierarchicalChunker
except ImportError:  # pragma: no cover
    from docling_core.transforms.chunker.hierarchical_chunker import HierarchicalChunker

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from app.preprocessing.first_pass_cleaner import (
    combine_suspect_pages,
    reindex_chunk_indices,
    split_noncombined_chunks_by_line,
)

logger = logging.getLogger(__name__)

def _pages_and_bboxes_from_doc_items(
    doc_items: Any, *, batch_start_page: int, batch_end_page: int
) -> tuple[list[int], list[list[float]]]:
    """
    Deduplicate global page numbers (first-seen order); collect bbox tuples from prov[0].
    Docling page_no may be batch-local (1..batch_len) OR already global (start..end),
    depending on backend/version. Normalize both forms to global page numbers.
    """
    page_numbers: list[int] = []
    seen_pages: set[int] = set()
    bboxes: list[list[float]] = []
    for item in doc_items or []:
        prov = getattr(item, "prov", None)
        if not prov:
            continue
        p0 = prov[0] if isinstance(prov, (list, tuple)) else prov
        raw_pn = int(getattr(p0, "page_no", 1) or 1)
        batch_len = max(1, batch_end_page - batch_start_page + 1)
        if batch_start_page <= raw_pn <= batch_end_page:
            # Already global numbering.
            global_pn = raw_pn
        elif 1 <= raw_pn <= batch_len:
            # Batch-local numbering.
            global_pn = batch_start_page + raw_pn - 1
        else:
            # Fallback for unexpected values; preserve monotonicity relative to batch start.
            global_pn = batch_start_page + raw_pn - 1
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


def _compute_batch_pages(total_pages: int) -> int:
    return max(1, (total_pages + _NUM_DOC_BATCHES - 1) // _NUM_DOC_BATCHES)


def estimate_docling_batch_count(pdf_path: str | Path) -> int:
    """How many Docling convert() batches this PDF will run with the current 10-batch strategy."""
    path = Path(pdf_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")
    pdf_doc = fitz.open(path)
    total_pages = len(pdf_doc)
    pdf_doc.close()
    if total_pages == 0:
        return 0
    batch_pages = _compute_batch_pages(total_pages)
    return (total_pages + batch_pages - 1) // batch_pages


def unpack_dense_list_chunks(output_dir: Path, stem: str) -> int:
    """
    Post-Docling: merge suspect sparse pages, line-split large chunks, reindex chunk_index to 1..N.
    Returns the final chunk JSON file count for this stem.
    """
    out = output_dir.resolve()
    combine_suspect_pages(out, stem_hint=stem)
    split_noncombined_chunks_by_line(out, stem)
    return reindex_chunk_indices(out, stem)


def extract_pdf_to_block_jsons(
    pdf_path: str | Path,
    output_dir: Path,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> int:
    """
    Run Docling in ceil(total_pages/10) page batches (10 batches for a 100-page PDF → 10 pages each),
    chunk each batch with HierarchicalChunker, write one JSON per chunk. Does not run combine/split/
    reindex or embedding; call unpack_dense_list_chunks then index_chunks_into_chromadb separately.
    Returns the raw chunk JSON count written by Docling.
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

    batch_pages = _compute_batch_pages(total_pages)
    total_batches = (total_pages + batch_pages - 1) // batch_pages
    completed_batches = 0

    for start_page in range(1, total_pages + 1, batch_pages):
        end_page = min(start_page + batch_pages - 1, total_pages)

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
                doc_items, batch_start_page=start_page, batch_end_page=end_page
            )

            chunk_index += 1
            payload = {
                "chunk_index": chunk_index,
                "headings": headings_out,
                "text": raw_text,
                "page_numbers": page_numbers,
                "bboxes": bboxes,
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
        completed_batches += 1
        if progress_callback is not None:
            progress_callback(completed_batches, total_batches)

    logger.info(
        "first_pass: Docling wrote %s raw chunk JSON(s) for stem %r in %s",
        chunk_index,
        stem,
        out,
    )
    return chunk_index


__all__ = [
    "estimate_docling_batch_count",
    "extract_pdf_to_block_jsons",
    "unpack_dense_list_chunks",
]
