import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.core.auth import get_user_from_token
from app.core.db import get_db
from app.core.deps import current_user

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

router = APIRouter(prefix="/pdfs", tags=["pdfs"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value.strip())
    safe = safe.strip("._")
    return safe or "unknown"


def expected_user_folder(user: dict) -> str:
    user_name = user.get("email", "").split("@")[0] or str(user["id"])
    return slugify(user_name)


def is_supported_storage_path(stored_name: str, user: dict) -> bool:
    normalized = stored_name.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    if len(parts) != 3:
        return False
    return parts[0] == expected_user_folder(user)


@router.get("")
def list_pdfs(user=Depends(current_user)):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, original_name, size_bytes, created_at, stored_name
            FROM pdfs
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user["id"],),
        ).fetchall()

    files = []
    for row in rows:
        row_dict = dict(row)
        if not is_supported_storage_path(row_dict["stored_name"], user):
            continue
        row_dict.pop("stored_name", None)
        files.append(row_dict)

    return {"files": files}


@router.post("")
async def upload_pdf(file: UploadFile = File(...), user=Depends(current_user)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only PDF files are allowed")

    original_name = file.filename
    safe_file_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in original_name)
    file_stem = os.path.splitext(safe_file_name)[0]
    user_folder = expected_user_folder(user)
    file_folder_base = slugify(file_stem)
    file_folder = file_folder_base

    user_path = Path(UPLOAD_DIR) / user_folder
    user_path.mkdir(parents=True, exist_ok=True)

    target_folder = user_path / file_folder
    if target_folder.exists():
        file_folder = f"{file_folder_base}_{secrets.token_hex(4)}"
        target_folder = user_path / file_folder
    target_folder.mkdir(parents=True, exist_ok=False)

    target_path = target_folder / safe_file_name
    stored_name = str(target_path.relative_to(Path(UPLOAD_DIR)))

    content = await file.read()
    with open(target_path, "wb") as f:
        f.write(content)

    created_at = now_iso()
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO pdfs (user_id, original_name, stored_name, size_bytes, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user["id"], original_name, stored_name, len(content), created_at),
        )
        conn.commit()
        file_id = cursor.lastrowid

    return {
        "file": {
            "id": file_id,
            "original_name": original_name,
            "size_bytes": len(content),
            "created_at": created_at,
        }
    }


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


@router.delete("/{pdf_id}")
def delete_pdf(pdf_id: int, user=Depends(current_user)):
    with get_db() as conn:
        row = conn.execute(
            "SELECT stored_name FROM pdfs WHERE id = ? AND user_id = ?",
            (pdf_id, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        if not is_supported_storage_path(row["stored_name"], user):
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="Legacy storage format is unsupported")

        conn.execute("DELETE FROM pdfs WHERE id = ? AND user_id = ?", (pdf_id, user["id"]))
        conn.commit()

    path = os.path.join(UPLOAD_DIR, row["stored_name"])
    if os.path.exists(path):
        os.remove(path)
        parent_folder = os.path.dirname(path)
        # Only remove generated per-file folders, never the uploads root.
        if parent_folder and os.path.isdir(parent_folder) and os.path.abspath(parent_folder) != os.path.abspath(UPLOAD_DIR):
            shutil.rmtree(parent_folder, ignore_errors=True)
    return {"ok": True}
