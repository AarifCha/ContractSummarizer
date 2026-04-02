import os

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import FileResponse

from app.api.pdfs.constants import UPLOAD_DIR
from app.api.pdfs.storage_paths import is_supported_storage_path
from app.core.auth import get_user_from_token
from app.core.db import get_db

router = APIRouter()


@router.get("/{pdf_id}/file")
def view_pdf(
    pdf_id: int,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
):
    auth_token = token
    if not auth_token and authorization and authorization.startswith("Bearer "):
        auth_token = authorization.removeprefix("Bearer ").strip()
    if not auth_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    user = get_user_from_token(auth_token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    with get_db() as conn:
        row = conn.execute(
            "SELECT stored_name, original_name FROM pdfs WHERE id = ? AND user_id = ?",
            (pdf_id, user["id"]),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    if not is_supported_storage_path(row["stored_name"], user):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Legacy storage format is unsupported")

    path = os.path.join(UPLOAD_DIR, row["stored_name"])
    if not os.path.exists(path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File missing from storage")

    return FileResponse(
        path=path,
        media_type="application/pdf",
        filename=row["original_name"],
        content_disposition_type="inline",
    )
