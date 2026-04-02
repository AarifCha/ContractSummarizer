from __future__ import annotations

from collections import defaultdict

from app.summary.chunk_data import chunk_order_sort_key


def build_page_windows(rows: list[dict]) -> list[tuple[int, int, list[dict]]]:
    page_to_rows: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        pages = row.get("page_numbers")
        if not isinstance(pages, list) or not pages:
            continue
        try:
            page0 = int(pages[0])
        except (TypeError, ValueError):
            continue
        page_to_rows[page0].append(row)
    if not page_to_rows:
        return []

    min_page = min(page_to_rows.keys())
    max_page = max(page_to_rows.keys())
    windows: list[tuple[int, int, list[dict]]] = []
    start = min_page
    while start <= max_page:
        end = start + 4
        bucket: list[dict] = []
        for p in range(start, end + 1):
            bucket.extend(page_to_rows.get(p, []))
        if bucket:
            bucket.sort(key=chunk_order_sort_key)
            windows.append((start, end, bucket))
        start += 4  # 1-page overlap on 5-page windows
    return windows


def format_window_text(rows: list[dict]) -> str:
    blocks: list[str] = []
    for row in rows:
        try:
            chunk_idx = int(row.get("chunk_index", 0))
        except (TypeError, ValueError):
            chunk_idx = 0
        pages = row.get("page_numbers")
        if isinstance(pages, list) and pages:
            try:
                page0 = int(pages[0])
            except (TypeError, ValueError):
                page0 = -1
        else:
            page0 = -1
        text = str(row.get("text") or "")
        blocks.append(
            f"[chunk_{chunk_idx}]\n"
            f"page: {page0}\n"
            f"chunk_id: {chunk_idx}\n"
            f"text:\n{text}"
        )
    return "\n\n".join(blocks)
