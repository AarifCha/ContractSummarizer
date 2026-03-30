"""
Kuzu GraphRAG generation for a single uploaded PDF folder.

Two-phase pipeline:

1. `ingest_graph_after_first_pass_chunk(base_folder, user_id)` after each chunk:
   inserts new Page nodes (Gemini embeddings for new summaries only), then
   rebuilds PageNext/PagePrev for all pages on disk. Does not add similarity or
   cross-reference edges.

2. `finalize_graph_semantic_edges(base_folder, user_id)` once after first-pass
   completes: adds PageSimilar and PageRefers from Kuzu-stored embeddings and
   disk JSON (no extra embedding API calls).

`rebuild_kuzu_graph` remains a full wipe + one-shot build for recovery or dev use.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from app.core.api_keys import get_user_api_key

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "models/gemini-embedding-2-preview"
SIMILAR_TOP_K = 5
SIMILAR_MIN_COSINE = 0.82

PAGE_JSON_GLOB = "page_*.json"


def graph_db_directory(base_folder: Path) -> Path:
    """Directory where Kuzu stores the graph for this upload."""
    return (base_folder / "graphDB").resolve()


def graph_db_file(base_folder: Path) -> Path:
    """Concrete Kuzu database file path for versions expecting a file."""
    return graph_db_directory(base_folder) / "contract.kuzu"


def first_pass_data_directory(base_folder: Path) -> Path:
    return (base_folder / "first_pass_per_page_data").resolve()


def load_sorted_page_records(data_dir: Path) -> list[dict[str, Any]]:
    """Load all `page_XXXX.json` files, sorted by `page_number`."""
    if not data_dir.is_dir():
        return []
    paths = sorted(data_dir.glob(PAGE_JSON_GLOB), key=lambda p: p.name)
    records: list[dict[str, Any]] = []
    for path in paths:
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skip unreadable page JSON %s: %s", path, exc)
    records.sort(key=lambda r: int(r.get("page_number", 0)))
    return records


def embed_summaries_for_pages(texts: list[str], *, api_key: str) -> list[list[float]]:
    """
    Embed one string per page using Gemini embedding model.
    Uses the same API key storage as chat.
    """
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    vectors: list[list[float]] = []
    for text in texts:
        content = (text or "").strip()
        if not content:
            content = "[no summary]"
        response = genai.embed_content(model=EMBEDDING_MODEL, content=content)
        embedding = response.get("embedding")
        if embedding is None:
            raise ValueError("embed_content returned no 'embedding' key")
        vectors.append([float(x) for x in embedding])
    return vectors


def _cosine_similarity_edges(
    embeddings: list[list[float]],
    *,
    min_cosine: float,
    top_k: int,
) -> list[tuple[int, int, float]]:
    """
    Directed edges (i -> j) where j is among top_k neighbors of i (excluding self),
    and cosine similarity >= min_cosine.
    """
    import numpy as np

    if len(embeddings) < 2:
        return []
    mat = np.asarray(embeddings, dtype=np.float64)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    normalized = mat / norms
    sim = normalized @ normalized.T
    edges: list[tuple[int, int, float]] = []
    n = sim.shape[0]
    for i in range(n):
        scores: list[tuple[int, float]] = []
        for j in range(n):
            if i == j:
                continue
            s = float(sim[i, j])
            if s >= min_cosine:
                scores.append((j, s))
        scores.sort(key=lambda t: -t[1])
        for j, s in scores[:top_k]:
            edges.append((i, j, s))
    return edges


def _normalize_match_token(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def cross_reference_edges(
    records: list[dict[str, Any]],
) -> list[tuple[int, int, str]]:
    """
    For each page index i, each ref in cross_referenced_sections, link to page index j
    when the ref matches a section label on page j (substring match, case-insensitive).
    Returns (from_index, to_index, ref_string).
    """
    # Build normalized labels per page index
    labels_per_index: list[list[str]] = []
    for rec in records:
        raw = rec.get("section_labels") or []
        labels: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                s = str(item).strip()
                if s and s.lower() != "none":
                    labels.append(_normalize_match_token(s))
        labels_per_index.append(labels)

    edges: list[tuple[int, int, str]] = []
    for i, rec in enumerate(records):
        refs = rec.get("cross_referenced_sections") or []
        if not isinstance(refs, list):
            continue
        for ref in refs:
            r = str(ref).strip()
            if not r:
                continue
            r_norm = _normalize_match_token(r)
            for j, labels in enumerate(labels_per_index):
                if i == j:
                    continue
                for label in labels:
                    if not label:
                        continue
                    if r_norm in label or label in r_norm or r_norm == label:
                        edges.append((i, j, r))
                        break
    return edges


def _init_schema(conn: Any) -> None:
    conn.execute(
        """
        CREATE NODE TABLE Page(
            page_number INT64 PRIMARY KEY,
            summary STRING,
            section_labels_json STRING,
            cross_referenced_sections_json STRING,
            usefulness BOOL,
            source_chunk STRING,
            embedding_json STRING
        )
        """
    )
    conn.execute("CREATE REL TABLE PageNext(FROM Page TO Page, rel_order INT64)")
    conn.execute("CREATE REL TABLE PagePrev(FROM Page TO Page, rel_order INT64)")
    conn.execute("CREATE REL TABLE PageSimilar(FROM Page TO Page, score DOUBLE)")
    conn.execute("CREATE REL TABLE PageRefers(FROM Page TO Page, ref STRING)")


def _reset_graph_directory(graph_dir: Path) -> None:
    if graph_dir.exists():
        shutil.rmtree(graph_dir)
    graph_dir.mkdir(parents=True, exist_ok=True)


def _insert_page(
    conn: Any,
    *,
    page_number: int,
    summary: str,
    section_labels_json: str,
    cross_refs_json: str,
    usefulness: bool,
    source_chunk: str,
    embedding_json: str,
) -> None:
    conn.execute(
        """
        CREATE (:Page {
            page_number: $page_number,
            summary: $summary,
            section_labels_json: $section_labels_json,
            cross_referenced_sections_json: $cross_refs_json,
            usefulness: $usefulness,
            source_chunk: $source_chunk,
            embedding_json: $embedding_json
        })
        """,
        {
            "page_number": page_number,
            "summary": summary,
            "section_labels_json": section_labels_json,
            "cross_refs_json": cross_refs_json,
            "usefulness": usefulness,
            "source_chunk": source_chunk,
            "embedding_json": embedding_json,
        },
    )


def _insert_page_next(conn: Any, from_page: int, to_page: int, rel_order: int) -> None:
    conn.execute(
        """
        MATCH (a:Page {page_number: $from_page}), (b:Page {page_number: $to_page})
        CREATE (a)-[:PageNext {rel_order: $rel_order}]->(b)
        """,
        {"from_page": from_page, "to_page": to_page, "rel_order": rel_order},
    )


def _insert_page_prev(conn: Any, from_page: int, to_page: int, rel_order: int) -> None:
    conn.execute(
        """
        MATCH (a:Page {page_number: $from_page}), (b:Page {page_number: $to_page})
        CREATE (a)-[:PagePrev {rel_order: $rel_order}]->(b)
        """,
        {"from_page": from_page, "to_page": to_page, "rel_order": rel_order},
    )


def _insert_page_similar(conn: Any, from_page: int, to_page: int, score: float) -> None:
    conn.execute(
        """
        MATCH (a:Page {page_number: $from_page}), (b:Page {page_number: $to_page})
        CREATE (a)-[:PageSimilar {score: $score}]->(b)
        """,
        {"from_page": from_page, "to_page": to_page, "score": float(score)},
    )


def _insert_page_refers(conn: Any, from_page: int, to_page: int, ref: str) -> None:
    conn.execute(
        """
        MATCH (a:Page {page_number: $from_page}), (b:Page {page_number: $to_page})
        CREATE (a)-[:PageRefers {ref: $ref}]->(b)
        """,
        {"from_page": from_page, "to_page": to_page, "ref": ref},
    )


_KUZU_REL_TABLES = frozenset({"PageNext", "PagePrev", "PageSimilar", "PageRefers"})


def _delete_all_rels_of_type(conn: Any, rel_name: str) -> None:
    if rel_name not in _KUZU_REL_TABLES:
        raise ValueError(f"unsupported rel table: {rel_name}")
    conn.execute(f"MATCH (a:Page)-[r:{rel_name}]->(b:Page) DELETE r")


def _fetch_existing_page_numbers(conn: Any) -> set[int]:
    result = conn.execute("MATCH (p:Page) RETURN p.page_number")
    numbers: set[int] = set()
    while result.has_next():
        row = result.get_next()
        numbers.add(int(row[0]))
    return numbers


def _fetch_embedding_vectors_by_page(conn: Any) -> dict[int, list[float]]:
    result = conn.execute(
        "MATCH (p:Page) RETURN p.page_number, p.embedding_json ORDER BY p.page_number"
    )
    out: dict[int, list[float]] = {}
    while result.has_next():
        row = result.get_next()
        pn = int(row[0])
        raw_json = row[1]
        out[pn] = [float(x) for x in json.loads(str(raw_json))]
    return out


def load_page_embeddings_map(conn: Any) -> dict[int, list[float]]:
    """Public helper: page_number -> embedding vector for GraphRAG retrieval."""
    return _fetch_embedding_vectors_by_page(conn)


def _record_payload_for_insert(rec: dict[str, Any]) -> tuple[int, str, str, str, bool, str]:
    pn = int(rec["page_number"])
    section_labels = rec.get("section_labels")
    if not isinstance(section_labels, list):
        section_labels = []
    cross_refs = rec.get("cross_referenced_sections")
    if not isinstance(cross_refs, list):
        cross_refs = []
    usefulness = bool(rec.get("usefulness", False))
    source_chunk = str(rec.get("source_chunk", ""))
    summary = str(rec.get("summary", ""))
    return (
        pn,
        summary,
        json.dumps(section_labels, ensure_ascii=False),
        json.dumps(cross_refs, ensure_ascii=False),
        usefulness,
        source_chunk,
    )


def _refresh_page_next_prev(conn: Any, page_numbers: list[int]) -> None:
    _delete_all_rels_of_type(conn, "PageNext")
    _delete_all_rels_of_type(conn, "PagePrev")
    for idx in range(len(page_numbers) - 1):
        a = page_numbers[idx]
        b = page_numbers[idx + 1]
        _insert_page_next(conn, a, b, 1)
        _insert_page_prev(conn, b, a, 1)


def incremental_ingest_after_first_pass_chunk(base_folder: Path, user_id: int) -> None:
    """
    Phase A: new Page rows (embed new summaries only) + full PageNext/PagePrev refresh.
    """
    import kuzu

    data_dir = first_pass_data_directory(base_folder)
    records = load_sorted_page_records(data_dir)
    if not records:
        logger.info("graphDBGen: no page JSON in %s, skipping incremental", data_dir)
        return

    graph_dir = graph_db_directory(base_folder)
    graph_path = graph_db_file(base_folder)
    graph_dir.mkdir(parents=True, exist_ok=True)

    if not graph_path.exists():
        db = kuzu.Database(str(graph_path))
        conn = kuzu.Connection(db)
        _init_schema(conn)
    else:
        db = kuzu.Database(str(graph_path))
        conn = kuzu.Connection(db)

    existing = _fetch_existing_page_numbers(conn)
    to_add = [r for r in records if int(r["page_number"]) not in existing]

    if to_add:
        api_key = get_user_api_key(user_id)
        if not api_key:
            raise ValueError("No Gemini API key configured for user; cannot embed for graph")
        summaries = [str(r.get("summary", "")) for r in to_add]
        new_embeddings = embed_summaries_for_pages(summaries, api_key=api_key)
        if len(new_embeddings) != len(to_add):
            raise ValueError("embedding count does not match new page count")
        for rec, emb in zip(to_add, new_embeddings):
            pn, summary, sl_json, cr_json, usefulness, source_chunk = _record_payload_for_insert(rec)
            _insert_page(
                conn,
                page_number=pn,
                summary=summary,
                section_labels_json=sl_json,
                cross_refs_json=cr_json,
                usefulness=usefulness,
                source_chunk=source_chunk,
                embedding_json=json.dumps(emb, ensure_ascii=False),
            )

    page_numbers = [int(r["page_number"]) for r in records]
    _refresh_page_next_prev(conn, page_numbers)

    logger.info(
        "graphDBGen: incremental ingest (%s pages on disk, %s new this chunk)",
        len(records),
        len(to_add),
    )


def finalize_graph_semantic_edges(base_folder: Path, user_id: int) -> None:
    """
    Phase B: PageSimilar + PageRefers from Kuzu embeddings and disk JSON (no new embed API calls).
    """
    _ = user_id
    import kuzu

    data_dir = first_pass_data_directory(base_folder)
    records_disk = load_sorted_page_records(data_dir)
    if not records_disk:
        logger.info("graphDBGen: finalize skipped, no page JSON in %s", data_dir)
        return

    graph_path = graph_db_file(base_folder)
    if not graph_path.exists():
        logger.warning("graphDBGen: finalize skipped, no graph at %s", graph_path)
        return

    db = kuzu.Database(str(graph_path))
    conn = kuzu.Connection(db)
    by_pn = _fetch_embedding_vectors_by_page(conn)
    json_pages = {int(r["page_number"]) for r in records_disk}
    if set(by_pn.keys()) != json_pages:
        raise ValueError("finalize: Kuzu Page set does not match JSON page set")

    embeddings: list[list[float]] = []
    page_numbers = [int(r["page_number"]) for r in records_disk]
    for r in records_disk:
        pn = int(r["page_number"])
        embeddings.append(by_pn[pn])

    _delete_all_rels_of_type(conn, "PageSimilar")
    _delete_all_rels_of_type(conn, "PageRefers")

    sim_edges = _cosine_similarity_edges(
        embeddings,
        min_cosine=SIMILAR_MIN_COSINE,
        top_k=SIMILAR_TOP_K,
    )
    seen_similar: set[tuple[int, int]] = set()
    for i, j, score in sim_edges:
        pair = (page_numbers[i], page_numbers[j])
        if pair in seen_similar:
            continue
        seen_similar.add(pair)
        _insert_page_similar(conn, pair[0], pair[1], score)

    ref_edges = cross_reference_edges(records_disk)
    seen_refs: set[tuple[int, int, str]] = set()
    for i, j, ref in ref_edges:
        triplet = (page_numbers[i], page_numbers[j], ref)
        if triplet in seen_refs:
            continue
        seen_refs.add(triplet)
        _insert_page_refers(conn, triplet[0], triplet[1], ref)

    logger.info(
        "graphDBGen: finalized semantic edges (%s pages, %s similar, %s cross-ref)",
        len(page_numbers),
        len(seen_similar),
        len(seen_refs),
    )


def rebuild_kuzu_graph(
    base_folder: Path,
    *,
    user_id: int,
) -> None:
    """
    Rebuild the Kuzu graph for this upload from all first-pass JSON files + embeddings.
    Safe to call after each chunk; replaces the previous `graphDB` directory.
    """
    import kuzu

    data_dir = first_pass_data_directory(base_folder)
    records = load_sorted_page_records(data_dir)
    if not records:
        logger.info("graphDBGen: no page JSON in %s, skipping", data_dir)
        return

    api_key = get_user_api_key(user_id)
    if not api_key:
        raise ValueError("No Gemini API key configured for user; cannot embed for graph")

    summaries = [str(r.get("summary", "")) for r in records]
    embeddings = embed_summaries_for_pages(summaries, api_key=api_key)
    if len(embeddings) != len(records):
        raise ValueError("embedding count does not match page record count")

    graph_dir = graph_db_directory(base_folder)
    _reset_graph_directory(graph_dir)

    db = kuzu.Database(str(graph_db_file(base_folder)))
    conn = kuzu.Connection(db)
    _init_schema(conn)

    page_numbers = [int(r["page_number"]) for r in records]

    for rec, emb in zip(records, embeddings):
        pn = int(rec["page_number"])
        section_labels = rec.get("section_labels")
        if not isinstance(section_labels, list):
            section_labels = []
        cross_refs = rec.get("cross_referenced_sections")
        if not isinstance(cross_refs, list):
            cross_refs = []
        usefulness = bool(rec.get("usefulness", False))
        source_chunk = str(rec.get("source_chunk", ""))
        _insert_page(
            conn,
            page_number=pn,
            summary=str(rec.get("summary", "")),
            section_labels_json=json.dumps(section_labels, ensure_ascii=False),
            cross_refs_json=json.dumps(cross_refs, ensure_ascii=False),
            usefulness=usefulness,
            source_chunk=source_chunk,
            embedding_json=json.dumps(emb, ensure_ascii=False),
        )

    # Sequential NEXT / PREV by sorted page_number list
    for idx in range(len(page_numbers) - 1):
        a = page_numbers[idx]
        b = page_numbers[idx + 1]
        _insert_page_next(conn, a, b, 1)
        _insert_page_prev(conn, b, a, 1)

    # Similarity edges (indices align with records order == page_numbers order)
    sim_edges = _cosine_similarity_edges(
        embeddings,
        min_cosine=SIMILAR_MIN_COSINE,
        top_k=SIMILAR_TOP_K,
    )
    seen_similar: set[tuple[int, int]] = set()
    for i, j, score in sim_edges:
        pair = (page_numbers[i], page_numbers[j])
        if pair in seen_similar:
            continue
        seen_similar.add(pair)
        _insert_page_similar(conn, pair[0], pair[1], score)

    ref_edges = cross_reference_edges(records)
    seen_refs: set[tuple[int, int, str]] = set()
    for i, j, ref in ref_edges:
        triplet = (page_numbers[i], page_numbers[j], ref)
        if triplet in seen_refs:
            continue
        seen_refs.add(triplet)
        _insert_page_refers(conn, triplet[0], triplet[1], ref)

    logger.info(
        "graphDBGen: rebuilt graph at %s (%s pages, %s similar edges, %s cross-ref edges)",
        graph_dir,
        len(page_numbers),
        len(seen_similar),
        len(seen_refs),
    )


def ingest_graph_after_first_pass_chunk(base_folder: Path, user_id: int) -> None:
    """
    Public entry point: call once per completed first-pass chunk (after JSON files exist).
    Phase A only (new pages + NEXT/PREV).
    """
    incremental_ingest_after_first_pass_chunk(base_folder, user_id)


def open_graph_connection(base_folder: Path) -> tuple[Any, Any] | None:
    """
    Open existing graph for queries; returns None if graph not initialized yet.
    """
    import kuzu

    graph_path = graph_db_file(base_folder)
    if not graph_path.exists():
        return None
    db = kuzu.Database(str(graph_path))
    return db, kuzu.Connection(db)
