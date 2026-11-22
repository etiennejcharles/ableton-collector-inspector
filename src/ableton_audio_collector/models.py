from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class ReferenceStatus(StrEnum):
    ALREADY_LOCAL = "already_local"
    EXTERNAL_COLLECTABLE = "external_collectable"
    MISSING = "missing"


class PluginFormat(StrEnum):
    AUDIO_UNIT = "audio_unit"
    VST2 = "vst2"
    VST3 = "vst3"
    UNKNOWN = "unknown"


class TrackType(StrEnum):
    AUDIO = "audio"
    MIDI = "midi"
    GROUP = "group"
    RETURN = "return"
    MAIN = "main"


@dataclass(frozen=True)
class AudioReference:
    ref_index: int
    referenced_path: str
    resolved_path: Path | None
    status: ReferenceStatus
    file_size: int | None
    matching_asd_exists: bool
    relative_path: str | None = None
    path: str | None = None
    relative_path_type: str | None = None
    original_file_size: int | None = None
    original_crc: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_index": self.ref_index,
            "referenced_path": self.referenced_path,
            "resolved_path": str(self.resolved_path) if self.resolved_path else None,
            "status": self.status.value,
            "file_size": self.file_size,
            "matching_asd_exists": self.matching_asd_exists,
            "relative_path": self.relative_path,
            "path": self.path,
            "relative_path_type": self.relative_path_type,
            "original_file_size": self.original_file_size,
            "original_crc": self.original_crc,
        }


@dataclass(frozen=True)
class PluginReference:
    track_index: int
    track_id: str | None
    track_name: str
    track_type: TrackType
    plugin_index: int
    plugin_name: str
    plugin_format: PluginFormat

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_index": self.track_index,
            "track_id": self.track_id,
            "track_name": self.track_name,
            "track_type": self.track_type.value,
            "plugin_index": self.plugin_index,
            "plugin_name": self.plugin_name,
            "plugin_format": self.plugin_format.value,
        }


@dataclass(frozen=True)
class InspectError:
    path: Path
    error_type: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "error_type": self.error_type,
            "message": self.message,
        }


@dataclass(frozen=True)
class SetReport:
    set_file: Path
    project_root: Path
    scan_date: str
    ableton_version: str | None
    set_modified_ns: int | None = None
    set_size: int | None = None
    audio_references: list[AudioReference] = field(default_factory=list)
    plugin_references: list[PluginReference] = field(default_factory=list)
    errors: list[InspectError] = field(default_factory=list)

    @property
    def total_audio_references(self) -> int:
        return len(self.audio_references)

    @property
    def already_local_count(self) -> int:
        return self._count(ReferenceStatus.ALREADY_LOCAL)

    @property
    def external_collectable_count(self) -> int:
        return self._count(ReferenceStatus.EXTERNAL_COLLECTABLE)

    @property
    def missing_count(self) -> int:
        return self._count(ReferenceStatus.MISSING)

    @property
    def unique_media_count(self) -> int:
        keys: set[tuple[str, int | None, str | None]] = set()
        for reference in self.audio_references:
            if reference.resolved_path:
                keys.add(_media_key(reference))
        return len(keys)

    @property
    def total_plugin_instances(self) -> int:
        return len(self.plugin_references)

    @property
    def tracks_with_plugins_count(self) -> int:
        return len(
            {
                (plugin.track_index, plugin.track_id, plugin.track_name)
                for plugin in self.plugin_references
            }
        )

    def plugin_count(self, plugin_format: PluginFormat) -> int:
        return sum(1 for plugin in self.plugin_references if plugin.plugin_format == plugin_format)

    def _count(self, status: ReferenceStatus) -> int:
        return sum(1 for reference in self.audio_references if reference.status == status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "set_file": str(self.set_file),
            "project_root": str(self.project_root),
            "scan_date": self.scan_date,
            "ableton_version": self.ableton_version,
            "set_modified_ns": self.set_modified_ns,
            "set_size": self.set_size,
            "total_audio_references": self.total_audio_references,
            "unique_media_count": self.unique_media_count,
            "already_local_count": self.already_local_count,
            "external_collectable_count": self.external_collectable_count,
            "missing_count": self.missing_count,
            "total_plugin_instances": self.total_plugin_instances,
            "tracks_with_plugins_count": self.tracks_with_plugins_count,
            "plugin_format_counts": {
                plugin_format.value: self.plugin_count(plugin_format)
                for plugin_format in PluginFormat
            },
            "audio_references": [reference.to_dict() for reference in self.audio_references],
            "plugin_references": [plugin.to_dict() for plugin in self.plugin_references],
            "errors": [error.to_dict() for error in self.errors],
        }


@dataclass(frozen=True)
class ScanReport:
    scan_folder: Path
    scan_date: str
    sets: list[SetReport]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_folder": str(self.scan_folder),
            "scan_date": self.scan_date,
            "set_count": len(self.sets),
            "sets": [set_report.to_dict() for set_report in self.sets],
        }


def _media_key(reference: AudioReference) -> tuple[str, int | None, str | None]:
    name = (
        reference.resolved_path.name.lower()
        if reference.resolved_path
        else Path(reference.referenced_path).name.lower()
    )
    size = (
        reference.original_file_size
        if reference.original_file_size is not None
        else reference.file_size
    )
    return (name, size, reference.original_crc)
