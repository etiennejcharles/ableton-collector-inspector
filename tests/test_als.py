from __future__ import annotations

from ableton_audio_collector.als import (
    extract_ableton_version,
    extract_audio_reference_paths,
    extract_plugin_refs,
    load_als_xml,
)

from .conftest import mixed_plugin_als, write_als


def test_loads_gzipped_als_and_extracts_supported_audio_refs(tmp_path):
    set_file = write_als(
        tmp_path / "Song.als",
        """
        <Ableton Creator="Ableton Live 12.0">
          <FileRef>
            <Path Value="/Users/me/Splice/Kicks/big kick.wav" />
            <Path Value="/Users/me/Movies/clip.mov" />
          </FileRef>
          <OtherNode Value="/Users/me/Desktop/ignored.wav" />
        </Ableton>
        """,
    )

    root = load_als_xml(set_file)

    assert extract_ableton_version(root) == "Ableton Live 12.0"
    assert extract_audio_reference_paths(root) == ["/Users/me/Splice/Kicks/big kick.wav"]


def test_deduplicates_audio_refs_within_file_ref(tmp_path):
    set_file = write_als(
        tmp_path / "Song.als",
        """
        <Ableton>
          <FileRef>
            <Name Value="Samples/Kick.wav" />
            <Path Value="Samples/Kick.wav" />
          </FileRef>
        </Ableton>
        """,
    )

    root = load_als_xml(set_file)

    assert extract_audio_reference_paths(root) == ["Samples/Kick.wav"]


def test_file_ref_with_relative_and_absolute_paths_counts_once(tmp_path):
    set_file = write_als(
        tmp_path / "Song.als",
        """
        <Ableton>
          <FileRef>
            <RelativePathType Value="3" />
            <RelativePath Value="Samples/Imported/YOU SO DONE.mp3" />
            <Path Value="/Users/me/Test Project/Samples/Imported/YOU SO DONE.mp3" />
            <Type Value="2" />
          </FileRef>
        </Ableton>
        """,
    )

    root = load_als_xml(set_file)

    assert extract_audio_reference_paths(root) == ["Samples/Imported/YOU SO DONE.mp3"]


def test_extracts_ogg_and_aiff_c_references(tmp_path):
    set_file = write_als(
        tmp_path / "Song.als",
        """
        <Ableton>
          <FileRef><Path Value="Samples/loop.ogg" /></FileRef>
          <FileRef><Path Value="Samples/recording.aifc" /></FileRef>
        </Ableton>
        """,
    )

    root = load_als_xml(set_file)

    assert extract_audio_reference_paths(root) == [
        "Samples/loop.ogg",
        "Samples/recording.aifc",
    ]


def test_extracts_plugin_formats_names_tracks_and_nested_rack_devices(tmp_path):
    root = load_als_xml(write_als(tmp_path / "Plugins.als", mixed_plugin_als()))

    plugins = extract_plugin_refs(root)

    assert [plugin.plugin_format for plugin in plugins] == [
        "audio_unit",
        "vst3",
        "vst2",
        "unknown",
        "vst3",
    ]
    assert [plugin.plugin_name for plugin in plugins] == [
        "Auto-Tune Pro",
        "Pro-Q 3",
        "Serum",
        "Mystery Device",
        "Limiter",
    ]
    assert [(plugin.track_name, plugin.track_type) for plugin in plugins] == [
        ("Vocals", "audio"),
        ("Vocals", "audio"),
        ("Synth", "midi"),
        ("Reverb", "return"),
        ("Main", "main"),
    ]
