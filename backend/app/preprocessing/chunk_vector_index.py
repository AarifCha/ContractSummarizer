from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

import numpy as np

logger = logging.getLogger(__name__)

MODEL_NAME = "jinaai/jina-embeddings-v3"
_MODEL_NAME = MODEL_NAME
_TASK_NAME = "retrieval.passage"
_QUERY_TASK_NAME = "retrieval.query"
_CHUNK_FILE_RE = re.compile(r"^(.+)_chunk_(\d+)\.json$")

_INJECTED_MODEL: Any | None = None


def set_embedding_model(model: Any | None) -> None:
    global _INJECTED_MODEL
    _INJECTED_MODEL = model


def _safe_chunk_index(data: dict[str, Any], path: Path) -> int:
    raw = data.get("chunk_index")
    try:
        if raw is not None and raw != "":
            return int(raw)
    except (TypeError, ValueError):
        pass
    m = re.search(r"_chunk_(\d+)\.json$", path.name)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return 0
    return 0


def _load_chunk_records_for_stem(
    output_dir: Path, stem: str
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    paths = sorted(output_dir.glob(f"{stem}_chunk_*.json"), key=lambda p: p.name)
    rows: list[tuple[int, str, str, dict[str, Any]]] = []
    for p in paths:
        m = _CHUNK_FILE_RE.match(p.name)
        if not m:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        chunk_index = _safe_chunk_index(data, p)
        text = str(data.get("text") or "").strip()
        if not text:
            continue
        page_numbers = data.get("page_numbers") if isinstance(data.get("page_numbers"), list) else []
        headings = data.get("headings") if isinstance(data.get("headings"), list) else []
        metadata = {
            "file_stem": stem,
            "source_file": p.name,
            "chunk_index": int(chunk_index),
            "page_number_first": int(page_numbers[0]) if page_numbers else -1,
            "page_numbers_json": json.dumps(page_numbers, ensure_ascii=False),
            "headings_json": json.dumps(headings, ensure_ascii=False),
        }
        row_id = f"{stem}:{chunk_index:06d}"
        rows.append((chunk_index, row_id, text, metadata))

    rows.sort(key=lambda r: r[0])
    ids = [r[1] for r in rows]
    docs = [r[2] for r in rows]
    metas = [r[3] for r in rows]
    return ids, docs, metas


def _get_embedding_model() -> Any:
    if _INJECTED_MODEL is None:
        raise RuntimeError(
            "Embedding model is not initialized. "
            "The API server must load jina-embeddings-v3 at startup (see app.main lifespan)."
        )
    return _INJECTED_MODEL


def _embedding_to_row(vec: Any) -> np.ndarray:
    arr = np.asarray(vec)
    if arr.ndim == 1:
        return arr
    return arr[0]


def _embed_passages(
    passages: list[str],
    *,
    model: Any | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[list[float]]:
    if not passages:
        return []
    resolved = model if model is not None else _get_embedding_model()
    out: list[list[float]] = []
    total = len(passages)
    if progress_callback is not None:
        progress_callback(0, total)
    for idx, text in enumerate(passages, start=1):
        try:
            vec = resolved.encode(
                text,
                task=_TASK_NAME,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except TypeError:
            vec = resolved.encode(text, convert_to_numpy=True, show_progress_bar=False)
        row = _embedding_to_row(vec)
        out.append([float(x) for x in row])
        if progress_callback is not None:
            progress_callback(idx, total)
    return out


def _embed_query(query_text: str, *, model: Any | None = None) -> list[float]:
    resolved = model if model is not None else _get_embedding_model()
    text = query_text.strip()
    if not text:
        raise ValueError("Query text cannot be empty")
    try:
        vec = resolved.encode(
            text,
            task=_QUERY_TASK_NAME,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
    except TypeError:
        vec = resolved.encode(text, convert_to_numpy=True, show_progress_bar=False)
    row = _embedding_to_row(vec)
    return [float(x) for x in row]


def _load_chunk_payload(output_dir: Path, source_file: str) -> dict[str, Any]:
    chunk_path = output_dir / source_file
    if not chunk_path.is_file():
        return {}
    try:
        data = json.loads(chunk_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _chunk_paths_by_index(output_dir: Path, stem: str) -> dict[int, Path]:
    by_index: dict[int, Path] = {}
    for p in output_dir.glob(f"{stem}_chunk_*.json"):
        m = _CHUNK_FILE_RE.match(p.name)
        if not m:
            continue
        try:
            idx = int(m.group(2))
        except ValueError:
            continue
        by_index[idx] = p
    return by_index


def _hydrate_search_row(
    stem: str,
    chunk_index: int,
    source_file: str,
    payload: dict[str, Any],
    *,
    rank: int | None,
    score: float | None,
    text_fallback: str = "",
) -> dict[str, Any]:
    page_numbers = payload.get("page_numbers") if isinstance(payload.get("page_numbers"), list) else []
    bboxes = payload.get("bboxes") if isinstance(payload.get("bboxes"), list) else []
    headings = payload.get("headings") if isinstance(payload.get("headings"), list) else []
    text = str(payload.get("text") or text_fallback or "").strip()
    chunk_id = f"{stem}:{chunk_index:06d}"
    return {
        "rank": rank,
        "chunk_id": chunk_id,
        "score": score,
        "text": text,
        "page_numbers": page_numbers,
        "bboxes": bboxes,
        "headings": headings,
        "chunk_index": chunk_index,
        "source_file": source_file,
    }


def search_chunks_in_chromadb(
    output_dir: Path,
    stem: str,
    query_text: str,
    *,
    top_k: int = 5,
    model: Any | None = None,
) -> list[dict[str, Any]]:
    output_dir = output_dir.resolve()
    vector_dir = output_dir.parent / "VectorDB"
    if not vector_dir.is_dir():
        raise FileNotFoundError(f"Vector DB directory not found: {vector_dir}")

    k = max(1, min(int(top_k), 20))
    query_vector = _embed_query(query_text, model=model)

    import chromadb

    client = chromadb.PersistentClient(path=str(vector_dir))
    collection_name = f"chunks_{stem}"
    try:
        collection = client.get_collection(name=collection_name)
    except Exception as exc:
        raise FileNotFoundError(f"Vector collection not found: {collection_name}") from exc

    response = collection.query(
        query_embeddings=[query_vector],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )

    ids = (response.get("ids") or [[]])[0]
    docs = (response.get("documents") or [[]])[0]
    metas = (response.get("metadatas") or [[]])[0]
    dists = (response.get("distances") or [[]])[0]

    vector_hits: list[dict[str, Any]] = []
    for idx, chroma_id in enumerate(ids):
        metadata = metas[idx] if idx < len(metas) and isinstance(metas[idx], dict) else {}
        source_file = str(metadata.get("source_file") or "")
        payload = _load_chunk_payload(output_dir, source_file)
        chunk_index = payload.get("chunk_index", metadata.get("chunk_index", 0))
        try:
            chunk_index = int(chunk_index)
        except (TypeError, ValueError):
            chunk_index = 0
        distance = dists[idx] if idx < len(dists) else None
        score = None if distance is None else float(distance)
        doc_fallback = docs[idx] if idx < len(docs) else ""
        vector_hits.append(
            _hydrate_search_row(
                stem,
                chunk_index,
                source_file,
                payload,
                rank=idx + 1,
                score=score,
                text_fallback=str(doc_fallback or ""),
            )
        )

    paths_by_index = _chunk_paths_by_index(output_dir, stem)
    merged: dict[str, dict[str, Any]] = {}
    for row in vector_hits:
        merged[row["chunk_id"]] = row

    hit_indices = {int(r["chunk_index"]) for r in vector_hits}
    for ci in hit_indices:
        for delta in (-2, -1, 1, 2):
            ni = ci + delta
            path = paths_by_index.get(ni)
            if path is None:
                continue
            chunk_id = f"{stem}:{ni:06d}"
            if chunk_id in merged:
                continue
            payload = _load_chunk_payload(output_dir, path.name)
            merged[chunk_id] = _hydrate_search_row(
                stem,
                ni,
                path.name,
                payload,
                rank=None,
                score=None,
            )

    ordered_rows = sorted(merged.values(), key=lambda r: str(r.get("chunk_id") or ""))

    # After neighbor expansion, drop exact duplicate texts while preserving first
    # occurrence in chunk-number order.
    seen_texts: set[str] = set()
    deduped_rows: list[dict[str, Any]] = []
    for row in ordered_rows:
        text = str(row.get("text") or "")
        if text in seen_texts:
            continue
        seen_texts.add(text)
        deduped_rows.append(row)

    return deduped_rows


def index_chunks_into_chromadb(
    output_dir: Path,
    stem: str,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
    model: Any | None = None,
) -> int:
    """
    Embed ordered chunk texts and persist into per-upload Chroma VectorDB folder.
    Returns number of indexed chunks.
    """
    output_dir = output_dir.resolve()
    vector_dir = output_dir.parent / "VectorDB"
    vector_dir.mkdir(parents=True, exist_ok=True)

    ids, docs, metas = _load_chunk_records_for_stem(output_dir, stem)
    if not ids:
        logger.info("chunk_vector_index: no chunks to index for stem=%r in %s", stem, output_dir)
        return 0

    embeddings = _embed_passages(docs, model=model, progress_callback=progress_callback)
    if len(embeddings) != len(ids):
        raise ValueError("embedding count mismatch for chunk vector indexing")

    import chromadb

    client = chromadb.PersistentClient(path=str(vector_dir))
    collection = client.get_or_create_collection(
        name=f"chunks_{stem}",
        metadata={"embedding_model": _MODEL_NAME, "task": _TASK_NAME},
    )
    collection.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)
    logger.info(
        "chunk_vector_index: indexed %s chunks into %s collection=%s",
        len(ids),
        vector_dir,
        f"chunks_{stem}",
    )
    return len(ids)


__all__ = [
    "MODEL_NAME",
    "index_chunks_into_chromadb",
    "search_chunks_in_chromadb",
    "set_embedding_model",
]
