from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, LoadingIndicator, ProgressBar, Static

from ableton_audio_collector.batch import ReviewRow, build_review_rows, selected_apply_ids
from ableton_audio_collector.collector import (
    CollectionApplyError,
    CollectionError,
    apply_collection,
)


class ReviewFilter(StrEnum):
    ALL = "all"
    READY = "ready"
    SAFE = "safe"
    BLOCKED = "blocked"
    ERRORS = "errors"


FILTER_ORDER = (
    ReviewFilter.ALL,
    ReviewFilter.READY,
    ReviewFilter.SAFE,
    ReviewFilter.BLOCKED,
    ReviewFilter.ERRORS,
)

STATUS_LABELS = {
    "collectable": "Ready",
    "safe": "Safe",
    "blocked_missing": "Missing files",
    "blocked": "Blocked",
    "error": "Error",
}

STATUS_STYLES = {
    "collectable": "bold #75D9A6",
    "safe": "bold #7FB5FF",
    "blocked_missing": "bold #F3BE63",
    "blocked": "bold #F3BE63",
    "error": "bold #FF6B63",
}


@dataclass(frozen=True)
class ApplyOutcome:
    row: ReviewRow
    succeeded: bool
    message: str


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("q", "cancel", "Cancel", show=False),
    ]

    def __init__(self, selected_rows: list[ReviewRow]) -> None:
        super().__init__()
        self.selected_rows = selected_rows

    def compose(self) -> ComposeResult:
        set_count = len(self.selected_rows)
        yield Vertical(
            Static("COLLECT AUDIO", classes="eyebrow"),
            Static(
                f"Collect external audio for {set_count} Set{'s' if set_count != 1 else ''}?",
                id="confirm-title",
            ),
            VerticalScroll(
                Static(self._summary(), id="confirm-summary"),
                id="confirm-summary-scroll",
            ),
            Static(
                "Each Set is backed up first. Collection runs sequentially and stops if a Set fails.",
                id="confirm-note",
            ),
            Horizontal(
                Button("Cancel", id="cancel"),
                Button("Collect selected", id="apply", variant="success"),
                id="confirm-buttons",
            ),
            id="confirm-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "apply")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def _summary(self) -> Text:
        item_count = sum(len(row.plan.items) for row in self.selected_rows if row.plan)
        reference_count = sum(
            len(item.references)
            for row in self.selected_rows
            if row.plan
            for item in row.plan.items
        )
        text = Text()
        text.append(f"{len(self.selected_rows)} Sets", style="bold #F2F2F2")
        text.append("  ·  ", style="#666666")
        text.append(f"{item_count} media files", style="bold #F2F2F2")
        text.append("  ·  ", style="#666666")
        text.append(f"{reference_count} references", style="bold #F2F2F2")
        text.append("\n\n")
        for row in self.selected_rows:
            count = len(row.plan.items) if row.plan else 0
            text.append("● ", style="#75D9A6")
            text.append(row.set_file.name, style="bold #E7E7E7")
            text.append("  ·  ", style="#666666")
            text.append(
                f"{count} file{'s' if count != 1 else ''}\n",
                style="#929292",
            )
        return text


class CollectionScreen(Screen[list[ApplyOutcome]]):
    BINDINGS = [
        Binding("enter", "done", "Back", show=False),
        Binding("escape", "done", "Back", show=False),
        Binding("q", "done", "Back", show=False),
    ]

    def __init__(self, selected_rows: list[ReviewRow]) -> None:
        super().__init__()
        self.selected_rows = selected_rows
        self.outcomes: list[ApplyOutcome] = []
        self.complete = False
        self._apply_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Static("ABLETON  COLLECTOR", classes="collection-brand"),
            Static("COLLECTION IN PROGRESS", id="collection-state"),
            id="collection-topbar",
        )
        yield Vertical(
            Static("COLLECTING AUDIO", classes="eyebrow"),
            Static("Preparing collection…", id="collection-title"),
            Static("", id="collection-current"),
            ProgressBar(
                total=len(self.selected_rows),
                show_percentage=True,
                show_eta=False,
                id="collection-progress",
            ),
            VerticalScroll(
                Vertical(id="outcome-cards"),
                id="collection-outcome-scroll",
            ),
            Horizontal(
                Static(
                    "Collection cannot be cancelled after file changes begin.", id="collection-note"
                ),
                Button("Return to review", id="collection-done", disabled=True),
                id="collection-actions",
            ),
            id="collection-panel",
        )

    def on_mount(self) -> None:
        self._apply_task = asyncio.create_task(self._apply_rows())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "collection-done":
            self.action_done()

    def action_done(self) -> None:
        if self.complete:
            self.dismiss(self.outcomes)

    async def _apply_rows(self) -> None:
        progress = self.query_one("#collection-progress", ProgressBar)
        for index, row in enumerate(self.selected_rows, start=1):
            self.query_one("#collection-title", Static).update(
                f"Collecting Set {index} of {len(self.selected_rows)}"
            )
            self.query_one("#collection-current", Static).update(str(row.set_file))
            try:
                result = await asyncio.to_thread(
                    apply_collection,
                    row.set_file,
                    selected_apply_ids(row),
                    project_root=row.set_report.project_root,
                )
                outcome = ApplyOutcome(
                    row=row,
                    succeeded=True,
                    message=(
                        f"Backup: {result.backup_path}\n"
                        f"Copied audio: {len(result.copied_audio)}\n"
                        f"Copied ASD: {len(result.copied_asd)}\n"
                        f"Rewritten references: {result.rewritten_reference_count}\n"
                        f"Final audit: {result.final_report.external_collectable_count} external, "
                        f"{result.final_report.missing_count} missing"
                    ),
                )
            except CollectionApplyError as exc:
                failure = exc.failure
                rollback = "not attempted"
                if failure.rollback_attempted:
                    rollback = "succeeded" if failure.rollback_succeeded else "failed"
                cleanup = "not needed"
                if failure.cleanup_attempted:
                    cleanup = "succeeded" if failure.cleanup_succeeded else "incomplete"
                outcome = ApplyOutcome(
                    row=row,
                    succeeded=False,
                    message=(
                        f"Failed step: {failure.failed_step}\n"
                        f"Error: {failure.message}\n"
                        f"Backup: {failure.backup_path}\n"
                        f"Rollback: {rollback}\n"
                        f"Copied-file cleanup: {cleanup}\n"
                        f"Copied before failure: {len(failure.copied_audio)} audio, "
                        f"{len(failure.copied_asd)} ASD"
                    ),
                )
            except CollectionError as exc:
                outcome = ApplyOutcome(row=row, succeeded=False, message=str(exc))
            except Exception as exc:
                outcome = ApplyOutcome(
                    row=row,
                    succeeded=False,
                    message=f"Unexpected collection error: {exc}",
                )

            self.outcomes.append(outcome)
            progress.update(progress=index)
            await self._append_outcome_card(outcome)
            if not outcome.succeeded:
                break

        self.complete = True
        succeeded = sum(outcome.succeeded for outcome in self.outcomes)
        failed = any(not outcome.succeeded for outcome in self.outcomes)
        title = "Collection stopped" if failed else "Collection complete"
        state = "ACTION REQUIRED" if failed else "COLLECTION COMPLETE"
        self.query_one("#collection-title", Static).update(title)
        self.query_one("#collection-state", Static).update(state)
        self.query_one("#collection-current", Static).update(
            f"{succeeded} of {len(self.selected_rows)} Set{'s' if len(self.selected_rows) != 1 else ''} completed"
        )
        self.query_one("#collection-note", Static).update(
            "Review the result below, then return to rescan the folder."
        )
        self.query_one("#collection-done", Button).disabled = False
        self.query_one("#collection-done", Button).focus()

    async def _append_outcome_card(self, outcome: ApplyOutcome) -> None:
        classes = (
            "outcome-card outcome-success" if outcome.succeeded else "outcome-card outcome-failure"
        )
        await self.query_one("#outcome-cards", Vertical).mount(
            Static(self._render_outcome(outcome), classes=classes)
        )

    @staticmethod
    def _render_outcome(outcome: ApplyOutcome) -> Text:
        text = Text()
        marker = "✓  SUCCESS" if outcome.succeeded else "!  FAILED"
        style = "bold #75D9A6" if outcome.succeeded else "bold #FF6B63"
        text.append(marker, style=style)
        text.append("  ·  ", style="#666666")
        text.append(f"{outcome.row.set_file.name}\n", style="bold #F2F2F2")
        text.append(outcome.message, style="#B7B7B7")
        return text


class ReviewApp(App[None]):
    CSS_PATH = "review.tcss"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("space", "toggle_selected", "Select"),
        Binding("ctrl+a", "select_all", "Select all"),
        Binding("escape", "clear_selection", "Clear"),
        Binding("f", "cycle_filter", "Filter"),
        Binding("a", "apply_selected", "Collect"),
        Binding("r", "refresh", "Rescan"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, folder: Path) -> None:
        super().__init__()
        self.folder = folder
        self.rows: list[ReviewRow] = []
        self.selected_ids: set[int] = set()
        self.active_filter = ReviewFilter.ALL
        self._load_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Static("ABLETON  COLLECTOR", id="brand"),
            Static("", id="folder-path"),
            Static("READY", id="scan-state"),
            id="topbar",
        )
        yield Horizontal(
            Static("", id="metric-total", classes="metric-card total-card"),
            Static("", id="metric-ready", classes="metric-card ready-card"),
            Static("", id="metric-safe", classes="metric-card safe-card"),
            Static("", id="metric-issues", classes="metric-card issue-card"),
            id="summary",
        )
        with Horizontal(id="workspace"):
            with Vertical(id="set-list-panel"):
                with Horizontal(id="filter-bar"):
                    yield Static("SETS", id="sets-heading", classes="eyebrow")
                    yield Button("All", id="filter-all", classes="filter-button active")
                    yield Button("Ready", id="filter-ready", classes="filter-button")
                    yield Button("Safe", id="filter-safe", classes="filter-button")
                    yield Button("Blocked", id="filter-blocked", classes="filter-button")
                    yield Button("Errors", id="filter-errors", classes="filter-button")
                yield DataTable(
                    id="sets",
                    cursor_type="row",
                    zebra_stripes=True,
                    show_row_labels=False,
                )
                yield Vertical(
                    LoadingIndicator(id="scan-spinner"),
                    Static("Scanning for Ableton Sets…", id="list-state-copy"),
                    Button("Scan again", id="retry-scan"),
                    id="list-state",
                )
            with Vertical(id="inspector-panel"):
                Static("INSPECTOR", classes="eyebrow", id="inspector-heading")
                yield VerticalScroll(
                    Static("Select a Set to inspect its audio references.", id="details"),
                    id="inspector-scroll",
                )
        yield Horizontal(
            Static("No Sets selected", id="selection-summary"),
            Button("Clear", id="clear-selection", disabled=True),
            Button("Collect selected", id="collect-button", variant="success", disabled=True),
            id="action-bar",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Collector Inspector"
        self.sub_title = str(self.folder)
        self.query_one("#folder-path", Static).update(str(self.folder))
        self._update_summary(loading=True)
        self._update_action_bar()
        self._start_load_rows()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("filter-"):
            self._set_filter(ReviewFilter(button_id.removeprefix("filter-")))
        elif button_id == "retry-scan":
            self.action_refresh()
        elif button_id == "clear-selection":
            self.action_clear_selection()
        elif button_id == "collect-button":
            self.action_apply_selected()

    def action_refresh(self) -> None:
        self.selected_ids.clear()
        self._start_load_rows()

    def action_toggle_selected(self) -> None:
        row = self._current_row()
        if row:
            self._toggle_row(row)

    def action_select_all(self) -> None:
        selectable_ids = {row.id for row in self._visible_rows() if row.selectable}
        if not selectable_ids:
            self._show_filter_message("No collectable Sets are visible in this filter.")
            return
        self.selected_ids.update(selectable_ids)
        current_id = self._current_row().id if self._current_row() else None
        self._refresh_table(cursor_row_id=current_id)
        self._update_action_bar()
        if current_id is not None:
            row = self._row_for_key(current_id)
            if row:
                self._show_details(row)

    def action_clear_selection(self) -> None:
        if not self.selected_ids:
            return
        current_id = self._current_row().id if self._current_row() else None
        self.selected_ids.clear()
        self._refresh_table(cursor_row_id=current_id)
        self._update_action_bar()
        if current_id is not None:
            row = self._row_for_key(current_id)
            if row:
                self._show_details(row)

    def action_cycle_filter(self) -> None:
        index = FILTER_ORDER.index(self.active_filter)
        self._set_filter(FILTER_ORDER[(index + 1) % len(FILTER_ORDER)])

    def action_apply_selected(self) -> None:
        selected_rows = self._selected_rows()
        if not selected_rows:
            self._show_filter_message("Select at least one Ready Set before collecting.")
            return

        def after_confirm(confirmed: bool) -> None:
            if not confirmed:
                return

            def after_collection(_: list[ApplyOutcome]) -> None:
                self.selected_ids.clear()
                self._start_load_rows()

            self.push_screen(CollectionScreen(selected_rows), after_collection)

        self.push_screen(ConfirmScreen(selected_rows), after_confirm)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row = self._row_for_key(event.row_key.value)
        if row:
            self._show_details(row)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = self._row_for_key(event.row_key.value)
        if row:
            self._toggle_row(row)

    def _toggle_row(self, row: ReviewRow) -> None:
        if not row.selectable:
            reason = row.blockers[0] if row.blockers else "This Set is already safe."
            self._show_details(row, notice=f"Cannot select this Set: {reason}")
            return
        if row.id in self.selected_ids:
            self.selected_ids.remove(row.id)
        else:
            self.selected_ids.add(row.id)
        self._refresh_table(cursor_row_id=row.id)
        self._update_action_bar()
        self._show_details(row)

    def _set_filter(self, review_filter: ReviewFilter) -> None:
        self.active_filter = review_filter
        for filter_value in FILTER_ORDER:
            button = self.query_one(f"#filter-{filter_value.value}", Button)
            button.set_class(filter_value == review_filter, "active")
        self._refresh_table()
        visible = self._visible_rows()
        if visible:
            self._show_details(visible[0])
            self._table().focus()
        else:
            self._details().update(
                self._empty_details(
                    f"No Sets match the {self._filter_label(review_filter)} filter."
                )
            )

    def _start_load_rows(self) -> None:
        if self._load_task and not self._load_task.done():
            self._load_task.cancel()
        self.rows = []
        self.selected_ids.clear()
        self._table().clear(columns=True)
        self._set_scan_state("SCANNING", "#F3BE63")
        self._update_summary(loading=True)
        self._update_action_bar()
        self._show_list_state(
            f"Scanning {self.folder}\nLarge external volumes can take a little while.",
            scanning=True,
            retry=False,
        )
        self._details().update(self._empty_details("Scanning audio references…"))
        self._load_task = asyncio.create_task(self._load_rows_async())

    async def _load_rows_async(self) -> None:
        try:
            rows = await asyncio.to_thread(build_review_rows, self.folder)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._set_scan_state("SCAN FAILED", "#FF6B63")
            self._update_summary()
            self._show_list_state(
                f"Scan failed\n{exc}",
                scanning=False,
                retry=True,
            )
            self._details().update(
                self._empty_details(
                    "The folder could not be scanned. Review the error and try again."
                )
            )
            return
        self._finish_load(rows)

    def _load_rows(self) -> None:
        self._finish_load(build_review_rows(self.folder))

    def _finish_load(self, rows: list[ReviewRow]) -> None:
        self.rows = rows
        self._set_scan_state("SCAN COMPLETE", "#75D9A6")
        self._update_summary()
        self._refresh_table()
        self._update_action_bar()
        visible = self._visible_rows()
        if visible:
            self._show_details(visible[0])
            self._table().focus()
        elif not self.rows:
            self._details().update(
                self._empty_details("No Ableton Sets were found in this folder.")
            )

    def _refresh_table(self, cursor_row_id: int | None = None) -> None:
        table = self._table()
        table.clear(columns=True)
        table.add_columns("", "Set", "Status", "External", "Missing")
        visible_rows = self._visible_rows()
        for row in visible_rows:
            table.add_row(
                self._selection_cell(row),
                self._display_set_path(row),
                Text(
                    STATUS_LABELS.get(row.status, row.status),
                    style=STATUS_STYLES.get(row.status, ""),
                ),
                str(row.set_report.external_collectable_count),
                str(row.set_report.missing_count),
                key=str(row.id),
            )

        if not visible_rows:
            if self.rows:
                self._show_list_state(
                    f"No Sets match the {self._filter_label(self.active_filter)} filter.",
                    scanning=False,
                    retry=False,
                )
            else:
                self._show_list_state(
                    "No Ableton Sets found in this folder.",
                    scanning=False,
                    retry=True,
                )
            return

        self._hide_list_state()
        target_index = 0
        if cursor_row_id is not None:
            for index, row in enumerate(visible_rows):
                if row.id == cursor_row_id:
                    target_index = index
                    break
        table.move_cursor(row=target_index, column=0)

    def _selection_cell(self, row: ReviewRow) -> Text:
        if not row.selectable:
            return Text(" · ", style="#555555")
        if row.id in self.selected_ids:
            return Text(" ✓ ", style="bold #FF746C")
        return Text(" ○ ", style="#777777")

    def _display_set_path(self, row: ReviewRow) -> Text:
        try:
            path = row.set_file.relative_to(self.folder)
        except ValueError:
            path = Path(row.set_file.name)
        text = Text()
        if len(path.parts) > 1:
            parent = str(Path(*path.parts[:-1]))
            text.append(f"{parent}/", style="#777777")
        text.append(path.name, style="bold #E7E7E7")
        return text

    def _show_details(self, row: ReviewRow, notice: str | None = None) -> None:
        self._details().update(self._render_details(row, notice=notice))

    def _render_details(self, row: ReviewRow, notice: str | None = None) -> Text:
        report = row.set_report
        text = Text()
        if notice:
            text.append("!  ", style="bold #F3BE63")
            text.append(notice, style="#F3BE63")
            text.append("\n\n")

        text.append(row.set_file.name, style="bold #F2F2F2")
        text.append("\n")
        text.append(str(row.set_file), style="#858585")
        text.append("\n\n")
        text.append(
            f" {STATUS_LABELS.get(row.status, row.status).upper()} ",
            style=STATUS_STYLES.get(row.status, ""),
        )
        text.append("\n\n")

        text.append("REFS ", style="#777777")
        text.append(str(report.total_audio_references), style="bold #F2F2F2")
        text.append("  ·  MEDIA ", style="#777777")
        text.append(str(report.unique_media_count), style="bold #F2F2F2")
        text.append("  ·  LOCAL ", style="#777777")
        text.append(str(report.already_local_count), style="bold #F2F2F2")
        text.append("\n")
        text.append("EXTERNAL ", style="#777777")
        text.append(str(report.external_collectable_count), style="bold #F2F2F2")
        text.append("  ·  MISSING ", style="#777777")
        text.append(str(report.missing_count), style="bold #F2F2F2")
        text.append("  ·  PLUG-INS ", style="#777777")
        text.append(str(report.total_plugin_instances), style="bold #F2F2F2")
        text.append("\n\n")

        self._append_section(text, "PROJECT")
        self._append_field(text, "Root", str(report.project_root))
        self._append_field(text, "Destination", str(report.project_root / "Samples" / "Imported"))

        text.append("\n")
        self._append_section(text, "PLUG-INS BY TRACK")
        if report.plugin_references:
            current_track: tuple[int, str | None, str] | None = None
            format_colors = {
                "audio_unit": ("AU", "#8DAAF8"),
                "vst2": ("VST2", "#E6C75A"),
                "vst3": ("VST3", "#F0AA4B"),
                "unknown": ("UNKNOWN", "#F184A2"),
            }
            for plugin in report.plugin_references:
                track = (plugin.track_index, plugin.track_id, plugin.track_name)
                if track != current_track:
                    text.append(
                        f"{plugin.track_name} [{plugin.track_type.value}]\n",
                        style="bold #E7E7E7",
                    )
                    current_track = track
                label, color = format_colors[plugin.plugin_format.value]
                text.append(f"  {label:<7}", style=f"bold {color}")
                text.append(f"{plugin.plugin_name}\n", style="#C7C7C7")
        else:
            text.append("No AU, VST2, or VST3 plug-ins found.\n", style="#AFAFAF")

        if row.blockers:
            text.append("\n")
            self._append_section(text, "BLOCKERS", "#F3BE63")
            for blocker in row.blockers:
                text.append("! ", style="#F3BE63")
                text.append(blocker, style="#C7C7C7")
                text.append("\n")

        if row.plan and row.plan.items:
            text.append("\n")
            self._append_section(text, f"COLLECTABLE MEDIA  {len(row.plan.items)}")
            for item in row.plan.items:
                text.append(f"{item.id:02d}  ", style="bold #FF746C")
                text.append(item.source_path.name, style="bold #E7E7E7")
                text.append("\n")
                text.append(f"    {item.source_path}\n", style="#858585")
                text.append("    → ", style="#FF746C")
                text.append(str(item.destination_path), style="#AFAFAF")
                text.append("\n")
                asd_style = "#75D9A6" if item.asd_source_path else "#777777"
                text.append(
                    f"    ASD {'included' if item.asd_source_path else 'not found'}"
                    f"  ·  {len(item.references)} reference"
                    f"{'s' if len(item.references) != 1 else ''}\n",
                    style=asd_style,
                )
        elif not row.blockers:
            text.append("\n")
            self._append_section(text, "AUDIO")
            text.append("All referenced media is already inside the project.\n", style="#AFAFAF")

        missing_references = [
            reference
            for reference in report.audio_references
            if reference.status.value == "missing"
        ]
        if missing_references:
            text.append("\n")
            self._append_section(text, "Missing audio paths:", "#F3BE63")
            for reference in missing_references:
                text.append(f"ref #{reference.ref_index}: ", style="bold #F3BE63")
                text.append(reference.referenced_path, style="#C7C7C7")
                text.append("\n")
                if reference.path and reference.path != reference.referenced_path:
                    text.append(f"  stored path: {reference.path}\n", style="#858585")
                if reference.relative_path and reference.relative_path != reference.referenced_path:
                    text.append(
                        f"  stored relative path: {reference.relative_path}\n",
                        style="#858585",
                    )

        text.append("\n")
        text.append("Space / Enter", style="bold #B7B7B7")
        text.append(" select   ", style="#707070")
        text.append("Ctrl+A", style="bold #B7B7B7")
        text.append(" select visible", style="#707070")
        return text

    @staticmethod
    def _append_section(text: Text, label: str, color: str = "#777777") -> None:
        text.append(label, style=f"bold {color}")
        text.append("\n")

    @staticmethod
    def _append_field(text: Text, label: str, value: str) -> None:
        text.append(f"{label:<12}", style="#777777")
        text.append(value, style="#B7B7B7")
        text.append("\n")

    def _empty_details(self, message: str) -> Text:
        text = Text()
        text.append("INSPECT A SET\n", style="bold #E7E7E7")
        text.append(message, style="#858585")
        return text

    def _show_filter_message(self, message: str) -> None:
        text = Text()
        text.append("!  ", style="bold #F3BE63")
        text.append(message, style="#F3BE63")
        self._details().update(text)

    def _update_summary(self, loading: bool = False) -> None:
        total: int | str = "—" if loading else len(self.rows)
        ready: int | str = "—" if loading else sum(row.selectable for row in self.rows)
        safe: int | str = "—" if loading else sum(row.status == "safe" for row in self.rows)
        issues: int | str = (
            "—" if loading else sum(row.status not in {"collectable", "safe"} for row in self.rows)
        )
        for widget_id, label, value, color in (
            ("#metric-total", "TOTAL SETS", total, "#E7E7E7"),
            ("#metric-ready", "READY", ready, "#75D9A6"),
            ("#metric-safe", "SAFE", safe, "#7FB5FF"),
            ("#metric-issues", "ISSUES", issues, "#F3BE63"),
        ):
            text = Text()
            text.append(f"{value}\n", style=f"bold {color}")
            text.append(label, style="#777777")
            self.query_one(widget_id, Static).update(text)

    def _update_action_bar(self) -> None:
        selected_rows = self._selected_rows()
        media_count = sum(len(row.plan.items) for row in selected_rows if row.plan)
        reference_count = sum(
            len(item.references) for row in selected_rows if row.plan for item in row.plan.items
        )
        summary = self.query_one("#selection-summary", Static)
        if selected_rows:
            text = Text()
            text.append(
                f"{len(selected_rows)} Set{'s' if len(selected_rows) != 1 else ''} selected",
                style="bold #FF746C",
            )
            text.append("  ·  ", style="#666666")
            text.append(
                f"{media_count} media",
                style="bold #E7E7E7",
            )
            text.append("  ·  ", style="#666666")
            text.append(
                f"{reference_count} references",
                style="bold #E7E7E7",
            )
            summary.update(text)
        else:
            summary.update("No Sets selected")
        self.query_one("#collect-button", Button).disabled = not selected_rows
        self.query_one("#clear-selection", Button).disabled = not selected_rows

    def _set_scan_state(self, label: str, color: str) -> None:
        text = Text()
        text.append("● ", style=color)
        text.append(label, style=f"bold {color}")
        self.query_one("#scan-state", Static).update(text)

    def _show_list_state(self, message: str, *, scanning: bool, retry: bool) -> None:
        self._table().display = False
        state = self.query_one("#list-state", Vertical)
        state.display = True
        self.query_one("#list-state-copy", Static).update(message)
        self.query_one("#scan-spinner", LoadingIndicator).display = scanning
        self.query_one("#retry-scan", Button).display = retry

    def _hide_list_state(self) -> None:
        self.query_one("#list-state", Vertical).display = False
        self._table().display = True

    def _visible_rows(self) -> list[ReviewRow]:
        return [row for row in self.rows if self._matches_filter(row)]

    def _matches_filter(self, row: ReviewRow) -> bool:
        if self.active_filter == ReviewFilter.ALL:
            return True
        if self.active_filter == ReviewFilter.READY:
            return row.selectable
        if self.active_filter == ReviewFilter.SAFE:
            return row.status == "safe"
        if self.active_filter == ReviewFilter.BLOCKED:
            return row.status in {"blocked", "blocked_missing"}
        return row.status == "error"

    @staticmethod
    def _filter_label(review_filter: ReviewFilter) -> str:
        return {
            ReviewFilter.ALL: "All Sets",
            ReviewFilter.READY: "Ready",
            ReviewFilter.SAFE: "Safe",
            ReviewFilter.BLOCKED: "Blocked",
            ReviewFilter.ERRORS: "Errors",
        }[review_filter]

    def _selected_rows(self) -> list[ReviewRow]:
        return [row for row in self.rows if row.id in self.selected_ids and row.selectable]

    def _current_row(self) -> ReviewRow | None:
        table = self._table()
        if table.cursor_row < 0 or table.cursor_row >= len(table.rows):
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return self._row_for_key(row_key.value)

    def _row_for_key(self, key: object) -> ReviewRow | None:
        try:
            row_id = int(str(key))
        except ValueError:
            return None
        return next((row for row in self.rows if row.id == row_id), None)

    def _table(self) -> DataTable:
        return self.query_one("#sets", DataTable)

    def _details(self) -> Static:
        return self.query_one("#details", Static)


def run_review(folder: Path) -> None:
    ReviewApp(folder.expanduser().resolve()).run()
