from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ableton_audio_collector.collector import CollectionPlan, build_collection_plan_from_report
from ableton_audio_collector.inspector import inspect_set, scan_folder
from ableton_audio_collector.models import SetReport
from ableton_audio_collector.project import is_backup_set_file


@dataclass(frozen=True)
class ReviewRow:
    id: int
    set_report: SetReport
    plan: CollectionPlan | None
    status: str
    selectable: bool
    blockers: list[str]

    @property
    def set_file(self) -> Path:
        return self.set_report.set_file

    @property
    def collectable_count(self) -> int:
        return len(self.plan.items) if self.plan else 0


@dataclass(frozen=True)
class ReviewSourceIssue:
    path: Path
    message: str


@dataclass(frozen=True)
class ReviewBatch:
    sources: tuple[Path, ...]
    rows: list[ReviewRow]
    issues: list[ReviewSourceIssue]


def build_review_rows(folder: Path) -> list[ReviewRow]:
    """Build review rows for one folder, preserving the original public API."""
    return build_review_batch([folder]).rows


def build_review_rows_from_sources(sources: Iterable[Path]) -> list[ReviewRow]:
    """Build deduplicated rows from any mix of folders and explicit Set files."""
    return build_review_batch(sources).rows


def build_review_batch(sources: Iterable[Path]) -> ReviewBatch:
    """Resolve, validate, scan, and deduplicate folder and Ableton Set inputs."""
    normalized_sources: list[Path] = []
    issues: list[ReviewSourceIssue] = []
    reports_by_set: dict[Path, SetReport] = {}

    for source in sources:
        path = Path(source).expanduser().resolve()
        if path in normalized_sources:
            continue
        normalized_sources.append(path)

        if not path.exists():
            issues.append(ReviewSourceIssue(path, "The dropped item no longer exists."))
            continue
        if path.is_dir():
            for report in scan_folder(path).sets:
                reports_by_set.setdefault(report.set_file, report)
            continue
        if path.suffix.casefold() != ".als":
            issues.append(
                ReviewSourceIssue(path, "Only folders and Ableton .als files are supported.")
            )
            continue
        if is_backup_set_file(path):
            issues.append(
                ReviewSourceIssue(path, "Backup Sets are excluded to protect archived versions.")
            )
            continue
        reports_by_set.setdefault(path, inspect_set(path))

    set_reports = [reports_by_set[path] for path in sorted(reports_by_set)]
    rows: list[ReviewRow] = []
    for set_report in set_reports:
        plan: CollectionPlan | None = None
        blockers: list[str] = []

        if set_report.errors:
            blockers.extend(f"{error.error_type}: {error.message}" for error in set_report.errors)
        else:
            plan = build_collection_plan_from_report(set_report)
            blockers.extend(plan.conflicts)
            if set_report.missing_count:
                blockers.append("Set has missing audio references.")

        selectable = bool(plan and plan.items and not blockers)
        rows.append(
            ReviewRow(
                id=len(rows) + 1,
                set_report=set_report,
                plan=plan,
                status=_status_for(set_report, blockers, selectable),
                selectable=selectable,
                blockers=blockers,
            )
        )
    return ReviewBatch(
        sources=tuple(normalized_sources),
        rows=rows,
        issues=issues,
    )


def selected_apply_ids(row: ReviewRow) -> set[int]:
    if not row.plan:
        return set()
    return {item.id for item in row.plan.items}


def _status_for(set_report: SetReport, blockers: list[str], selectable: bool) -> str:
    if blockers and set_report.errors:
        return "error"
    if blockers and set_report.missing_count:
        return "blocked_missing"
    if blockers:
        return "blocked"
    if selectable:
        return "collectable"
    return "safe"
