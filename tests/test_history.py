from __future__ import annotations

import json
import os

import pytest

from ableton_audio_collector.history import HistoryStore, HistoryStoreError
from ableton_audio_collector.inspector import inspect_set

from .conftest import mixed_plugin_als, touch_audio, write_als


def test_history_persists_plugins_files_and_multiple_scan_snapshots(tmp_path):
    audio_file = touch_audio(tmp_path / "External" / "lead.wav", b"audio data")
    set_file = write_als(
        tmp_path / "Project" / "Song.als",
        mixed_plugin_als().replace(
            "</Ableton>",
            f'<FileRef><Path Value="{audio_file}" /></FileRef></Ableton>',
        ),
    )
    store_path = tmp_path / "data" / "scan-history.json"
    store = HistoryStore(store_path)
    first_report = inspect_set(set_file, scan_date="2026-07-31T10:00:00Z")
    assert first_report.set_modified_ns == set_file.stat().st_mtime_ns
    assert first_report.set_size == set_file.stat().st_size

    store.record_reports([first_report], [tmp_path / "Project"])
    first_entry = HistoryStore(store_path).load()[0]

    assert first_entry.set_file == str(set_file.resolve())
    assert first_entry.source_paths == (str(tmp_path / "Project"),)
    assert [plugin.plugin_name for plugin in first_entry.plugins] == [
        "Auto-Tune Pro",
        "Pro-Q 3",
        "Serum",
        "Mystery Device",
        "Limiter",
    ]
    assert first_entry.plugins[0].track_name == "Vocals"
    assert first_entry.plugins[0].plugin_format == "audio_unit"
    assert len(first_entry.audio_files) == 1
    assert first_entry.audio_files[0].referenced_path == str(audio_file)
    assert first_entry.audio_files[0].resolved_path == str(audio_file.resolve())
    assert first_entry.audio_files[0].status == "external_collectable"
    assert first_entry.freshness().state == "current"

    first_mtime_ns = set_file.stat().st_mtime_ns
    os.utime(set_file, ns=(first_mtime_ns + 2_000_000_000,) * 2)
    assert first_entry.freshness().state == "changed"
    assert first_entry.freshness().warning is True

    second_report = inspect_set(set_file, scan_date="2026-07-31T11:00:00Z")
    store.record_reports([second_report], [set_file])
    entries = HistoryStore(store_path).load()

    assert len(entries) == 2
    assert entries[0].scanned_at == "2026-07-31T11:00:00Z"
    assert entries[0].freshness().state == "current"
    assert entries[1].freshness().state == "changed"
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert len(payload["entries"]) == 2


def test_history_warns_when_the_scanned_set_is_missing(tmp_path):
    set_file = write_als(tmp_path / "Project" / "Missing Later.als", "<Ableton />")
    store = HistoryStore(tmp_path / "history.json")
    store.record_reports([inspect_set(set_file)])
    entry = store.load()[0]

    set_file.unlink()

    freshness = entry.freshness()
    assert freshness.state == "missing"
    assert freshness.warning is True
    assert "no longer" in freshness.message


def test_history_refuses_to_overwrite_corrupt_data(tmp_path):
    history_path = tmp_path / "history.json"
    history_path.write_text("not json", encoding="utf-8")
    set_file = write_als(tmp_path / "Project" / "Song.als", "<Ableton />")
    store = HistoryStore(history_path)

    with pytest.raises(HistoryStoreError, match="Could not read"):
        store.load()
    with pytest.raises(HistoryStoreError, match="Could not read"):
        store.record_reports([inspect_set(set_file)])

    assert history_path.read_text(encoding="utf-8") == "not json"
