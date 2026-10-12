"""Diagnostics and pruning of discovery's isolated namespace only."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def inspect_cache(root: Path) -> dict[str, Any]:
    files = list(root.glob("*/*.jpg"))
    stamps = [p.stat().st_mtime for p in files if p.is_file()]
    return {
        "size": sum(p.stat().st_size for p in files if p.is_file()),
        "entries": len(stamps),
        "oldest": datetime.fromtimestamp(min(stamps), UTC).isoformat() if stamps else None,
        "limit_bytes": 300 * 1024 * 1024,
        "max_age_days": 30,
    }


def prune(root: Path, protected: frozenset[str] = frozenset()) -> int:
    files = sorted(
        root.glob("*/*.jpg"),
        key=lambda p: (p.relative_to(root).as_posix() in protected, p.stat().st_mtime),
    )
    size = sum(p.stat().st_size for p in files)
    cutoff = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    count = 0
    for path in files:
        if size <= 300 * 1024 * 1024 and path.stat().st_mtime >= cutoff:
            continue
        size -= path.stat().st_size
        path.unlink(missing_ok=True)
        count += 1
    return count
