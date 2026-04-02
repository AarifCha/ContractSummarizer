from __future__ import annotations

from pathlib import Path


def final_summary_dir(upload_folder: Path) -> Path:
    path = upload_folder / "final_summary"
    path.mkdir(parents=True, exist_ok=True)
    return path


def final_summary_path(upload_folder: Path, stem: str) -> Path:
    return final_summary_dir(upload_folder) / f"{stem}_final_summary.md"


def load_cached_final_summary(upload_folder: Path, stem: str) -> str | None:
    path = final_summary_path(upload_folder, stem)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def persist_final_summary(upload_folder: Path, stem: str, summary_text: str) -> None:
    if not summary_text.strip():
        return
    path = final_summary_path(upload_folder, stem)
    path.write_text(summary_text, encoding="utf-8")
