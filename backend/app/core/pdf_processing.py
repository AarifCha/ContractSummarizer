import logging
from pathlib import Path
from typing import Any

from app.ai.first_pass_metadata_extraction import extract_pdf_to_block_jsons
from app.core.db import get_db

logger = logging.getLogger(__name__)


def set_processing_state(
    pdf_id: int,
    *,
    stage: str | None = None,
    status: str | None = None,
    total_chunks: int | None = None,
    completed_chunks: int | None = None,
    error: str | None = None,
) -> None:
    updates: list[str] = []
    values: list[Any] = []
    if stage is not None:
        updates.append("processing_stage = ?")
        values.append(stage)
    if status is not None:
        updates.append("processing_status = ?")
        values.append(status)
    if total_chunks is not None:
        updates.append("processing_total_chunks = ?")
        values.append(total_chunks)
    if completed_chunks is not None:
        updates.append("processing_completed_chunks = ?")
        values.append(completed_chunks)
    if error is not None:
        updates.append("processing_error = ?")
        values.append(error)
    if not updates:
        return

    values.append(pdf_id)
    with get_db() as conn:
        conn.execute(f"UPDATE pdfs SET {', '.join(updates)} WHERE id = ?", tuple(values))
        conn.commit()


def _run_first_pass_local(source_pdf: Path, output_dir: Path) -> int:
    """Local Docling + disk I/O; propagate OSError / MemoryError without HTTP-style retries."""
    try:
        return extract_pdf_to_block_jsons(source_pdf, output_dir)
    except (OSError, MemoryError):
        raise


def run_first_pass_for_pdf(*, pdf_id: int, user_id: int, source_pdf_path: Path) -> None:
    target_folder = source_pdf_path.parent.resolve()
    first_pass_dir = target_folder / "first_pass_data"
    first_pass_dir.mkdir(parents=True, exist_ok=True)
    for stale in first_pass_dir.glob("*.json"):
        try:
            stale.unlink()
        except OSError:
            logger.warning("Could not remove stale first_pass_data file %s", stale)

    set_processing_state(
        pdf_id,
        stage="first_pass_extraction",
        status="running",
        total_chunks=1,
        completed_chunks=0,
        error=None,
    )

    logger.info(
        "first_pass: Docling on full PDF; writing JSON under %s",
        first_pass_dir,
    )

    try:
        _run_first_pass_local(source_pdf_path.resolve(), first_pass_dir)
        set_processing_state(
            pdf_id,
            stage="first_pass_extraction",
            status="done",
            completed_chunks=1,
            error=None,
        )
    except Exception as exc:
        error_message = f"first_pass_error: {exc}"
        if len(error_message) > 1200:
            error_message = f"{error_message[:1200]}..."
        set_processing_state(
            pdf_id,
            stage="first_pass_extraction",
            status="failed",
            error=error_message,
        )
