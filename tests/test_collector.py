from __future__ import annotations

import gzip
from pathlib import Path
from xml.etree import ElementTree as ET

from ableton_audio_collector.collector import (
    CollectionApplyError,
    CollectionError,
    _create_backup,
    _exclusive_set_lock,
    _portable_filename,
    apply_collection,
    build_collection_plan,
    restore_set_backup,
)

from .conftest import touch_audio, write_als


def test_build_plan_groups_same_media_identity_from_multiple_external_refs(tmp_path):
    project, set_file, source_a, source_b = _write_relative_project_fixture(tmp_path)

    plan = build_collection_plan(set_file)

    assert plan.set_report.total_audio_references == 2
    assert plan.set_report.unique_media_count == 1
    assert len(plan.items) == 1
    assert plan.items[0].source_path == source_a
    assert plan.items[0].destination_path == project / "Samples" / "Imported" / "YOU SO DONE.mp3"
    assert [reference.ref_index for reference in plan.items[0].references] == [1, 2]
    assert {reference.resolved_path for reference in plan.items[0].references} == {
        source_a,
        source_b,
    }


def test_apply_collection_copies_once_and_rewrites_all_matching_refs(tmp_path):
    project, set_file, source_a, _source_b = _write_relative_project_fixture(tmp_path)

    result = apply_collection(set_file, {1})

    imported = project / "Samples" / "Imported" / "YOU SO DONE.mp3"
    imported_asd = project / "Samples" / "Imported" / "YOU SO DONE.mp3.asd"
    assert imported.read_bytes() == source_a.read_bytes()
    assert imported_asd.exists()
    assert result.backup_path.parent == project / "Backup"
    assert result.backup_path.exists()
    assert result.rewritten_reference_count == 2
    assert result.final_report.total_audio_references == 2
    assert result.final_report.unique_media_count == 1
    assert result.final_report.already_local_count == 2
    assert result.final_report.external_collectable_count == 0

    refs = _audio_file_ref_fields(set_file)
    assert refs == [
        {
            "RelativePathType": "3",
            "RelativePath": "Samples/Imported/YOU SO DONE.mp3",
            "Path": str(imported),
        },
        {
            "RelativePathType": "3",
            "RelativePath": "Samples/Imported/YOU SO DONE.mp3",
            "Path": str(imported),
        },
    ]


def test_apply_renames_same_destination_name_with_different_content(tmp_path):
    project = tmp_path / "Project"
    source_a = touch_audio(tmp_path / "A" / "kick.wav", b"one")
    source_b = touch_audio(tmp_path / "B" / "kick.wav", b"two")
    set_file = write_als(
        project / "Song.als",
        f"""
        <Ableton>
          <FileRef>
            <Path Value="{source_a}" />
            <OriginalFileSize Value="{source_a.stat().st_size}" />
            <OriginalCrc Value="111" />
          </FileRef>
          <FileRef>
            <Path Value="{source_b}" />
            <OriginalFileSize Value="{source_b.stat().st_size}" />
            <OriginalCrc Value="222" />
          </FileRef>
        </Ableton>
        """,
    )

    plan = build_collection_plan(set_file)

    assert plan.conflicts == []
    assert [item.destination_path.name for item in plan.items] == [
        "kick.wav",
        "kick (2).wav",
    ]

    result = apply_collection(set_file, {1, 2})

    assert (project / "Samples" / "Imported" / "kick.wav").read_bytes() == b"one"
    assert (project / "Samples" / "Imported" / "kick (2).wav").read_bytes() == b"two"
    assert result.rewritten_reference_count == 2


def test_apply_blocks_unknown_or_missing_selection_id(tmp_path):
    _project, set_file, _source_a, _source_b = _write_relative_project_fixture(tmp_path)

    try:
        apply_collection(set_file, {99})
    except CollectionError as exc:
        assert "Unknown collection item id" in str(exc)
    else:
        raise AssertionError("Expected CollectionError")


def test_apply_auto_rolls_back_als_when_rewrite_fails(tmp_path, monkeypatch):
    project, set_file, _source_a, _source_b = _write_relative_project_fixture(tmp_path)
    original_bytes = set_file.read_bytes()

    def failing_rewrite(path, items):
        path.write_bytes(b"corrupted")
        raise RuntimeError("forced rewrite failure")

    monkeypatch.setattr("ableton_audio_collector.collector._rewrite_set_file", failing_rewrite)

    try:
        apply_collection(set_file, {1})
    except CollectionApplyError as exc:
        assert exc.failure.failed_step == "rewrite"
        assert exc.failure.rollback_attempted is True
        assert exc.failure.rollback_succeeded is True
        assert exc.failure.backup_path is not None
        assert exc.failure.backup_path.exists()
        assert exc.failure.cleanup_attempted is True
        assert exc.failure.cleanup_succeeded is True
    else:
        raise AssertionError("Expected CollectionApplyError")

    assert set_file.read_bytes() == original_bytes
    assert not (project / "Samples" / "Imported" / "YOU SO DONE.mp3").exists()
    assert not (project / "Samples" / "Imported" / "YOU SO DONE.mp3.asd").exists()


def test_plan_does_not_reuse_same_size_destination_with_different_bytes(tmp_path):
    project = tmp_path / "Project"
    existing = touch_audio(project / "Samples" / "Imported" / "kick.wav", b"old")
    source = touch_audio(tmp_path / "External" / "kick.wav", b"new")
    set_file = write_als(
        project / "Song.als",
        f'<Ableton><FileRef><Path Value="{source}" /></FileRef></Ableton>',
    )

    plan = build_collection_plan(set_file)

    assert plan.items[0].destination_path.name == "kick (2).wav"
    apply_collection(set_file, {1})
    assert existing.read_bytes() == b"old"
    assert (existing.parent / "kick (2).wav").read_bytes() == b"new"


def test_plan_reuses_byte_identical_existing_destination(tmp_path):
    project = tmp_path / "Project"
    existing = touch_audio(project / "Samples" / "Imported" / "loop.wav", b"same")
    source = touch_audio(tmp_path / "External" / "loop.wav", b"same")
    set_file = write_als(
        project / "Song.als",
        f'<Ableton><FileRef><Path Value="{source}" /></FileRef></Ableton>',
    )

    plan = build_collection_plan(set_file)
    result = apply_collection(set_file, {1})

    assert plan.items[0].destination_path == existing
    assert result.copied_audio == []
    assert result.final_report.already_local_count == 1


def test_portable_destination_names_replace_windows_reserved_characters():
    assert _portable_filename("bad:name?.wav") == "bad_name_.wav"
    assert _portable_filename("CON.wav") == "CON_.wav"


def test_backups_are_unique_even_when_created_in_quick_succession(tmp_path):
    project = tmp_path / "Project"
    set_file = write_als(project / "Song.als", "<Ableton />")

    first = _create_backup(set_file, project)
    second = _create_backup(set_file, project)

    assert first != second
    assert first.read_bytes() == set_file.read_bytes()
    assert second.read_bytes() == set_file.read_bytes()


def test_apply_refuses_a_second_collector_for_the_same_set(tmp_path):
    _project, set_file, _source_a, _source_b = _write_relative_project_fixture(tmp_path)

    with _exclusive_set_lock(set_file.resolve()):
        try:
            apply_collection(set_file, {1})
        except CollectionError as exc:
            assert "already being collected" in str(exc)
        else:
            raise AssertionError("Expected CollectionError")


def test_restore_replaces_set_and_preserves_a_safety_backup(tmp_path):
    project, set_file, _source_a, _source_b = _write_relative_project_fixture(tmp_path)
    original_bytes = set_file.read_bytes()
    collection = apply_collection(set_file, {1})
    collected_bytes = set_file.read_bytes()

    result = restore_set_backup(set_file, collection.backup_path)

    assert set_file.read_bytes() == original_bytes
    assert result.restored_from == collection.backup_path
    assert result.safety_backup_path.read_bytes() == collected_bytes
    assert result.final_report.external_collectable_count == 2


def _write_relative_project_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    project = tmp_path / "Project-With-Relative Project"
    source_a = touch_audio(tmp_path / "Downloads" / "YOU SO DONE.mp3", b"same audio")
    source_b = touch_audio(tmp_path / "Desktop" / "YOU SO DONE.mp3", b"same audio")
    touch_audio(Path(f"{source_a}.asd"), b"analysis")
    size = source_a.stat().st_size
    set_file = write_als(
        project / "Project-With-Relative.als",
        f"""
        <Ableton Creator="Ableton Live 12.4.2">
          <FileRef>
            <RelativePathType Value="1" />
            <RelativePath Value="../Downloads/YOU SO DONE.mp3" />
            <Path Value="{source_a}" />
            <Type Value="2" />
            <OriginalFileSize Value="{size}" />
            <OriginalCrc Value="21161" />
          </FileRef>
          <FileRef>
            <RelativePathType Value="1" />
            <RelativePath Value="../Desktop/YOU SO DONE.mp3" />
            <Path Value="{source_b}" />
            <Type Value="2" />
            <OriginalFileSize Value="{size}" />
            <OriginalCrc Value="21161" />
          </FileRef>
        </Ableton>
        """,
    )
    return project, set_file, source_a, source_b


def _audio_file_ref_fields(set_file: Path) -> list[dict[str, str]]:
    root = ET.fromstring(gzip.open(set_file, "rb").read())
    refs: list[dict[str, str]] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "FileRef":
            continue
        refs.append(
            {
                child.tag.rsplit("}", 1)[-1]: child.attrib["Value"]
                for child in list(element)
                if child.tag.rsplit("}", 1)[-1] in {"RelativePathType", "RelativePath", "Path"}
            }
        )
    return refs
