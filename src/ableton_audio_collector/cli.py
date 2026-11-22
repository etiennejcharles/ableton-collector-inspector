from __future__ import annotations

from pathlib import Path

import typer

from ableton_audio_collector import __version__
from ableton_audio_collector.collector import (
    CollectionApplyError,
    CollectionError,
    CollectionPlan,
    CollectionResult,
    apply_collection,
    build_collection_plan,
    parse_include_ids,
    restore_set_backup,
)
from ableton_audio_collector.inspector import inspect_set, scan_folder
from ableton_audio_collector.models import PluginFormat, ReferenceStatus, ScanReport, SetReport
from ableton_audio_collector.reporting import write_json_report, write_project_markdown_report
from ableton_audio_collector.tui import run_review

app = typer.Typer(
    help="Inspect plug-ins and audio dependencies in Ableton Live Sets and collect external audio.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"Collector Inspector {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the application version and exit.",
    ),
) -> None:
    """Inspect VSTs, other plug-ins, and audio dependencies in Ableton Live Sets."""


@app.command()
def scan(
    folder: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True, readable=True),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write a JSON scan report."),
    project_reports: bool = typer.Option(
        False,
        "--project-reports",
        help="Write a Markdown report inside each inspected project root.",
    ),
) -> None:
    """Scan a folder recursively for Ableton Live Set files."""
    report = scan_folder(folder)
    _print_scan_summary(report)
    if output:
        write_json_report(report, output)
        typer.echo(f"Wrote JSON report: {output}")
    if project_reports:
        for set_report in report.sets:
            report_path = write_project_markdown_report(set_report)
            typer.echo(f"Wrote project report: {report_path}")
    if any(set_report.errors for set_report in report.sets):
        raise typer.Exit(2)


@app.command()
def inspect(
    set_file: Path = typer.Argument(
        ..., exists=True, file_okay=True, dir_okay=False, readable=True
    ),
    project_root: Path | None = typer.Option(
        None, "--project-root", help="Override inferred project root."
    ),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write a JSON Set report."),
    project_report: bool = typer.Option(
        False,
        "--project-report",
        help="Write a Markdown report inside the inferred project root.",
    ),
) -> None:
    """Inspect one Ableton Live Set file."""
    report = inspect_set(set_file, project_root=project_root)
    _print_set_summary(report)
    _print_set_details(report)
    if output:
        write_json_report(report, output)
        typer.echo(f"Wrote JSON report: {output}")
    if project_report:
        report_path = write_project_markdown_report(report)
        typer.echo(f"Wrote project report: {report_path}")
    if report.errors:
        raise typer.Exit(2)


@app.command()
def collect(
    set_file: Path = typer.Argument(
        ..., exists=True, file_okay=True, dir_okay=False, readable=True
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the collection plan without changing files."
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the selected collection plan."),
    include: str | None = typer.Option(
        None, "--include", help="Comma-separated dry-run item IDs to collect."
    ),
    all: bool = typer.Option(False, "--all", help="Collect all collectable external source files."),
    project_root: Path | None = typer.Option(
        None, "--project-root", help="Override inferred project root."
    ),
) -> None:
    """Collect selected external audio files into Samples/Imported."""
    if dry_run and apply:
        typer.echo("Choose either --dry-run or --apply, not both.", err=True)
        raise typer.Exit(1)
    if not dry_run and not apply:
        dry_run = True

    try:
        plan = build_collection_plan(set_file, project_root=project_root)
        _print_collection_plan(plan)
        if plan.conflicts:
            raise typer.Exit(2)
        if dry_run:
            return

        if all:
            selected_ids = {item.id for item in plan.items}
        else:
            selected_ids = parse_include_ids(include)
        if not selected_ids:
            typer.echo("Apply requires --all or --include 1,3.", err=True)
            raise typer.Exit(1)

        result = apply_collection(set_file, selected_ids, project_root=project_root)
        _print_collection_result(result)
    except CollectionError as exc:
        if hasattr(exc, "failure"):
            failure = exc.failure
            rollback = "not attempted"
            if failure.rollback_attempted:
                rollback = "succeeded" if failure.rollback_succeeded else "failed"
            typer.echo("Collection failed", err=True)
            typer.echo(f"Failed step: {failure.failed_step}", err=True)
            typer.echo(f"Error: {failure.message}", err=True)
            typer.echo(f"Backup: {failure.backup_path}", err=True)
            typer.echo(f"Rollback: {rollback}", err=True)
            typer.echo(f"Copied audio before failure: {len(failure.copied_audio)}", err=True)
            typer.echo(f"Copied ASD before failure: {len(failure.copied_asd)}", err=True)
            if failure.cleanup_attempted:
                cleanup = "succeeded" if failure.cleanup_succeeded else "incomplete"
                typer.echo(f"Copied-file cleanup: {cleanup}", err=True)
                for cleanup_error in failure.cleanup_errors:
                    typer.echo(f"  cleanup error: {cleanup_error}", err=True)
        else:
            typer.echo(f"Collection failed: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command()
def restore(
    set_file: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Active Ableton Live Set to restore.",
    ),
    backup_file: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Backup .als file to restore from.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Perform the restore. Without this flag, only show the plan.",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Override the inferred project root.",
    ),
) -> None:
    """Safely restore an active Set from a backup, preserving the current Set first."""
    typer.echo(f"Active Set: {set_file.resolve()}")
    typer.echo(f"Restore from: {backup_file.resolve()}")
    if not apply:
        typer.echo("Dry run only. Re-run with --apply to restore this backup.")
        return

    try:
        result = restore_set_backup(
            set_file,
            backup_file,
            project_root=project_root,
        )
    except CollectionApplyError as exc:
        failure = exc.failure
        rollback = "succeeded" if failure.rollback_succeeded else "failed"
        typer.echo(f"Restore failed: {failure.message}", err=True)
        typer.echo(f"Safety backup: {failure.backup_path}", err=True)
        typer.echo(f"Rollback: {rollback}", err=True)
        raise typer.Exit(1) from exc
    except CollectionError as exc:
        typer.echo(f"Restore failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo("Restore succeeded")
    typer.echo(f"Safety backup of replaced Set: {result.safety_backup_path}")
    typer.echo(
        "Restored audit: "
        f"refs={result.final_report.total_audio_references} "
        f"local={result.final_report.already_local_count} "
        f"external={result.final_report.external_collectable_count} "
        f"missing={result.final_report.missing_count}"
    )


@app.command()
def review(
    folder: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True, readable=True),
) -> None:
    """Open an interactive batch review TUI for selecting Sets to collect."""
    run_review(folder)


def _print_scan_summary(report: ScanReport) -> None:
    typer.echo(f"Scanned: {report.scan_folder}")
    typer.echo(f"Sets found: {len(report.sets)}")
    for set_report in report.sets:
        _print_set_summary(set_report, prefix="- ")


def _print_set_summary(report: SetReport, prefix: str = "") -> None:
    status = "error" if report.errors else _status_label(report)
    typer.echo(
        f"{prefix}{report.set_file.name}: "
        f"refs={report.total_audio_references} "
        f"media={report.unique_media_count} "
        f"local={report.already_local_count} "
        f"external={report.external_collectable_count} "
        f"missing={report.missing_count} "
        f"status={status} "
        f"plugins={report.total_plugin_instances} "
        f"au={report.plugin_count(PluginFormat.AUDIO_UNIT)} "
        f"vst2={report.plugin_count(PluginFormat.VST2)} "
        f"vst3={report.plugin_count(PluginFormat.VST3)}"
    )
    if report.errors and not prefix:
        for error in report.errors:
            typer.echo(f"  error: {error.error_type}: {error.message}")


def _print_set_details(report: SetReport) -> None:
    typer.echo(f"Project root: {report.project_root}")
    typer.echo(
        "Classification rule: inside project root = already saved; outside project root = external, not collected."
    )
    typer.echo("")
    _print_plugin_details(report)
    _print_reference_group(report, ReferenceStatus.ALREADY_LOCAL, "Already saved in project")
    _print_reference_group(report, ReferenceStatus.EXTERNAL_COLLECTABLE, "External, not collected")
    _print_reference_group(report, ReferenceStatus.MISSING, "Missing")


def _print_plugin_details(report: SetReport) -> None:
    typer.echo(f"Plug-ins by track ({report.total_plugin_instances})")
    if not report.plugin_references:
        typer.echo("  None")
        typer.echo("")
        return

    labels = {
        PluginFormat.AUDIO_UNIT: "AU",
        PluginFormat.VST2: "VST2",
        PluginFormat.VST3: "VST3",
        PluginFormat.UNKNOWN: "UNKNOWN",
    }
    current_track: tuple[int, str | None, str] | None = None
    for plugin in report.plugin_references:
        track = (plugin.track_index, plugin.track_id, plugin.track_name)
        if track != current_track:
            typer.echo(f"  {plugin.track_name} [{plugin.track_type.value}]")
            current_track = track
        typer.echo(f"    - {labels[plugin.plugin_format]}: {plugin.plugin_name}")
    typer.echo("")


def _print_reference_group(report: SetReport, status: ReferenceStatus, title: str) -> None:
    references = [reference for reference in report.audio_references if reference.status == status]
    typer.echo(f"{title} ({len(references)})")
    if not references:
        typer.echo("  None")
        typer.echo("")
        return

    for reference in references:
        typer.echo(f"  - ref #{reference.ref_index}: {reference.referenced_path}")
        if reference.resolved_path:
            typer.echo(f"    resolved:   {reference.resolved_path}")
        else:
            typer.echo("    resolved:   not found")
        if reference.file_size is not None:
            typer.echo(f"    size:       {reference.file_size} bytes")
        typer.echo(f"    asd:        {'yes' if reference.matching_asd_exists else 'no'}")
    typer.echo("")


def _print_collection_plan(plan: CollectionPlan) -> None:
    _print_set_summary(plan.set_report)
    typer.echo(f"Destination: {plan.set_report.project_root / 'Samples' / 'Imported'}")
    typer.echo("")
    if plan.conflicts:
        typer.echo("Conflicts")
        for conflict in plan.conflicts:
            typer.echo(f"  - {conflict}")
        typer.echo("")
    if not plan.items:
        typer.echo("No external audio files to collect.")
        return

    typer.echo("Dry-run collection plan")
    for item in plan.items:
        typer.echo(f"{item.id}. {item.source_path}")
        typer.echo(f"   to: {item.destination_path}")
        typer.echo(f"   asd: {'yes' if item.asd_source_path else 'no'}")
        typer.echo("   rewrites:")
        for reference in item.references:
            typer.echo(
                f"     - ref #{reference.ref_index}: {reference.referenced_path} -> {item.relative_destination}"
            )
    typer.echo("")
    typer.echo("Apply with --apply --all or --apply --include 1,3")


def _print_collection_result(result: CollectionResult) -> None:
    typer.echo("")
    typer.echo("Collection succeeded")
    typer.echo(f"Backup: {result.backup_path}")
    typer.echo(f"Copied audio files: {len(result.copied_audio)}")
    for source, destination in result.copied_audio:
        typer.echo(f"  - {source} -> {destination}")
    typer.echo(f"Copied ASD files: {len(result.copied_asd)}")
    for source, destination in result.copied_asd:
        typer.echo(f"  - {source} -> {destination}")
    typer.echo(f"Rewritten ALS references: {result.rewritten_reference_count}")
    typer.echo(
        "Final audit: "
        f"refs={result.final_report.total_audio_references} "
        f"media={result.final_report.unique_media_count} "
        f"local={result.final_report.already_local_count} "
        f"external={result.final_report.external_collectable_count} "
        f"missing={result.final_report.missing_count} "
        f"status={_status_label(result.final_report)}"
    )


def _status_label(report: SetReport) -> str:
    if report.missing_count:
        return "missing_files"
    if report.external_collectable_count:
        return "needs_collection"
    return "safe"


if __name__ == "__main__":
    app()
