from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QEasingCurve,
    QModelIndex,
    QObject,
    QPropertyAnimation,
    QRect,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QVariantAnimation,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QPainter,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ableton_audio_collector import __version__
from ableton_audio_collector.batch import (
    ReviewBatch,
    ReviewRow,
    build_review_batch,
    selected_apply_ids,
)
from ableton_audio_collector.collector import (
    CollectionApplyError,
    CollectionError,
    CollectionResult,
    apply_collection,
)
from ableton_audio_collector.gui_style import (
    APP_STYLE,
    COLORS,
    STATUS_COLORS,
    STATUS_LABELS,
)
from ableton_audio_collector.history import (
    HistoryEntry,
    HistoryPlugin,
    HistoryStore,
    HistoryStoreError,
    default_history_path,
)
from ableton_audio_collector.models import PluginFormat, PluginReference, ReferenceStatus

FILTERS = ("all", "ready", "safe", "blocked", "errors")
STATUS_ROLE = Qt.ItemDataRole.UserRole.value + 1
STATUS_COLOR_ROLE = Qt.ItemDataRole.UserRole.value + 2
ANIMATION_DURATIONS = {
    "page_fade": 180,
    "metric_count": 240,
    "issue_banner": 160,
    "collection_progress": 180,
}

PLUGIN_FORMAT_LABELS = {
    PluginFormat.AUDIO_UNIT: "AU",
    PluginFormat.VST2: "VST2",
    PluginFormat.VST3: "VST3",
    PluginFormat.UNKNOWN: "UNKNOWN",
}

PLUGIN_FORMAT_COLORS = {
    PluginFormat.AUDIO_UNIT: COLORS["blue"],
    PluginFormat.VST2: COLORS["yellow"],
    PluginFormat.VST3: COLORS["amber"],
    PluginFormat.UNKNOWN: COLORS["pink"],
}

TRACK_TYPE_LABELS = {
    "audio": "Audio",
    "midi": "MIDI",
    "group": "Group",
    "return": "Return",
    "main": "Main",
}


def _widget_animations_enabled(widget: QWidget) -> bool:
    return bool(widget.style().styleHint(QStyle.StyleHint.SH_Widget_Animate, None, widget))


@dataclass(frozen=True)
class CollectionOutcome:
    row: ReviewRow
    succeeded: bool
    details: tuple[str, ...]


class ScanSignals(QObject):
    completed = Signal(int, object)
    failed = Signal(int, object)


class ScanWorker(QRunnable):
    def __init__(self, generation: int, sources: tuple[Path, ...]) -> None:
        super().__init__()
        self.generation = generation
        self.sources = sources
        self.signals = ScanSignals()

    @Slot()
    def run(self) -> None:
        try:
            batch = build_review_batch(self.sources)
        except Exception as exc:
            self.signals.failed.emit(self.generation, exc)
            return
        self.signals.completed.emit(self.generation, batch)


class CollectionSignals(QObject):
    row_started = Signal(int, int, str)
    outcome = Signal(object)
    completed = Signal(object)


class CollectionWorker(QRunnable):
    def __init__(self, rows: list[ReviewRow]) -> None:
        super().__init__()
        self.rows = rows
        self.signals = CollectionSignals()

    @Slot()
    def run(self) -> None:
        outcomes: list[CollectionOutcome] = []
        for index, row in enumerate(self.rows, start=1):
            self.signals.row_started.emit(index, len(self.rows), str(row.set_file))
            outcome = self._apply_row(row)
            outcomes.append(outcome)
            self.signals.outcome.emit(outcome)
            if not outcome.succeeded:
                break
        self.signals.completed.emit(outcomes)

    @staticmethod
    def _apply_row(row: ReviewRow) -> CollectionOutcome:
        try:
            result = apply_collection(
                row.set_file,
                selected_apply_ids(row),
                project_root=row.set_report.project_root,
            )
            return CollectionOutcome(
                row=row,
                succeeded=True,
                details=_success_details(result),
            )
        except CollectionApplyError as exc:
            failure = exc.failure
            rollback = "Not attempted"
            if failure.rollback_attempted:
                rollback = "Succeeded" if failure.rollback_succeeded else "Failed"
            cleanup = "Not needed"
            if failure.cleanup_attempted:
                cleanup = "Succeeded" if failure.cleanup_succeeded else "Incomplete"
            return CollectionOutcome(
                row=row,
                succeeded=False,
                details=(
                    f"Failed step: {failure.failed_step}",
                    f"Error: {failure.message}",
                    f"Backup: {failure.backup_path or 'Not created'}",
                    f"Rollback: {rollback}",
                    f"Copied-file cleanup: {cleanup}",
                    f"Copied before failure: {len(failure.copied_audio)} audio, "
                    f"{len(failure.copied_asd)} ASD",
                    *(
                        f"Cleanup error: {cleanup_error}"
                        for cleanup_error in failure.cleanup_errors
                    ),
                ),
            )
        except CollectionError as exc:
            return CollectionOutcome(row=row, succeeded=False, details=(str(exc),))
        except Exception as exc:
            return CollectionOutcome(
                row=row,
                succeeded=False,
                details=(f"Unexpected collection error: {exc}",),
            )


class DropPage(QWidget):
    browse_sets_requested = Signal()
    browse_folder_requested = Signal()
    history_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("launchPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.addStretch(1)

        horizontal = QHBoxLayout()
        horizontal.addStretch(1)

        self.drop_frame = QFrame()
        self.drop_frame.setObjectName("dropFrame")
        self.drop_frame.setProperty("dropActive", False)
        self.drop_frame.setMaximumWidth(680)
        self.drop_frame.setMinimumWidth(560)
        frame_layout = QVBoxLayout(self.drop_frame)
        frame_layout.setContentsMargins(48, 38, 48, 36)
        frame_layout.setSpacing(14)

        icon = QLabel()
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(QIcon(str(_asset_path("app_icon.svg"))).pixmap(QSize(68, 68)))
        icon.setAccessibleName("Collector Inspector application icon")
        frame_layout.addWidget(icon)

        eyebrow = QLabel("COLLECTOR INSPECTOR")
        eyebrow.setObjectName("heroEyebrow")
        eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frame_layout.addWidget(eyebrow)

        title = QLabel("Inspect Set dependencies")
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frame_layout.addWidget(title)

        hint = QLabel(
            "Inspect VSTs, other plug-ins, and audio dependencies in Ableton Live projects. "
            "Safely collect selected external audio into the project."
        )
        hint.setObjectName("dropHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setMaximumWidth(520)
        frame_layout.addWidget(hint)

        drop_hint = QLabel("You can also drop .als files or project folders anywhere here.")
        drop_hint.setObjectName("mutedLabel")
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frame_layout.addWidget(drop_hint)

        frame_layout.addSpacing(4)
        self.browse_folder_button = QPushButton("Choose Project Folder")
        self.browse_folder_button.setObjectName("heroPrimaryButton")
        self.browse_folder_button.setAccessibleName("Choose an Ableton Project folder")
        self.browse_folder_button.setAccessibleDescription(
            "Opens a folder picker and recursively scans the chosen folder for Ableton Sets."
        )
        self.browse_folder_button.clicked.connect(self.browse_folder_requested)
        frame_layout.addWidget(self.browse_folder_button)

        self.browse_sets_button = QPushButton("Choose Ableton Sets")
        self.browse_sets_button.setObjectName("heroSecondaryButton")
        self.browse_sets_button.setAccessibleName("Choose Ableton Set files")
        self.browse_sets_button.setAccessibleDescription(
            "Opens a file picker for one or more Ableton Live Set files."
        )
        self.browse_sets_button.clicked.connect(self.browse_sets_requested)
        frame_layout.addWidget(self.browse_sets_button)

        self.history_button = QPushButton("View Scan History")
        self.history_button.setObjectName("heroTertiaryButton")
        self.history_button.setAccessibleName("View saved scan history")
        self.history_button.setAccessibleDescription(
            "Shows previously scanned Ableton Sets, plug-ins, referenced files, and freshness warnings."
        )
        self.history_button.clicked.connect(self.history_requested)
        frame_layout.addWidget(self.history_button)

        frame_layout.addSpacing(6)
        reassurances = QHBoxLayout()
        reassurances.setSpacing(8)
        for text in ("✓  Local processing", "✓  Automatic backups", "✓  Recursive scan"):
            label = QLabel(text)
            label.setObjectName("reassuranceLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            reassurances.addWidget(label)
        frame_layout.addLayout(reassurances)

        horizontal.addWidget(self.drop_frame, 1)
        horizontal.addStretch(1)
        outer.addLayout(horizontal)
        outer.addStretch(1)

        QWidget.setTabOrder(self.browse_folder_button, self.browse_sets_button)
        QWidget.setTabOrder(self.browse_sets_button, self.history_button)

    def set_history_count(self, count: int) -> None:
        self.history_button.setText(
            f"View Scan History  ·  {count}" if count else "View Scan History"
        )
        self.history_button.setAccessibleDescription(
            f"Shows {count} saved scan snapshot{'s' if count != 1 else ''}, including plug-ins, "
            "referenced files, and freshness warnings."
        )

    def set_drop_active(self, active: bool) -> None:
        self.drop_frame.setProperty("dropActive", active)
        self.drop_frame.style().unpolish(self.drop_frame)
        self.drop_frame.style().polish(self.drop_frame)


class PluginFilterBar(QWidget):
    filters_changed = Signal()

    def __init__(self, *, context: str = "current scan") -> None:
        super().__init__()
        self.setObjectName("pluginFilterBar")
        self.search = QLineEdit()
        self.search.setObjectName("pluginSearch")
        self.search.setPlaceholderText("Search plug-ins or tracks…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName(f"Search {context} plug-ins or tracks")
        self.search.setAccessibleDescription(
            "Filters the plug-in list by plug-in name or track name."
        )
        self.type_filter = QComboBox()
        self.type_filter.setObjectName("pluginTypeFilter")
        self.type_filter.setAccessibleName(f"Filter {context} by plug-in type")
        self.type_filter.addItem("All types", "all")
        self.type_filter.addItem("VST3", PluginFormat.VST3.value)
        self.type_filter.addItem("Audio Unit", PluginFormat.AUDIO_UNIT.value)
        self.type_filter.addItem("VST2", PluginFormat.VST2.value)
        self.type_filter.addItem("Unknown", PluginFormat.UNKNOWN.value)
        self.results_label = QLabel("0 plug-ins")
        self.results_label.setObjectName("pluginResultsLabel")
        self.results_label.setAccessibleName("Visible plug-in count")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(7)
        layout.addWidget(self.search, 1)
        layout.addWidget(self.type_filter)
        layout.addWidget(self.results_label)

        self.search.textChanged.connect(lambda _text: self.filters_changed.emit())
        self.type_filter.currentIndexChanged.connect(lambda _index: self.filters_changed.emit())
        self.set_filters_enabled(False)

    @property
    def query(self) -> str:
        return self.search.text().strip()

    @property
    def format_value(self) -> str | None:
        value = self.type_filter.currentData()
        return None if value == "all" else str(value)

    @property
    def filters_active(self) -> bool:
        return bool(self.query or self.format_value)

    def set_filters_enabled(self, enabled: bool) -> None:
        self.search.setEnabled(enabled)
        self.type_filter.setEnabled(enabled)

    def set_result_count(self, visible: int, total: int) -> None:
        if self.filters_active:
            self.results_label.setText(f"{visible} of {total}")
        else:
            self.results_label.setText(f"{total} plug-in{'s' if total != 1 else ''}")


class HistoryPage(QWidget):
    home_requested = Signal()
    rescan_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("historyPage")
        self._entries: list[HistoryEntry] = []
        self._focused_entry: HistoryEntry | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top_bar = QWidget()
        top_bar.setObjectName("topBar")
        top_bar.setFixedHeight(56)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 8, 14, 8)
        top_layout.setSpacing(10)
        brand = QLabel("ABLETON  INSPECTOR")
        brand.setObjectName("brandLabel")
        title = QLabel("SCAN HISTORY")
        title.setObjectName("historyTitle")
        self.history_count = QLabel("0 snapshots")
        self.history_count.setObjectName("mutedLabel")
        self.rescan_button = QPushButton("Rescan Selected")
        self.rescan_button.setObjectName("primaryButton")
        self.rescan_button.setEnabled(False)
        self.rescan_button.setAccessibleName("Rescan selected history project")
        self.rescan_button.clicked.connect(self._rescan_selected)
        self.home_button = QPushButton("Home")
        self.home_button.setObjectName("navButton")
        self.home_button.setAccessibleName("Return to the home page")
        self.home_button.clicked.connect(self.home_requested)
        top_layout.addWidget(brand)
        top_layout.addSpacing(8)
        top_layout.addWidget(title)
        top_layout.addWidget(self.history_count)
        top_layout.addStretch(1)
        top_layout.addWidget(self.rescan_button)
        top_layout.addWidget(self.home_button)
        root.addWidget(top_bar)

        self.warning_banner = QLabel("")
        self.warning_banner.setObjectName("historyWarning")
        self.warning_banner.setWordWrap(True)
        self.warning_banner.setAccessibleName("Stale project warning")
        self.warning_banner.hide()
        root.addWidget(self.warning_banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("historySplitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)

        self.history_list = QListWidget()
        self.history_list.setObjectName("historyList")
        self.history_list.setMinimumWidth(310)
        self.history_list.setAlternatingRowColors(True)
        self.history_list.setWordWrap(True)
        self.history_list.setAccessibleName("Saved scan snapshots")
        self.history_list.setAccessibleDescription(
            "Select a previous Ableton Set scan to view its plug-ins and referenced files."
        )
        self.history_list.currentRowChanged.connect(self._current_changed)

        self.history_inspector = QTextBrowser()
        self.history_inspector.setObjectName("historyInspector")
        self.history_inspector.setOpenExternalLinks(False)
        self.history_inspector.setAccessibleName("Scan snapshot details")
        self.history_inspector.setHtml(
            _empty_inspector("No scan history yet. Scan a Set to create the first snapshot.")
        )
        self.plugin_filter_bar = PluginFilterBar(context="history")
        self.plugin_search = self.plugin_filter_bar.search
        self.plugin_type_filter = self.plugin_filter_bar.type_filter
        self.plugin_results_label = self.plugin_filter_bar.results_label
        self.plugin_filter_bar.filters_changed.connect(self._refresh_history_inspector)
        details_panel = QWidget()
        details_layout = QVBoxLayout(details_panel)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(0)
        details_layout.addWidget(self.plugin_filter_bar)
        details_layout.addWidget(self.history_inspector, 1)

        splitter.addWidget(self.history_list)
        splitter.addWidget(details_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes((390, 650))

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 16)
        body_layout.addWidget(splitter)
        root.addWidget(body, 1)

        QWidget.setTabOrder(self.history_list, self.plugin_search)
        QWidget.setTabOrder(self.plugin_search, self.plugin_type_filter)
        QWidget.setTabOrder(self.plugin_type_filter, self.rescan_button)
        QWidget.setTabOrder(self.rescan_button, self.home_button)

    def set_entries(self, entries: list[HistoryEntry]) -> None:
        self._entries = entries
        self._focused_entry = None
        self.history_list.clear()
        self.history_count.setText(f"{len(entries)} snapshot{'s' if len(entries) != 1 else ''}")
        warning_count = 0
        seen_projects: set[str] = set()
        for entry in entries:
            freshness = entry.freshness()
            is_latest = entry.set_file not in seen_projects
            seen_projects.add(entry.set_file)
            if is_latest and freshness.warning:
                warning_count += 1
            symbol = "⚠" if freshness.warning else "✓"
            latest_label = "  ·  Latest" if is_latest else ""
            item = QListWidgetItem(
                f"{symbol}  {Path(entry.set_file).name}\n"
                f"{_friendly_timestamp(entry.scanned_at)}  ·  {Path(entry.project_root).name}"
                f"{latest_label}"
            )
            item.setToolTip(entry.set_file)
            item.setData(
                Qt.ItemDataRole.AccessibleTextRole,
                f"{'May be stale' if freshness.warning else 'Current'}: "
                f"{Path(entry.set_file).name}, scanned {_friendly_timestamp(entry.scanned_at)}",
            )
            item.setSizeHint(QSize(280, 54))
            self.history_list.addItem(item)

        if warning_count:
            self.warning_banner.setText(
                f"⚠  {warning_count} project{'s may' if warning_count != 1 else ' may'} be stale "
                "because the Set changed, moved, or cannot be verified since its latest scan. "
                "Rescan it before relying on the saved inventory."
            )
            self.warning_banner.show()
        else:
            self.warning_banner.hide()

        if entries:
            self.history_list.setCurrentRow(0)
            self.history_list.setFocus()
        else:
            self.rescan_button.setEnabled(False)
            self.plugin_filter_bar.set_filters_enabled(False)
            self.plugin_results_label.setText("0 plug-ins")
            self.history_inspector.setHtml(
                _empty_inspector("No scan history yet. Scan a Set to create the first snapshot.")
            )

    def set_error(self, message: str) -> None:
        self._entries = []
        self._focused_entry = None
        self.history_list.clear()
        self.history_count.setText("History unavailable")
        self.warning_banner.setText(f"⚠  {message}")
        self.warning_banner.show()
        self.rescan_button.setEnabled(False)
        self.plugin_filter_bar.set_filters_enabled(False)
        self.plugin_results_label.setText("Unavailable")
        self.history_inspector.setHtml(_empty_inspector(message))

    @Slot(int)
    def _current_changed(self, row: int) -> None:
        if not 0 <= row < len(self._entries):
            self._focused_entry = None
            self.rescan_button.setEnabled(False)
            self.plugin_filter_bar.set_filters_enabled(False)
            return
        entry = self._entries[row]
        self._focused_entry = entry
        self.plugin_filter_bar.set_filters_enabled(bool(entry.plugins))
        self._refresh_history_inspector()
        exists = Path(entry.set_file).is_file()
        self.rescan_button.setEnabled(exists)
        self.rescan_button.setToolTip(
            "Scan this Ableton Set again and save a fresh snapshot."
            if exists
            else "This Set is no longer at its scanned location."
        )

    @Slot()
    def _refresh_history_inspector(self) -> None:
        entry = self._focused_entry
        if entry is None:
            return
        plugins = _filtered_history_plugins(
            entry,
            self.plugin_filter_bar.query,
            self.plugin_filter_bar.format_value,
        )
        self.history_inspector.setHtml(
            _history_entry_inspector(
                entry,
                plugins,
                filters_active=self.plugin_filter_bar.filters_active,
            )
        )
        self.plugin_filter_bar.set_result_count(len(plugins), len(entry.plugins))
        self.history_inspector.verticalScrollBar().setValue(0)

    @Slot()
    def _rescan_selected(self) -> None:
        row = self.history_list.currentRow()
        if not 0 <= row < len(self._entries):
            return
        set_file = Path(self._entries[row].set_file)
        if set_file.is_file():
            self.rescan_requested.emit([set_file])


class ElidedPathLabel(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self._full_text = ""

    def set_path_text(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._update_elision()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._update_elision()

    def _update_elision(self) -> None:
        if not self._full_text:
            self.setText("")
            return
        width = max(40, self.contentsRect().width())
        self.setText(
            self.fontMetrics().elidedText(
                self._full_text,
                Qt.TextElideMode.ElideMiddle,
                width,
            )
        )


class MetricCell(QFrame):
    def __init__(self, label: str, *, animations_enabled: bool = True) -> None:
        super().__init__()
        self.setObjectName("metricCell")
        self._animations_enabled = animations_enabled
        self._numeric_value: int | None = None
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(ANIMATION_DURATIONS["metric_count"])
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._animation_value_changed)
        self.animation.finished.connect(self._animation_finished)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 9)
        layout.setSpacing(1)
        self.value_label = QLabel("—")
        self.value_label.setObjectName("metricValue")
        name_label = QLabel(label)
        name_label.setObjectName("metricName")
        self.setAccessibleName(label.title())
        layout.addWidget(self.value_label)
        layout.addWidget(name_label)

    def set_value(self, value: int | str, color: str | None = None) -> None:
        self.value_label.setStyleSheet(f"color: {color};" if color else "")
        if not isinstance(value, int):
            self.animation.stop()
            self.value_label.setText(str(value))
            return

        previous = self._numeric_value
        self._numeric_value = value
        self.setAccessibleDescription(f"{value} {self.accessibleName().lower()}")
        if not self._animations_enabled or previous is None or previous == value:
            self.animation.stop()
            self.value_label.setText(str(value))
            return

        self.animation.stop()
        self.animation.setStartValue(previous)
        self.animation.setEndValue(value)
        self.animation.start()

    def set_placeholder(self) -> None:
        self.animation.stop()
        self.value_label.setText("—")

    @Slot(object)
    def _animation_value_changed(self, value: object) -> None:
        self.value_label.setText(str(round(float(value))))

    @Slot()
    def _animation_finished(self) -> None:
        if self._numeric_value is not None:
            self.value_label.setText(str(self._numeric_value))


class StatusPillDelegate(QStyledItemDelegate):
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        if option.widget:
            option.widget.style().drawPrimitive(
                QStyle.PrimitiveElement.PE_PanelItemViewItem,
                option,
                painter,
                option.widget,
            )

        label = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        color = QColor(str(index.data(STATUS_COLOR_ROLE) or COLORS["divider"]))
        metrics = option.fontMetrics
        pill = QRect(0, 0, max(58, metrics.horizontalAdvance(label) + 18), 22)
        pill.moveCenter(option.rect.center())

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(pill, 11, 11)
        font = option.font
        font.setBold(True)
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1.0))
        painter.setFont(font)
        painter.setPen(QColor(COLORS["amber_text"]))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        hint = super().sizeHint(option, index)
        return QSize(max(84, hint.width()), max(38, hint.height()))


class TableState(QWidget):
    action_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("tableState")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(8)
        layout.addStretch(1)
        self.symbol = QLabel("◎")
        self.symbol.setObjectName("stateSymbol")
        self.symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel("")
        self.title.setObjectName("stateTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body = QLabel("")
        self.body.setObjectName("stateBody")
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.setWordWrap(True)
        self.action = QPushButton("")
        self.action.clicked.connect(self.action_requested)
        action_row = QHBoxLayout()
        action_row.addStretch(1)
        action_row.addWidget(self.action)
        action_row.addStretch(1)
        layout.addWidget(self.symbol)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        layout.addSpacing(6)
        layout.addLayout(action_row)
        layout.addStretch(1)

    def configure(self, symbol: str, title: str, body: str, action: str | None = None) -> None:
        self.symbol.setText(symbol)
        self.title.setText(title)
        self.body.setText(body)
        self.action.setText(action or "")
        self.action.setVisible(bool(action))


class ReviewTableModel(QAbstractTableModel):
    selection_toggled = Signal(str, bool)

    HEADERS = ("", "", "SET", "STATUS", "EXTERNAL", "MISSING", "PROJECT")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.rows: tuple[ReviewRow, ...] = ()
        self.selected_paths: set[Path] = set()

    def set_rows(self, rows: list[ReviewRow], selected_paths: set[Path]) -> None:
        self.beginResetModel()
        self.rows = tuple(rows)
        self.selected_paths = set(selected_paths)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        column = index.column()
        path_text = str(row.set_file)

        if role == Qt.ItemDataRole.DisplayRole:
            return {
                0: "",
                1: "" if row.selectable else "·",
                2: row.set_file.name,
                3: STATUS_LABELS.get(row.status, row.status.upper()),
                4: str(row.set_report.external_collectable_count),
                5: str(row.set_report.missing_count),
                6: row.set_report.project_root.name,
            }.get(column)
        if role == Qt.ItemDataRole.UserRole and column > 0:
            return path_text
        if role == STATUS_ROLE and column == 3:
            return row.status
        if role == STATUS_COLOR_ROLE and column == 3:
            return STATUS_COLORS.get(row.status, COLORS["divider"])
        if role == Qt.ItemDataRole.CheckStateRole and column == 1 and row.selectable:
            return (
                Qt.CheckState.Checked
                if row.set_file in self.selected_paths
                else Qt.CheckState.Unchecked
            )
        if role == Qt.ItemDataRole.BackgroundRole and column == 0:
            return QColor(STATUS_COLORS.get(row.status, COLORS["divider"]))
        if role == Qt.ItemDataRole.ForegroundRole and column == 3:
            return QColor(COLORS["text"])
        if role == Qt.ItemDataRole.TextAlignmentRole and column in {1, 4, 5}:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.ToolTipRole:
            if column == 2:
                return path_text
            if column == 3:
                return f"Status: {STATUS_LABELS.get(row.status, row.status.upper())}"
            if column == 6:
                return str(row.set_report.project_root)
        if role == Qt.ItemDataRole.AccessibleTextRole:
            if column == 1:
                if not row.selectable:
                    return "Not available for collection"
                return (
                    "Selected for collection"
                    if row.set_file in self.selected_paths
                    else "Not selected for collection"
                )
            value = self.data(index, Qt.ItemDataRole.DisplayRole)
            if value is not None and column < len(self.HEADERS):
                header = self.HEADERS[column] or "Selection"
                return f"{header.title()}: {value}"
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        row = self.rows[index.row()]
        if index.column() == 0:
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if index.column() == 1 and row.selectable:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(
        self,
        index: QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not index.isValid() or index.column() != 1 or role != Qt.ItemDataRole.CheckStateRole:
            return False
        row = self.rows[index.row()]
        if not row.selectable:
            return False
        checked = value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value
        if checked:
            self.selected_paths.add(row.set_file)
        else:
            self.selected_paths.discard(row.set_file)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        self.selection_toggled.emit(str(row.set_file), checked)
        return True


class ReviewTableView(QTableView):
    def rowCount(self) -> int:
        model = self.model()
        return model.rowCount() if model else 0


class WorkspacePage(QWidget):
    change_source_requested = Signal()
    history_requested = Signal()
    rescan_requested = Signal()
    filter_requested = Signal(str)
    row_toggled = Signal(str, bool)
    row_focused = Signal(str)
    select_ready_requested = Signal()
    clear_requested = Signal()
    collect_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("workspacePage")
        self._rows_by_path: dict[str, ReviewRow] = {}
        self._rows: list[ReviewRow] = []
        self._focused_row: ReviewRow | None = None
        self._active_filter = "all"
        self._state_action_mode = "change"
        self.animations_enabled = _widget_animations_enabled(self)
        self.issue_animation = QPropertyAnimation(self)
        self.issue_animation.setPropertyName(b"maximumHeight")
        self.issue_animation.setDuration(ANIMATION_DURATIONS["issue_banner"])
        self.issue_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top_bar = QWidget()
        top_bar.setObjectName("topBar")
        top_bar.setFixedHeight(56)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 8, 14, 8)
        top_layout.setSpacing(10)
        brand = QLabel("ABLETON  INSPECTOR")
        brand.setObjectName("brandLabel")
        self.source_label = ElidedPathLabel()
        self.source_label.setObjectName("sourceLabel")
        self.source_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.source_label.setAccessibleName("Current scan source")
        self.scan_state = QLabel("READY TO SCAN")
        self.scan_state.setObjectName("scanState")
        self.scan_state.setAccessibleName("Scan status")
        self.scan_progress = QProgressBar()
        self.scan_progress.setObjectName("scanProgress")
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setAccessibleName("Scanning Ableton Sets")
        self.scan_progress.setAccessibleDescription(
            "Indeterminate progress indicator shown while audio references are scanned."
        )
        self.scan_progress.hide()
        self.change_button = QPushButton("Home")
        self.change_button.setObjectName("navButton")
        self.history_button = QPushButton("History")
        self.history_button.setObjectName("navButton")
        self.rescan_button = QPushButton("Rescan")
        self.change_button.setAccessibleName("Return to the home page")
        self.history_button.setAccessibleName("View saved scan history")
        self.rescan_button.setAccessibleName("Rescan current source")
        self.change_button.clicked.connect(self.change_source_requested)
        self.history_button.clicked.connect(self.history_requested)
        self.rescan_button.clicked.connect(self.rescan_requested)
        top_layout.addWidget(brand)
        top_layout.addSpacing(4)
        top_layout.addWidget(self.source_label)
        top_layout.addWidget(self.scan_progress)
        top_layout.addWidget(self.scan_state)
        top_layout.addWidget(self.change_button)
        top_layout.addWidget(self.history_button)
        top_layout.addWidget(self.rescan_button)
        root.addWidget(top_bar)

        metric_strip = QWidget()
        metric_strip.setObjectName("metricStrip")
        metric_strip.setFixedHeight(92)
        metric_layout = QHBoxLayout(metric_strip)
        metric_layout.setContentsMargins(16, 12, 16, 8)
        metric_layout.setSpacing(10)
        self.metrics = {
            "total": MetricCell("TOTAL SETS", animations_enabled=self.animations_enabled),
            "ready": MetricCell("READY", animations_enabled=self.animations_enabled),
            "safe": MetricCell("SAFE", animations_enabled=self.animations_enabled),
            "issues": MetricCell("ISSUES", animations_enabled=self.animations_enabled),
        }
        for cell in self.metrics.values():
            metric_layout.addWidget(cell, 1)
        root.addWidget(metric_strip)

        filter_strip = QWidget()
        filter_strip.setObjectName("filterStrip")
        filter_strip.setFixedHeight(48)
        filter_layout = QHBoxLayout(filter_strip)
        filter_layout.setContentsMargins(16, 5, 16, 9)
        filter_layout.setSpacing(7)
        self.filter_group = QButtonGroup(self)
        self.filter_group.setExclusive(True)
        self.filter_buttons: dict[str, QPushButton] = {}
        for filter_name in FILTERS:
            button = QPushButton(f"{filter_name.title()}  0")
            button.setObjectName("filterButton")
            button.setCheckable(True)
            button.setAccessibleName(f"Show {filter_name} Sets")
            button.clicked.connect(
                lambda checked=False, name=filter_name: self.filter_requested.emit(name)
            )
            self.filter_group.addButton(button)
            self.filter_buttons[filter_name] = button
            filter_layout.addWidget(button)
        self.filter_buttons["all"].setChecked(True)
        filter_layout.addStretch(1)
        root.addWidget(filter_strip)

        self.issue_banner_frame = QFrame()
        self.issue_banner_frame.setObjectName("issueBanner")
        self.issue_banner_frame.setMaximumHeight(0)
        banner_layout = QHBoxLayout(self.issue_banner_frame)
        banner_layout.setContentsMargins(14, 9, 14, 9)
        banner_layout.setSpacing(8)
        banner_symbol = QLabel("!")
        banner_symbol.setStyleSheet(f"color: {COLORS['pink']}; font-weight: 800;")
        self.issue_banner = QLabel("")
        self.issue_banner.setObjectName("issueBannerText")
        self.issue_banner.setWordWrap(True)
        self.issue_banner.setAccessibleName("Scan issue summary")
        banner_layout.addWidget(banner_symbol, 0, Qt.AlignmentFlag.AlignTop)
        banner_layout.addWidget(self.issue_banner, 1)
        self.issue_animation.setTargetObject(self.issue_banner_frame)
        root.addWidget(self.issue_banner_frame)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("contentSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)

        self.table_card = QFrame()
        self.table_card.setObjectName("tableCard")
        self.table_card.setMinimumWidth(430)
        table_card_layout = QVBoxLayout(self.table_card)
        table_card_layout.setContentsMargins(0, 0, 0, 0)
        table_card_layout.setSpacing(0)
        self.table_stack = QStackedWidget()
        self.table_model = ReviewTableModel(self)
        self.table_model.selection_toggled.connect(self.row_toggled)
        self.table = ReviewTableView()
        self.table.setObjectName("reviewTable")
        self.table.setModel(self.table_model)
        self.table.setItemDelegateForColumn(3, StatusPillDelegate(self.table))
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.setAccessibleName("Ableton Set review table")
        self.table.setAccessibleDescription(
            "Review scan status, missing audio, and collection readiness for each Ableton Set."
        )
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.verticalHeader().setMinimumSectionSize(38)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 7)
        self.table.setColumnWidth(1, 34)
        self.table.setColumnWidth(3, 94)
        self.table.setColumnWidth(4, 72)
        self.table.setColumnWidth(5, 64)
        self.table.selectionModel().currentChanged.connect(self._current_changed)

        self.table_state = TableState()
        self.table_state.action_requested.connect(self._state_action_requested)
        self.table_stack.addWidget(self.table)
        self.table_stack.addWidget(self.table_state)
        table_card_layout.addWidget(self.table_stack)

        self.inspector_card = QFrame()
        self.inspector_card.setObjectName("inspectorCard")
        self.inspector_card.setMinimumWidth(270)
        inspector_layout = QVBoxLayout(self.inspector_card)
        inspector_layout.setContentsMargins(12, 10, 12, 10)
        inspector_layout.setSpacing(4)
        inspector_heading = QLabel("SET INSPECTOR")
        inspector_heading.setObjectName("panelTitle")
        self.inspector = QTextBrowser()
        self.inspector.setObjectName("inspectorBrowser")
        self.inspector.setOpenExternalLinks(False)
        self.inspector.setAccessibleName("Set details inspector")
        self.inspector.setAccessibleDescription(
            "Shows project paths, blockers, and media collection details for the focused Set."
        )
        self.inspector.setHtml(_empty_inspector("Select a Set to inspect its audio references."))
        self.plugins_inspector = QTextBrowser()
        self.plugins_inspector.setObjectName("pluginsInspectorBrowser")
        self.plugins_inspector.setOpenExternalLinks(False)
        self.plugins_inspector.setAccessibleName("Plug-in format inspector")
        self.plugins_inspector.setAccessibleDescription(
            "Lists every AU, VST2, VST3, or unknown third-party plug-in grouped by track."
        )
        self.plugins_inspector.setHtml(
            _empty_inspector("Select a Set to inspect its plug-in formats by track.")
        )
        self.plugin_filter_bar = PluginFilterBar()
        self.plugin_search = self.plugin_filter_bar.search
        self.plugin_type_filter = self.plugin_filter_bar.type_filter
        self.plugin_results_label = self.plugin_filter_bar.results_label
        self.plugin_filter_bar.filters_changed.connect(self._refresh_plugin_inspector)
        plugin_page = QWidget()
        plugin_page.setObjectName("pluginPage")
        plugin_page_layout = QVBoxLayout(plugin_page)
        plugin_page_layout.setContentsMargins(0, 0, 0, 0)
        plugin_page_layout.setSpacing(0)
        plugin_page_layout.addWidget(self.plugin_filter_bar)
        plugin_page_layout.addWidget(self.plugins_inspector, 1)
        self._set_plugin_filters_enabled(False)
        self.inspector_tabs = QTabWidget()
        self.inspector_tabs.setObjectName("inspectorTabs")
        self.inspector_tabs.setAccessibleName("Set inspector views")
        self.inspector_tabs.addTab(self.inspector, "Overview")
        self.inspector_tabs.addTab(plugin_page, "Plug-ins")
        inspector_layout.addWidget(inspector_heading)
        inspector_layout.addWidget(self.inspector_tabs, 1)

        splitter.addWidget(self.table_card)
        splitter.addWidget(self.inspector_card)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes((650, 430))

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(16, 0, 16, 12)
        body_layout.setSpacing(0)
        body_layout.addWidget(splitter)
        root.addWidget(body, 1)

        action_strip = QWidget()
        action_strip.setObjectName("actionStrip")
        action_strip.setFixedHeight(64)
        action_layout = QHBoxLayout(action_strip)
        action_layout.setContentsMargins(16, 10, 14, 10)
        action_layout.setSpacing(8)
        self.selection_summary = QLabel("No Sets selected")
        self.selection_summary.setObjectName("selectionSummary")
        self.selection_summary.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.select_ready_button = QPushButton("Select Ready")
        self.clear_button = QPushButton("Clear")
        self.collect_button = QPushButton("Collect Selected")
        self.collect_button.setObjectName("primaryButton")
        self.select_ready_button.setAccessibleName("Select all Ready Sets in the active filter")
        self.clear_button.setAccessibleName("Clear selected Sets")
        self.collect_button.setAccessibleName("Collect audio for selected Ready Sets")
        self.select_ready_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.collect_button.setEnabled(False)
        self.collect_button.setToolTip(
            "Collection is unavailable until at least one Ready Set is selected."
        )
        self.select_ready_button.clicked.connect(self.select_ready_requested)
        self.clear_button.clicked.connect(self.clear_requested)
        self.collect_button.clicked.connect(self.collect_requested)
        action_layout.addWidget(self.selection_summary)
        action_layout.addWidget(self.select_ready_button)
        action_layout.addWidget(self.clear_button)
        action_layout.addWidget(self.collect_button)
        root.addWidget(action_strip)

        QWidget.setTabOrder(self.change_button, self.history_button)
        QWidget.setTabOrder(self.history_button, self.rescan_button)
        QWidget.setTabOrder(self.rescan_button, self.filter_buttons["all"])
        for current, following in zip(FILTERS[:-1], FILTERS[1:], strict=True):
            QWidget.setTabOrder(self.filter_buttons[current], self.filter_buttons[following])
        QWidget.setTabOrder(self.filter_buttons["errors"], self.table)
        QWidget.setTabOrder(self.table, self.inspector_tabs)
        QWidget.setTabOrder(self.inspector_tabs, self.plugin_search)
        QWidget.setTabOrder(self.plugin_search, self.plugin_type_filter)
        QWidget.setTabOrder(self.plugin_type_filter, self.select_ready_button)
        QWidget.setTabOrder(self.select_ready_button, self.clear_button)
        QWidget.setTabOrder(self.clear_button, self.collect_button)

    def set_source_text(self, sources: tuple[Path, ...]) -> None:
        if len(sources) == 1:
            text = str(sources[0])
        else:
            text = f"{len(sources)} dropped sources"
        self.source_label.set_path_text(text)
        self.source_label.setToolTip("\n".join(str(source) for source in sources))
        self.source_label.setAccessibleDescription(self.source_label.toolTip())

    def set_loading(self) -> None:
        self._rows = []
        self._focused_row = None
        self.scan_state.setText("●  SCANNING")
        self.scan_state.setStyleSheet(f"color: {COLORS['amber']};")
        self.scan_progress.show()
        self.rescan_button.setEnabled(False)
        self.change_button.setEnabled(True)
        self.table_model.set_rows([], set())
        self.table.setEnabled(False)
        self.table_state.configure(
            "◌",
            "Scanning projects…",
            "Checking every Set for external, missing, and already collected audio.",
        )
        self.table_stack.setCurrentWidget(self.table_state)
        self.inspector.setHtml(_empty_inspector("Scanning audio references…"))
        self.plugins_inspector.setHtml(_empty_inspector("Scanning plug-in formats…"))
        self._set_plugin_filters_enabled(False)
        self.plugin_results_label.setText("Scanning…")
        self.inspector_tabs.setTabText(1, "Plug-ins")
        self._hide_issue_banner()
        self.selection_summary.setText("Scanning Sets…")
        self.select_ready_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.collect_button.setEnabled(False)
        for metric in self.metrics.values():
            metric.set_placeholder()
        self._update_filter_counts([])

    def set_failure(self, message: str) -> None:
        self._focused_row = None
        self.scan_state.setText("●  SCAN FAILED")
        self.scan_state.setStyleSheet(f"color: {COLORS['error']};")
        self.scan_progress.hide()
        self.rescan_button.setEnabled(True)
        self.table.setEnabled(False)
        self._state_action_mode = "rescan"
        self.table_state.configure(
            "!",
            "The scan could not finish",
            f"{message}\n\nCheck that the source is still available, then try again.",
            "Try Again",
        )
        self.table_stack.setCurrentWidget(self.table_state)
        self.inspector.setHtml(_empty_inspector(f"Scan failed: {message}"))
        self.plugins_inspector.setHtml(_empty_inspector(f"Scan failed: {message}"))
        self._set_plugin_filters_enabled(False)
        self.plugin_results_label.setText("Unavailable")
        self.inspector_tabs.setTabText(1, "Plug-ins")
        self._show_issue_banner(f"Scan error: {message}")
        self.selection_summary.setText("Scan failed — review the error and try again")
        self.select_ready_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.collect_button.setEnabled(False)
        self.collect_button.setToolTip("Collection is unavailable until the scan succeeds.")

    def set_batch(
        self,
        batch: ReviewBatch,
        selected_paths: set[Path],
        active_filter: str,
    ) -> None:
        self.scan_state.setText("●  SCAN COMPLETE")
        self.scan_state.setStyleSheet(f"color: {COLORS['green']};")
        self.scan_progress.hide()
        self.rescan_button.setEnabled(True)
        self.table.setEnabled(True)
        rows = batch.rows
        self._rows = rows
        self._update_filter_counts(rows)
        self.metrics["total"].set_value(len(rows))
        self.metrics["ready"].set_value(
            sum(row.selectable for row in rows), STATUS_COLORS["collectable"]
        )
        self.metrics["safe"].set_value(
            sum(row.status == "safe" for row in rows), STATUS_COLORS["safe"]
        )
        self.metrics["issues"].set_value(
            sum(row.status not in {"collectable", "safe"} for row in rows),
            STATUS_COLORS["blocked"],
        )
        self._focused_row = None
        if batch.issues:
            lines = [f"{issue.path.name}: {issue.message}" for issue in batch.issues]
            self._show_issue_banner("  •  ".join(lines))
        else:
            self._hide_issue_banner()
        self.set_filter(active_filter)
        self.populate_rows(rows, selected_paths, active_filter)

    def set_filter(self, active_filter: str) -> None:
        button = self.filter_buttons.get(active_filter)
        if button:
            button.setChecked(True)

    def populate_rows(
        self,
        rows: list[ReviewRow],
        selected_paths: set[Path],
        active_filter: str,
    ) -> None:
        self._active_filter = active_filter
        visible_rows = [row for row in rows if _matches_filter(row, active_filter)]
        ready_in_filter = sum(row.selectable for row in visible_rows)
        self.select_ready_button.setEnabled(ready_in_filter > 0)
        self.select_ready_button.setToolTip(
            f"Select {ready_in_filter} Ready Set"
            f"{'s' if ready_in_filter != 1 else ''} in this filter."
            if ready_in_filter
            else "No Ready Sets are available in this filter."
        )
        self._rows_by_path = {str(row.set_file): row for row in visible_rows}
        self.table_model.set_rows(visible_rows, selected_paths)
        if visible_rows:
            self.table_stack.setCurrentWidget(self.table)
            self.table.setCurrentIndex(self.table_model.index(0, 2))
            self.row_focused.emit(str(visible_rows[0].set_file))
        else:
            self._state_action_mode = "change"
            if rows:
                self.table_state.configure(
                    "◇",
                    "No Sets in this view",
                    "Try another filter to see the Sets found in this scan.",
                )
            else:
                self.table_state.configure(
                    "＋",
                    "No Ableton Sets found",
                    "Choose a project folder containing .als files, or select Ableton Sets directly.",
                    "Choose Another Source",
                )
            self.table_stack.setCurrentWidget(self.table_state)
            self.inspector.setHtml(
                _empty_inspector(
                    "No Sets match this filter. Choose another filter to continue."
                    if rows
                    else "No Ableton Sets were found. Choose another source and try again."
                )
            )
            self.plugins_inspector.setHtml(
                _empty_inspector(
                    "No Sets match this filter. Choose another filter to continue."
                    if rows
                    else "No Ableton Sets were found. Choose another source and try again."
                )
            )
            self._set_plugin_filters_enabled(False)
            self.plugin_results_label.setText("0 plug-ins")
            self.inspector_tabs.setTabText(1, "Plug-ins")

    def _update_filter_counts(self, rows: list[ReviewRow]) -> None:
        counts = {
            "all": len(rows),
            "ready": sum(row.selectable for row in rows),
            "safe": sum(row.status == "safe" for row in rows),
            "blocked": sum(row.status in {"blocked", "blocked_missing"} for row in rows),
            "errors": sum(row.status == "error" for row in rows),
        }
        for name, count in counts.items():
            self.filter_buttons[name].setText(f"{name.title()}  {count}")
            self.filter_buttons[name].setAccessibleDescription(
                f"{count} Set{'s' if count != 1 else ''} in this filter."
            )

    def _show_issue_banner(self, text: str) -> None:
        self.issue_banner.setText(text)
        target_height = max(42, self.issue_banner_frame.sizeHint().height())
        self.issue_banner_frame.show()
        self.issue_animation.stop()
        if not self.animations_enabled:
            self.issue_banner_frame.setMaximumHeight(target_height)
            return
        self.issue_animation.setStartValue(self.issue_banner_frame.maximumHeight())
        self.issue_animation.setEndValue(target_height)
        self.issue_animation.start()

    def _hide_issue_banner(self) -> None:
        self.issue_animation.stop()
        self.issue_banner_frame.setMaximumHeight(0)
        self.issue_banner_frame.hide()

    def _state_action_requested(self) -> None:
        if self._state_action_mode == "rescan":
            self.rescan_requested.emit()
        else:
            self.change_source_requested.emit()

    def update_selection_summary(self, selected_rows: list[ReviewRow]) -> None:
        media_count = sum(len(row.plan.items) for row in selected_rows if row.plan)
        reference_count = sum(
            len(item.references) for row in selected_rows if row.plan for item in row.plan.items
        )
        if selected_rows:
            self.selection_summary.setText(
                f"{len(selected_rows)} Set{'s' if len(selected_rows) != 1 else ''} selected"
                f"   ·   {media_count} media   ·   {reference_count} references"
            )
        else:
            ready_count = sum(row.selectable for row in self._rows)
            if ready_count:
                self.selection_summary.setText(
                    f"{ready_count} Ready Set{'s' if ready_count != 1 else ''}"
                    " — select one to enable collection"
                )
            elif self._rows:
                self.selection_summary.setText(
                    "No Ready Sets — select an issue to review its blockers"
                )
            else:
                self.selection_summary.setText("No Ableton Sets found")
        self.clear_button.setEnabled(bool(selected_rows))
        self.collect_button.setEnabled(bool(selected_rows))
        self.collect_button.setToolTip(
            "Back up each selected Set, collect its external audio, and validate the result."
            if selected_rows
            else (
                "Select at least one Ready Set before collecting."
                if any(row.selectable for row in self._rows)
                else "Collection is unavailable because no Sets are Ready."
            )
        )

    def show_row(self, row: ReviewRow, notice: str | None = None) -> None:
        self._focused_row = row
        self.inspector.setHtml(_row_inspector(row, notice))
        self._set_plugin_filters_enabled(row.set_report.total_plugin_instances > 0)
        self._refresh_plugin_inspector()
        self.inspector.verticalScrollBar().setValue(0)
        self.plugins_inspector.verticalScrollBar().setValue(0)

    def _set_plugin_filters_enabled(self, enabled: bool) -> None:
        self.plugin_filter_bar.set_filters_enabled(enabled)

    @Slot()
    def _refresh_plugin_inspector(self) -> None:
        row = self._focused_row
        if row is None:
            return
        format_value = self.plugin_filter_bar.format_value
        plugin_format = PluginFormat(format_value) if format_value else None
        plugins = _filtered_plugins(row, self.plugin_filter_bar.query, plugin_format)
        total = row.set_report.total_plugin_instances
        filters_active = self.plugin_filter_bar.filters_active
        self.plugins_inspector.setHtml(
            _plugin_inspector(row, plugins, filters_active=filters_active)
        )
        self.plugin_filter_bar.set_result_count(len(plugins), total)
        if filters_active:
            self.inspector_tabs.setTabText(1, f"Plug-ins  {len(plugins)} of {total}")
        else:
            self.inspector_tabs.setTabText(1, f"Plug-ins  {total}")
        self.plugins_inspector.verticalScrollBar().setValue(0)

    def _current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid():
            return
        path = self.table_model.data(
            self.table_model.index(current.row(), 2),
            Qt.ItemDataRole.UserRole,
        )
        if path:
            self.row_focused.emit(path)


class ConfirmCollectionDialog(QDialog):
    def __init__(self, rows: list[ReviewRow], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm Collection")
        self.setModal(True)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        set_count = len(rows)
        media_count = sum(len(row.plan.items) for row in rows if row.plan)
        reference_count = sum(
            len(item.references) for row in rows if row.plan for item in row.plan.items
        )
        title = QLabel(
            f"Collect external audio for {set_count} Set{'s' if set_count != 1 else ''}?"
        )
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        summary = QLabel(
            f"{set_count} Sets   ·   {media_count} media files   ·   {reference_count} references"
        )
        summary.setObjectName("mutedLabel")
        layout.addWidget(summary)

        sets = QTextBrowser()
        sets.setObjectName("dialogBrowser")
        sets.setAccessibleName("Selected Ableton Sets")
        sets.setMaximumHeight(180)
        sets.setHtml(
            "<br>".join(
                f"<b>{escape(row.set_file.name)}</b>"
                f"<br><span style='color:{COLORS['muted']}'>{escape(str(row.set_file))}</span>"
                for row in rows
            )
        )
        layout.addWidget(sets)
        warning_card = QFrame()
        warning_card.setObjectName("warningCard")
        warning_layout = QHBoxLayout(warning_card)
        warning_layout.setContentsMargins(12, 10, 12, 10)
        warning_layout.setSpacing(9)
        warning_symbol = QLabel("!")
        warning_symbol.setStyleSheet(f"color: {COLORS['yellow']}; font-weight: 800;")
        warning = QLabel(
            "Close the affected Sets in Ableton Live before continuing. "
            "Each Set is backed up first; collection stops if a Set fails."
        )
        warning.setObjectName("warningText")
        warning.setWordWrap(True)
        warning.setAccessibleName("Collection safety notice")
        warning.setAccessibleDescription(
            "Each Set is backed up before audio is collected, and work stops on the first failure."
        )
        warning_layout.addWidget(warning_symbol, 0, Qt.AlignmentFlag.AlignTop)
        warning_layout.addWidget(warning, 1)
        layout.addWidget(warning_card)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        collect = QPushButton("Collect Selected")
        collect.setObjectName("primaryButton")
        cancel.setAccessibleName("Cancel collection")
        collect.setAccessibleName("Confirm and collect selected Sets")
        cancel.clicked.connect(self.reject)
        collect.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(collect)
        layout.addLayout(buttons)

        QWidget.setTabOrder(sets, cancel)
        QWidget.setTabOrder(cancel, collect)


class AnimatedProgressBar(QProgressBar):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.animations_enabled = _widget_animations_enabled(self)
        self.animation = QPropertyAnimation(self, b"value", self)
        self.animation.setDuration(ANIMATION_DURATIONS["collection_progress"])
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def set_animated_value(self, value: int) -> None:
        target = max(self.minimum(), min(value, self.maximum()))
        if not self.animations_enabled:
            self.animation.stop()
            self.setValue(target)
            return
        self.animation.stop()
        self.animation.setStartValue(self.value())
        self.animation.setEndValue(target)
        self.animation.start()


class CollectionDialog(QDialog):
    def __init__(
        self,
        rows: list[ReviewRow],
        thread_pool: QThreadPool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.rows = rows
        self.thread_pool = thread_pool
        self.complete = False
        self.outcomes: list[CollectionOutcome] = []
        self._worker: CollectionWorker | None = None
        self.setWindowTitle("Collecting Audio")
        self.setModal(True)
        self.resize(680, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(10)
        self.title_label = QLabel("Preparing collection…")
        self.title_label.setObjectName("dialogTitle")
        self.current_label = QLabel("")
        self.current_label.setObjectName("dialogDetail")
        self.current_label.setWordWrap(True)
        self.progress = AnimatedProgressBar()
        self.progress.setRange(0, len(rows))
        self.progress.setValue(0)
        self.progress.setAccessibleName("Collection progress")
        self.progress.setAccessibleDescription(
            "Shows how many selected Ableton Sets have completed collection."
        )
        self.results = QTextBrowser()
        self.results.setObjectName("dialogBrowser")
        self.results.setAccessibleName("Collection results")
        self.results.setHtml(_empty_inspector("Collection results will appear here."))
        self.close_button = QPushButton("Return to Review")
        self.close_button.setObjectName("primaryButton")
        self.close_button.setAccessibleName("Return to collection review")
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self.accept)
        layout.addWidget(self.title_label)
        layout.addWidget(self.current_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.results, 1)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

    def start(self) -> None:
        self._worker = CollectionWorker(self.rows)
        self._worker.signals.row_started.connect(self._row_started)
        self._worker.signals.outcome.connect(self._outcome)
        self._worker.signals.completed.connect(self._completed)
        self.thread_pool.start(self._worker)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.complete:
            event.ignore()
            return
        event.accept()

    @Slot(int, int, str)
    def _row_started(self, index: int, total: int, path: str) -> None:
        self.title_label.setText(f"Collecting Set {index} of {total}")
        self.current_label.setText(path)

    @Slot(object)
    def _outcome(self, outcome: CollectionOutcome) -> None:
        self.outcomes.append(outcome)
        self.progress.set_animated_value(len(self.outcomes))
        self.results.append(_outcome_html(outcome))

    @Slot(object)
    def _completed(self, outcomes: list[CollectionOutcome]) -> None:
        self.complete = True
        failed = any(not outcome.succeeded for outcome in outcomes)
        succeeded = sum(outcome.succeeded for outcome in outcomes)
        self.title_label.setText("Collection stopped" if failed else "Collection complete")
        self.current_label.setText(
            f"{succeeded} of {len(self.rows)} Set{'s' if len(self.rows) != 1 else ''} completed"
        )
        self.close_button.setEnabled(True)
        self.close_button.setFocus()


class CollectorWindow(QMainWindow):
    def __init__(
        self,
        initial_sources: list[Path] | tuple[Path, ...] | None = None,
        *,
        thread_pool: QThreadPool | None = None,
        history_store: HistoryStore | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Collector Inspector")
        self.resize(1120, 720)
        self.setMinimumSize(820, 560)
        self.setAcceptDrops(True)
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self.history_store = history_store
        self.sources: tuple[Path, ...] = ()
        self.batch = ReviewBatch((), [], [])
        self.selected_paths: set[Path] = set()
        self.active_filter = "all"
        self.scan_generation = 0
        self.collection_running = False
        self._scan_worker: ScanWorker | None = None
        self.animations_enabled = _widget_animations_enabled(self)
        self.page_animation: QPropertyAnimation | None = None
        self._page_effect: QGraphicsOpacityEffect | None = None

        self.stack = QStackedWidget()
        self.drop_page = DropPage()
        self.workspace = WorkspacePage()
        self.history_page = HistoryPage()
        self.stack.addWidget(self.drop_page)
        self.stack.addWidget(self.workspace)
        self.stack.addWidget(self.history_page)
        self.setCentralWidget(self.stack)

        self.drop_page.browse_sets_requested.connect(self._browse_sets)
        self.drop_page.browse_folder_requested.connect(self._browse_folder)
        self.drop_page.history_requested.connect(self.show_history_page)
        self.workspace.change_source_requested.connect(self.show_drop_page)
        self.workspace.history_requested.connect(self.show_history_page)
        self.workspace.rescan_requested.connect(self.rescan)
        self.workspace.filter_requested.connect(self._set_filter)
        self.workspace.row_toggled.connect(self._toggle_path)
        self.workspace.row_focused.connect(self._focus_path)
        self.workspace.select_ready_requested.connect(self._select_ready)
        self.workspace.clear_requested.connect(self._clear_selection)
        self.workspace.collect_requested.connect(self._collect_selected)
        self.history_page.home_requested.connect(self.show_drop_page)
        self.history_page.rescan_requested.connect(self.scan_sources)
        self._setup_actions()
        self._refresh_history_count()

        if initial_sources:
            QTimer.singleShot(0, lambda: self.scan_sources(initial_sources))

    def _setup_actions(self) -> None:
        self.open_sets_action = QAction("Choose Ableton Sets", self)
        self.open_sets_action.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.Open))
        self.open_sets_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.open_sets_action.triggered.connect(self._browse_sets)

        self.open_folder_action = QAction("Choose Project Folder", self)
        self.open_folder_action.setShortcut(QKeySequence("Ctrl+Shift+O"))
        self.open_folder_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.open_folder_action.triggered.connect(self._browse_folder)

        self.rescan_action = QAction("Rescan", self)
        self.rescan_action.setShortcut(QKeySequence("Ctrl+R"))
        self.rescan_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.rescan_action.triggered.connect(self.rescan)

        self.home_action = QAction("Home", self)
        self.home_action.setShortcut(QKeySequence("Alt+Left"))
        self.home_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.home_action.triggered.connect(self.show_drop_page)

        self.history_action = QAction("Scan History", self)
        self.history_action.setShortcut(QKeySequence("Ctrl+Shift+H"))
        self.history_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.history_action.triggered.connect(self.show_history_page)

        self.select_ready_action = QAction("Select Ready Sets", self)
        self.select_ready_action.setShortcut(QKeySequence("Ctrl+A"))
        self.select_ready_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.select_ready_action.triggered.connect(self._select_ready)

        self.clear_selection_action = QAction("Clear Selection", self)
        self.clear_selection_action.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        self.clear_selection_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.clear_selection_action.triggered.connect(self._clear_selection)

        for action in (
            self.open_sets_action,
            self.open_folder_action,
            self.home_action,
            self.history_action,
            self.rescan_action,
            self.select_ready_action,
            self.clear_selection_action,
        ):
            self.addAction(action)
        self.rescan_action.setEnabled(False)

        self.workspace.change_button.setToolTip("Return to the home page (Alt+Left)")
        self.workspace.history_button.setToolTip("View saved scan history (Ctrl+Shift+H)")
        self.workspace.rescan_button.setToolTip("Scan the current source again (Ctrl+R)")
        self.workspace.select_ready_button.setToolTip("Select Ready Sets in this filter (Ctrl+A)")
        self.workspace.clear_button.setToolTip("Clear the selection (Escape)")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if any(url.isLocalFile() for url in urls):
            event.acceptProposedAction()
            self.drop_page.set_drop_active(True)

    def dragLeaveEvent(self, event: Any) -> None:
        self.drop_page.set_drop_active(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        self.drop_page.set_drop_active(False)
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.scan_sources(paths)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.collection_running:
            QMessageBox.warning(
                self,
                "Collection in progress",
                "Collector Inspector cannot close while project files are being changed.",
            )
            event.ignore()
            return
        event.accept()

    def show_drop_page(self) -> None:
        self.scan_generation += 1
        self.rescan_action.setEnabled(False)
        self._refresh_history_count()
        self._show_page(self.drop_page)

    def show_history_page(self) -> None:
        self.scan_generation += 1
        self.rescan_action.setEnabled(False)
        if self.history_store is None:
            self.history_page.set_entries([])
        else:
            try:
                self.history_page.set_entries(self.history_store.load())
            except HistoryStoreError as exc:
                self.history_page.set_error(str(exc))
        self._show_page(self.history_page)

    def _refresh_history_count(self) -> None:
        if self.history_store is None:
            self.drop_page.set_history_count(0)
            return
        try:
            self.drop_page.set_history_count(len(self.history_store.load()))
        except HistoryStoreError:
            self.drop_page.set_history_count(0)

    def scan_sources(self, sources: list[Path] | tuple[Path, ...]) -> None:
        normalized = tuple(Path(source).expanduser().resolve() for source in sources)
        if not normalized:
            return
        self.sources = normalized
        self.scan_generation += 1
        generation = self.scan_generation
        self.workspace.set_source_text(normalized)
        self.workspace.set_loading()
        self.rescan_action.setEnabled(False)
        self._show_page(self.workspace)
        worker = ScanWorker(generation, normalized)
        worker.signals.completed.connect(self._scan_completed)
        worker.signals.failed.connect(self._scan_failed)
        self._scan_worker = worker
        self.thread_pool.start(worker)

    def _show_page(self, page: QWidget) -> None:
        changed = self.stack.currentWidget() is not page
        self.stack.setCurrentWidget(page)
        if not changed or not self.animations_enabled:
            return
        if self.page_animation:
            self.page_animation.stop()
        self._page_effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(self._page_effect)
        self.page_animation = QPropertyAnimation(self._page_effect, b"opacity", self)
        self.page_animation.setDuration(ANIMATION_DURATIONS["page_fade"])
        self.page_animation.setStartValue(0.0)
        self.page_animation.setEndValue(1.0)
        self.page_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.page_animation.finished.connect(lambda target=page: self._finish_page_fade(target))
        self.page_animation.start()

    def _finish_page_fade(self, page: QWidget) -> None:
        if page.graphicsEffect() is self._page_effect:
            page.setGraphicsEffect(None)
        self._page_effect = None

    def rescan(self) -> None:
        if self.sources:
            self.scan_sources(self.sources)

    @Slot(int, object)
    def _scan_completed(self, generation: int, batch: ReviewBatch) -> None:
        if generation != self.scan_generation:
            return
        self.batch = batch
        if self.history_store is not None:
            try:
                entries = self.history_store.record_reports(
                    (row.set_report for row in batch.rows),
                    batch.sources,
                )
                self.drop_page.set_history_count(len(entries))
            except HistoryStoreError as exc:
                self.statusBar().showMessage(
                    f"Scan complete, but history could not be saved: {exc}",
                    12_000,
                )
        selectable_paths = {row.set_file for row in batch.rows if row.selectable}
        self.selected_paths.intersection_update(selectable_paths)
        self.workspace.set_batch(batch, self.selected_paths, self.active_filter)
        self._update_selection()
        self.rescan_action.setEnabled(True)

    @Slot(int, object)
    def _scan_failed(self, generation: int, exc: Exception) -> None:
        if generation != self.scan_generation:
            return
        self.workspace.set_failure(str(exc))
        self.rescan_action.setEnabled(True)

    @Slot(str)
    def _set_filter(self, filter_name: str) -> None:
        if filter_name not in FILTERS:
            return
        self.active_filter = filter_name
        self.workspace.populate_rows(self.batch.rows, self.selected_paths, filter_name)

    @Slot(str, bool)
    def _toggle_path(self, path_text: str, checked: bool) -> None:
        path = Path(path_text)
        row = self._row_for_path(path)
        if not row or not row.selectable:
            if row:
                reason = row.blockers[0] if row.blockers else "This Set is already safe."
                self.workspace.show_row(row, f"Cannot select this Set: {reason}")
            return
        if checked:
            self.selected_paths.add(path)
        else:
            self.selected_paths.discard(path)
        self._update_selection()

    @Slot(str)
    def _focus_path(self, path_text: str) -> None:
        row = self._row_for_path(Path(path_text))
        if row:
            self.workspace.show_row(row)

    def _select_ready(self) -> None:
        self.selected_paths.update(
            row.set_file
            for row in self.batch.rows
            if row.selectable and _matches_filter(row, self.active_filter)
        )
        self.workspace.populate_rows(self.batch.rows, self.selected_paths, self.active_filter)
        self._update_selection()

    def _clear_selection(self) -> None:
        self.selected_paths.clear()
        self.workspace.populate_rows(self.batch.rows, self.selected_paths, self.active_filter)
        self._update_selection()

    def _update_selection(self) -> None:
        self.workspace.update_selection_summary(self._selected_rows())

    def _selected_rows(self) -> list[ReviewRow]:
        return [row for row in self.batch.rows if row.set_file in self.selected_paths]

    def _row_for_path(self, path: Path) -> ReviewRow | None:
        return next((row for row in self.batch.rows if row.set_file == path), None)

    def _collect_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        confirm = ConfirmCollectionDialog(rows, self)
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        self.collection_running = True
        dialog = CollectionDialog(rows, self.thread_pool, self)
        QTimer.singleShot(0, dialog.start)
        dialog.exec()
        self.collection_running = False
        self.selected_paths.clear()
        self.rescan()

    def _browse_sets(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(
            self,
            "Choose Ableton Set Files",
            str(Path.home()),
            "Ableton Live Sets (*.als)",
        )
        if filenames:
            self.scan_sources([Path(filename) for filename in filenames])

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose Ableton Project Folder",
            str(Path.home()),
        )
        if folder:
            self.scan_sources([Path(folder)])


def _matches_filter(row: ReviewRow, filter_name: str) -> bool:
    if filter_name == "all":
        return True
    if filter_name == "ready":
        return row.selectable
    if filter_name == "safe":
        return row.status == "safe"
    if filter_name == "blocked":
        return row.status in {"blocked", "blocked_missing"}
    if filter_name == "errors":
        return row.status == "error"
    return True


def _success_details(result: CollectionResult) -> tuple[str, ...]:
    return (
        f"Backup: {result.backup_path}",
        f"Copied audio: {len(result.copied_audio)}",
        f"Copied ASD: {len(result.copied_asd)}",
        f"Rewritten references: {result.rewritten_reference_count}",
        f"Final audit: {result.final_report.external_collectable_count} external, "
        f"{result.final_report.missing_count} missing",
    )


def _empty_inspector(message: str) -> str:
    return (
        f"<div style='color:{COLORS['text']}; font-size:13px; font-weight:700;'>"
        "INSPECT A SET</div>"
        f"<p style='color:{COLORS['muted']};'>{escape(message)}</p>"
    )


def _filtered_history_plugins(
    entry: HistoryEntry,
    name_query: str = "",
    plugin_format: str | None = None,
) -> list[HistoryPlugin]:
    query = name_query.strip().casefold()
    return [
        plugin
        for plugin in entry.plugins
        if (plugin_format is None or plugin.plugin_format == plugin_format)
        and (
            not query
            or query in plugin.plugin_name.casefold()
            or query in plugin.track_name.casefold()
        )
    ]


def _history_entry_inspector(
    entry: HistoryEntry,
    plugins: list[HistoryPlugin] | None = None,
    *,
    filters_active: bool = False,
) -> str:
    freshness = entry.freshness()
    visible_plugins = list(entry.plugins) if plugins is None else plugins
    status_color = COLORS["pink"] if freshness.warning else COLORS["green"]
    status_label = "MAY BE STALE" if freshness.warning else "CURRENT"
    missing_files = sum(file.status == ReferenceStatus.MISSING.value for file in entry.audio_files)
    chunks = [
        f"<div style='font-size:18px; font-weight:700;'>{escape(Path(entry.set_file).name)}</div>",
        f"<div style='color:{COLORS['muted']}; margin-bottom:10px;'>{escape(entry.set_file)}</div>",
        f"<div style='border:1px solid {status_color}; border-radius:7px; padding:8px;'>"
        f"<b style='color:{status_color};'>{status_label}</b><br>"
        f"<span style='color:{COLORS['muted']};'>{escape(freshness.message)}</span></div>",
        _section("SNAPSHOT TIMES"),
        _field("Scanned", _friendly_timestamp(entry.scanned_at)),
        _field("Saved then", _friendly_timestamp(entry.set_saved_at_scan)),
        _field("Saved now", _friendly_timestamp(freshness.current_saved_at)),
        _field("Project", entry.project_root),
        _field("Live version", entry.ableton_version or "Unknown"),
        "<table cellspacing='0' cellpadding='3' style='margin-top:10px;'>",
        _stat_row(
            ("PLUG-INS", len(visible_plugins)),
            ("FILES", len(entry.audio_files)),
            ("MISSING", missing_files),
        ),
        "</table>",
    ]

    chunks.append(_section("PLUG-INS BY TRACK"))
    if filters_active:
        chunks.append(
            f"<div style='color:{COLORS['muted']}; margin-top:6px;'>"
            f"Showing {len(visible_plugins)} of {len(entry.plugins)} saved plug-in"
            f"{'s' if len(entry.plugins) != 1 else ''}.</div>"
        )
    if visible_plugins:
        grouped_plugins: dict[tuple[str, str], list[Any]] = {}
        for plugin in visible_plugins:
            grouped_plugins.setdefault((plugin.track_name, plugin.track_type), []).append(plugin)
        format_labels = {
            PluginFormat.AUDIO_UNIT.value: "AU",
            PluginFormat.VST2.value: "VST2",
            PluginFormat.VST3.value: "VST3",
            PluginFormat.UNKNOWN.value: "UNKNOWN",
        }
        format_colors = {
            PluginFormat.AUDIO_UNIT.value: COLORS["blue"],
            PluginFormat.VST2.value: COLORS["yellow"],
            PluginFormat.VST3.value: COLORS["amber"],
            PluginFormat.UNKNOWN.value: COLORS["pink"],
        }
        for (track_name, track_type), plugins in grouped_plugins.items():
            type_label = TRACK_TYPE_LABELS.get(track_type, track_type.title())
            chunks.append(
                f"<div style='margin-top:9px; font-weight:700;'>{escape(track_name)}"
                f" <span style='color:{COLORS['muted']};'>· {escape(type_label)}</span></div>"
            )
            for plugin in plugins:
                label = format_labels.get(plugin.plugin_format, plugin.plugin_format.upper())
                color = format_colors.get(plugin.plugin_format, COLORS["muted"])
                chunks.append(
                    f"<div style='margin:4px 0 0 12px;'>"
                    f"<b style='color:{color};'>{escape(label)}</b>"
                    f"&nbsp;&nbsp;{escape(plugin.plugin_name)}</div>"
                )
    elif entry.plugins:
        chunks.append("<div>No saved plug-ins match the current name and type filters.</div>")
    else:
        chunks.append("<div>No third-party plug-ins were recorded in this scan.</div>")

    chunks.append(_section("REFERENCED AUDIO FILES"))
    if entry.audio_files:
        status_labels = {
            ReferenceStatus.ALREADY_LOCAL.value: "IN PROJECT",
            ReferenceStatus.EXTERNAL_COLLECTABLE.value: "EXTERNAL",
            ReferenceStatus.MISSING.value: "MISSING",
        }
        status_colors = {
            ReferenceStatus.ALREADY_LOCAL.value: COLORS["green"],
            ReferenceStatus.EXTERNAL_COLLECTABLE.value: COLORS["yellow"],
            ReferenceStatus.MISSING.value: COLORS["pink"],
        }
        for audio_file in entry.audio_files:
            label = status_labels.get(audio_file.status, audio_file.status.upper())
            color = status_colors.get(audio_file.status, COLORS["muted"])
            size = _format_file_size(audio_file.file_size)
            chunks.append(
                f"<div style='margin-top:8px;'><b style='color:{color};'>{escape(label)}</b>"
                f"&nbsp;&nbsp;{escape(audio_file.referenced_path)}"
                f"{f'&nbsp;&nbsp;·&nbsp;&nbsp;{escape(size)}' if size else ''}</div>"
            )
            if audio_file.resolved_path and audio_file.resolved_path != audio_file.referenced_path:
                chunks.append(
                    f"<div style='color:{COLORS['muted']}; margin-left:12px;'>"
                    f"Resolved: {escape(audio_file.resolved_path)}</div>"
                )
    else:
        chunks.append("<div>No referenced audio files were recorded in this scan.</div>")

    if entry.errors:
        chunks.append(_section("SCAN ERRORS", COLORS["pink"]))
        chunks.extend(f"<div>{escape(error)}</div>" for error in entry.errors)
    return "".join(chunks)


def _friendly_timestamp(timestamp: str | None) -> str:
    if not timestamp:
        return "Unknown"
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%b %d, %Y · %I:%M:%S %p")
    except ValueError:
        return timestamp


def _format_file_size(file_size: int | None) -> str:
    if file_size is None:
        return ""
    value = float(file_size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return ""


def _row_inspector(row: ReviewRow, notice: str | None = None) -> str:
    report = row.set_report
    status_color = STATUS_COLORS.get(row.status, COLORS["divider"])
    chunks = [
        f"<div style='font-size:16px; font-weight:700;'>{escape(row.set_file.name)}</div>",
        f"<div style='color:{COLORS['muted']}; margin-bottom:8px;'>"
        f"{escape(str(row.set_file))}</div>",
        f"<div style='margin-top:4px;'><span style='background:{status_color}; "
        f"border:1px solid {COLORS['divider']};"
        f" color:{COLORS['amber_text']}; padding:3px 8px; font-weight:700;'>"
        f"{escape(STATUS_LABELS.get(row.status, row.status.upper()))}</span></div>",
    ]
    if notice:
        chunks.insert(
            0,
            f"<div style='background:#342B1F; color:#F5D69B; border:1px solid #695537;"
            f" padding:8px; margin-bottom:8px;'>{escape(notice)}</div>",
        )

    chunks.extend(
        [
            "<table cellspacing='0' cellpadding='3' style='margin-top:10px;'>",
            _stat_row(
                ("REFS", report.total_audio_references),
                ("MEDIA", report.unique_media_count),
                ("LOCAL", report.already_local_count),
            ),
            _stat_row(
                ("EXTERNAL", report.external_collectable_count),
                ("MISSING", report.missing_count),
            ),
            "</table>",
            _section("PROJECT"),
            _field("Root", str(report.project_root)),
            _field("Destination", str(report.project_root / "Samples" / "Imported")),
        ]
    )
    if row.blockers:
        chunks.append(_section("BLOCKERS", STATUS_COLORS["blocked"]))
        chunks.extend(
            f"<div style='margin:3px 0;'>!&nbsp; {escape(blocker)}</div>"
            for blocker in row.blockers
        )
    if row.plan and row.plan.items:
        chunks.append(_section(f"COLLECTABLE MEDIA  {len(row.plan.items)}"))
        for item in row.plan.items:
            chunks.extend(
                [
                    f"<div style='margin-top:7px; font-weight:700;'>"
                    f"{item.id:02d}&nbsp;&nbsp;{escape(item.source_path.name)}</div>",
                    f"<div style='color:{COLORS['muted']}; margin-left:18px;'>"
                    f"{escape(str(item.source_path))}</div>",
                    f"<div style='margin-left:18px;'>→ {escape(str(item.destination_path))}</div>",
                    f"<div style='color:{COLORS['muted']}; margin-left:18px;'>"
                    f"ASD {'included' if item.asd_source_path else 'not found'}"
                    f"&nbsp;&nbsp;·&nbsp;&nbsp;{len(item.references)} reference"
                    f"{'s' if len(item.references) != 1 else ''}</div>",
                ]
            )
    elif not row.blockers:
        chunks.extend(
            [
                _section("AUDIO"),
                "<div>All referenced media is already inside the project.</div>",
            ]
        )

    missing = [
        reference
        for reference in report.audio_references
        if reference.status == ReferenceStatus.MISSING
    ]
    if missing:
        chunks.append(_section("MISSING AUDIO PATHS", STATUS_COLORS["blocked"]))
        for reference in missing:
            chunks.append(
                f"<div style='margin-top:5px;'><b>ref #{reference.ref_index}</b>"
                f"&nbsp;&nbsp;{escape(reference.referenced_path)}</div>"
            )
            if reference.path and reference.path != reference.referenced_path:
                chunks.append(
                    f"<div style='color:{COLORS['muted']}; margin-left:18px;'>"
                    f"Stored path: {escape(reference.path)}</div>"
                )
            if reference.relative_path and reference.relative_path != reference.referenced_path:
                chunks.append(
                    f"<div style='color:{COLORS['muted']}; margin-left:18px;'>"
                    f"Relative path: {escape(reference.relative_path)}</div>"
                )
    return "".join(chunks)


def _filtered_plugins(
    row: ReviewRow,
    name_query: str = "",
    plugin_format: PluginFormat | None = None,
) -> list[PluginReference]:
    query = name_query.strip().casefold()
    return [
        plugin
        for plugin in row.set_report.plugin_references
        if (plugin_format is None or plugin.plugin_format == plugin_format)
        and (
            not query
            or query in plugin.plugin_name.casefold()
            or query in plugin.track_name.casefold()
        )
    ]


def _plugin_inspector(
    row: ReviewRow,
    plugins: list[PluginReference] | None = None,
    *,
    filters_active: bool = False,
) -> str:
    report = row.set_report
    visible_plugins = list(report.plugin_references) if plugins is None else plugins

    def visible_count(plugin_format: PluginFormat) -> int:
        return sum(plugin.plugin_format == plugin_format for plugin in visible_plugins)

    visible_tracks = len(
        {(plugin.track_index, plugin.track_id, plugin.track_name) for plugin in visible_plugins}
    )
    chunks = [
        f"<div style='font-size:16px; font-weight:700;'>{escape(row.set_file.name)}</div>",
        f"<div style='color:{COLORS['muted']}; margin-bottom:8px;'>"
        f"{escape(str(row.set_file))}</div>",
        "<table cellspacing='0' cellpadding='3' style='margin-top:10px;'>",
        _stat_row(
            ("AU", visible_count(PluginFormat.AUDIO_UNIT)),
            ("VST2", visible_count(PluginFormat.VST2)),
            ("VST3", visible_count(PluginFormat.VST3)),
        ),
        _stat_row(
            ("TRACKS", visible_tracks),
            ("TOTAL", len(visible_plugins)),
            ("UNKNOWN", visible_count(PluginFormat.UNKNOWN)),
        ),
        "</table>",
        f"<div style='color:{COLORS['muted']}; margin-top:8px;'>"
        "Read from the last saved .als file. Save in Live, then Rescan to refresh.</div>",
    ]
    if not report.plugin_references:
        chunks.extend(
            [
                _section("PLUG-INS"),
                "<div>No AU, VST2, or VST3 plug-ins were found in this Set.</div>",
            ]
        )
        return "".join(chunks)

    if filters_active:
        chunks.append(
            f"<div style='color:{COLORS['muted']}; margin-top:8px;'>"
            f"Showing {len(visible_plugins)} of {report.total_plugin_instances} plug-in"
            f"{'s' if report.total_plugin_instances != 1 else ''}.</div>"
        )

    if not visible_plugins:
        chunks.extend(
            [
                _section("FILTERED RESULTS"),
                "<div>No plug-ins match the current name and type filters.</div>",
            ]
        )
        return "".join(chunks)

    grouped: dict[tuple[int, str | None, str, str], list[Any]] = {}
    for plugin in visible_plugins:
        key = (
            plugin.track_index,
            plugin.track_id,
            plugin.track_name,
            plugin.track_type.value,
        )
        grouped.setdefault(key, []).append(plugin)

    for (_, _, track_name, track_type), plugins in grouped.items():
        type_label = TRACK_TYPE_LABELS.get(track_type, track_type.title())
        chunks.extend(
            [
                _section(
                    f"{track_name}  ·  {type_label}  ·  {len(plugins)} plug-in"
                    f"{'s' if len(plugins) != 1 else ''}"
                ),
            ]
        )
        for plugin in plugins:
            label = PLUGIN_FORMAT_LABELS[plugin.plugin_format]
            color = PLUGIN_FORMAT_COLORS[plugin.plugin_format]
            chunks.append(
                f"<div style='margin-top:7px;'>"
                f"<span style='color:{color}; font-weight:700;'>{escape(label)}</span>"
                f"&nbsp;&nbsp;{escape(plugin.plugin_name)}</div>"
            )
    return "".join(chunks)


def _stat_row(*pairs: tuple[str, int]) -> str:
    cells = "".join(
        f"<td style='color:{COLORS['muted']}; font-size:10px; font-weight:700;'>{label}</td>"
        f"<td style='font-size:15px; font-weight:700; padding-right:12px;'>{value}</td>"
        for label, value in pairs
    )
    return f"<tr>{cells}</tr>"


def _section(label: str, color: str | None = None) -> str:
    return (
        f"<div style='margin-top:12px; padding-top:5px; border-top:1px solid "
        f"{COLORS['divider']}; color:{color or COLORS['muted']}; font-size:10px;"
        f" font-weight:700;'>{escape(label)}</div>"
    )


def _field(label: str, value: str) -> str:
    return (
        f"<div><span style='color:{COLORS['muted']}; display:inline-block;"
        f" width:80px;'>{escape(label)}</span>&nbsp;&nbsp;{escape(value)}</div>"
    )


def _outcome_html(outcome: CollectionOutcome) -> str:
    color = COLORS["green"] if outcome.succeeded else COLORS["error"]
    label = "SUCCESS" if outcome.succeeded else "FAILED"
    details = "<br>".join(escape(detail) for detail in outcome.details)
    return (
        f"<div style='border-top:1px solid {COLORS['divider']}; padding:8px 0;'>"
        f"<b style='color:{color};'>{label}</b>&nbsp;&nbsp;·&nbsp;&nbsp;"
        f"<b>{escape(outcome.row.set_file.name)}</b><br>"
        f"<span style='color:{COLORS['muted']};'>{details}</span></div>"
    )


def _asset_path(filename: str) -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    packaged = bundle_root / "assets" / filename
    if packaged.exists():
        return packaged
    return Path(__file__).parent / "assets" / filename


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(args)
    app.setApplicationName("Collector Inspector")
    app.setOrganizationName("Collector Inspector")
    app.setApplicationVersion(__version__)
    app.setStyleSheet(APP_STYLE)
    icon_path = _asset_path("app_icon.svg")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    initial_sources = [
        Path(arg) for arg in args[1:] if not arg.startswith("-") and Path(arg).expanduser().exists()
    ]
    window = CollectorWindow(
        initial_sources,
        history_store=HistoryStore(default_history_path()),
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
