import os
import secrets
import shutil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status

from app.api.pdfs.constants import UPLOAD_DIR, now_iso
from app.api.pdfs.storage_paths import expected_user_folder, is_supported_storage_path, slugify
from app.core.db import get_db
from app.core.deps import current_user
from app.preprocessing.first_pass_pipeline import run_first_pass_for_pdf

router = APIRouter()


def list_pdfs(user=Depends(current_user)):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
              id,
              original_name,
              size_bytes,
              created_at,
              stored_name,
              processing_stage,
              processing_total_chunks,
              processing_completed_chunks,
              processing_status,
              processing_error
            FROM pdfs
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user["id"],),
        ).fetchall()

    files = []
    stale_ids = []
    for row in rows:
        row_dict = dict(row)
        if not is_supported_storage_path(row_dict["stored_name"], user):
            stale_ids.append(row_dict["id"])
            continue

        file_path = os.path.join(UPLOAD_DIR, row_dict["stored_name"])
        if not os.path.exists(file_path):
            stale_ids.append(row_dict["id"])
            continue

        row_dict.pop("stored_name", None)
        files.append(row_dict)

    if stale_ids:
        placeholders = ",".join("?" for _ in stale_ids)
        with get_db() as conn:
            conn.execute(
                f"DELETE FROM pdfs WHERE user_id = ? AND id IN ({placeholders})",
                (user["id"], *stale_ids),
            )
            conn.commit()

    return {"files": files}


async def upload_pdf(background_tasks: BackgroundTasks, file: UploadFile = File(...), user=Depends(current_user)):
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
            INSERT INTO pdfs (
              user_id,
              original_name,
              stored_name,
              size_bytes,
              created_at,
              processing_stage,
              processing_total_chunks,
              processing_completed_chunks,
              processing_status,
              processing_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                original_name,
                stored_name,
                len(content),
                created_at,
                "first_pass_extraction",
                1,
                0,
                "queued",
                None,
            ),
        )
        conn.commit()
        file_id = cursor.lastrowid

    background_tasks.add_task(
        run_first_pass_for_pdf,
        pdf_id=file_id,
        user_id=user["id"],
        source_pdf_path=target_path,
    )

    return {
        "file": {
            "id": file_id,
            "original_name": original_name,
            "size_bytes": len(content),
            "created_at": created_at,
        }
    }


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
        if parent_folder and os.path.isdir(parent_folder) and os.path.abspath(parent_folder) != os.path.abspath(UPLOAD_DIR):
            shutil.rmtree(parent_folder, ignore_errors=True)
    return {"ok": True}
