"""Creator: read videos on a worker, then build the selected wallpapers safely."""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from .. import external, theme
from ..engines import creator as engine
from ..settings import DEFAULT_CREATOR_MODE, default_creator_target
from ..services import Run
from ..ui.kit import (
    AccentButton, ActivityLine, BusyCell, Callout, CardTitle, Cell, CheckCell, Chip,
    ChipCell, Column, ConfirmDialog, EmptyState, GhostButton, GlassPanel, IconButton, IconDisc,
    LiveDot, LogPanel, MetricStrip, NavState, Overline, PathField, ProgressBar,
    ProgressRing, SecondaryButton, SegmentedControl, SkeletonRows, Table, TableBar,
    TableFooter, TableModel, TagSelect, TagsCell, format as fmt, label,
)
from .base import Page, SideScroll


def tag_cell(item, batch_tags):
    """Preserve all three tag decisions; NeedsTags describes their resolved value."""
    tags = engine.resolve_tags(item, batch_tags)
    if not tags:
        return ChipCell("NeedsTags")
    return TagsCell(tuple(tags), "text.lo" if item.tags is None else "text.body")


def build_items(items, selected, batch_tags, skip=True):
    return [item for item in items if item.video_path in selected and item.valid
            and (not skip or not engine.needs_tags(item, batch_tags))]


def playlist_available(target, destination):
    """Compare configured paths without touching their drives on the UI thread."""
    def key(value):
        return os.path.normcase(os.path.abspath(value)) if value else ""
    return bool(target and destination and key(target) == key(destination))


def estimate_seconds(journal, count):
    """Only measured build history supplies a time estimate."""
    if journal is None or not count:
        return None
    samples = []
    for entry in journal.recent(100):
        if entry.tool != "creator" or entry.kind != "build.clean":
            continue
        match = re.search(r"created=(\d+) · seconds=([\d.]+)", entry.detail)
        if match and int(match[1]) > 0:
            samples.append(float(match[2]) / int(match[1]))
    return count * sum(samples) / len(samples) if samples else None


class ReadWorker(QThread):
    item = Signal(object, int, int)
    checking = Signal(str, int, int)
    completed = Signal(object)
    error = Signal(str)

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = source
        self.cancelled = threading.Event()

    def run(self):
        try:
            items = engine.read_source(self.source, self.item.emit, self.cancelled,
                                       on_check=self.checking.emit)
            self.completed.emit(items)
        except Exception as exc:
            self.error.emit(str(exc))


class BuildSignals(QObject):
    log = Signal(str)
    progress = Signal(int, int)
    item_done = Signal(str, str, object, object)
    item_progress = Signal(str, str, object)
    item_result = Signal(object)
    finished = Signal(object)


class SourceModel(TableModel):
    def __init__(self, page):
        self.page = page
        widths = theme.CREATOR_COLUMNS
        columns = [Column("", widths[0], sortable=False), Column("FILE", widths[1]),
                   Column("TAG", widths[2], sortable=False),
                   Column("RESOLUTION", widths[3], mono=True, align="right"),
                   Column("LENGTH", widths[4], mono=True, align="right"),
                   Column("SIZE", widths[5], mono=True, align="right")]
        super().__init__(columns, parent=page)

    def cell(self, item, column):
        if isinstance(item, str):
            return BusyCell(item) if column == 1 else Cell("reading…", "accent.hover") if column == 5 else None
        if column == 0:
            return CheckCell(item.video_path in self.page.selected, item.valid)
        if column == 1:
            return Cell(item.basename, strong=True, icon="video",
                        sub=item.reason or "", sub_tone="warn" if item.reason else None)
        if column == 2:
            return tag_cell(item, self.page.batch_tags)
        if column == 3:
            return item.resolution or fmt.DASH
        if column == 4:
            return video_length(item.duration)
        return fmt.size(item.size)

    def sort_key(self, item, column):
        if isinstance(item, str):
            return "" if column in (1, 2) else -1
        if column == 0:
            return item.video_path in self.page.selected
        if column == 1:
            return item.basename.casefold()
        if column == 3:
            return (item.width or 0) * (item.height or 0)
        if column == 4:
            return item.duration or 0
        return item.size

    def row_tone(self, item):
        return "accent" if isinstance(item, str) else "warn" if item.reason else None


def video_length(seconds):
    if seconds is None:
        return fmt.DASH
    seconds = round(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


class ResultModel(TableModel):
    def __init__(self, page, *, omitted=False):
        self.omitted = omitted
        columns = [Column("FILE", thumb=None if omitted else "sm", sortable=False),
                   Column("STATE", theme.CREATOR_RESULT_COLUMNS[1], sortable=False)]
        if not omitted:
            columns.append(Column("PROGRESS", theme.CREATOR_RESULT_COLUMNS[2], sortable=False))
        super().__init__(columns, parent=page)

    def cell(self, item, column):
        status = item.get("status", "queued")
        if column == 0:
            if self.omitted:
                # what was left out, as frame 21 lists it: the glyph in the row's hue
                return Cell(item["name"], strong=True, icon="warn",
                            icon_tone="danger" if status == "failed" else "warn",
                            sub=item.get("reason") or status)
            return Cell(item["name"], strong=True,
                        sub=item.get("reason") or item.get("phase") or item.get("folder") or "waiting",
                        sub_tone="warn" if status == "skipped" else "danger" if status == "failed" else None)
        if column == 1:
            return (ChipCell("Done") if status == "ok" else ChipCell("Failed") if status == "failed"
                    else Cell("skipped", "warn") if status == "skipped" else BusyCell("working")
                    if status == "working" else Cell("queued", "text.lo"))
        from ..ui.kit import ProgressCell
        return ProgressCell(1.0 if status == "ok" else None,
                            "ok" if status == "ok" else "accent",
                            status == "working")

    def thumb_source(self, item):
        return item.get("preview_path")

    def row_tone(self, item):
        if self.omitted:
            return "danger" if item.get("status") == "failed" else None
        return "accent" if item.get("status") == "working" else None


def panel(title="", tone=None):
    widget = GlassPanel(tone=tone)
    column = QVBoxLayout(widget)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(theme.ROTATOR_PANEL_GAP)
    if title:
        column.addWidget(CardTitle(title))
    return widget, column


def words(text, kind="type.bodySm", tone="mid"):
    widget = label(text, kind, tone)
    widget.setWordWrap(True)
    return widget


class CreatorPage(Page):
    key, title, icon = "creator", "Creator", "creator"
    FIXTURES = ("empty", "reading", "scanned", "building", "done", "ffmpeg-missing")

    def __init__(self, settings, services=None, config=None, *, on_rebuild=None, parent=None):
        super().__init__(parent)
        self.settings, self.services, self.config = settings, services, config
        self.on_rebuild = on_rebuild
        self.state = "empty"
        self.items, self.report, self.run_rows = [], [], []
        self.selected = set()
        self.batch_tags = engine.clean_tags(settings.get("creator", "tags", []))
        self._fixture = None
        self._on_screen = False
        self._job = None
        self._cancelled = False
        self._started = None
        self._started_clock = None
        self._done, self._total = 0, 0
        self._reading_name = ""
        self._read_worker = None
        self._workers = []
        self._tag_editor = None
        self._target = settings.get("creator", "target", default_creator_target())
        self._estimate = None
        self.messages = []
        self.signals = BuildSignals(self)
        self.engine = engine.BuildEngine(log=self.signals.log.emit, progress=self.signals.progress.emit,
                                        item_done=self.signals.item_done.emit,
                                        item_progress=self.signals.item_progress.emit,
                                        item_result=self.signals.item_result.emit,
                                        finished=self.signals.finished.emit)
        self.signals.log.connect(self._log)
        self.signals.progress.connect(self._progress)
        self.signals.item_done.connect(self._item_done)
        self.signals.item_progress.connect(self._item_progress)
        self.signals.item_result.connect(self._item_result)
        self.signals.finished.connect(self._on_finished)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(theme.CREATOR_RENDER_MS)
        self._render_timer.timeout.connect(self._render_tables)
        self._build_ui()
        self._set_state("empty")

    def _build_ui(self):
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SP_16, theme.SP_14, theme.SP_16, theme.SP_12)
        row.setSpacing(theme.SP_12)
        side = QWidget()
        self.side_layout = QVBoxLayout(side)
        self.side_layout.setContentsMargins(0, 0, 0, 0)
        self.side_layout.setSpacing(theme.SP_12)
        row.addWidget(SideScroll(side, theme.CREATOR_SIDE))

        self.source_panel, source = panel("Source and output")
        source.addWidget(Overline("SOURCE FOLDER"))
        self.source = PathField(self.settings.get("creator", "source", ""),
                                placeholder="Choose a folder of videos")
        self.source.path_changed.connect(self.read_folder)
        source.addWidget(self.source)
        source.addWidget(Overline("CREATES INTO"))
        self.target = PathField(self._target, editable=False)
        source.addWidget(self.target)
        change = GhostButton("Change output in Settings", icon="settings", size="sm")
        change.clicked.connect(lambda: self.navigate.emit("settings"))
        source.addWidget(change, 0, Qt.AlignLeft)
        mode_row = QHBoxLayout()
        mode_row.addWidget(Overline("MODE"), 1)
        self.mode = SegmentedControl(("Copy", "Move"), current=int(
            self.settings.get("creator", "mode", DEFAULT_CREATOR_MODE) == "Move"))
        self.mode.changed.connect(self._options_changed)
        mode_row.addWidget(self.mode)
        source.addLayout(mode_row)
        self.move_warning = Callout("Successfully built clips are moved out, rather than copied. "
                                    "Skipped and failed files stay in the source.", tone="warn",
                                    title="Move empties the source folder")
        source.addWidget(self.move_warning)
        source.addWidget(Overline("TAGS FOR EVERY NEW WALLPAPER"))
        self.tags = TagSelect()
        self.tags.set_value(self.batch_tags)
        self.tags.changed.connect(self._options_changed)
        source.addWidget(self.tags)
        self.side_layout.addWidget(self.source_panel)

        self.how_panel, how = panel("How it works")
        how.addWidget(words("Each video becomes its own wallpaper folder, with the video, "
                            "a preview and project.json. Nothing is created until you press Build."))
        how.addStretch(1)
        how.addWidget(words(" · ".join(ext.lstrip(".") for ext in engine.VIDEO_EXTS), "type.monoSm", "lo"))
        how.addWidget(words(f"Preview: {engine.GIF_SIZE}×{engine.GIF_SIZE} @ {engine.GIF_FPS} fps · "
                            f"{engine.GIF_DURATION:g} s from {engine.GIF_SKIP:g} s", "type.monoSm", "lo"))
        self.side_layout.addWidget(self.how_panel, 1)

        self.read_panel, read = panel("Reading the folder")
        self.read_line = ActivityLine("Checking video metadata", spinning=True)
        read.addWidget(self.read_line)
        read_numbers = QHBoxLayout()
        self.read_count = label("0 / 0", "type.numeric", "hi")
        read_numbers.addWidget(self.read_count)
        read_numbers.addWidget(label("files checked", "type.bodySm", "mid"), 1)
        read.addLayout(read_numbers)
        self.read_bar = ProgressBar()
        read.addWidget(self.read_bar)
        self.stop_read = SecondaryButton("Stop reading", icon="stop")
        self.stop_read.clicked.connect(self.stop_reading)
        read.addWidget(self.stop_read, 0, Qt.AlignLeft)
        self.side_layout.addWidget(self.read_panel)

        self.found_panel, found = panel()
        self.found_overline = Overline("FOUND IN THE SOURCE")
        found.addWidget(self.found_overline)
        self.found_metrics = MetricStrip([(0, "files"), (0, "ready", "ok"), (0, "need tags", "warn")])
        found.addWidget(self.found_metrics)
        from ..ui.kit import Checkbox
        self.skip = Checkbox("Skip files that still need tags")
        self.skip.setChecked(True)
        self.skip.toggled.connect(self._refresh_counts)
        found.addWidget(self.skip)
        self.read_note = words("Nothing is written while reading. Tags and mode can still be changed before you build.")
        found.addWidget(self.read_note)
        found.addStretch(1)
        self.build_button = AccentButton("Build 0 wallpapers")
        self.build_button.clicked.connect(self.start_build)
        found.addWidget(self.build_button)
        self.estimate_label = words("", "type.bodySm", "lo")
        self.estimate_label.setAlignment(Qt.AlignHCenter)
        found.addWidget(self.estimate_label)
        self.side_layout.addWidget(self.found_panel, 1)

        self.run_panel, run = panel()
        run_title = QHBoxLayout()
        run_title.addWidget(LiveDot("accent"))
        run_title.addWidget(CardTitle("Building wallpapers"), 1)
        run.addLayout(run_title)
        count_row = QHBoxLayout()
        self.ring = ProgressRing(size=theme.CREATOR_RING)
        count_row.addWidget(self.ring)
        self.run_count = label("0 / 0", "type.numeric", "hi")
        count_row.addWidget(self.run_count, 1)
        run.addLayout(count_row)
        self.eta_label = words("", "type.monoSm", "lo")
        run.addWidget(self.eta_label)
        self.run_bar = ProgressBar()
        run.addWidget(self.run_bar)
        self.current_line = ActivityLine("")
        run.addWidget(self.current_line)
        controls = QHBoxLayout()
        self.pause_button = SecondaryButton("Pause", icon="pause")
        self.pause_button.clicked.connect(self.pause_resume)
        self.stop_button = SecondaryButton("Stop", icon="stop")
        self.stop_button.clicked.connect(self.stop_build)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.stop_button)
        controls.addStretch(1)
        run.addLayout(controls)
        self.side_layout.addWidget(self.run_panel)

        self.run_facts, facts = panel()
        facts.addWidget(Overline("SO FAR"))
        self.run_metrics = MetricStrip([(0, "created", "ok"), (0, "skipped", "warn"), ("0 B", "written")])
        facts.addWidget(self.run_metrics)
        self.run_warning = Callout("Skipped and failed files remain in the source.", tone="warn",
                                    title="Move empties the source folder")
        facts.addWidget(self.run_warning)
        facts.addStretch(1)
        facts.addWidget(words("Source files are removed only after each wallpaper is written and verified.", tone="lo"))
        self.side_layout.addWidget(self.run_facts, 1)

        self.result_panel, result = panel(tone="ok")
        head = QHBoxLayout()
        head.setSpacing(theme.RUN_HEAD_GAP)
        self.result_disc = IconDisc("check", "ok")
        self.result_title = label("Wallpapers created", "type.h3", "hi")
        head.addWidget(self.result_disc, 0, Qt.AlignVCenter)
        head.addWidget(self.result_title, 1)
        result.addLayout(head)
        self.result_sentence = words("")
        result.addWidget(self.result_sentence)
        self.result_metrics = MetricStrip([(0, "created"), (0, "skipped", "warn"), (0, "failed", "danger")])
        result.addWidget(self.result_metrics)
        actions = QHBoxLayout()
        self.rebuild_button = AccentButton("Rebuild playlist", icon="refresh")
        self.rebuild_button.clicked.connect(self.rebuild_playlist)
        actions.addWidget(self.rebuild_button)
        open_folder = SecondaryButton("Open folder", icon="ext")
        open_folder.clicked.connect(lambda: self._open(self._target))
        actions.addWidget(open_folder)
        actions.addStretch(1)
        result.addLayout(actions)
        self.side_layout.addWidget(self.result_panel)

        self.omissions_panel, omitted = panel()
        omitted.addWidget(Overline("WHAT WAS LEFT OUT"))
        self.omissions = Table()
        self.omissions.setAccessibleName("What was left out")
        self.omissions_model = ResultModel(self, omitted=True)
        # The narrow panel shows reasons under the filename, and needs no second column.
        self.omissions_model.columns = self.omissions_model.columns[:1]
        self.omissions.setModel(self.omissions_model)
        self.omissions.horizontalHeader().hide()
        omitted.addWidget(self.omissions, 1)
        self.retry_button = SecondaryButton("Add tags and build these", icon="tag")
        self.retry_button.clicked.connect(self.prepare_retry)
        omitted.addWidget(self.retry_button, 0, Qt.AlignLeft)
        self.side_layout.addWidget(self.omissions_panel, 1)

        self.right = QStackedWidget()
        row.addWidget(self.right, 1)
        empty_panel, empty_layout = panel()
        self.empty = EmptyState("No source folder yet", "Drop a folder here, or choose one. "
                                "Nothing is created until you press Build.", icon="folderPlus",
                                drop_zone=True, width=theme.CREATOR_EMPTY_WIDTH)
        self.choose_button = AccentButton("Choose a folder", size="lg")
        self.choose_button.clicked.connect(self.source.browse)
        self.empty.add_action(self.choose_button)
        self.empty.dropped.connect(lambda paths: self.read_folder(paths[0]) if paths else None)
        empty_layout.addWidget(self.empty)
        self.right.addWidget(empty_panel)

        table_panel = GlassPanel(padding="none")
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(0)
        bar = TableBar()
        self.table_title = label("Files found", "type.h3", "hi")
        bar.add(self.table_title)
        self.needs_chip = Chip("NeedsTags", "0 need tags")
        bar.add(self.needs_chip)
        bar.add_stretch()
        self.filter = SegmentedControl(("All", "Needs tags"))
        self.filter.changed.connect(self._render_tables)
        bar.add(self.filter)
        self.refresh_button = IconButton("refresh", "Read the source folder again")
        self.refresh_button.clicked.connect(lambda: self.read_folder(self.source.path()))
        bar.add(self.refresh_button)
        table_layout.addWidget(bar)
        self.header_bar = ProgressBar(height=theme.PROGRESS_HEIGHTS[0])
        self.header_bar.set_state("indeterminate")
        table_layout.addWidget(self.header_bar)
        self.table = Table()
        self.table.setAccessibleName("The videos in the source folder")
        self.source_model = SourceModel(self)
        self.table.setModel(self.source_model)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.installEventFilter(self)
        self.table.clicked.connect(self._table_click)
        self.table.activated.connect(self._table_click)
        table_layout.addWidget(self.table, 1)
        self.skeletons = SkeletonRows()
        self.skeletons.setFixedHeight(theme.CREATOR_READ_SKELETONS * self.skeletons.row_height())
        table_layout.addWidget(self.skeletons)
        self.read_space = QWidget()
        table_layout.addWidget(self.read_space, 1)
        self.footer = TableFooter("0 selected")
        self.select_all_button = GhostButton("Select all", size="sm")
        self.select_all_button.clicked.connect(self.select_all)
        self.footer.add_action(self.select_all_button)
        open_source = GhostButton("Open source folder", icon="ext", size="sm")
        open_source.clicked.connect(lambda: self._open(self.source.path()))
        self.footer.add_action(open_source)
        table_layout.addWidget(self.footer)
        self.right.addWidget(table_panel)

        run_right = QWidget()
        run_layout = QVBoxLayout(run_right)
        run_layout.setContentsMargins(0, 0, 0, 0)
        run_layout.setSpacing(theme.SP_12)
        run_list, run_list_layout = panel()
        run_list_layout.addWidget(CardTitle("Building", "Each file is verified before its source can be removed"))
        self.run_table = Table()
        self.run_table.setAccessibleName("Building")
        self.run_model = ResultModel(self)
        self.run_table.setModel(self.run_model)
        run_list_layout.addWidget(self.run_table, 1)
        run_layout.addWidget(run_list, 1)
        self.log = LogPanel("Creator log", file="creator.log", on_open_folder=self._open_logs)
        self.log.set_console_height(theme.CREATOR_LOG_HEIGHT)
        run_layout.addWidget(self.log)
        self.right.addWidget(run_right)

        done_panel = GlassPanel(padding="none")
        done_layout = QVBoxLayout(done_panel)
        done_layout.setContentsMargins(0, 0, 0, 0)
        done_layout.setSpacing(0)
        done_bar = TableBar()
        done_bar.add(CardTitle("Created just now"))
        self.done_chip = Chip("Done", "0 done")
        done_bar.add(self.done_chip)
        done_bar.add_stretch()
        done_layout.addWidget(done_bar)
        self.done_table = Table()
        self.done_table.setAccessibleName("Created just now")
        self.done_model = ResultModel(self)
        self.done_model.columns = (Column("WALLPAPER", thumb="row", sortable=False),
                                   Column("STATE", theme.CREATOR_RESULT_COLUMNS[1], sortable=False))
        self.done_table.setModel(self.done_model)
        self.done_table.doubleClicked.connect(self._open_created)
        done_layout.addWidget(self.done_table, 1)
        self.done_footer = TableFooter("0 created")
        another = GhostButton("Start another folder", icon="plus", size="sm")
        another.clicked.connect(self.clear)
        self.done_footer.add_action(another)
        done_layout.addWidget(self.done_footer)
        self.right.addWidget(done_panel)

    def make_header_actions(self):
        self.header_button = GhostButton("Clear", outlined=True)
        self.header_button.clicked.connect(self._header_action)
        self.header_button.setVisible(self.state in ("reading", "scanned", "done"))
        return [self.header_button]

    def _header_action(self):
        if self.state == "reading":
            self.stop_reading()
        else:
            self.clear()

    def _set_state(self, state):
        self.state = state
        setup = state in ("empty", "reading", "scanned", "error", "ffmpeg-missing")
        self.source_panel.setVisible(setup)
        self.how_panel.setVisible(state in ("empty", "error", "ffmpeg-missing"))
        self.read_panel.setVisible(state == "reading")
        self.found_panel.setVisible(state in ("reading", "scanned"))
        self.run_panel.setVisible(state == "building")
        self.run_facts.setVisible(state == "building")
        self.result_panel.setVisible(state == "done")
        self.omissions_panel.setVisible(state == "done")
        self.source.setEnabled(state != "reading")
        self.tags.setEnabled(state != "building")
        self.mode.setEnabled(state != "building")
        self.right.setCurrentIndex({"reading": 1, "scanned": 1, "building": 2, "done": 3}.get(state, 0))
        self.header_bar.setVisible(state == "reading")
        self.skeletons.setVisible(state == "reading")
        self.read_space.setVisible(state == "reading")
        self.filter.setEnabled(state == "scanned")
        self.refresh_button.setEnabled(state == "scanned")
        self.select_all_button.setEnabled(state == "scanned")
        self.skip.setVisible(state == "scanned")
        self.read_note.setVisible(state == "reading")
        self.estimate_label.setVisible(state == "scanned")
        self.found_overline.setText("SO FAR" if state == "reading" else "FOUND IN THE SOURCE")
        if hasattr(self, "header_button"):
            self.header_button.setText("Cancel" if state == "reading" else "Start another folder" if state == "done" else "Clear")
            self.header_button.setVisible(state in ("reading", "scanned", "done"))
        self.set_nav_state(NavState.status("working" if state in ("reading", "building") else "idle"))
        self._refresh_counts()
        self._update_header()
        self._render_tables()

    def _update_header(self):
        if self.state == "reading":
            text = f"{self.source.path()} · reading {self._done} of {self._total}"
        elif self.state == "scanned":
            text = f"{self.source.path()} · {len(self.items)} files"
        elif self.state == "building":
            text = f"Building {self._build_count} wallpapers · started {fmt.clock(self._started)}"
        elif self.state == "done":
            text = f"Finished {fmt.clock(self._ended)} · {fmt.duration(self._elapsed)}"
        else:
            text = "Makes wallpapers out of your own videos"
        self.set_subtitle(text)

    def _options_changed(self, *_):
        self.batch_tags = self.tags.tags()
        self.settings.set("creator", "mode", "Move" if self.mode.current_index() else "Copy")
        self.settings.set("creator", "tags", self.batch_tags)
        if self._fixture is None:
            self.settings.save()
        self._refresh_counts()
        self._render_tables()

    def _refresh_counts(self, *_):
        if not hasattr(self, "source_panel"):
            return
        move = self.mode.current_index() == 1
        self.move_warning.setVisible(move)
        self.run_warning.setVisible(move)
        usable = [item for item in self.items if item.valid]
        need = sum(engine.needs_tags(item, self.batch_tags) for item in usable)
        unsupported = sum(bool(item.reason and item.reason.startswith("unsupported")) for item in self.items)
        self.found_metrics.set_value(0, len(self.items))
        self.found_metrics.set_value(1, len(usable) if self.state == "reading" else len(usable) - need)
        self.found_metrics.set_value(2, unsupported if self.state == "reading" else need)
        self.found_metrics.set_caption(1, "usable" if self.state == "reading" else "ready")
        self.found_metrics.set_caption(2, "unsupported" if self.state == "reading" else "need tags")
        chosen = build_items(self.items, self.selected, self.batch_tags, self.skip.isChecked())
        self.build_button.setText("Build — waiting for the scan" if self.state == "reading"
                                  else f"Build {len(chosen)} wallpapers")
        self.build_button.setEnabled(self.state == "scanned" and bool(chosen) and bool(self.target.path()))
        self.needs_chip.set_text(f"{need} need tags")
        self.needs_chip.setVisible(need > 0)
        size = sum(item.size for item in chosen)
        estimate = self._estimate
        if self._fixture is None:
            estimate = estimate_seconds(self.services.journal if self.services else None, len(chosen))
        self.estimate_label.setText((f"{fmt.approx(fmt.duration(estimate, exact=False))} · " if estimate else "")
                                    + f"{fmt.size(size)} of video will be written + previews")
        self.footer.set_text(f"{len(self.selected)} selected")

    def _render_tables(self, *_):
        if not hasattr(self, "source_model"):
            return
        rows = self.items
        if self.filter.current_index() == 1 and self.state == "scanned":
            rows = [item for item in rows if item.valid and engine.needs_tags(item, self.batch_tags)]
        self.source_model.set_rows(rows + ([self._reading_name] if self.state == "reading" and self._reading_name else []))
        if self.state == "reading":
            height = self.table.horizontalHeader().sizeHint().height() + min(
                theme.CREATOR_RUN_ROWS, self.source_model.rowCount()) * self.table.row_height()
            self.table.setFixedHeight(height)
            self.table.scrollToBottom()
        else:
            self.table.setMinimumHeight(0)
            self.table.setMaximumHeight((1 << 24) - 1)
        self.table.set_spinning([len(rows)] if self.state == "reading" and self._reading_name else [])
        self.table_title.setText(f"Reading {self._done} of {self._total}" if self.state == "reading"
                                  else f"{len(self.items)} files found")
        self.run_model.set_rows(self.run_rows)
        self.run_table.set_spinning([n for n, entry in enumerate(self.run_rows) if entry.get("status") == "working"])
        self.done_model.set_rows([entry for entry in self.report if entry["status"] == "ok"])
        self.omissions_model.set_rows([entry for entry in self.report if entry["status"] != "ok"])

    def _queue_render(self):
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _table_click(self, index):
        if self.state != "scanned":
            return
        item = self.source_model.item_at(index.row())
        if index.column() == 0 and item.valid:
            if item.video_path in self.selected:
                self.selected.remove(item.video_path)
            else:
                self.selected.add(item.video_path)
            self._refresh_counts()
            self.table.viewport().update()
        elif index.column() == 2:
            self.edit_tags(item, index)

    def eventFilter(self, watched, event):
        if watched is getattr(self, "table", None) and event.type() == QEvent.KeyPress and self.state == "scanned":
            if event.key() == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
                self.select_all()
                return True
            if event.key() == Qt.Key_Space:
                index = self.table.currentIndex()
                if index.isValid():
                    self._table_click(index)
                    return True
        return super().eventFilter(watched, event)

    def edit_tags(self, item, index=None):
        if self._tag_editor is not None:
            self._tag_editor.close_popup()
            self._tag_editor.deleteLater()
        editor = self._tag_editor = TagSelect(self.table.viewport(), per_file=True)
        editor.set_batch_tags(self.batch_tags)
        editor.set_value(item.tags)
        box = self.table.visualRect(index) if index is not None else self.table.rect()
        editor.setGeometry(box)
        editor.changed.connect(lambda: self._tag_changed(item, editor))
        editor.hide()
        editor.open_popup()

    def _tag_changed(self, item, editor):
        item.tags = editor.value()
        self._refresh_counts()
        self._queue_render()

    def select_all(self):
        visible = [item for item in self.source_model.items() if not isinstance(item, str) and item.valid]
        paths = {item.video_path for item in visible}
        if paths and paths <= self.selected:
            self.selected.difference_update(paths)
        else:
            self.selected.update(paths)
        self._refresh_counts()
        self.table.viewport().update()

    def read_folder(self, source):
        if self.state in ("reading", "building") or self.engine.is_running():
            return
        self._fixture = None
        self.source.set_path(source)
        self.settings.set("creator", "source", source)
        self.settings.save()
        self.items, self.report, self.selected = [], [], set()
        self._done, self._total, self._reading_name = 0, 0, ""
        self._job = Run(self.services, "creator", "Reading the source folder")
        worker = ReadWorker(source, self)
        self._read_worker = worker
        self.stop_read.setEnabled(True)
        self._workers.append(worker)
        worker.checking.connect(self._checking)
        worker.item.connect(self._read_item)
        worker.completed.connect(self._read_completed)
        worker.error.connect(self._read_error)
        worker.finished.connect(lambda: self._workers.remove(worker))
        worker.finished.connect(worker.deleteLater)
        self._set_state("reading")
        worker.start()

    def _checking(self, name, checked, total):
        self._reading_name, self._done, self._total = name, checked, total
        self.read_count.setText(f"{checked} / {total}")
        self.read_line.set_text(f"{name} — reading size and resolution")
        self.read_bar.set_value(checked, total)
        if self._job:
            self._job.update("reading the source folder", checked, total)
        self._update_header()
        self._queue_render()

    def _read_item(self, item, checked, total):
        self.items.append(item)
        if item.valid:
            self.selected.add(item.video_path)
        self._done, self._total = checked, total
        self.read_count.setText(f"{checked} / {total}")
        self.read_bar.set_value(checked, total)
        self._refresh_counts()
        self._queue_render()

    def _read_completed(self, items):
        stopped = bool(self._read_worker and self._read_worker.cancelled.is_set())
        self._read_worker = None
        self.items = items
        self._reading_name = ""
        if self._job:
            self._job.finish("stopped" if stopped else "clean", f"{len(items)} files checked", journal=False)
            self._job = None
        if not any(item.valid for item in items):
            self.empty.set_title("No videos found")
            self.empty.set_body("Choose a folder containing readable videos. "
                                + " · ".join(ext.lstrip(".") for ext in engine.VIDEO_EXTS))
            self.empty.set_tone("neutral")
            self._set_state("error")
        else:
            self._set_state("scanned")

    def _read_error(self, message):
        self._read_worker = None
        if self._job:
            self._job.fail(message)
            self._job = None
        self.empty.set_title("ffmpeg is missing" if "ffmpeg" in message else "Could not read the source folder")
        self.empty.set_body("Install ffmpeg and add it to PATH, or restore the imageio-ffmpeg package, "
                            "then choose the folder again." if "ffmpeg" in message else message)
        self.empty.set_tone("danger")
        self._set_state("ffmpeg-missing" if "ffmpeg" in message else "error")

    def stop_reading(self):
        if self._read_worker:
            self._read_worker.cancelled.set()
            self.stop_read.setEnabled(False)
            self.read_line.set_text("Stopping after the current probe…")

    def start_build(self):
        if self.state != "scanned" or self.engine.is_running():
            return
        chosen = build_items(self.items, self.selected, self.batch_tags, self.skip.isChecked())
        if not chosen:
            return
        target = self.target.path()
        move = self.mode.current_index() == 1
        dialog = ConfirmDialog(f"Build {len(chosen)} wallpapers?",
                               f"Write {fmt.size(sum(i.size for i in chosen))} of video, previews and "
                               f"project.json into {target}.", self.window(), icon="creator",
                               destructive=move, confirm_text=f"{'Move' if move else 'Copy'} and build {len(chosen)}",
                               steps=[("Create each wallpaper", "Copy video, render preview, write project.json"),
                                      ("Verify the output", "Check video size, preview and read project.json back")]
                               + ([("Remove verified sources", "Skipped and failed files stay untouched")] if move else []))
        self.last_dialog = dialog
        if not dialog.ask():
            return
        # Include selected no-tag rows in the report even when they are filtered out of the work.
        selected = [copy.copy(item) for item in self.items if item.video_path in self.selected]
        for item in selected:
            item.tags = None if item.tags is None else list(item.tags)
        self._begin_build(selected, target, move)

    def _begin_build(self, items, target, move):
        chosen = build_items(items, {i.video_path for i in items}, self.batch_tags, self.skip.isChecked())
        paths = {item.video_path for item in chosen}
        self._excluded = [engine.BuildEngine._entry(item,
                         "failed" if item.reason and not item.reason.startswith("unsupported") else "skipped",
                         item.reason or "skipped — no tags")
                         for item in items if item.video_path not in paths]
        self._excluded += [engine.BuildEngine._entry(item,
                           "skipped" if item.reason.startswith("unsupported") else "failed", item.reason)
                           for item in self.items if item.reason and item.video_path not in {i.video_path for i in items}]
        items = chosen
        self._target = target
        self._cancelled = False
        self._started = datetime.now()
        self._started_clock = time.monotonic()
        self._done, self._total = 0, len(items)
        self._build_count = len(build_items(items, {i.video_path for i in items}, self.batch_tags, self.skip.isChecked()))
        self._estimate = estimate_seconds(self.services.journal if self.services else None, self._build_count)
        self.report = []
        self.run_rows = [dict(name=item.basename, status="queued", size=item.size) for item in items] + self._excluded
        self._job = Run(self.services, "creator", f"Building {self._build_count} wallpapers", activity="build")
        self.log.clear()
        self.log.set_file(Path(self._job.job.log_path).name if self._job.job else "creator.log", writing=True)
        self.log.set_live(True)
        self.pause_button.setText("Pause")
        self.stop_button.setEnabled(True)
        self._set_state("building")
        self.engine.start(items, target, move=move, tags=list(self.batch_tags), skip_needs_tags=self.skip.isChecked())

    def _progress(self, done, total):
        self._done, self._total = done, total
        self.run_count.setText(f"{done} / {total}")
        self.ring.set_value(done, total)
        self.run_bar.set_value(done, total)
        if self._job:
            self._job.update(f"building {done} of {total} wallpapers", done, total)
        left = self._estimate * (total - done) / total if self._estimate and total else None
        self.eta_label.setText(fmt.left(left) if left else "")

    def _item_progress(self, name, phase, preview):
        for entry in self.run_rows:
            if entry["name"] == name:
                entry.update(status="working", phase=f"{phase} · {fmt.size(entry['size'])}", preview_path=preview)
                self.current_line.set_text(f"{name} · {entry['phase']}")
                self.current_line.set_thumb(preview)
                break
        self._queue_render()

    def _item_done(self, name, status, folder, preview):
        for entry in self.run_rows:
            if entry["name"] == name:
                entry.update(status=status, folder=folder, preview_path=preview,
                             phase=f"done · {folder}" if folder else entry.get("reason") or "see log")
                break
        self.run_metrics.set_value(0, sum(e["status"] == "ok" for e in self.run_rows))
        self.run_metrics.set_value(1, sum(e["status"] == "skipped" for e in self.run_rows))
        self._queue_render()

    def _item_result(self, result):
        for row in self.run_rows:
            if row["name"] == result["name"]:
                row.update(result)
                break
        self.run_metrics.set_value(2, fmt.size(sum(e.get("bytes_written", 0) for e in self.run_rows)))
        self._queue_render()

    def _on_finished(self, report):
        report = list(report) + getattr(self, "_excluded", [])
        self.report = report
        self._ended = datetime.now()
        self._elapsed = max(0, time.monotonic() - self._started_clock) if self._started_clock is not None else 0
        created = sum(entry["status"] == "ok" for entry in report)
        skipped = sum(entry["status"] == "skipped" for entry in report)
        failed = sum(entry["status"] == "failed" for entry in report)
        written = sum(entry.get("bytes_written", 0) for entry in report)
        result = "stopped" if self._cancelled else "problems" if created and failed else "failed" if failed or not report else "clean"
        summary = f"{created} wallpaper{'s' if created != 1 else ''} created"
        if self._job:
            self._job.finish(result, summary, detail=f"created={created} · seconds={self._elapsed:.2f} · "
                              f"bytes={written} · {skipped} skipped · {failed} failed · in {self._target}",
                              chip="Failed" if failed else None)
            self._job = None
        self.result_title.setText(summary)
        self.result_sentence.setText(f"Took {fmt.duration(self._elapsed)} · {fmt.size(written)} written. "
                                    + ("Stopped; files not built remain in the source." if self._cancelled
                                       else "Wallpaper Engine will show them after the next playlist rebuild."))
        tone = "warn" if failed or self._cancelled else "ok"
        self.result_panel.set_tone(tone)
        self.result_disc.set("warn" if tone == "warn" else "check", tone)
        for index, value in enumerate((created, skipped, failed)):
            self.result_metrics.set_value(index, value)
        self.done_chip.set_text(f"{created} done")
        self.done_footer.set_text(f"{created} created")
        self.retry_button.setEnabled(any(e["status"] != "ok" and e.get("video_path") for e in report))
        self.rebuild_button.setVisible(playlist_available(self._target, getattr(self.config, "destination", "")))
        self.log.set_live(False)
        self.log.set_file(self.services.logs.path_for("creator").name if self.services else "creator.log", writing=False)
        self._set_state("done")
        if not self._on_screen:
            self.messages.append((result, summary))
            toasts = getattr(self.window(), "toasts", None)
            if toasts:
                toasts.show_toast(summary, "warn" if failed else "ok", action="Show",
                                  on_action=lambda: self.navigate.emit("creator"))

    def pause_resume(self):
        if self.engine.is_paused():
            self.engine.resume()
            self.pause_button.setText("Pause")
            self.pause_button.set_icon("pause")
        else:
            self.engine.pause()
            self.pause_button.setText("Resume")
            self.pause_button.set_icon("play")
            self.eta_label.setText("Paused after the current wallpaper")

    def stop_build(self):
        self._cancelled = True
        self.engine.cancel()
        self.stop_button.setEnabled(False)
        self.current_line.set_text("Stopping; unfinished output will be removed")

    def prepare_retry(self):
        paths = {e.get("video_path") for e in self.report if e["status"] != "ok"}
        self.items = [item for item in self.items if item.video_path in paths]
        self.selected = {item.video_path for item in self.items if item.valid}
        self._set_state("scanned")
        self.filter.set_current_index(0)
        # The normal tag selector, subset and explicit build confirmation are used again.
        if self.items:
            self.tags.open_popup()

    def rebuild_playlist(self):
        if self.on_rebuild and playlist_available(self._target, getattr(self.config, "destination", "")):
            self.navigate.emit("rotator")
            self.on_rebuild()

    def clear(self):
        if self.state in ("reading", "building"):
            return
        self.items, self.report, self.selected = [], [], set()
        self.source.set_path("")
        self.settings.set("creator", "source", "")
        if self._fixture is None:
            self.settings.save()
        self.empty.set_title("No source folder yet")
        self.empty.set_body("Drop a folder here, or choose one. Nothing is created until you press Build.")
        self.empty.set_tone("neutral")
        self._set_state("empty")

    def _log(self, text):
        if self._job:
            self._job.log_text(text)
        kind = "error" if "[ERROR]" in text else "warn" if "[WARN]" in text else "done" if "[OK]" in text else "info"
        self.log.append(datetime.now(), kind, text)

    def _open(self, path):
        if path and self._fixture is None:
            external.popen(["explorer", str(path)])

    def _open_logs(self):
        if self.services:
            self._open(str(self.services.logs.root / "creator"))

    def _open_created(self, index):
        entry = self.done_model.item_at(index.row())
        if entry.get("folder"):
            self._open(os.path.join(self._target, entry["folder"]))

    def on_shown(self):
        self._on_screen = True
        if self._fixture is not None or self.state in ("reading", "building"):
            return
        target = self.settings.get("creator", "target", default_creator_target())
        self.target.set_path(target)
        source = self.settings.get("creator", "source", "")
        if source and (source != self.source.path() or self.state == "empty"):
            self.read_folder(source)

    def on_hidden(self):
        self._on_screen = False

    def frame_fixture(self, state):
        if state not in self.FIXTURES:
            return None
        data = json.loads((Path(__file__).resolve().parents[2] / "tests/fixtures/ui/creator.json").read_text(encoding="utf-8"))
        spec = data[state]
        jobs = []
        if state in ("reading", "building"):
            phase = "reading the source folder" if state == "reading" else f"building {spec['checked']} of {spec['total']} wallpapers"
            jobs = [dict(tool="creator", title="Creator", phase=phase,
                         done=spec["checked"], total=spec["total"])]
        return {"frame": "idle", "jobs": jobs,
                "nav": {"creator": {"kind": "status", "text": "working" if state in ("reading", "building") else "idle"}}}

    def load_fixture(self, state):
        if state not in self.FIXTURES:
            raise KeyError(state)
        fixture = json.loads((Path(__file__).resolve().parents[2] / "tests/fixtures/ui/creator.json").read_text(encoding="utf-8"))
        self.tags.close_popup()
        self.fixture_dialog = None
        self._fixture = fixture
        spec = fixture[state]
        self.source.set_path(spec.get("source", ""))
        self._target = fixture["target"]
        self.target.set_path(self._target)
        self.tags.set_value(fixture["tags"])
        self.batch_tags = self.tags.tags()
        self.mode.set_current_index(int(spec.get("move", False)))
        self.items = []
        for data in spec.get("items", []):
            # Fixture metadata is already read: never stat a fabricated drive.
            item = engine.VideoItem.__new__(engine.VideoItem)
            item.__dict__.update(video_path=os.path.join(spec["source"], data["name"]), basename=data["name"],
                                 filename=os.path.splitext(data["name"])[0], size=data["size"], _exists=True,
                                 width=data.get("width"), height=data.get("height"), duration=data.get("duration"),
                                 tags=data.get("tags"), metadata_read=True, reason=data.get("reason"))
            self.items.append(item)
        self.selected = {item.video_path for item in self.items if item.valid}
        self._estimate = spec.get("estimate")
        self._done, self._total = spec.get("checked", len(self.items)), spec.get("total", len(self.items))
        self._reading_name = spec.get("current", "")
        if state == "reading":
            self.read_count.setText(f"{self._done} / {self._total}")
            self.read_bar.set_value(self._done, self._total)
            self.read_line.set_text(self._reading_name + " — reading size and resolution")
        elif state == "building":
            self._build_count = spec["total"]
            self._started = datetime.fromisoformat(spec["started"])
            self.run_rows = spec["rows"]
            self._progress(spec["checked"], spec["total"])
            self.current_line.set_text(spec["current"] + " · making preview")
            self.run_metrics.set_value(0, spec["checked"])
            self.run_metrics.set_value(1, 1)
            self.run_metrics.set_value(2, fmt.size(spec["written"]))
            self.log.append(self._started, "done", "Verified wallpaper written")
            self.log.set_live(True)
        elif state == "done":
            self._ended = datetime.fromisoformat(spec["ended"])
            self._elapsed = spec["elapsed"]
            self.report = spec["report"]
            created = sum(e["status"] == "ok" for e in self.report)
            self.result_title.setText(f"{created} wallpapers created")
            self.result_sentence.setText(f"Took {fmt.duration(self._elapsed)} · {fmt.size(spec['written'])} written. "
                                        "Wallpaper Engine will show them after the next playlist rebuild.")
            for i, status in enumerate(("ok", "skipped", "failed")):
                self.result_metrics.set_value(i, sum(e["status"] == status for e in self.report))
            self.done_chip.set_text(f"{created} done")
            self.done_footer.set_text(f"{created} created")
            self.rebuild_button.setVisible(playlist_available(self._target, getattr(self.config, "destination", "")))
        elif state == "ffmpeg-missing":
            self.empty.set_title("ffmpeg is missing")
            self.empty.set_body("Install ffmpeg and add it to PATH, or restore the imageio-ffmpeg package. Then choose your source folder again.")
            self.empty.set_tone("danger")
        self._set_state(state)
        if state == "empty":
            self.tags.open_popup()
            self.fixture_dialog = self.tags._popup
