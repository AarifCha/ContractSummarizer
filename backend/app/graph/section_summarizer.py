"""
Section summarizer: pick page numbers per preset section using pooled embeddings + graph expansion.

For each section, seed pages are chosen by **sum-over-queries** (aggregated) similarity: for each page p,
score(p) = sum_i cosine(norm(emb_p), norm(emb_q_i)) across all section query embeddings i.
This rewards pages that align with multiple concepts in the section (e.g. contract face pages).
The top S = ceil(max_pages / 2) pages by score (ties: lower page_number first) are seeds, ordered
by score descending.

The final page list is built **in order**, stopping when len == max_pages (cap-by-stopping, no
round-robin tail trim):

1. Append seed pages in score order until the cap.
2. **Immediate** graph neighbors only: for each page already in the set (iteration order:
   ascending page_number), append distinct neighbors via PageNext / PagePrev until the cap.
3. **PageRefers** only: from every page in the set, follow refers edges; append targets in stable
   sorted order. Optional extra rounds if new pages were added and room remains.

No PageSimilar expansion. Cross-section behavior: each section returns its own page list; no
cross-section deduplication unless added later.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.graph.graphDBGen import load_page_embeddings_map


def _connect_kuzu(graph_path: str):
    import kuzu

    db = kuzu.Database(str(graph_path))
    return kuzu.Connection(db)


def _normalize_vec(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n == 0.0:
        return v.astype(np.float64)
    return (v / n).astype(np.float64)


def _next_prev_neighbors(conn, page_number: int) -> set[int]:
    """Immediate document-order neighbors via outgoing PageNext and PagePrev."""
    out: set[int] = set()
    q_next = """
    MATCH (p:Page)-[:PageNext]->(n:Page)
    WHERE p.page_number = $pn
    RETURN n.page_number
    """
    q_prev = """
    MATCH (p:Page)-[:PagePrev]->(n:Page)
    WHERE p.page_number = $pn
    RETURN n.page_number
    """
    for q in (q_next, q_prev):
        res = conn.execute(q, {"pn": int(page_number)})
        while res.has_next():
            out.add(int(res.get_next()[0]))
    return out


def _load_preset_json(
    preset_dir: str,
) -> Tuple[Dict[str, Tuple[int, List[str]]], Dict[str, List[float]]]:
    """
    Load SectionToQueries.json and embedDict.json.

    Section entries must be either:
      { "max_pages": int, "queries": [str, ...] }
    or legacy:
      [str, ...]  (treated as queries with max_pages=15).

    Returns:
      section_specs: mapping section_title -> (max_pages, query_phrases)
      embed_dict: phrase -> embedding vector (list[float])
    """
    section_path = os.path.join(preset_dir, "SectionToQueries.json")
    embed_path = os.path.join(preset_dir, "embedDict.json")

    with open(section_path, "r", encoding="utf-8") as f:
        raw_sections: Dict[str, Any] = json.load(f)
    with open(embed_path, "r", encoding="utf-8") as f:
        embed_raw: Any = json.load(f)
    # build_preset_summary_embeddings.py writes a wrapper with metadata + "embedDict" map
    if isinstance(embed_raw, dict) and "embedDict" in embed_raw:
        embed_dict = embed_raw["embedDict"]
    else:
        embed_dict = embed_raw
    if not isinstance(embed_dict, dict):
        raise TypeError(f"embedDict.json: expected object or wrapper with 'embedDict', got {type(embed_dict)}")

    section_specs: Dict[str, Tuple[int, List[str]]] = {}
    for title, entry in raw_sections.items():
        if isinstance(entry, list):
            max_pages = 15
            queries = [str(x) for x in entry]
        elif isinstance(entry, dict):
            max_pages = int(entry.get("max_pages", 15))
            qraw = entry.get("queries")
            if not isinstance(qraw, list):
                raise ValueError(
                    f"Section {title!r}: expected 'queries' to be a list, got {type(qraw)}"
                )
            queries = [str(x) for x in qraw]
        else:
            raise ValueError(
                f"Section {title!r}: expected object with max_pages/queries or legacy array, "
                f"got {type(entry)}"
            )
        section_specs[title] = (max_pages, queries)

    return section_specs, embed_dict


def _query_vectors_for_section(
    phrases: List[str], embed_dict: Dict[str, List[float]]
) -> List[np.ndarray]:
    """Return normalized query vectors for phrases that exist in embed_dict."""
    vecs: List[np.ndarray] = []
    for phrase in phrases:
        emb = embed_dict.get(phrase)
        if emb is None:
            continue
        vecs.append(_normalize_vec(np.asarray(emb, dtype=np.float64)))
    return vecs


def _scores_aggregated_over_queries(
    emb_by_page: Dict[int, List[float]], query_vecs: List[np.ndarray]
) -> Dict[int, float]:
    """
    For each page, score is the SUM of cosine similarities against all query vectors.
    This rewards pages that contain MULTIPLE concepts from the category (like face pages),
    rather than just spiking on a single dense legal term.
    """
    if not query_vecs:
        return {}
    scores: Dict[int, float] = {}
    for pn, vec in emb_by_page.items():
        vp = _normalize_vec(np.asarray(vec, dtype=np.float64))
        sims = [float(np.dot(vp, q)) for q in query_vecs]
        scores[pn] = sum(sims)
    return scores


def _seed_pages_ordered(
    scores: Dict[int, float], max_pages: int
) -> List[int]:
    """
    S = ceil(max_pages / 2). Take the S pages with highest score;
    order by score descending, tie-break lower page_number first.
    """
    if not scores:
        return []
    s = max(1, int(math.ceil(max_pages / 2)))
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [pn for pn, _ in ranked[:s]]


def _refers_targets(conn, page_number: int) -> List[int]:
    """Distinct target page numbers from PageRefers, ascending order."""
    q = """
    MATCH (p:Page)-[:PageRefers]->(t:Page)
    WHERE p.page_number = $pn
    RETURN DISTINCT t.page_number AS tgt
    ORDER BY tgt
    """
    res = conn.execute(q, {"pn": int(page_number)})
    out: List[int] = []
    while res.has_next():
        row = res.get_next()
        out.append(int(row[0]))
    return out


def _gather_pages_v2(
    conn,
    emb_by_page: Dict[int, List[float]],
    embed_dict: Dict[str, List[float]],
    phrases: List[str],
    max_pages: int,
) -> List[int]:
    """
    Ordered page list: seeds (sum-over-queries, top S), then NEXT/PREV neighbors, then PageRefers.
    Stop when len == max_pages.
    """
    if max_pages <= 0:
        return []

    query_vecs = _query_vectors_for_section(phrases, embed_dict)
    scores = _scores_aggregated_over_queries(emb_by_page, query_vecs)
    seed_order = _seed_pages_ordered(scores, max_pages)

    ordered: List[int] = []
    seen: set[int] = set()

    def try_add(pn: int) -> bool:
        if len(ordered) >= max_pages:
            return False
        if pn in seen:
            return False
        ordered.append(pn)
        seen.add(pn)
        return True

    # 1) Seeds in score order (already sorted by _seed_pages_ordered)
    for pn in seed_order:
        if len(ordered) >= max_pages:
            break
        try_add(pn)

    # 2) Immediate NEXT/PREV neighbors; iterate pages in ascending page_number
    for pn in sorted(seen):
        if len(ordered) >= max_pages:
            break
        for nb in sorted(_next_prev_neighbors(conn, pn)):
            if len(ordered) >= max_pages:
                break
            try_add(nb)

    # 3) PageRefers — repeat until no new adds or cap (new pages may add new refers)
    while len(ordered) < max_pages:
        added = False
        for pn in sorted(seen):
            if len(ordered) >= max_pages:
                break
            for tgt in _refers_targets(conn, pn):
                if len(ordered) >= max_pages:
                    break
                if try_add(tgt):
                    added = True
        if not added:
            break

    return sorted(ordered)


def gather_page_numbers_for_section(
    base_folder: str,
    section_title: str,
    *,
    preset_dir: Optional[str] = None,
    max_pages: Optional[int] = None,
) -> List[int]:
    """
    Return sorted page numbers for one preset section using pooled embeddings + graph expansion.

    If max_pages is None, uses max_pages from SectionToQueries.json for that section.
    """
    if preset_dir is None:
        here = os.path.dirname(os.path.abspath(__file__))
        preset_dir = os.path.normpath(
            os.path.join(here, "..", "..", "presetSummaryEmbbedings")
        )

    section_specs, embed_dict = _load_preset_json(preset_dir)
    if section_title not in section_specs:
        raise KeyError(f"Unknown section title: {section_title!r}")

    mp_default, phrases = section_specs[section_title]
    limit = int(max_pages) if max_pages is not None else mp_default

    graph_path = os.path.join(base_folder, "graphDB", "contract.kuzu")
    if not os.path.exists(graph_path):
        raise FileNotFoundError(f"Kuzu DB not found at {graph_path}")

    conn = _connect_kuzu(graph_path)
    try:
        emb_by_page = load_page_embeddings_map(conn)
        pages = _gather_pages_v2(
            conn, emb_by_page, embed_dict, phrases, limit
        )
    finally:
        conn.close()

    return pages


def gather_page_numbers_for_all_sections(
    base_folder: str,
    *,
    preset_dir: Optional[str] = None,
    max_pages: Optional[int] = None,
) -> Dict[str, List[int]]:
    """
    Run gather_page_numbers_for_section for every section in SectionToQueries.json.

    If max_pages is not None, applies that cap to every section (override).
    Otherwise each section uses its own max_pages from JSON.
    """
    if preset_dir is None:
        here = os.path.dirname(os.path.abspath(__file__))
        preset_dir = os.path.normpath(
            os.path.join(here, "..", "..", "presetSummaryEmbbedings")
        )

    section_specs, _ = _load_preset_json(preset_dir)
    out: Dict[str, List[int]] = {}
    for title in section_specs.keys():
        out[title] = gather_page_numbers_for_section(
            base_folder,
            title,
            preset_dir=preset_dir,
            max_pages=max_pages,
        )
    return out


__all__ = [
    "gather_page_numbers_for_section",
    "gather_page_numbers_for_all_sections",
]
