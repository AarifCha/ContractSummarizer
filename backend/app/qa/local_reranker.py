from __future__ import annotations

import logging
from typing import Any

import torch
from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)


def _best_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


device = _best_device()

try:
    logger.info("Loading BAAI/bge-reranker-base on device: %s...", device)
    reranker = CrossEncoder("BAAI/bge-reranker-base", device=device)
except Exception as e:
    logger.exception("Failed to load CrossEncoder: %s", e)
    reranker = None


def _extract_text(chunk: Any) -> str:
    if isinstance(chunk, dict):
        return str(chunk.get("text") or "").strip()
    return str(getattr(chunk, "text", "") or "").strip()


def rerank_and_filter(query: str, chunks: list, top_k: int = 20) -> list:
    if not chunks:
        return chunks[:top_k]
    if reranker is None:
        return chunks[:top_k]

    sentence_pairs: list[list[str]] = []
    for chunk in chunks:
        sentence_pairs.append([query, _extract_text(chunk)])

    try:
        scores = reranker.predict(sentence_pairs)
    except Exception as e:
        logger.exception("CrossEncoder rerank failed: %s", e)
        return chunks[:top_k]

    for idx, chunk in enumerate(chunks):
        score = float(scores[idx]) if idx < len(scores) else float("-inf")
        if isinstance(chunk, dict):
            chunk["relevance_score"] = score
        else:
            try:
                setattr(chunk, "relevance_score", score)
            except Exception:
                pass

    def _score_key(item: Any) -> float:
        if isinstance(item, dict):
            raw = item.get("relevance_score", float("-inf"))
        else:
            raw = getattr(item, "relevance_score", float("-inf"))
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float("-inf")

    chunks.sort(key=_score_key, reverse=True)
    return chunks[:top_k]


__all__ = ["rerank_and_filter"]
