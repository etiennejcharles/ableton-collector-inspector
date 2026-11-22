from __future__ import annotations

import gzip
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

SUPPORTED_AUDIO_EXTENSIONS = (
    ".wav",
    ".aiff",
    ".aif",
    ".aifc",
    ".flac",
    ".ogg",
    ".mp3",
    ".m4a",
)
_AUDIO_PATH_RE = re.compile(
    r"(?P<path>[^\"'<>]*?(?:\.wav|\.aiff|\.aif|\.aifc|\.flac|\.ogg|\.mp3|\.m4a))"
    r"(?=$|[?#\"'<>])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AlsAudioFileRef:
    index: int
    referenced_path: str
    relative_path: str | None
    path: str | None
    relative_path_type: str | None
    original_file_size: int | None
    original_crc: str | None


@dataclass(frozen=True)
class AlsPluginRef:
    track_index: int
    track_id: str | None
    track_name: str
    track_type: str
    plugin_index: int
    plugin_name: str
    plugin_format: str


def load_als_xml(set_file: Path) -> ElementTree.Element:
    with gzip.open(set_file, "rb") as handle:
        return ElementTree.parse(handle).getroot()


def extract_ableton_version(root: ElementTree.Element) -> str | None:
    version = root.attrib.get("Creator") or root.attrib.get("MinorVersion")
    if version:
        return version

    for element in root.iter():
        local_name = _local_name(element.tag).lower()
        if "version" not in local_name and "creator" not in local_name:
            continue
        for value in (*element.attrib.values(), element.text or ""):
            if value and value.strip():
                return value.strip()
    return None


def extract_audio_reference_paths(root: ElementTree.Element) -> list[str]:
    return [file_ref.referenced_path for file_ref in extract_audio_file_refs(root)]


def extract_audio_file_refs(root: ElementTree.Element) -> list[AlsAudioFileRef]:
    references: list[AlsAudioFileRef] = []

    for element in root.iter():
        if _local_name(element.tag) != "FileRef":
            continue
        direct_values = _direct_child_values_by_name(element)
        path = _extract_best_file_ref_path(direct_values, element)
        if not path:
            continue
        references.append(
            AlsAudioFileRef(
                index=len(references) + 1,
                referenced_path=path,
                relative_path=_first(direct_values.get("RelativePath")),
                path=_first(direct_values.get("Path")),
                relative_path_type=_first(direct_values.get("RelativePathType")),
                original_file_size=_to_int(_first(direct_values.get("OriginalFileSize"))),
                original_crc=_first(direct_values.get("OriginalCrc")),
            )
        )

    return references


def extract_plugin_refs(root: ElementTree.Element) -> list[AlsPluginRef]:
    live_set = _direct_child(root, "LiveSet")
    if live_set is None:
        return []

    track_elements: list[ElementTree.Element] = []
    tracks = _direct_child(live_set, "Tracks")
    if tracks is not None:
        track_elements.extend(
            element
            for element in list(tracks)
            if _local_name(element.tag) in {"AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack"}
        )
    for main_name in ("MainTrack", "MasterTrack"):
        main_track = _direct_child(live_set, main_name)
        if main_track is not None:
            track_elements.append(main_track)
            break

    references: list[AlsPluginRef] = []
    for track_index, track in enumerate(track_elements, start=1):
        track_tag = _local_name(track.tag)
        track_type = _track_type(track_tag)
        track_id = track.attrib.get("Id")
        track_name = _track_name(track, track_tag, track_index)
        plugin_index = 0
        for device in track.iter():
            device_tag = _local_name(device.tag)
            if device_tag not in {"PluginDevice", "AuPluginDevice"}:
                continue
            plugin_index += 1
            info = _plugin_info(device)
            references.append(
                AlsPluginRef(
                    track_index=track_index,
                    track_id=track_id,
                    track_name=track_name,
                    track_type=track_type,
                    plugin_index=plugin_index,
                    plugin_name=_plugin_name(device, info, plugin_index),
                    plugin_format=_plugin_format(device_tag, info),
                )
            )
    return references


def _extract_best_file_ref_path(
    direct_values: dict[str, list[str]],
    element: ElementTree.Element,
) -> str | None:
    preferred_values = [
        *direct_values.get("RelativePath", []),
        *direct_values.get("Path", []),
        *_iter_values(element),
    ]

    for raw_value in preferred_values:
        for match in _AUDIO_PATH_RE.finditer(raw_value):
            path = _clean_path(match.group("path"))
            if path and _has_supported_extension(path):
                return path
    return None


def _direct_child_values_by_name(element: ElementTree.Element) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for child in list(element):
        child_values = list(child.attrib.values())
        if child.text:
            child_values.append(child.text)
        values.setdefault(_local_name(child.tag), []).extend(child_values)
    return values


def _direct_child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next(
        (child for child in list(element) if _local_name(child.tag) == name),
        None,
    )


def _element_value(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    value = element.attrib.get("Value")
    if value and value.strip():
        return value.strip()
    if element.text and element.text.strip():
        return element.text.strip()
    return None


def _track_type(track_tag: str) -> str:
    return {
        "AudioTrack": "audio",
        "MidiTrack": "midi",
        "GroupTrack": "group",
        "ReturnTrack": "return",
        "MainTrack": "main",
        "MasterTrack": "main",
    }.get(track_tag, "audio")


def _track_name(track: ElementTree.Element, track_tag: str, track_index: int) -> str:
    name = _direct_child(track, "Name")
    for field_name in ("UserName", "EffectiveName"):
        value = _element_value(_direct_child(name, field_name)) if name is not None else None
        if value:
            return value
    if track_tag in {"MainTrack", "MasterTrack"}:
        return "Main"
    label = {
        "AudioTrack": "Audio Track",
        "MidiTrack": "MIDI Track",
        "GroupTrack": "Group Track",
        "ReturnTrack": "Return Track",
    }.get(track_tag, "Track")
    return f"{label} {track_index}"


def _plugin_info(device: ElementTree.Element) -> ElementTree.Element | None:
    plugin_desc = _direct_child(device, "PluginDesc")
    if plugin_desc is None:
        return None
    return next(
        (
            child
            for child in list(plugin_desc)
            if _local_name(child.tag) in {"AuPluginInfo", "VstPluginInfo", "Vst3PluginInfo"}
        ),
        None,
    )


def _plugin_format(device_tag: str, info: ElementTree.Element | None) -> str:
    info_tag = _local_name(info.tag) if info is not None else ""
    if info_tag == "Vst3PluginInfo":
        return "vst3"
    if info_tag == "VstPluginInfo":
        return "vst2"
    if info_tag == "AuPluginInfo" or device_tag == "AuPluginDevice":
        return "audio_unit"
    return "unknown"


def _plugin_name(
    device: ElementTree.Element,
    info: ElementTree.Element | None,
    plugin_index: int,
) -> str:
    if info is not None:
        info_tag = _local_name(info.tag)
        name_field = "PlugName" if info_tag == "VstPluginInfo" else "Name"
        value = _element_value(_direct_child(info, name_field))
        if value:
            return value
    for field_name in ("UserName", "Name"):
        value = _element_value(_direct_child(device, field_name))
        if value:
            return value
    return f"Unknown plug-in {plugin_index}"


def _iter_values(element: ElementTree.Element) -> list[str]:
    values: list[str] = []
    for descendant in element.iter():
        values.extend(descendant.attrib.values())
        if descendant.text:
            values.append(descendant.text)
        if descendant.tail:
            values.append(descendant.tail)
    return values


def _clean_path(value: str) -> str:
    return unquote(value.strip())


def _has_supported_extension(path: str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS


def _first(values: list[str] | None) -> str | None:
    if not values:
        return None
    return values[0]


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
