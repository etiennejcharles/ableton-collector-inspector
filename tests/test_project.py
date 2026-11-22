from __future__ import annotations

from ableton_audio_collector.project import infer_project_root, is_backup_set_file


def test_infers_nearest_project_marker(tmp_path):
    project = tmp_path / "Album Project"
    nested = project / "Sets" / "Drafts"
    (project / "Samples").mkdir(parents=True)
    set_file = nested / "Song.als"
    set_file.parent.mkdir(parents=True)
    set_file.touch()

    assert infer_project_root(set_file) == project


def test_falls_back_to_set_folder_without_marker(tmp_path):
    set_file = tmp_path / "Loose Set" / "Song.als"
    set_file.parent.mkdir()
    set_file.touch()

    assert infer_project_root(set_file) == set_file.parent


def test_backup_detection_does_not_reject_unrelated_parent_names(tmp_path):
    set_file = tmp_path / "backupuser" / "Active Project" / "Song.als"
    set_file.parent.mkdir(parents=True)
    set_file.touch()

    assert is_backup_set_file(set_file) is False
