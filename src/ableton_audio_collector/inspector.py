from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from ableton_audio_collector.als import (
    AlsAudioFileRef,
    extract_ableton_version,
    extract_audio_file_refs,
    extract_plugin_refs,
    load_als_xml,
)
from ableton_audio_collector.models import (
    AudioReference,
    InspectError,
    PluginFormat,
    PluginReference,
    ReferenceStatus,
    ScanReport,
    SetReport,
    TrackType,
)
from ableton_audio_collector.project import infer_project_root, is_backup_set_file


def inspect_set(
    set_file: Path, project_root: Path | None = None, scan_date: str | None = None
) -> SetReport:
    set_file = set_file.expanduser().resolve()
    inferred_root = (
        project_root.expanduser().resolve() if project_root else infer_project_root(set_file)
    )
    timestamp = scan_date or _utc_now()
    try:
        set_stat = set_file.stat()
        set_modified_ns = set_stat.st_mtime_ns
        set_size = set_stat.st_size
    except OSError:
        set_modified_ns = None
        set_size = None

    try:
        root = load_als_xml(set_file)
        file_refs = extract_audio_file_refs(root)
        references = [
            classify_reference(file_ref, set_file.parent, inferred_root) for file_ref in file_refs
        ]
        plugins = [
            PluginReference(
                track_index=plugin.track_index,
                track_id=plugin.track_id,
                track_name=plugin.track_name,
                track_type=TrackType(plugin.track_type),
                plugin_index=plugin.plugin_index,
                plugin_name=plugin.plugin_name,
                plugin_format=PluginFormat(plugin.plugin_format),
            )
            for plugin in extract_plugin_refs(root)
        ]
        return SetReport(
            set_file=set_file,
            project_root=inferred_root,
            scan_date=timestamp,
            ableton_version=extract_ableton_version(root),
            set_modified_ns=set_modified_ns,
            set_size=set_size,
            audio_references=references,
            plugin_references=plugins,
        )
    except Exception as exc:
        return SetReport(
            set_file=set_file,
            project_root=inferred_root,
            scan_date=timestamp,
            ableton_version=None,
            set_modified_ns=set_modified_ns,
            set_size=set_size,
            errors=[
                InspectError(
                    path=set_file,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            ],
        )


def scan_folder(folder: Path) -> ScanReport:
    folder = folder.expanduser().resolve()
    timestamp = _utc_now()
    set_reports = [
        inspect_set(set_file, scan_date=timestamp)
        for set_file in sorted(folder.rglob("*"), key=lambda path: str(path).casefold())
        if set_file.is_file()
        and set_file.suffix.casefold() == ".als"
        and not is_backup_set_file(set_file)
    ]
    return ScanReport(scan_folder=folder, scan_date=timestamp, sets=set_reports)


def classify_reference(
    file_ref: AlsAudioFileRef, set_folder: Path, project_root: Path
) -> AudioReference:
    resolved_path = _resolve_file_ref(file_ref, set_folder, project_root)

    if resolved_path is None:
        return AudioReference(
            ref_index=file_ref.index,
            referenced_path=file_ref.referenced_path,
            resolved_path=None,
            status=ReferenceStatus.MISSING,
            file_size=None,
            matching_asd_exists=False,
            relative_path=file_ref.relative_path,
            path=file_ref.path,
            relative_path_type=file_ref.relative_path_type,
            original_file_size=file_ref.original_file_size,
            original_crc=file_ref.original_crc,
        )

    status = (
        ReferenceStatus.ALREADY_LOCAL
        if _is_relative_to(resolved_path, project_root)
        else ReferenceStatus.EXTERNAL_COLLECTABLE
    )
    return AudioReference(
        ref_index=file_ref.index,
        referenced_path=file_ref.referenced_path,
        resolved_path=resolved_path,
        status=status,
        file_size=resolved_path.stat().st_size,
        matching_asd_exists=Path(f"{resolved_path}.asd").exists(),
        relative_path=file_ref.relative_path,
        path=file_ref.path,
        relative_path_type=file_ref.relative_path_type,
        original_file_size=file_ref.original_file_size,
        original_crc=file_ref.original_crc,
    )


def _resolve_file_ref(
    file_ref: AlsAudioFileRef,
    set_folder: Path,
    project_root: Path,
) -> Path | None:
    stored_paths = dict.fromkeys(
        path for path in (file_ref.relative_path, file_ref.path, file_ref.referenced_path) if path
    )
    for stored_path in stored_paths:
        resolved = _resolve_reference(stored_path, set_folder, project_root)
        if resolved is not None:
            return resolved
    return None


def _resolve_reference(reference_path: str, set_folder: Path, project_root: Path) -> Path | None:
    candidates: list[Path] = []
    for variant in _path_variants(reference_path):
        candidate = Path(variant).expanduser()
        if candidate.is_absolute():
            candidates.append(candidate)
        else:
            candidates.extend((set_folder / candidate, project_root / candidate))

    for path in dict.fromkeys(candidates):
        resolved = _resolve_existing_path(path)
        if resolved is not None and resolved.is_file():
            return resolved
    return None


def _path_variants(path: str) -> tuple[str, ...]:
    variants = [path]
    if os.sep == "/" and "\\" in path:
        variants.append(path.replace("\\", "/"))
    elif os.sep == "\\" and "/" in path:
        variants.append(path.replace("/", "\\"))
    return tuple(dict.fromkeys(variants))


def _resolve_existing_path(path: Path) -> Path | None:
    try:
        return path.resolve(strict=True)
    except (FileNotFoundError, OSError):
        pass

    # Sets moved between case-insensitive and case-sensitive file systems may
    # retain a different filename casing. Resolve that case only when every
    # path component has a single unambiguous match.
    normalized = path.resolve(strict=False)
    anchor = Path(normalized.anchor)
    parts = normalized.parts[1:] if normalized.anchor else normalized.parts
    current = anchor if normalized.anchor else Path.cwd()
    try:
        for part in parts:
            exact = current / part
            if exact.exists():
                current = exact
                continue
            matches = [
                child for child in current.iterdir() if child.name.casefold() == part.casefold()
            ]
            if len(matches) != 1:
                return None
            current = matches[0]
        return current.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
