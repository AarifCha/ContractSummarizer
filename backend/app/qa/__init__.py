"""Post-retrieval QA helpers (local models)."""

from app.qa.local_usefulness_classifier import filter_retrieved_chunks

__all__ = ["filter_retrieved_chunks"]
