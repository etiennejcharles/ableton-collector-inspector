from __future__ import annotations

import gzip
import hashlib
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from ableton_audio_collector.als import (
    SUPPORTED_AUDIO_EXTENSIONS,
    _local_name,
    extract_audio_file_refs,
    load_als_xml,
)
from ableton_audio_collector.inspector import inspect_set
from ableton_audio_collector.models import AudioReference, ReferenceStatus, SetReport

IMPORTED_RELATIVE_DIR = Path("Samples") / "Imported"
_COPY_BUFFER_SIZE = 1024 * 1024
_INVALID_PORTABLE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_STEMS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


@dataclass(frozen=True)
class CollectionItem:
    id: int
    source_path: Path
    source_size: int
    destination_path: Path
    relative_destination: str
    references: list[AudioReference]
    asd_source_path: Path | None
    asd_destination_path: Path | None


@dataclass(frozen=True)
class CollectionPlan:
    set_report: SetReport
    items: list[CollectionItem]
    conflicts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CollectionResult:
    plan: CollectionPlan
    selected_items: list[CollectionItem]
    backup_path: Path
    copied_audio: list[tuple[Path, Path]]
    copied_asd: list[tuple[Path, Path]]
    rewritten_reference_count: int
    final_report: SetReport


@dataclass(frozen=True)
class RestoreResult:
    set_file: Path
    restored_from: Path
    safety_backup_path: Path
    final_report: SetReport


class CollectionError(Exception):
    pass


@dataclass(frozen=True)
class CollectionFailure:
    message: str
    failed_step: str
    backup_path: Path | None
    rollback_attempted: bool
    rollback_succeeded: bool
    copied_audio: list[tuple[Path, Path]] = field(default_factory=list)
    copied_asd: list[tuple[Path, Path]] = field(default_factory=list)
    cleanup_attempted: bool = False
    cleanup_succeeded: bool = True
    cleanup_errors: list[str] = field(default_factory=list)


class CollectionApplyError(CollectionError):
    def __init__(self, failure: CollectionFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


def build_collection_plan(set_file: Path, project_root: Path | None = None) -> CollectionPlan:
    report = inspect_set(set_file, project_root=project_root)
    return build_collection_plan_from_report(report)


def build_collection_plan_from_report(report: SetReport) -> CollectionPlan:
    conflicts = [
        f"Set inspection failed: {error.error_type}: {error.message}" for error in report.errors
    ]
    groups: list[list[AudioReference]] = []
    group_indexes_by_shape: dict[tuple[str, int], list[int]] = {}
    for reference in report.audio_references:
        if reference.status != ReferenceStatus.EXTERNAL_COLLECTABLE or not reference.resolved_path:
            continue
        source_path = reference.resolved_path
        try:
            source_size = source_path.stat().st_size
        except OSError as exc:
            conflicts.append(f"External source is no longer readable: {source_path}: {exc}")
            continue

        shape = (source_path.name.casefold(), source_size)
        matched_group: int | None = None
        for group_index in group_indexes_by_shape.get(shape, []):
            first_path = groups[group_index][0].resolved_path
            if first_path and _files_match(source_path, first_path):
                matched_group = group_index
                break
        if matched_group is None:
            matched_group = len(groups)
            groups.append([])
            group_indexes_by_shape.setdefault(shape, []).append(matched_group)
        groups[matched_group].append(reference)

    items: list[CollectionItem] = []
    imported_dir = report.project_root / IMPORTED_RELATIVE_DIR
    existing_by_name = _existing_paths_by_portable_name(imported_dir)
    reserved_names: set[str] = set()
    for references in groups:
        first = references[0]
        assert first.resolved_path is not None
        asd_source_path = Path(f"{first.resolved_path}.asd")
        destination_path = _choose_destination(
            imported_dir,
            first.resolved_path,
            asd_source_path if asd_source_path.is_file() else None,
            existing_by_name,
            reserved_names,
        )
        relative_destination = destination_path.relative_to(report.project_root).as_posix()
        asd_destination_path = Path(f"{destination_path}.asd")
        items.append(
            CollectionItem(
                id=len(items) + 1,
                source_path=first.resolved_path,
                source_size=first.resolved_path.stat().st_size,
                destination_path=destination_path,
                relative_destination=relative_destination,
                references=references,
                asd_source_path=asd_source_path if asd_source_path.exists() else None,
                asd_destination_path=asd_destination_path if asd_source_path.exists() else None,
            )
        )

    return CollectionPlan(set_report=report, items=items, conflicts=conflicts)


def apply_collection(
    set_file: Path,
    selected_ids: set[int],
    project_root: Path | None = None,
) -> CollectionResult:
    set_path = Path(set_file).expanduser().resolve()
    with _exclusive_set_lock(set_path):
        return _apply_collection_locked(set_path, selected_ids, project_root)


def _apply_collection_locked(
    set_file: Path,
    selected_ids: set[int],
    project_root: Path | None,
) -> CollectionResult:
    plan = build_collection_plan(set_file, project_root=project_root)
    if plan.conflicts:
        raise CollectionError("\n".join(plan.conflicts))

    selected_items = [item for item in plan.items if item.id in selected_ids]
    missing_ids = sorted(selected_ids - {item.id for item in plan.items})
    if missing_ids:
        raise CollectionError(
            f"Unknown collection item id(s): {', '.join(str(item_id) for item_id in missing_ids)}"
        )
    if not selected_items:
        raise CollectionError("No collectable external files were selected.")

    for item in selected_items:
        if not item.source_path.is_file():
            raise CollectionError(f"Selected source file is missing: {item.source_path}")
        if item.source_path.stat().st_size != item.source_size:
            raise CollectionError(
                f"Selected source file changed after the collection plan was built: {item.source_path}"
            )

    set_path = plan.set_report.set_file
    backup_path: Path | None = None
    copied_audio: list[tuple[Path, Path]] = []
    copied_asd: list[tuple[Path, Path]] = []
    failed_step = "backup"
    try:
        backup_path = _create_backup(set_path, plan.set_report.project_root)
        failed_step = "copy"
        _copy_selected_items(selected_items, copied_audio, copied_asd)
        failed_step = "rewrite"
        rewritten_count = _rewrite_set_file(set_path, selected_items)
        failed_step = "validate"
        final_report = inspect_set(set_path, project_root=plan.set_report.project_root)
        _validate_apply_result(
            selected_items,
            final_report,
            backup_path,
            rewritten_count,
        )
    except Exception as exc:
        rollback_attempted = backup_path is not None
        rollback_succeeded = False
        if backup_path is not None:
            try:
                _atomic_copy(backup_path, set_path, replace_existing=True)
                rollback_succeeded = True
            except Exception:
                rollback_succeeded = False
        cleanup_errors = _cleanup_copied_files(copied_asd, copied_audio)
        raise CollectionApplyError(
            CollectionFailure(
                message=str(exc),
                failed_step=failed_step,
                backup_path=backup_path,
                rollback_attempted=rollback_attempted,
                rollback_succeeded=rollback_succeeded,
                copied_audio=copied_audio,
                copied_asd=copied_asd,
                cleanup_attempted=bool(copied_audio or copied_asd),
                cleanup_succeeded=not cleanup_errors,
                cleanup_errors=cleanup_errors,
            )
        ) from exc

    return CollectionResult(
        plan=plan,
        selected_items=selected_items,
        backup_path=backup_path,
        copied_audio=copied_audio,
        copied_asd=copied_asd,
        rewritten_reference_count=rewritten_count,
        final_report=final_report,
    )


def restore_set_backup(
    set_file: Path,
    backup_file: Path,
    project_root: Path | None = None,
) -> RestoreResult:
    set_path = Path(set_file).expanduser().resolve()
    backup_path = Path(backup_file).expanduser().resolve()
    if set_path == backup_path:
        raise CollectionError("The active Set and restore source must be different files.")
    if not set_path.is_file():
        raise CollectionError(f"Active Set does not exist: {set_path}")
    if not backup_path.is_file():
        raise CollectionError(f"Backup Set does not exist: {backup_path}")

    try:
        load_als_xml(backup_path)
    except Exception as exc:
        raise CollectionError(
            f"Backup Set is not a readable .als file: {backup_path}: {exc}"
        ) from exc

    with _exclusive_set_lock(set_path):
        current_report = inspect_set(set_path, project_root=project_root)
        resolved_project_root = current_report.project_root
        safety_backup = _create_backup(set_path, resolved_project_root)
        try:
            _atomic_copy(backup_path, set_path, replace_existing=True)
            final_report = inspect_set(set_path, project_root=resolved_project_root)
            if final_report.errors:
                messages = "; ".join(error.message for error in final_report.errors)
                raise CollectionError(f"Restored Set validation failed: {messages}")
        except Exception as exc:
            rollback_succeeded = False
            try:
                _atomic_copy(safety_backup, set_path, replace_existing=True)
                rollback_succeeded = True
            except Exception:
                pass
            raise CollectionApplyError(
                CollectionFailure(
                    message=str(exc),
                    failed_step="restore",
                    backup_path=safety_backup,
                    rollback_attempted=True,
                    rollback_succeeded=rollback_succeeded,
                )
            ) from exc

    return RestoreResult(
        set_file=set_path,
        restored_from=backup_path,
        safety_backup_path=safety_backup,
        final_report=final_report,
    )


def parse_include_ids(include: str | None) -> set[int]:
    if not include:
        return set()
    ids: set[int] = set()
    for raw_part in include.split(","):
        part = raw_part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise CollectionError(f"Invalid --include id: {part}") from exc
    return ids


def _create_backup(set_file: Path, project_root: Path) -> Path:
    backup_dir = project_root / "Backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H%M%S.%f")
    backup_path = backup_dir / f"{set_file.stem} [ableton-inspector {timestamp}].als"
    _atomic_copy(set_file, backup_path)
    if not _files_match(set_file, backup_path):
        backup_path.unlink(missing_ok=True)
        raise CollectionError(f"Backup verification failed: {backup_path}")
    return backup_path


def _copy_selected_items(
    items: list[CollectionItem],
    copied_audio: list[tuple[Path, Path]],
    copied_asd: list[tuple[Path, Path]],
) -> None:
    for item in items:
        item.destination_path.parent.mkdir(parents=True, exist_ok=True)
        if _atomic_copy(item.source_path, item.destination_path):
            copied_audio.append((item.source_path, item.destination_path))
        if (
            item.asd_source_path
            and item.asd_destination_path
            and _atomic_copy(item.asd_source_path, item.asd_destination_path)
        ):
            copied_asd.append((item.asd_source_path, item.asd_destination_path))


def _rewrite_set_file(set_file: Path, items: list[CollectionItem]) -> int:
    root = load_als_xml(set_file)
    file_ref_elements = _audio_file_ref_elements(root)
    rewrites_by_ref_index: dict[int, CollectionItem] = {}
    for item in items:
        for reference in item.references:
            rewrites_by_ref_index[reference.ref_index] = item

    rewritten_count = 0
    for ref_index, element in file_ref_elements:
        item = rewrites_by_ref_index.get(ref_index)
        if not item:
            continue
        _set_child_value(element, "RelativePathType", "3")
        _set_child_value(element, "RelativePath", item.relative_destination)
        _set_child_value(element, "Path", str(item.destination_path))
        rewritten_count += 1

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{set_file.name}.",
        suffix=".tmp",
        dir=set_file.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with gzip.open(temporary_path, "wb") as handle:
            ElementTree.ElementTree(root).write(
                handle,
                encoding="utf-8",
                xml_declaration=True,
            )
        load_als_xml(temporary_path)
        shutil.copystat(set_file, temporary_path)
        os.replace(temporary_path, set_file)
        _fsync_directory(set_file.parent)
    finally:
        temporary_path.unlink(missing_ok=True)
    return rewritten_count


def _audio_file_ref_elements(root: ElementTree.Element) -> list[tuple[int, ElementTree.Element]]:
    audio_refs = extract_audio_file_refs(root)
    wanted_indexes = {audio_ref.index for audio_ref in audio_refs}
    elements: list[tuple[int, ElementTree.Element]] = []
    audio_index = 0
    for element in root.iter():
        if _local_name(element.tag) != "FileRef":
            continue
        if not _element_contains_supported_audio_path(element):
            continue
        audio_index += 1
        if audio_index in wanted_indexes:
            elements.append((audio_index, element))
    return elements


def _set_child_value(element: ElementTree.Element, child_name: str, value: str) -> None:
    for child in list(element):
        if _local_name(child.tag) == child_name:
            child.set("Value", value)
            child.text = None
            return
    child = ElementTree.SubElement(element, child_name)
    child.set("Value", value)


def _element_contains_supported_audio_path(element: ElementTree.Element) -> bool:
    values: list[str] = []
    for descendant in element.iter():
        values.extend(descendant.attrib.values())
        if descendant.text:
            values.append(descendant.text)
    text = " ".join(values).lower()
    return any(extension in text for extension in SUPPORTED_AUDIO_EXTENSIONS)


def _validate_apply_result(
    items: list[CollectionItem],
    final_report: SetReport,
    backup_path: Path,
    rewritten_count: int,
) -> None:
    if not backup_path.exists():
        raise CollectionError(f"Backup was not created: {backup_path}")
    if final_report.errors:
        messages = "; ".join(error.message for error in final_report.errors)
        raise CollectionError(f"Post-apply Set validation failed: {messages}")

    expected_rewrites = sum(len(item.references) for item in items)
    if rewritten_count != expected_rewrites:
        raise CollectionError(
            "Post-apply validation failed; "
            f"expected {expected_rewrites} rewritten references, got {rewritten_count}."
        )

    references_by_index = {
        reference.ref_index: reference for reference in final_report.audio_references
    }
    for item in items:
        if not item.destination_path.is_file() or not _files_match(
            item.source_path,
            item.destination_path,
        ):
            raise CollectionError(
                f"Post-apply validation failed; copied audio differs from source: {item.destination_path}"
            )
        expected_path = item.destination_path.resolve()
        for original_reference in item.references:
            final_reference = references_by_index.get(original_reference.ref_index)
            if (
                final_reference is None
                or final_reference.status != ReferenceStatus.ALREADY_LOCAL
                or final_reference.resolved_path is None
                or final_reference.resolved_path.resolve() != expected_path
            ):
                raise CollectionError(
                    "Post-apply validation failed; "
                    f"reference #{original_reference.ref_index} does not point to {expected_path}."
                )


def _existing_paths_by_portable_name(folder: Path) -> dict[str, list[Path]]:
    paths_by_name: dict[str, list[Path]] = {}
    if not folder.is_dir():
        return paths_by_name
    try:
        children = list(folder.iterdir())
    except OSError:
        return paths_by_name
    for path in children:
        paths_by_name.setdefault(path.name.casefold(), []).append(path)
    return paths_by_name


def _choose_destination(
    imported_dir: Path,
    source_path: Path,
    asd_source_path: Path | None,
    existing_by_name: dict[str, list[Path]],
    reserved_names: set[str],
) -> Path:
    portable_name = _portable_filename(source_path.name)
    portable_path = Path(portable_name)
    for number in range(1, 10000):
        name = (
            portable_name
            if number == 1
            else f"{portable_path.stem} ({number}){portable_path.suffix}"
        )
        key = name.casefold()
        if key in reserved_names:
            continue

        existing = existing_by_name.get(key, [])
        if existing:
            if (
                len(existing) == 1
                and existing[0].is_file()
                and _files_match(source_path, existing[0])
            ):
                reserved_names.add(key)
                return existing[0]
            continue

        sidecar_key = f"{name}.asd".casefold()
        sidecars = existing_by_name.get(sidecar_key, [])
        if sidecars and (
            asd_source_path is None
            or len(sidecars) != 1
            or not sidecars[0].is_file()
            or not _files_match(asd_source_path, sidecars[0])
        ):
            continue

        reserved_names.add(key)
        return imported_dir / name
    raise CollectionError(f"Could not choose a collision-free destination for {source_path.name}.")


def _portable_filename(filename: str) -> str:
    normalized = unicodedata.normalize("NFC", filename)
    cleaned = _INVALID_PORTABLE_FILENAME.sub("_", normalized).strip().rstrip(". ")
    if not cleaned:
        cleaned = "audio"

    path = Path(cleaned)
    stem = path.stem.rstrip(". ") or "audio"
    suffix = path.suffix
    if stem.upper() in _WINDOWS_RESERVED_STEMS:
        stem = f"{stem}_"
    maximum_stem_length = max(1, 180 - len(suffix))
    return f"{stem[:maximum_stem_length]}{suffix}"


def _files_match(left: Path, right: Path) -> bool:
    try:
        if left.resolve() == right.resolve():
            return True
        if left.stat().st_size != right.stat().st_size:
            return False
        with left.open("rb") as left_handle, right.open("rb") as right_handle:
            while True:
                left_chunk = left_handle.read(_COPY_BUFFER_SIZE)
                right_chunk = right_handle.read(_COPY_BUFFER_SIZE)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except OSError:
        return False


def _atomic_copy(
    source: Path,
    destination: Path,
    *,
    replace_existing: bool = False,
) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not replace_existing:
        if destination.is_file() and _files_match(source, destination):
            return False
        raise CollectionError(
            f"Destination appeared or changed after planning; refusing to overwrite: {destination}"
        )

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        destination_handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with destination_handle:
            with source.open("rb") as source_handle:
                shutil.copyfileobj(
                    source_handle,
                    destination_handle,
                    length=_COPY_BUFFER_SIZE,
                )
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        shutil.copystat(source, temporary_path)
        if destination.exists() and not replace_existing:
            if destination.is_file() and _files_match(source, destination):
                return False
            raise CollectionError(
                f"Destination appeared or changed during copy; refusing to overwrite: {destination}"
            )
        os.replace(temporary_path, destination)
        _fsync_directory(destination.parent)
        return True
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        temporary_path.unlink(missing_ok=True)


def _cleanup_copied_files(
    copied_asd: list[tuple[Path, Path]],
    copied_audio: list[tuple[Path, Path]],
) -> list[str]:
    errors: list[str] = []
    for _source, destination in reversed([*copied_asd, *copied_audio]):
        try:
            destination.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{destination}: {exc}")
    return errors


def _fsync_directory(folder: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


@contextmanager
def _exclusive_set_lock(set_file: Path) -> Iterator[None]:
    lock_root = Path(tempfile.gettempdir()) / "ableton-audio-collector-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    normalized_path = os.path.normcase(str(set_file))
    if os.name == "nt" or sys.platform == "darwin":
        normalized_path = normalized_path.casefold()
    lock_name = hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()
    lock_path = lock_root / f"{lock_name}.lock"
    lock_handle = lock_path.open("a+b")
    if lock_path.stat().st_size == 0:
        lock_handle.write(b"\0")
        lock_handle.flush()
    lock_handle.seek(0)

    try:
        _lock_handle(lock_handle)
    except OSError as exc:
        lock_handle.close()
        raise CollectionError(
            f"This Set is already being collected by another Collector Inspector process: {set_file}"
        ) from exc

    try:
        yield
    finally:
        try:
            _unlock_handle(lock_handle)
        finally:
            lock_handle.close()


def _lock_handle(handle: object) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_handle(handle: object) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
