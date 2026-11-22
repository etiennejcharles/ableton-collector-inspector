from __future__ import annotations

from ableton_audio_collector.inspector import inspect_set, scan_folder
from ableton_audio_collector.models import PluginFormat, ReferenceStatus

from .conftest import mixed_plugin_als, touch_audio, write_als


def test_inspect_classifies_local_external_and_missing_refs(tmp_path):
    project = tmp_path / "Project"
    set_file = project / "Song.als"
    local_audio = touch_audio(project / "Samples" / "Recorded" / "local.wav", b"local")
    external_audio = touch_audio(tmp_path / "Splice" / "Kicks" / "external.wav", b"external")
    touch_audio(tmp_path / "Splice" / "Kicks" / "external.wav.asd")
    write_als(
        set_file,
        f"""
        <Ableton Creator="Ableton Live 11">
          <FileRef><Path Value="Samples/Recorded/local.wav" /></FileRef>
          <FileRef><Path Value="{external_audio}" /></FileRef>
          <FileRef><Path Value="Samples/Missing/missing.aiff" /></FileRef>
        </Ableton>
        """,
    )

    report = inspect_set(set_file)

    by_path = {reference.referenced_path: reference for reference in report.audio_references}
    assert report.project_root == project
    assert report.ableton_version == "Ableton Live 11"
    assert report.total_audio_references == 3
    assert by_path["Samples/Recorded/local.wav"].status == ReferenceStatus.ALREADY_LOCAL
    assert by_path["Samples/Recorded/local.wav"].resolved_path == local_audio
    assert by_path[str(external_audio)].status == ReferenceStatus.EXTERNAL_COLLECTABLE
    assert by_path[str(external_audio)].matching_asd_exists is True
    assert by_path["Samples/Missing/missing.aiff"].status == ReferenceStatus.MISSING


def test_resolves_relative_path_outside_project_as_external(tmp_path):
    project = tmp_path / "Project"
    external_audio = touch_audio(tmp_path / "Shared" / "loop.flac")
    set_file = project / "Song.als"
    write_als(
        set_file,
        """
        <Ableton>
          <FileRef><Path Value="../Shared/loop.flac" /></FileRef>
        </Ableton>
        """,
    )

    report = inspect_set(set_file)

    assert report.audio_references[0].resolved_path == external_audio
    assert report.audio_references[0].status == ReferenceStatus.EXTERNAL_COLLECTABLE


def test_inspect_reports_plugin_counts_tracks_and_json_shape(tmp_path):
    report = inspect_set(write_als(tmp_path / "Project" / "Plugins.als", mixed_plugin_als()))

    assert report.total_plugin_instances == 5
    assert report.tracks_with_plugins_count == 4
    assert report.plugin_count(PluginFormat.AUDIO_UNIT) == 1
    assert report.plugin_count(PluginFormat.VST2) == 1
    assert report.plugin_count(PluginFormat.VST3) == 2
    assert report.plugin_count(PluginFormat.UNKNOWN) == 1
    data = report.to_dict()
    assert data["plugin_format_counts"] == {
        "audio_unit": 1,
        "unknown": 1,
        "vst2": 1,
        "vst3": 2,
    }
    assert data["plugin_references"][0]["track_name"] == "Vocals"
    assert data["plugin_references"][0]["plugin_name"] == "Auto-Tune Pro"


def test_falls_back_to_stored_absolute_path_when_relative_path_is_stale(tmp_path):
    project = tmp_path / "Project"
    external_audio = touch_audio(tmp_path / "External" / "loop.wav", b"audio")
    set_file = write_als(
        project / "Song.als",
        f"""
        <Ableton>
          <FileRef>
            <RelativePath Value="../Gone/loop.wav" />
            <Path Value="{external_audio}" />
          </FileRef>
        </Ableton>
        """,
    )

    report = inspect_set(set_file)

    assert report.audio_references[0].referenced_path == "../Gone/loop.wav"
    assert report.audio_references[0].resolved_path == external_audio
    assert report.audio_references[0].status == ReferenceStatus.EXTERNAL_COLLECTABLE


def test_resolves_windows_separators_and_case_after_project_move(tmp_path):
    project = tmp_path / "Project"
    local_audio = touch_audio(project / "Samples" / "Recorded" / "Kick.wav", b"audio")
    set_file = write_als(
        project / "Song.als",
        r"""
        <Ableton>
          <FileRef><RelativePath Value="Samples\Recorded\kick.wav" /></FileRef>
        </Ableton>
        """,
    )

    report = inspect_set(set_file)

    assert report.audio_references[0].resolved_path.samefile(local_audio)
    assert report.audio_references[0].status == ReferenceStatus.ALREADY_LOCAL


def test_scan_continues_after_unparsable_set(tmp_path):
    project = tmp_path / "Project"
    good_set = write_als(
        project / "Good.als",
        '<Ableton><FileRef><Path Value="Missing.mp3" /></FileRef></Ableton>',
    )
    bad_set = project / "Bad.als"
    bad_set.write_text("not gzip", encoding="utf-8")

    report = scan_folder(tmp_path)

    reports = {set_report.set_file: set_report for set_report in report.sets}
    assert set(reports) == {good_set.resolve(), bad_set.resolve()}
    assert reports[good_set.resolve()].missing_count == 1
    assert reports[bad_set.resolve()].errors


def test_scan_finds_uppercase_als_extensions(tmp_path):
    set_file = write_als(tmp_path / "Project" / "Song.ALS", "<Ableton />")

    report = scan_folder(tmp_path)

    assert [set_report.set_file for set_report in report.sets] == [set_file.resolve()]


def test_scan_ignores_sets_inside_backup_folder(tmp_path):
    project = tmp_path / "Project"
    active_set = write_als(
        project / "Song.als",
        '<Ableton><FileRef><Path Value="Missing.mp3" /></FileRef></Ableton>',
    )
    backup_set = write_als(
        project / "Backup" / "Song [2026-06-14 005407].als",
        '<Ableton><FileRef><Path Value="Missing.mp3" /></FileRef></Ableton>',
    )

    report = scan_folder(tmp_path)

    set_files = {set_report.set_file for set_report in report.sets}
    assert active_set.resolve() in set_files
    assert backup_set.resolve() not in set_files


def test_scan_ignores_sets_inside_backup_like_folder(tmp_path):
    project = tmp_path / "Project"
    active_set = write_als(
        project / "Song.als",
        '<Ableton><FileRef><Path Value="Missing.mp3" /></FileRef></Ableton>',
    )
    other_backup_set = write_als(
        project / "Other Backups" / "Song [2021-04-02 184657].als",
        '<Ableton><FileRef><Path Value="Missing.mp3" /></FileRef></Ableton>',
    )

    report = scan_folder(tmp_path)

    set_files = {set_report.set_file for set_report in report.sets}
    assert active_set.resolve() in set_files
    assert other_backup_set.resolve() not in set_files
