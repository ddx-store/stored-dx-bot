"""Small shared helpers."""
from __future__ import annotations

import html
import re
from pathlib import Path


def human_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    step = 1024.0
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if size < step:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= step
    return f"{size:.1f} PB"


def human_seconds(seconds: float | int | None) -> str:
    if seconds is None:
        return "?"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def safe_html(text: str | None) -> str:
    return html.escape(text or "", quote=False)


_INVALID_FS_CHARS = re.compile(r"[^\w\-.]+")


def safe_filename(name: str, max_len: int = 80) -> str:
    name = _INVALID_FS_CHARS.sub("_", name).strip("._") or "file"
    return name[:max_len]


def ensure_file_cleanup(path: Path | None) -> None:
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
    # Also sweep sibling partial files from yt-dlp.
    if path is not None:
        try:
            for sibling in path.parent.glob(path.name + "*"):
                try:
                    sibling.unlink()
                except OSError:
                    pass
        except OSError:
            pass
