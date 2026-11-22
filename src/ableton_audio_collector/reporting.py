from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from ableton_audio_collector.models import PluginFormat, ReferenceStatus, SetReport


class JsonReport(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


def write_json_report(report: JsonReport, output_path: Path) -> None:
    output_path = output_path.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_project_markdown_report(report: SetReport) -> Path:
    output_path = report.project_root / f"{report.set_file.stem}.audio-reference-report.md"
    output_path.write_text(render_project_markdown_report(report), encoding="utf-8")
    return output_path


def render_project_markdown_report(report: SetReport) -> str:
    lines = [
        "# Collector Inspector Report",
        "",
        f"Set file: `{report.set_file}`",
        f"Project root: `{report.project_root}`",
        f"Scan date: `{report.scan_date}`",
        f"Ableton version: `{report.ableton_version or 'Unknown'}`",
        "",
        "## How This Tool Knows",
        "",
        "This read-only inspector resolves audio paths and reads track and plug-in metadata from the `.als` file.",
        "",
        "- Already saved in project: the file exists and its resolved path is inside the project root.",
        "- External, not collected: the file exists, but its resolved path is outside the project root.",
        "- Missing: the referenced file could not be resolved from the Set folder or project root.",
        "",
        "The tool does not rewrite the Set or copy audio in this phase.",
        "",
        "## Summary",
        "",
        f"- Total supported audio references: {report.total_audio_references}",
        f"- Already saved in project: {report.already_local_count}",
        f"- External, not collected: {report.external_collectable_count}",
        f"- Missing: {report.missing_count}",
        f"- Third-party plug-in instances: {report.total_plugin_instances}",
        f"- Tracks containing plug-ins: {report.tracks_with_plugins_count}",
        f"- Audio Units: {report.plugin_count(PluginFormat.AUDIO_UNIT)}",
        f"- VST2: {report.plugin_count(PluginFormat.VST2)}",
        f"- VST3: {report.plugin_count(PluginFormat.VST3)}",
        f"- Unknown plug-in formats: {report.plugin_count(PluginFormat.UNKNOWN)}",
        "",
    ]

    if report.errors:
        lines.extend(["## Errors", ""])
        for error in report.errors:
            lines.append(f"- `{error.error_type}` for `{error.path}`: {error.message}")
        lines.append("")

    lines.extend(_plugin_section(report))
    lines.extend(
        _reference_section(report, ReferenceStatus.ALREADY_LOCAL, "Already Saved In Project")
    )
    lines.extend(
        _reference_section(report, ReferenceStatus.EXTERNAL_COLLECTABLE, "External, Not Collected")
    )
    lines.extend(_reference_section(report, ReferenceStatus.MISSING, "Missing"))

    return "\n".join(lines).rstrip() + "\n"


def _plugin_section(report: SetReport) -> list[str]:
    lines = ["## Plug-ins By Track", ""]
    if not report.plugin_references:
        lines.extend(["No AU, VST2, or VST3 plug-ins found.", ""])
        return lines

    lines.extend(
        [
            "| Track | Track type | Format | Plug-in |",
            "| --- | --- | --- | --- |",
        ]
    )
    labels = {
        PluginFormat.AUDIO_UNIT: "AU",
        PluginFormat.VST2: "VST2",
        PluginFormat.VST3: "VST3",
        PluginFormat.UNKNOWN: "Unknown",
    }
    for plugin in report.plugin_references:
        lines.append(
            "| "
            f"{_cell(plugin.track_name)} | "
            f"{_cell(plugin.track_type.value)} | "
            f"{labels[plugin.plugin_format]} | "
            f"{_cell(plugin.plugin_name)} |"
        )
    lines.append("")
    return lines


def _reference_section(report: SetReport, status: ReferenceStatus, title: str) -> list[str]:
    references = [reference for reference in report.audio_references if reference.status == status]
    lines = [f"## {title}", ""]
    if not references:
        lines.extend(["None.", ""])
        return lines

    lines.extend(
        [
            "| Referenced path | Resolved path | Size | ASD |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for reference in references:
        lines.append(
            "| "
            f"{_cell(reference.referenced_path)} | "
            f"{_cell(str(reference.resolved_path) if reference.resolved_path else '')} | "
            f"{reference.file_size if reference.file_size is not None else ''} | "
            f"{'yes' if reference.matching_asd_exists else 'no'} |"
        )
    lines.append("")
    return lines


def _cell(value: str) -> str:
    return value.replace("|", "\\|") if value else "-"
