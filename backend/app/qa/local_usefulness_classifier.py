"""
Local CPU/GPU relevance gate via llama-cpp-python (Qwen2.5 0.5B GGUF).
Model loads globally and stays resident for fast True/False checks.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from huggingface_hub import hf_hub_download
from llama_cpp import Llama

logger = logging.getLogger(__name__)

_REPO_ID = "Qwen/Qwen2.5-0.5B-Instruct-GGUF"
_FILENAME = "qwen2.5-0.5b-instruct-q4_k_m.gguf"
# _REPO_ID = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
# _FILENAME = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
_N_CTX = 2048
_MAX_GEN_TOKENS = 5
_TOKEN_SAFETY_MARGIN = 32

try:
    _HF_CACHE_DIR = os.getenv("HF_HOME") or None
    _MODEL_PATH = hf_hub_download(
        repo_id=_REPO_ID,
        filename=_FILENAME,
        cache_dir=_HF_CACHE_DIR,
    )
    llm = Llama(
        model_path=_MODEL_PATH,
        n_gpu_layers=-1,
        n_ctx=_N_CTX,
        verbose=False,
    )
except Exception:
    logger.exception("Failed to initialize local usefulness model")
    llm = None


def _build_prompt(query: str, chunk_text: str) -> str:
    return f"""<|im_start|>system
You are a strict data classifier. Your ONLY job is to determine if the provided text contains the information requested in the target query.
You must output ONLY the word "True" or "False". Do not output any other text.<|im_end|>
<|im_start|>user
Target Query: {query}

Text to Evaluate:
{chunk_text}<|im_end|>
<|im_start|>assistant
"""


def _clip_chunk_text_to_ctx(query: str, chunk_text: str) -> str:
    if llm is None:
        return chunk_text

    static_prompt = _build_prompt(query, "")
    try:
        static_tokens = len(llm.tokenize(static_prompt.encode("utf-8")))
    except Exception:
        # If tokenization fails, use a conservative character clip fallback.
        return chunk_text[:4000]

    budget_for_chunk = _N_CTX - _MAX_GEN_TOKENS - _TOKEN_SAFETY_MARGIN - static_tokens
    if budget_for_chunk <= 0:
        return ""

    text = chunk_text
    try:
        chunk_tokens = llm.tokenize(text.encode("utf-8"))
        if len(chunk_tokens) <= budget_for_chunk:
            return text
        return llm.detokenize(chunk_tokens[:budget_for_chunk]).decode("utf-8", errors="ignore")
    except Exception:
        return text[:4000]


def is_chunk_relevant(query: str, chunk_text: str) -> bool:
    clipped_chunk_text = _clip_chunk_text_to_ctx(query, chunk_text)
    prompt = _build_prompt(query, clipped_chunk_text)
    try:
        if llm is None:
            logger.error("Local usefulness model is unavailable; failing open")
            return True

        response = llm(
            prompt,
            max_tokens=_MAX_GEN_TOKENS,
            temperature=0.0,
            stop=["<|im_end|>"],
        )
        result = response["choices"][0]["text"].strip().lower()
    except Exception as e:
        logger.exception("is_chunk_relevant llama-cpp failure: %s", e)
        return True

    return "true" in result


def filter_retrieved_chunks(query: str, merged_chunks: list) -> list:
    surviving_chunks: list[Any] = []
    for chunk in merged_chunks:
        if isinstance(chunk, dict):
            combined_text = str(chunk.get("text") or "").strip()
        elif isinstance(chunk, str):
            combined_text = chunk.strip()
        else:
            combined_text = ""

        if is_chunk_relevant(query, combined_text):
            surviving_chunks.append(chunk)

    return surviving_chunks


__all__ = ["is_chunk_relevant", "filter_retrieved_chunks"]
