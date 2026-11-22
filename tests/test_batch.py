from __future__ import annotations

from ableton_audio_collector.batch import build_review_batch, build_review_rows

from .conftest import touch_audio, write_als


def test_review_rows_mark_collectable_and_safe_sets(tmp_path):
    safe_project = tmp_path / "Safe Project"
    touch_audio(safe_project / "Samples" / "Imported" / "local.wav")
    safe_set = write_als(
        safe_project / "Safe.als",
        '<Ableton><FileRef><RelativePath Value="Samples/Imported/local.wav" /></FileRef></Ableton>',
    )

    risky_project = tmp_path / "Risky Project"
    external = touch_audio(tmp_path / "External" / "loop.wav")
    risky_set = write_als(
        risky_project / "Risky.als",
        f'<Ableton><FileRef><Path Value="{external}" /></FileRef></Ableton>',
    )

    rows = build_review_rows(tmp_path)

    by_set = {row.set_file: row for row in rows}
    assert by_set[safe_set.resolve()].status == "safe"
    assert by_set[safe_set.resolve()].selectable is False
    assert by_set[risky_set.resolve()].status == "collectable"
    assert by_set[risky_set.resolve()].selectable is True


def test_review_rows_do_not_include_backup_sets(tmp_path):
    project = tmp_path / "Project"
    active = write_als(
        project / "Song.als",
        '<Ableton><FileRef><Path Value="Missing.mp3" /></FileRef></Ableton>',
    )
    backup = write_als(
        project / "Backup" / "Song [ableton-inspector 2026-06-14 005407].als",
        '<Ableton><FileRef><Path Value="Missing.mp3" /></FileRef></Ableton>',
    )

    rows = build_review_rows(tmp_path)

    set_files = {row.set_file for row in rows}
    assert active.resolve() in set_files
    assert backup.resolve() not in set_files


def test_review_rows_do_not_include_other_backups_sets(tmp_path):
    project = tmp_path / "Project"
    active = write_als(
        project / "Song.als",
        '<Ableton><FileRef><Path Value="Missing.mp3" /></FileRef></Ableton>',
    )
    backup = write_als(
        project / "Other Backups" / "Song [2022-02-25 133125].als",
        '<Ableton><FileRef><Path Value="Missing.mp3" /></FileRef></Ableton>',
    )

    rows = build_review_rows(tmp_path)

    set_files = {row.set_file for row in rows}
    assert active.resolve() in set_files
    assert backup.resolve() not in set_files


def test_review_batch_accepts_mixed_folders_and_explicit_sets_without_duplicates(tmp_path):
    project = tmp_path / "Project"
    first = write_als(
        project / "First.als",
        '<Ableton><FileRef><Path Value="Missing.wav" /></FileRef></Ableton>',
    )
    second = write_als(
        project / "Nested" / "Second.als",
        '<Ableton><FileRef><Path Value="Missing.aiff" /></FileRef></Ableton>',
    )

    batch = build_review_batch([project, first, second, project])

    assert batch.sources == (project.resolve(), first.resolve(), second.resolve())
    assert [row.set_file for row in batch.rows] == [first.resolve(), second.resolve()]
    assert batch.issues == []


def test_review_batch_reports_invalid_missing_and_backup_sources(tmp_path):
    project = tmp_path / "Project"
    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("not a Set")
    backup = write_als(
        project / "Backup" / "Song [2026-01-01].als",
        "<Ableton />",
    )
    missing = tmp_path / "Gone.als"

    batch = build_review_batch([unsupported, backup, missing])

    assert batch.rows == []
    assert [issue.path for issue in batch.issues] == [
        unsupported.resolve(),
        backup.resolve(),
        missing.resolve(),
    ]
    assert "Only folders" in batch.issues[0].message
    assert "Backup Sets" in batch.issues[1].message
    assert "no longer exists" in batch.issues[2].message


def test_review_batch_folder_scan_excludes_nested_backups(tmp_path):
    project = tmp_path / "Project"
    active = write_als(project / "Song.als", "<Ableton />")
    write_als(project / "Backup" / "Song [old].als", "<Ableton />")

    batch = build_review_batch([project])

    assert [row.set_file for row in batch.rows] == [active.resolve()]
