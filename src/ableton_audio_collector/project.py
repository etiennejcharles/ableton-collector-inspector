from __future__ import annotations

from pathlib import Path

PROJECT_MARKERS = ("Ableton Project Info", "Samples", "Backup")


def infer_project_root(set_file: Path) -> Path:
    """Infer the Ableton project folder for a Set file."""
    start = set_file.resolve().parent
    for candidate in (start, *start.parents):
        if any((candidate / marker).is_dir() for marker in PROJECT_MARKERS):
            return candidate
    return start


def is_backup_set_file(set_file: Path) -> bool:
    return any(
        part.casefold() in {"backup", "backups"}
        or part.casefold().endswith((" backup", " backups"))
        for part in set_file.resolve().parent.parts
    )
