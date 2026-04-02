import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sentence_transformers import SentenceTransformer

from app.preprocessing.chunk_vector_index import set_embedding_model
from app.api.api_key import router as api_key_router
from app.api.auth import router as auth_router
from app.api.pdfs import router as pdf_router
from app.core.db import init_db

logger = logging.getLogger(__name__)


def _purge_hf_dynamic_module_cache() -> None:
    """
    Remove corrupted Hugging Face dynamic module cache paths used by trust_remote_code.
    """
    modules_root = Path.home() / ".cache" / "huggingface" / "modules" / "transformers_modules"
    candidates = [modules_root]
    for path in candidates:
        if path.exists():
            try:
                shutil.rmtree(path)
                logger.warning("Removed corrupted HF dynamic module cache at %s", path)
            except OSError:
                logger.exception("Failed to remove HF dynamic module cache at %s", path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        model = SentenceTransformer("jinaai/jina-embeddings-v3", trust_remote_code=True)
    except FileNotFoundError as exc:
        logger.warning(
            "jina-embeddings-v3: load failed from local cache (%s), purging dynamic module cache and retrying",
            exc,
        )
        from huggingface_hub import snapshot_download

        repo_id = "jinaai/jina-embeddings-v3"
        _purge_hf_dynamic_module_cache()
        local_dir = snapshot_download(
            repo_id=repo_id,
            force_download=True,
            local_files_only=False,
        )
        model = SentenceTransformer(local_dir, trust_remote_code=True, local_files_only=False)
    except Exception as exc:
        raise RuntimeError(
            "Failed to load jina-embeddings-v3 locally. "
            "Ensure internet access for first download (or a valid local HF cache)."
        ) from exc
    set_embedding_model(model)
    yield
    set_embedding_model(None)


app = FastAPI(title="PDF Library API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(auth_router, prefix="/api")
app.include_router(pdf_router, prefix="/api")
app.include_router(api_key_router, prefix="/api")
