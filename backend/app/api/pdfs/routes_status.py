from fastapi import APIRouter, Depends, HTTPException, status

from app.core.db import get_db
from app.core.deps import current_user

router = APIRouter()


@router.get("/{pdf_id}/processing-status")
def processing_status(pdf_id: int, user=Depends(current_user)):
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
              processing_stage,
              processing_total_chunks,
              processing_completed_chunks,
              processing_status,
              processing_error
            FROM pdfs
            WHERE id = ? AND user_id = ?
            """,
            (pdf_id, user["id"]),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    total_chunks = int(row["processing_total_chunks"] or 0)
    completed_chunks = int(row["processing_completed_chunks"] or 0)
    progress_percent = 0
    if total_chunks > 0:
        progress_percent = int((completed_chunks / total_chunks) * 100)
        if progress_percent > 100:
            progress_percent = 100

    return {
        "stage": row["processing_stage"],
        "status": row["processing_status"],
        "total_chunks": total_chunks,
        "completed_chunks": completed_chunks,
        "progress_percent": progress_percent,
        "error": row["processing_error"],
    }
