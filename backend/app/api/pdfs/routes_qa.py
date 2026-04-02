import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.pdfs.constants import UPLOAD_DIR
from app.api.pdfs.schemas import QaSearchRequest
from app.api.pdfs.storage_paths import is_supported_storage_path
from app.core.db import get_db
from app.core.deps import current_user
from app.preprocessing.chunk_vector_index import search_chunks_in_chromadb
from app.qa.local_usefulness_classifier import filter_retrieved_chunks

router = APIRouter()


@router.post("/{pdf_id}/qa-search")
def qa_search(pdf_id: int, payload: QaSearchRequest, user=Depends(current_user)):
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT stored_name, original_name, processing_status
            FROM pdfs
            WHERE id = ? AND user_id = ?
            """,
            (pdf_id, user["id"]),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    if not is_supported_storage_path(row["stored_name"], user):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Legacy storage format is unsupported")
    if row["processing_status"] != "done":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="PDF processing is not complete yet",
        )

    pdf_path = Path(UPLOAD_DIR) / row["stored_name"]
    upload_folder = pdf_path.parent
    first_pass_dir = upload_folder / "first_pass_data"
    if not first_pass_dir.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chunk data not found for this PDF")

    stem = Path(row["original_name"]).stem
    try:
        results = search_chunks_in_chromadb(
            first_pass_dir,
            stem,
            payload.query,
            top_k=payload.top_k,
        )
        results = filter_retrieved_chunks(payload.query, results)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"qa_search_error: {exc}") from exc

    return {"results": results}
