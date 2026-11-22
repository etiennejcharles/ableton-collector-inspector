from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from ableton_audio_collector.batch import build_review_batch
from ableton_audio_collector.collector import CollectionApplyError, CollectionFailure
from ableton_audio_collector.gui import (
    ANIMATION_DURATIONS,
    STATUS_COLOR_ROLE,
    STATUS_ROLE,
    AnimatedProgressBar,
    CollectionDialog,
    CollectionWorker,
    CollectorWindow,
    ConfirmCollectionDialog,
    MetricCell,
    PluginFilterBar,
    WorkspacePage,
)
from ableton_audio_collector.gui_style import APP_STYLE, STATUS_COLORS
from ableton_audio_collector.history import HistoryStore

from .conftest import mixed_plugin_als, touch_audio, write_als


def test_window_scans_filters_and_selects_ready_sets(qtbot, tmp_path):
    _build_dashboard_folder(tmp_path)
    pool = QThreadPool()
    window = CollectorWindow(thread_pool=pool)
    qtbot.addWidget(window)
    window.show()

    window.scan_sources([tmp_path])
    qtbot.waitUntil(lambda: len(window.batch.rows) == 3)

    assert window.workspace.metrics["total"].value_label.text() == "3"
    assert window.workspace.metrics["ready"].value_label.text() == "1"
    assert window.workspace.metrics["safe"].value_label.text() == "1"
    assert window.workspace.metrics["issues"].value_label.text() == "1"
    assert window.workspace.table.rowCount() == 3

    window._set_filter("ready")
    assert window.workspace.table.rowCount() == 1
    checkbox = window.workspace.table_model.index(0, 1)
    qtbot.mouseClick(
        window.workspace.table.viewport(),
        Qt.MouseButton.LeftButton,
        pos=window.workspace.table.visualRect(checkbox).center(),
    )
    assert len(window.selected_paths) == 1
    window._clear_selection()
    window._select_ready()
    assert len(window.selected_paths) == 1
    assert window.workspace.collect_button.isEnabled()
    assert "1 Set selected" in window.workspace.selection_summary.text()

    window._set_filter("safe")
    assert window.workspace.table.rowCount() == 1
    assert len(window.selected_paths) == 1
    pool.waitForDone()


def test_filter_chips_show_live_counts(qtbot, tmp_path):
    _build_dashboard_folder(tmp_path)
    pool = QThreadPool()
    window = CollectorWindow(thread_pool=pool)
    qtbot.addWidget(window)
    window.show()

    window.scan_sources([tmp_path])
    qtbot.waitUntil(lambda: window.workspace.scan_state.text() == "●  SCAN COMPLETE")

    assert window.workspace.filter_buttons["all"].text() == "All  3"
    assert window.workspace.filter_buttons["ready"].text() == "Ready  1"
    assert window.workspace.filter_buttons["safe"].text() == "Safe  1"
    assert window.workspace.filter_buttons["blocked"].text() == "Blocked  1"
    assert window.workspace.filter_buttons["errors"].text() == "Errors  0"
    pool.waitForDone()


def test_keyboard_actions_are_bound_and_operate_on_active_filter(qtbot, tmp_path):
    _build_dashboard_folder(tmp_path)
    window = CollectorWindow()
    qtbot.addWidget(window)
    window.batch = build_review_batch([tmp_path])
    window.workspace.set_batch(window.batch, set(), "ready")
    window.active_filter = "ready"

    standard_open = QKeySequence.keyBindings(QKeySequence.StandardKey.Open)
    assert any(
        action_shortcut.matches(expected) == QKeySequence.SequenceMatch.ExactMatch
        for action_shortcut in window.open_sets_action.shortcuts()
        for expected in standard_open
    )
    assert window.open_folder_action.shortcut().toString(
        QKeySequence.SequenceFormat.PortableText
    ) == ("Ctrl+Shift+O")
    assert window.rescan_action.shortcut().toString(QKeySequence.SequenceFormat.PortableText) == (
        "Ctrl+R"
    )
    assert (
        window.select_ready_action.shortcut().toString(QKeySequence.SequenceFormat.PortableText)
        == "Ctrl+A"
    )
    assert (
        window.clear_selection_action.shortcut().toString(QKeySequence.SequenceFormat.PortableText)
        == "Esc"
    )

    window.select_ready_action.trigger()
    assert len(window.selected_paths) == 1
    assert next(iter(window.selected_paths)).name == "Song.als"
    window.clear_selection_action.trigger()
    assert window.selected_paths == set()


def test_scan_progress_primary_actions_and_state_pages(qtbot):
    workspace = WorkspacePage()
    qtbot.addWidget(workspace)
    workspace.show()

    workspace.set_loading()
    assert workspace.scan_progress.isVisible()
    assert workspace.scan_progress.minimum() == 0
    assert workspace.scan_progress.maximum() == 0
    assert workspace.collect_button.isEnabled() is False
    assert workspace.table_stack.currentWidget() is workspace.table_state
    assert workspace.table_state.title.text() == "Scanning projects…"

    workspace.set_failure("Source disconnected")
    assert workspace.scan_progress.isVisible() is False
    assert workspace.collect_button.isEnabled() is False
    assert workspace.table_state.title.text() == "The scan could not finish"
    assert "Source disconnected" in workspace.table_state.body.text()
    assert workspace.table_state.action.text() == "Try Again"


def test_empty_state_is_actionable(qtbot):
    workspace = WorkspacePage()
    qtbot.addWidget(workspace)
    workspace.show()

    workspace.set_batch(build_review_batch([]), set(), "all")

    assert workspace.table_stack.currentWidget() is workspace.table_state
    assert workspace.table_state.title.text() == "No Ableton Sets found"
    assert workspace.table_state.action.text() == "Choose Another Source"
    assert workspace.collect_button.isEnabled() is False


def test_plugin_tab_groups_formats_by_track_and_home_returns_to_launch(qtbot, tmp_path):
    set_file = write_als(tmp_path / "Project" / "Plugins.als", mixed_plugin_als())
    window = CollectorWindow()
    qtbot.addWidget(window)
    window.batch = build_review_batch([set_file])
    window.stack.setCurrentWidget(window.workspace)
    window.workspace.set_batch(window.batch, set(), "all")

    assert window.workspace.inspector_tabs.count() == 2
    assert window.workspace.inspector_tabs.tabText(1) == "Plug-ins  5"
    plugin_text = window.workspace.plugins_inspector.toPlainText()
    assert "Vocals · Audio · 2 plug-ins" in plugin_text
    assert "AU  Auto-Tune Pro" in plugin_text
    assert "VST3  Pro-Q 3" in plugin_text
    assert "Synth · MIDI · 1 plug-in" in plugin_text
    assert "VST2  Serum" in plugin_text
    assert "UNKNOWN  Mystery Device" in plugin_text
    assert "Save in Live, then Rescan" in plugin_text

    assert window.workspace.change_button.text() == "Home"
    qtbot.mouseClick(window.workspace.change_button, Qt.MouseButton.LeftButton)
    assert window.stack.currentWidget() is window.drop_page


def test_plugin_tab_filters_by_name_track_and_type(qtbot, tmp_path):
    set_file = write_als(tmp_path / "Project" / "Plugins.als", mixed_plugin_als())
    window = CollectorWindow()
    qtbot.addWidget(window)
    window.batch = build_review_batch([set_file])
    window.stack.setCurrentWidget(window.workspace)
    window.workspace.set_batch(window.batch, set(), "all")

    search = window.workspace.plugin_search
    type_filter = window.workspace.plugin_type_filter
    assert search.placeholderText() == "Search plug-ins or tracks…"
    assert [type_filter.itemText(index) for index in range(type_filter.count())] == [
        "All types",
        "VST3",
        "Audio Unit",
        "VST2",
        "Unknown",
    ]

    search.setText("auto-tune")
    plugin_text = window.workspace.plugins_inspector.toPlainText()
    assert "Vocals · Audio · 1 plug-in" in plugin_text
    assert "AU  Auto-Tune Pro" in plugin_text
    assert "Pro-Q 3" not in plugin_text
    assert window.workspace.plugin_results_label.text() == "1 of 5"

    search.setText("vocals")
    plugin_text = window.workspace.plugins_inspector.toPlainText()
    assert "AU  Auto-Tune Pro" in plugin_text
    assert "VST3  Pro-Q 3" in plugin_text
    assert "Serum" not in plugin_text

    search.clear()
    type_filter.setCurrentIndex(type_filter.findData("vst3"))
    plugin_text = window.workspace.plugins_inspector.toPlainText()
    assert "VST3  Pro-Q 3" in plugin_text
    assert "VST3  Limiter" in plugin_text
    assert "Auto-Tune Pro" not in plugin_text
    assert window.workspace.plugin_results_label.text() == "2 of 5"

    search.setText("vocals")
    plugin_text = window.workspace.plugins_inspector.toPlainText()
    assert "VST3  Pro-Q 3" in plugin_text
    assert "Limiter" not in plugin_text
    assert "Auto-Tune Pro" not in plugin_text
    assert window.workspace.inspector_tabs.tabText(1) == "Plug-ins  1 of 5"

    search.setText("does-not-exist")
    plugin_text = window.workspace.plugins_inspector.toPlainText()
    assert "No plug-ins match the current name and type filters." in plugin_text
    assert window.workspace.plugin_results_label.text() == "0 of 5"


def test_scan_history_persists_inventory_and_warns_after_set_changes(qtbot, tmp_path):
    audio_file = touch_audio(tmp_path / "External" / "vocal.wav", b"vocal")
    set_file = write_als(
        tmp_path / "Project" / "History.als",
        mixed_plugin_als().replace(
            "</Ableton>",
            f'<FileRef><Path Value="{audio_file}" /></FileRef></Ableton>',
        ),
    )
    store = HistoryStore(tmp_path / "app-data" / "scan-history.json")
    pool = QThreadPool()
    window = CollectorWindow(thread_pool=pool, history_store=store)
    qtbot.addWidget(window)
    window.show()

    window.scan_sources([set_file])
    qtbot.waitUntil(lambda: window.workspace.scan_state.text() == "●  SCAN COMPLETE")

    entries = store.load()
    assert len(entries) == 1
    assert window.drop_page.history_button.text() == "View Scan History  ·  1"
    qtbot.mouseClick(window.workspace.history_button, Qt.MouseButton.LeftButton)
    assert window.stack.currentWidget() is window.history_page
    assert window.history_page.history_list.count() == 1
    history_text = window.history_page.history_inspector.toPlainText()
    assert "CURRENT" in history_text
    assert "Auto-Tune Pro" in history_text
    assert "AU" in history_text
    assert str(audio_file) in history_text
    assert "EXTERNAL" in history_text
    assert isinstance(window.workspace.plugin_filter_bar, PluginFilterBar)
    assert isinstance(window.history_page.plugin_filter_bar, PluginFilterBar)
    assert [
        window.history_page.plugin_type_filter.itemText(index)
        for index in range(window.history_page.plugin_type_filter.count())
    ] == ["All types", "VST3", "Audio Unit", "VST2", "Unknown"]

    window.history_page.plugin_search.setText("auto-tune")
    history_text = window.history_page.history_inspector.toPlainText()
    assert "AU  Auto-Tune Pro" in history_text
    assert "Pro-Q 3" not in history_text
    assert window.history_page.plugin_results_label.text() == "1 of 5"

    window.history_page.plugin_search.clear()
    window.history_page.plugin_type_filter.setCurrentIndex(
        window.history_page.plugin_type_filter.findData("vst3")
    )
    history_text = window.history_page.history_inspector.toPlainText()
    assert "VST3  Pro-Q 3" in history_text
    assert "VST3  Limiter" in history_text
    assert "Auto-Tune Pro" not in history_text
    assert window.history_page.plugin_results_label.text() == "2 of 5"

    window.history_page.plugin_search.setText("vocals")
    history_text = window.history_page.history_inspector.toPlainText()
    assert "VST3  Pro-Q 3" in history_text
    assert "Auto-Tune Pro" not in history_text
    assert "Limiter" not in history_text
    assert window.history_page.plugin_results_label.text() == "1 of 5"
    window.history_page.plugin_search.clear()
    window.history_page.plugin_type_filter.setCurrentIndex(0)

    qtbot.mouseClick(window.history_page.home_button, Qt.MouseButton.LeftButton)
    assert window.stack.currentWidget() is window.drop_page
    saved_mtime_ns = set_file.stat().st_mtime_ns
    os.utime(set_file, ns=(saved_mtime_ns + 2_000_000_000,) * 2)
    qtbot.mouseClick(window.drop_page.history_button, Qt.MouseButton.LeftButton)

    assert window.history_page.warning_banner.isVisible()
    assert "may be stale" in window.history_page.warning_banner.text()
    assert "MAY BE STALE" in window.history_page.history_inspector.toPlainText()
    assert window.history_page.rescan_button.isEnabled()

    qtbot.mouseClick(window.history_page.rescan_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: window.workspace.scan_state.text() == "●  SCAN COMPLETE" and len(store.load()) == 2
    )
    qtbot.mouseClick(window.workspace.history_button, Qt.MouseButton.LeftButton)
    assert window.history_page.history_list.count() == 2
    assert "Latest" in window.history_page.history_list.item(0).text()
    assert window.history_page.warning_banner.isVisible() is False
    assert "CURRENT" in window.history_page.history_inspector.toPlainText()
    pool.waitForDone()


def test_status_delegate_roles_expose_semantic_state(qtbot, tmp_path):
    _build_dashboard_folder(tmp_path)
    workspace = WorkspacePage()
    qtbot.addWidget(workspace)
    batch = build_review_batch([tmp_path])
    workspace.set_batch(batch, set(), "all")

    ready_index = next(
        workspace.table_model.index(index, 3)
        for index, row in enumerate(workspace.table_model.rows)
        if row.status == "collectable"
    )
    assert ready_index.data(STATUS_ROLE) == "collectable"
    assert ready_index.data(STATUS_COLOR_ROLE) == STATUS_COLORS["collectable"]
    assert ready_index.data(Qt.ItemDataRole.DisplayRole) == "READY"
    assert "Status: READY" == ready_index.data(Qt.ItemDataRole.ToolTipRole)


def test_refresh_animations_reach_their_targets(qtbot):
    metric = MetricCell("READY", animations_enabled=True)
    qtbot.addWidget(metric)
    metric.set_value(1)
    metric.set_value(7)
    assert metric.animation.duration() == ANIMATION_DURATIONS["metric_count"]
    metric.animation.setCurrentTime(metric.animation.duration())
    assert metric.value_label.text() == "7"

    progress = AnimatedProgressBar()
    qtbot.addWidget(progress)
    progress.animations_enabled = True
    progress.setRange(0, 4)
    progress.setValue(0)
    progress.set_animated_value(3)
    assert progress.animation.duration() == ANIMATION_DURATIONS["collection_progress"]
    progress.animation.setCurrentTime(progress.animation.duration())
    assert progress.value() == 3

    window = CollectorWindow()
    qtbot.addWidget(window)
    window.animations_enabled = True
    window._show_page(window.workspace)
    assert window.page_animation is not None
    assert window.page_animation.duration() == ANIMATION_DURATIONS["page_fade"]
    window.page_animation.setCurrentTime(window.page_animation.duration())
    assert window.workspace.graphicsEffect() is None

    workspace = window.workspace
    workspace.animations_enabled = True
    workspace._show_issue_banner("One source could not be scanned")
    assert workspace.issue_animation.duration() == ANIMATION_DURATIONS["issue_banner"]
    workspace.issue_animation.setCurrentTime(workspace.issue_animation.duration())
    assert workspace.issue_banner_frame.maximumHeight() > 0


def test_stylesheet_targets_the_model_based_table_view():
    assert "QTableView" in APP_STYLE
    assert "QTableWidget" not in APP_STYLE


def test_window_shows_invalid_source_issues(qtbot, tmp_path):
    invalid = tmp_path / "notes.txt"
    invalid.write_text("not a Set")
    pool = QThreadPool()
    window = CollectorWindow(thread_pool=pool)
    qtbot.addWidget(window)
    window.show()

    window.scan_sources([invalid])
    qtbot.waitUntil(lambda: window.workspace.scan_state.text() == "●  SCAN COMPLETE")

    assert window.batch.rows == []
    assert window.workspace.issue_banner.isVisible()
    assert "Only folders and Ableton .als files" in window.workspace.issue_banner.text()
    pool.waitForDone()


def test_blocked_only_window_explains_why_collection_is_unavailable(qtbot, tmp_path):
    write_als(
        tmp_path / "Blocked Project" / "Blocked.als",
        '<Ableton><FileRef><Path Value="Missing/kick.wav" /></FileRef></Ableton>',
    )
    pool = QThreadPool()
    window = CollectorWindow(thread_pool=pool)
    qtbot.addWidget(window)
    window.show()

    window.scan_sources([tmp_path])
    qtbot.waitUntil(lambda: window.workspace.scan_state.text() == "●  SCAN COMPLETE")

    assert window.workspace.collect_button.isEnabled() is False
    assert window.workspace.select_ready_button.isEnabled() is False
    assert "No Ready Sets" in window.workspace.selection_summary.text()
    assert "no Sets are Ready" in window.workspace.collect_button.toolTip()
    assert "QPushButton#primaryButton:disabled" in APP_STYLE
    pool.waitForDone()


def test_showing_a_set_resets_the_inspector_to_its_blocker_summary(qtbot, tmp_path):
    missing_refs = "".join(
        f'<FileRef><Path Value="Missing/sample-{index}.wav" /></FileRef>' for index in range(50)
    )
    set_file = write_als(
        tmp_path / "Blocked Project" / "Blocked.als",
        f"<Ableton>{missing_refs}</Ableton>",
    )
    window = CollectorWindow()
    qtbot.addWidget(window)
    window.stack.setCurrentWidget(window.workspace)
    window.show()
    window.resize(900, 600)
    row = build_review_batch([set_file]).rows[0]

    window.workspace.show_row(row)
    qtbot.wait(50)
    scroll_bar = window.workspace.inspector.verticalScrollBar()
    scroll_bar.setValue(scroll_bar.maximum())
    assert scroll_bar.value() > 0

    window.workspace.show_row(row)

    assert scroll_bar.value() == 0


def test_window_ignores_stale_scan_results(qtbot, tmp_path):
    first = write_als(tmp_path / "First" / "First.als", "<Ableton />")
    second = write_als(tmp_path / "Second" / "Second.als", "<Ableton />")
    window = CollectorWindow()
    qtbot.addWidget(window)
    window.scan_generation = 2
    current = build_review_batch([second])
    stale = build_review_batch([first])

    window._scan_completed(2, current)
    window._scan_completed(1, stale)

    assert [row.set_file for row in window.batch.rows] == [second.resolve()]


def test_collection_cancel_does_not_start_worker(qtbot, tmp_path, monkeypatch):
    _build_ready_set(tmp_path)
    window = CollectorWindow()
    qtbot.addWidget(window)
    window.batch = build_review_batch([tmp_path])
    window.selected_paths = {window.batch.rows[0].set_file}
    calls: list[Path] = []
    monkeypatch.setattr(
        "ableton_audio_collector.gui.apply_collection",
        lambda set_file, *_args, **_kwargs: calls.append(set_file),
    )
    monkeypatch.setattr(
        ConfirmCollectionDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Rejected,
    )

    window._collect_selected()

    assert calls == []
    assert window.selected_paths


def test_collect_selected_button_runs_end_to_end(qtbot, tmp_path):
    external = touch_audio(tmp_path / "External" / "loop.wav", b"validated audio")
    touch_audio(Path(f"{external}.asd"), b"validated analysis")
    project = tmp_path / "Ready Project"
    set_file = write_als(
        project / "Ready.als",
        f'<Ableton><FileRef><Path Value="{external}" /></FileRef></Ableton>',
    )
    pool = QThreadPool()
    window = CollectorWindow(thread_pool=pool)
    qtbot.addWidget(window)
    window.show()
    window.scan_sources([tmp_path])
    qtbot.waitUntil(lambda: window.workspace.scan_state.text() == "●  SCAN COMPLETE")

    qtbot.mouseClick(window.workspace.select_ready_button, Qt.MouseButton.LeftButton)
    assert window.workspace.collect_button.isEnabled()

    def drive_modal_dialogs() -> None:
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, ConfirmCollectionDialog):
            QTimer.singleShot(10, drive_modal_dialogs)
            collect = next(
                button
                for button in dialog.findChildren(QPushButton)
                if button.text() == "Collect Selected"
            )
            collect.click()
        elif isinstance(dialog, CollectionDialog):
            if dialog.close_button.isEnabled():
                dialog.close_button.click()
            else:
                QTimer.singleShot(10, drive_modal_dialogs)
        else:
            QTimer.singleShot(10, drive_modal_dialogs)

    QTimer.singleShot(0, drive_modal_dialogs)
    qtbot.mouseClick(window.workspace.collect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: (
            window.workspace.scan_state.text() == "●  SCAN COMPLETE"
            and window.batch.rows[0].status == "safe"
        ),
        timeout=5000,
    )

    imported = project / "Samples" / "Imported" / "loop.wav"
    assert imported.read_bytes() == b"validated audio"
    assert Path(f"{imported}.asd").read_bytes() == b"validated analysis"
    assert list((project / "Backup").glob("Ready*.als"))
    assert window.batch.rows[0].set_file == set_file.resolve()
    assert window.workspace.collect_button.isEnabled() is False
    pool.waitForDone()


def test_collection_worker_stops_on_failure_and_reports_rollback(tmp_path, monkeypatch):
    _build_ready_set(tmp_path, "First")
    _build_ready_set(tmp_path, "Second")
    rows = build_review_batch([tmp_path]).rows
    calls: list[Path] = []

    def fail(set_file, *_args, **_kwargs):
        calls.append(set_file)
        raise CollectionApplyError(
            CollectionFailure(
                message="destination unavailable",
                failed_step="copy",
                backup_path=tmp_path / "Backup" / set_file.name,
                rollback_attempted=True,
                rollback_succeeded=True,
            )
        )

    monkeypatch.setattr("ableton_audio_collector.gui.apply_collection", fail)
    worker = CollectionWorker(rows)
    outcomes = []
    completed = []
    worker.signals.outcome.connect(outcomes.append)
    worker.signals.completed.connect(completed.extend)

    worker.run()

    assert len(calls) == 1
    assert len(outcomes) == 1
    assert outcomes[0].succeeded is False
    assert "Rollback: Succeeded" in outcomes[0].details
    assert completed == outcomes


def test_collection_worker_reports_success(tmp_path, monkeypatch):
    _build_ready_set(tmp_path)
    row = build_review_batch([tmp_path]).rows[0]
    monkeypatch.setattr(
        "ableton_audio_collector.gui.apply_collection",
        lambda *_args, **_kwargs: SimpleNamespace(
            backup_path=tmp_path / "Backup" / "Song.als",
            copied_audio=[(Path("source.wav"), Path("destination.wav"))],
            copied_asd=[],
            rewritten_reference_count=1,
            final_report=SimpleNamespace(
                external_collectable_count=0,
                missing_count=0,
            ),
        ),
    )
    worker = CollectionWorker([row])
    outcomes = []
    worker.signals.outcome.connect(outcomes.append)

    worker.run()

    assert outcomes[0].succeeded is True
    assert "Copied audio: 1" in outcomes[0].details
    assert "Rewritten references: 1" in outcomes[0].details


def test_window_rejects_close_during_collection(qtbot, monkeypatch):
    window = CollectorWindow()
    qtbot.addWidget(window)
    window.collection_running = True
    monkeypatch.setattr(
        "ableton_audio_collector.gui.QMessageBox.warning",
        lambda *_args, **_kwargs: None,
    )
    event = QCloseEvent()

    window.closeEvent(event)

    assert event.isAccepted() is False
    window.collection_running = False


def _build_dashboard_folder(root: Path) -> None:
    safe_project = root / "Safe Project"
    touch_audio(safe_project / "Samples" / "Imported" / "local.wav")
    write_als(
        safe_project / "Safe.als",
        '<Ableton><FileRef><RelativePath Value="Samples/Imported/local.wav" /></FileRef></Ableton>',
    )
    _build_ready_set(root)
    write_als(
        root / "Missing Project" / "Missing.als",
        '<Ableton><FileRef><Path Value="Missing/ghost.wav" /></FileRef></Ableton>',
    )


def _build_ready_set(root: Path, name: str = "Song") -> Path:
    external = touch_audio(root / "External" / f"{name}.wav")
    return write_als(
        root / f"{name} Project" / f"{name}.als",
        f'<Ableton><FileRef><Path Value="{external}" /></FileRef></Ableton>',
    )
