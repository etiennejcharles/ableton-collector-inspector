from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from ableton_audio_collector.models import SetReport

SCHEMA_VERSION = 1


class HistoryStoreError(RuntimeError):
    """Raised when scan history cannot be read or written safely."""


@dataclass(frozen=True)
class HistoryPlugin:
    track_name: str
    track_type: str
    plugin_name: str
    plugin_format: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoryPlugin:
        return cls(
            track_name=str(data.get("track_name", "Unknown track")),
            track_type=str(data.get("track_type", "unknown")),
            plugin_name=str(data.get("plugin_name", "Unknown plug-in")),
            plugin_format=str(data.get("plugin_format", "unknown")),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "track_name": self.track_name,
            "track_type": self.track_type,
            "plugin_name": self.plugin_name,
            "plugin_format": self.plugin_format,
        }


@dataclass(frozen=True)
class HistoryAudioFile:
    referenced_path: str
    resolved_path: str | None
    status: str
    file_size: int | None
    matching_asd_exists: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoryAudioFile:
        resolved_path = data.get("resolved_path")
        file_size = data.get("file_size")
        return cls(
            referenced_path=str(data.get("referenced_path", "Unknown file")),
            resolved_path=str(resolved_path) if resolved_path is not None else None,
            status=str(data.get("status", "missing")),
            file_size=int(file_size) if isinstance(file_size, int) else None,
            matching_asd_exists=bool(data.get("matching_asd_exists", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "referenced_path": self.referenced_path,
            "resolved_path": self.resolved_path,
            "status": self.status,
            "file_size": self.file_size,
            "matching_asd_exists": self.matching_asd_exists,
        }


@dataclass(frozen=True)
class HistoryFreshness:
    state: str
    warning: bool
    message: str
    current_saved_at: str | None = None


@dataclass(frozen=True)
class HistoryEntry:
    entry_id: str
    scanned_at: str
    source_paths: tuple[str, ...]
    set_file: str
    project_root: str
    set_modified_ns: int | None
    set_saved_at_scan: str | None
    set_size_at_scan: int | None
    ableton_version: str | None
    plugins: tuple[HistoryPlugin, ...]
    audio_files: tuple[HistoryAudioFile, ...]
    errors: tuple[str, ...]

    @classmethod
    def from_report(
        cls,
        report: SetReport,
        source_paths: Iterable[Path | str] = (),
    ) -> HistoryEntry:
        modified_ns = report.set_modified_ns
        size = report.set_size
        if modified_ns is None and size is None:
            try:
                stat = report.set_file.stat()
                modified_ns = stat.st_mtime_ns
                size = stat.st_size
            except OSError:
                pass
        saved_at = _timestamp_from_ns(modified_ns) if modified_ns is not None else None
        return cls(
            entry_id=uuid4().hex,
            scanned_at=report.scan_date,
            source_paths=tuple(str(Path(path)) for path in source_paths),
            set_file=str(report.set_file),
            project_root=str(report.project_root),
            set_modified_ns=modified_ns,
            set_saved_at_scan=saved_at,
            set_size_at_scan=size,
            ableton_version=report.ableton_version,
            plugins=tuple(
                HistoryPlugin(
                    track_name=plugin.track_name,
                    track_type=plugin.track_type.value,
                    plugin_name=plugin.plugin_name,
                    plugin_format=plugin.plugin_format.value,
                )
                for plugin in report.plugin_references
            ),
            audio_files=tuple(
                HistoryAudioFile(
                    referenced_path=reference.referenced_path,
                    resolved_path=(
                        str(reference.resolved_path) if reference.resolved_path else None
                    ),
                    status=reference.status.value,
                    file_size=reference.file_size,
                    matching_asd_exists=reference.matching_asd_exists,
                )
                for reference in report.audio_references
            ),
            errors=tuple(f"{error.error_type}: {error.message}" for error in report.errors),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoryEntry:
        modified_ns = data.get("set_modified_ns")
        size = data.get("set_size_at_scan")
        ableton_version = data.get("ableton_version")
        saved_at = data.get("set_saved_at_scan")
        return cls(
            entry_id=str(data["entry_id"]),
            scanned_at=str(data["scanned_at"]),
            source_paths=tuple(str(path) for path in data.get("source_paths", [])),
            set_file=str(data["set_file"]),
            project_root=str(data["project_root"]),
            set_modified_ns=int(modified_ns) if isinstance(modified_ns, int) else None,
            set_saved_at_scan=str(saved_at) if saved_at is not None else None,
            set_size_at_scan=int(size) if isinstance(size, int) else None,
            ableton_version=(str(ableton_version) if ableton_version is not None else None),
            plugins=tuple(HistoryPlugin.from_dict(plugin) for plugin in data.get("plugins", [])),
            audio_files=tuple(
                HistoryAudioFile.from_dict(audio_file) for audio_file in data.get("audio_files", [])
            ),
            errors=tuple(str(error) for error in data.get("errors", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "scanned_at": self.scanned_at,
            "source_paths": list(self.source_paths),
            "set_file": self.set_file,
            "project_root": self.project_root,
            "set_modified_ns": self.set_modified_ns,
            "set_saved_at_scan": self.set_saved_at_scan,
            "set_size_at_scan": self.set_size_at_scan,
            "ableton_version": self.ableton_version,
            "plugins": [plugin.to_dict() for plugin in self.plugins],
            "audio_files": [audio_file.to_dict() for audio_file in self.audio_files],
            "errors": list(self.errors),
        }

    def freshness(self) -> HistoryFreshness:
        set_path = Path(self.set_file)
        try:
            stat = set_path.stat()
        except FileNotFoundError:
            return HistoryFreshness(
                state="missing",
                warning=True,
                message="The Ableton Set file is no longer at its scanned location.",
            )
        except OSError as exc:
            return HistoryFreshness(
                state="unavailable",
                warning=True,
                message=f"The Ableton Set save time could not be checked: {exc}",
            )

        current_saved_at = _timestamp_from_ns(stat.st_mtime_ns)
        if self.set_modified_ns is None:
            return HistoryFreshness(
                state="unverified",
                warning=True,
                message="This snapshot has no recorded Set save time, so freshness is unknown.",
                current_saved_at=current_saved_at,
            )
        if (
            stat.st_mtime_ns != self.set_modified_ns
            or self.set_size_at_scan is not None
            and stat.st_size != self.set_size_at_scan
        ):
            return HistoryFreshness(
                state="changed",
                warning=True,
                message="The Ableton Set was saved or replaced after this scan.",
                current_saved_at=current_saved_at,
            )
        return HistoryFreshness(
            state="current",
            warning=False,
            message="The Set save timestamp still matches this scan snapshot.",
            current_saved_at=current_saved_at,
        )


class HistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()

    def load(self) -> list[HistoryEntry]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HistoryStoreError(f"Could not read scan history: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            raise HistoryStoreError("The scan history uses an unsupported format.")
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            raise HistoryStoreError("The scan history does not contain a valid entry list.")
        try:
            entries = [HistoryEntry.from_dict(entry) for entry in raw_entries]
        except (KeyError, TypeError, ValueError) as exc:
            raise HistoryStoreError(f"The scan history contains an invalid entry: {exc}") from exc
        return sorted(entries, key=lambda entry: entry.scanned_at, reverse=True)

    def record_reports(
        self,
        reports: Iterable[SetReport],
        source_paths: Iterable[Path | str] = (),
    ) -> list[HistoryEntry]:
        sources = tuple(source_paths)
        new_entries = [HistoryEntry.from_report(report, sources) for report in reports]
        if not new_entries:
            return self.load()
        entries = [*new_entries, *self.load()]
        self._write(entries)
        return sorted(entries, key=lambda entry: entry.scanned_at, reverse=True)

    def _write(self, entries: list[HistoryEntry]) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "entries": [entry.to_dict() for entry in entries],
        }
        temporary_path = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_path, self.path)
        except OSError as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise HistoryStoreError(f"Could not save scan history: {exc}") from exc


def default_history_path() -> Path:
    if sys.platform == "darwin":
        data_root = Path.home() / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        data_root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        data_root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_root / "Ableton Inspector" / "scan-history.json"


def _timestamp_from_ns(timestamp_ns: int) -> str:
    return (
        datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
