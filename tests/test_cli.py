from __future__ import annotations

import json

from typer.testing import CliRunner

from ableton_audio_collector.cli import app

from .conftest import mixed_plugin_als, touch_audio, write_als

runner = CliRunner()


def test_cli_prints_version():
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output == "Collector Inspector 1.0.0\n"


def test_inspect_cli_writes_json_report(tmp_path):
    project = tmp_path / "Project"
    touch_audio(project / "Samples" / "kick.wav")
    set_file = write_als(
        project / "Song.als",
        '<Ableton><FileRef><Path Value="Samples/kick.wav" /></FileRef></Ableton>',
    )
    output = tmp_path / "report.json"

    result = runner.invoke(app, ["inspect", str(set_file), "--output", str(output)])

    assert result.exit_code == 0
    assert "Song.als: refs=1 media=1 local=1 external=0 missing=0 status=safe" in result.output
    assert "Classification rule: inside project root = already saved" in result.output
    assert "Already saved in project (1)" in result.output
    assert "ref #1: Samples/kick.wav" in result.output
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["set_file"] == str(set_file.resolve())
    assert data["already_local_count"] == 1


def test_inspect_cli_writes_project_markdown_report(tmp_path):
    project = tmp_path / "Project"
    touch_audio(project / "Samples" / "kick.wav")
    external = touch_audio(tmp_path / "External" / "snare.wav")
    set_file = write_als(
        project / "Song.als",
        f"""
        <Ableton>
          <FileRef><Path Value="Samples/kick.wav" /></FileRef>
          <FileRef><Path Value="{external}" /></FileRef>
          <FileRef><Path Value="Missing/ghost.aiff" /></FileRef>
        </Ableton>
        """,
    )

    result = runner.invoke(app, ["inspect", str(set_file), "--project-report"])

    assert result.exit_code == 0
    report_path = project / "Song.audio-reference-report.md"
    assert f"Wrote project report: {report_path}" in result.output
    report = report_path.read_text(encoding="utf-8")
    assert "## How This Tool Knows" in report
    assert "Already saved in project: the file exists" in report
    assert "## Already Saved In Project" in report
    assert "Samples/kick.wav" in report
    assert "## External, Not Collected" in report
    assert str(external) in report
    assert "## Missing" in report
    assert "Missing/ghost.aiff" in report


def test_scan_cli_writes_json_report(tmp_path):
    write_als(
        tmp_path / "Project" / "Song.als",
        '<Ableton><FileRef><Path Value="Missing.m4a" /></FileRef></Ableton>',
    )
    output = tmp_path / "scan.json"

    result = runner.invoke(app, ["scan", str(tmp_path), "--output", str(output)])

    assert result.exit_code == 0
    assert "Sets found: 1" in result.output
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["set_count"] == 1
    assert data["sets"][0]["missing_count"] == 1


def test_inspect_cli_and_markdown_report_plugin_formats_by_track(tmp_path):
    set_file = write_als(tmp_path / "Project" / "Plugins.als", mixed_plugin_als())
    output = tmp_path / "plugins.json"

    result = runner.invoke(
        app,
        ["inspect", str(set_file), "--output", str(output), "--project-report"],
    )

    assert result.exit_code == 0
    assert "plugins=5 au=1 vst2=1 vst3=2" in result.output
    assert "Plug-ins by track (5)" in result.output
    assert "AU: Auto-Tune Pro" in result.output
    assert "VST3: Pro-Q 3" in result.output
    assert "VST2: Serum" in result.output
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["tracks_with_plugins_count"] == 4
    report = (set_file.parent / "Plugins.audio-reference-report.md").read_text()
    assert "## Plug-ins By Track" in report
    assert "| Vocals | audio | AU | Auto-Tune Pro |" in report
    assert "| Synth | midi | VST2 | Serum |" in report


def test_scan_cli_returns_nonzero_for_unreadable_sets_but_writes_report(tmp_path):
    bad_set = tmp_path / "Project" / "Broken.als"
    bad_set.parent.mkdir(parents=True)
    bad_set.write_text("not gzip")
    output = tmp_path / "nested" / "reports" / "scan.json"

    result = runner.invoke(app, ["scan", str(tmp_path), "--output", str(output)])

    assert result.exit_code == 2
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["sets"][0]["errors"]


def test_scan_cli_writes_project_reports(tmp_path):
    project = tmp_path / "Project"
    set_file = write_als(
        project / "Song.als",
        '<Ableton><FileRef><Path Value="Missing.m4a" /></FileRef></Ableton>',
    )

    result = runner.invoke(app, ["scan", str(tmp_path), "--project-reports"])

    assert result.exit_code == 0
    report_path = set_file.parent / "Song.audio-reference-report.md"
    assert report_path.exists()
    assert "External, not collected: 0" in report_path.read_text(encoding="utf-8")


def test_collect_cli_dry_run_prints_numbered_external_sources(tmp_path):
    project = tmp_path / "Project"
    external = touch_audio(tmp_path / "Downloads" / "loop.wav")
    set_file = write_als(
        project / "Song.als",
        f"""
        <Ableton>
          <FileRef>
            <RelativePathType Value="1" />
            <RelativePath Value="../Downloads/loop.wav" />
            <Path Value="{external}" />
            <OriginalFileSize Value="{external.stat().st_size}" />
            <OriginalCrc Value="abc" />
          </FileRef>
        </Ableton>
        """,
    )

    result = runner.invoke(app, ["collect", str(set_file), "--dry-run"])

    assert result.exit_code == 0
    assert "Dry-run collection plan" in result.output
    assert f"1. {external}" in result.output
    assert "to: " in result.output
    assert "ref #1: ../Downloads/loop.wav -> Samples/Imported/loop.wav" in result.output


def test_restore_cli_requires_apply_and_preserves_current_set(tmp_path):
    project = tmp_path / "Project"
    set_file = write_als(project / "Song.als", '<Ableton Creator="Current" />')
    backup_file = write_als(project / "Backup" / "Song.als", '<Ableton Creator="Backup" />')
    current_bytes = set_file.read_bytes()

    dry_run = runner.invoke(app, ["restore", str(set_file), str(backup_file)])

    assert dry_run.exit_code == 0
    assert "Dry run only" in dry_run.output
    assert set_file.read_bytes() == current_bytes

    applied = runner.invoke(
        app,
        ["restore", str(set_file), str(backup_file), "--apply"],
    )

    assert applied.exit_code == 0
    assert "Restore succeeded" in applied.output
    assert set_file.read_bytes() == backup_file.read_bytes()
