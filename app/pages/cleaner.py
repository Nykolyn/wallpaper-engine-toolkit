"""Workshop wallpapers that are still on disk but unavailable through Steam."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QStackedLayout, QVBoxLayout

from .. import theme
from ..engines import outside_playlists as outside
from ..engines import unavailable as engine
from ..engines.review import DEFAULT_SCOPE
from ..engines.rotator.config import Config
from ..engines.wallpaper_delete import delete_wallpapers
from ..services import begin
from ..ui.kit import (
    ButtonsCell, Callout, Cell, CellButton, Checkbox, CheckCell, Column, ConfirmDialog, Dropdown, EmptyState,
    GlassPanel, NavState, SecondaryButton, SegmentedControl, Table, TableBar, TableFooter, TableModel, TagsCell, format as fmt,
)
from . import on_steam_found
from .base import Page
from .folder_actions import delete_dialog, delete_tip, deleted_words, open_in_explorer
from .tracker import _Offload


INTRO = {
    "unavailable": "These Workshop items are still on disk, but Steam cannot describe them. "
                   "Open a row to inspect its folder, or keep it in your reserve.",
    "outside": "Subscribed wallpapers in this folder that are in no Wallpaper Engine playlist. "
               "Playlists are read again just before unsubscribing.",
}


FOOTER = {
    "unavailable": "DATE is the folder's creation time · + moves to the Rotator's reserve",
    "outside": "DATE is the folder's creation time · Trash unsubscribes and recycles",
}
SOURCE_NAME = {"unavailable": "Filter unavailable wallpapers by Wallpaper Engine folder",
               "outside": "The Wallpaper Engine folder whose wallpapers outside playlists are listed"}
TABLE_NAME = {"unavailable": "Unavailable wallpapers; click a row to open its folder",
              "outside": "Subscribed wallpapers in no playlist; click a row to open its folder"}
KEPT = object()   # a worker's result for a wallpaper found in a playlist at delete time


class WallpaperModel(TableModel):
    def __init__(self, page):
        self.page = page
        super().__init__((Column("", 32, align="center", sortable=False),
                          Column("Folder", thumb="rowxs", min_width=200), Column("Author", 150),
                          Column("Type", 80), Column("Size", 100, mono=True, align="right"),
                          Column("Date", 136, mono=True), Column("Tags", theme.WALLPAPER_TAGS_WIDTH),
                          Column("Age", theme.WALLPAPER_AGE_WIDTH), Column("", 70, sortable=False)), parent=page)

    def cell(self, row, column):
        if column == 0:
            return CheckCell(row.folder in self.page.selected, not self.page.scanning and not self.page.acting)
        column -= 1
        if column == 0:
            return Cell(row.title, strong=True, sub=Path(row.folder).name)
        if column == 1:
            return row.author or fmt.DASH
        if column == 2:
            return row.kind.title() or fmt.DASH
        if column == 3:
            return fmt.size(row.size) if row.size is not None else fmt.DASH
        if column == 4:
            return (Cell(row.created.strftime("%Y-%m-%d"), sub=row.created.strftime("%H:%M:%S"))
                    if row.created else fmt.DASH)
        if column == 5:
            return TagsCell(row.tags) if row.tags else fmt.DASH
        if column == 6:
            return row.age or fmt.DASH
        enabled = not self.page.scanning and not self.page.acting
        trash = CellButton("delete", "trash", delete_tip(row.folder), enabled)
        if self.page.mode == "outside":
            return ButtonsCell((trash,))
        return ButtonsCell((trash, CellButton("reserve", "plus", "Move to reserve…", enabled)))

    def sort_key(self, row, column):
        if column == 4:
            return row.size
        if column == 5:
            return row.created
        return super().sort_key(row, column)

    def thumb_source(self, row):
        return row.folder

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.ToolTipRole and index.isValid() and index.column() == 1:
            row = self.item_at(index.row())
            return f"{row.folder}\n{row.note}".strip() if row is not None else None
        return super().data(index, role)


class CleanerPage(Page):
    key, title, icon = "cleaner", "Cleaner", "cleaner"
    FIXTURES = ("found", "empty", "checking", "failed", "outside")
    _progress = Signal(int, int)
    _open_failed = Signal(str)
    _action_progress = Signal(object, int, int)

    def __init__(self, settings, services=None, config=None, parent=None):
        super().__init__(parent)
        self.settings, self.services = settings, services
        self.config = config if config is not None else Config(source="", destination="")
        self._offload = _Offload(self)
        self.scanning = self.acting = False
        self._started = False
        self._check_waiting = False     # the startup check, skipped while Outside playlists was shown
        self._fixture = None
        self._rows = []
        self.selected = set()
        self._folders = {}
        self._checked = None
        # The tab not shown keeps its rows, folders, check and chosen folder here.
        self.mode = "unavailable"
        self._parked = {"outside": {"rows": [], "folders": {}, "checked": None, "source": None}}
        self._last_open = ("", 0.0)
        self.messages = []
        self._progress.connect(self._scan_progress)
        self._open_failed.connect(lambda words: self._say("danger", words))
        self._action_progress.connect(lambda run, done, total: run.update(done=done, total=total))

        column = QVBoxLayout(self)
        v, h = theme.BODY_PAD
        column.setContentsMargins(h, v, h, v)
        column.setSpacing(theme.PANEL_GAP)
        self.intro = Callout(INTRO["unavailable"], tone="neutral")
        column.addWidget(self.intro)
        self.problem = Callout(tone="warn")
        self.problem.hide()
        column.addWidget(self.problem)
        card = GlassPanel(padding="none")
        body = QVBoxLayout(card)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        toolbar = TableBar()
        self.tabs = SegmentedControl(("Unavailable", "Outside playlists"))
        self.tabs.setAccessibleName("Unavailable wallpapers, or subscribed wallpapers in no playlist")
        self.tabs.changed.connect(self._switch)
        toolbar.add(self.tabs)
        self.all_rows = Checkbox("Select all", tristate=True)
        self.all_rows.setAccessibleName("Select all visible wallpapers")
        self.all_rows.clicked.connect(self.select_all)
        toolbar.add(self.all_rows)
        self.source = Dropdown(prefix="Folder")
        self.source.setAccessibleName(SOURCE_NAME["unavailable"])
        self.source.add_item("All wallpapers", "")
        self.source.currentIndexChanged.connect(self._source_chosen)
        toolbar.add(self.source)
        self.delete_selected = SecondaryButton("Delete selected", icon="trash")
        self.delete_selected.clicked.connect(lambda: self._selected_action("delete"))
        toolbar.add(self.delete_selected)
        self.reserve_selected = SecondaryButton("Move selected to reserve", icon="plus")
        self.reserve_selected.clicked.connect(lambda: self._selected_action("reserve"))
        toolbar.add(self.reserve_selected)
        toolbar.add_stretch()
        self.refresh = SecondaryButton("Check again", icon="refresh")
        self.refresh.clicked.connect(lambda: self.check(refresh=True))
        toolbar.add(self.refresh)
        body.addWidget(toolbar)
        self.model = WallpaperModel(self)
        self.table = Table()
        self.table.setAccessibleName(TABLE_NAME["unavailable"])
        self.table.setModel(self.model)
        self.table.clicked.connect(self._open)
        self.table.activated.connect(self._open)
        self.table.action_clicked.connect(self._action)
        self.table.installEventFilter(self)
        self.empty = EmptyState("Not checked yet", "The library is checked in the background when "
                                "the app starts.", icon="warn")
        self.content = QStackedLayout()
        body.addLayout(self.content, 1)
        self.content.addWidget(self.table)
        self.content.addWidget(self.empty)
        self.footer = TableFooter()
        body.addWidget(self.footer)
        column.addWidget(card, 1)
        self._render()

    def startup(self):
        """Called once by the live window, never by snapshots or start=False tests."""
        if not self._started and self._fixture is None:
            self._started = True
            on_steam_found(self, self._startup_check)

    def _startup_check(self):
        """The Steam check at start. If Outside playlists is shown it waits:
        switching back to Unavailable runs it."""
        self._check_waiting = self.mode != "unavailable"
        if not self._check_waiting:
            self.check()

    def check(self, *, refresh=False):
        if self.scanning or self.acting or self._fixture is not None:
            return False
        self.scanning = True
        self.problem.hide()
        self._render()
        config_path = self.settings.get("tracker", "we_config", None)
        cache = self.services.data_dir / "steam_cache.sqlite" if self.services is not None else None
        if self.mode == "outside":
            self._offload.run(lambda: outside.scan(config_path=config_path, cache_path=cache),
                              self._scanned)
            return True
        self._offload.run(lambda: engine.scan(config_path=config_path, cache_path=cache,
                                              refresh=refresh, on_progress=self._report_progress),
                          self._scanned)
        return True

    def _switch(self, index):
        """Park the shown tab's state, bring the other's back; its first showing scans."""
        mode = ("unavailable", "outside")[index]
        if mode == self.mode:
            return
        self._parked[self.mode] = {"rows": self._rows, "folders": self._folders,
                                   "checked": self._checked, "source": self.source.currentData()}
        state = self._parked.pop(mode)
        self.mode = mode
        self._rows, self._folders, self._checked = state["rows"], state["folders"], state["checked"]
        self.selected.clear()
        self.problem.hide()
        self.intro.set_body(INTRO[mode])
        self.reserve_selected.setVisible(mode == "unavailable")
        self._fill_sources(state["source"])
        self._filter()
        self.source.setAccessibleName(SOURCE_NAME[mode])
        self.table.setAccessibleName(TABLE_NAME[mode])
        if mode == "outside" and self._checked is None and self._fixture is None:
            self.check()
        elif mode == "unavailable" and self._check_waiting:
            self._check_waiting = False
            self.check()

    def _fill_sources(self, selected):
        """The dropdown for the shown tab. Outside playlists starts at the remembered
        folder, else Review's (see outside_playlists.default_folder)."""
        if self.mode == "outside":
            selected = outside.default_folder(
                self.settings.get("cleaner", "outside_folder", None),
                str(self.settings.get("review", "scope", DEFAULT_SCOPE) or DEFAULT_SCOPE),
                self._folders)
        self.source.blockSignals(True)
        self.source.clear()
        self.source.add_item("All wallpapers", "")
        for name in sorted(self._folders, key=str.casefold):
            self.source.add_item(name, name)
        self.source.setCurrentIndex(max(0, self.source.findData(selected)))
        self.source.blockSignals(False)

    def _source_chosen(self, *_):
        if self.mode == "outside":
            self.settings.set("cleaner", "outside_folder", self.source.currentData() or "")
        self._filter()

    def _report_progress(self, done, total):
        try:
            self._progress.emit(done, total)
        except RuntimeError:
            pass

    def _scan_progress(self, done, total):
        if self.scanning:
            self.set_subtitle(f"Checking {fmt.count(done)} of {fmt.count(total)} wallpapers")

    def _scanned(self, result):
        self.scanning = False
        if isinstance(result, Exception):
            self.problem.set_body(f"Check failed: {result} "
                                  + ("The previous results are still shown." if self._checked is not None else ""))
            self.problem.show()
        else:
            self._rows, self._folders = result.rows, result.folders
            self._checked = len(result.rows) if self.mode == "outside" else result.checked
            self._fill_sources(self.source.currentData())
            if getattr(result, "warning", ""):
                self.problem.set_body(result.warning)
                self.problem.show()
        self._filter()

    def _filter(self, *_):
        source = self.source.currentData()
        ids = set(self._folders.get(source, ())) if source else None
        self.model.set_rows(r for r in self._rows if ids is None or Path(r.folder).name in ids)
        self.selected.intersection_update(r.folder for r in self.model.items())
        self._render()

    def _render(self):
        total, shown = len(self._rows), self.model.rowCount()
        self.refresh.setEnabled(not self.scanning and not self.acting and self._fixture is None)
        enabled = not self.scanning and not self.acting
        self.tabs.setEnabled(enabled)
        self.source.setEnabled(enabled)
        self.all_rows.setEnabled(enabled and shown > 0)
        self.all_rows.blockSignals(True)
        self.all_rows.setCheckState(Qt.Unchecked if not self.selected else
                                   Qt.Checked if len(self.selected) == shown else Qt.PartiallyChecked)
        self.all_rows.blockSignals(False)
        self.delete_selected.setEnabled(enabled and bool(self.selected))
        self.reserve_selected.setEnabled(enabled and bool(self.selected))
        self.table.viewport().update()
        # The sidebar counts unavailable wallpapers only, whichever tab is shown.
        if self.mode == "unavailable":
            badge, checked, busy = total, self._checked, self.scanning
        else:
            parked = self._parked["unavailable"]
            badge, checked, busy = len(parked["rows"]), parked["checked"], False
        self.set_nav_state(NavState.badge(badge) if badge else NavState()
                           if checked is not None else
                           NavState.status("checking" if busy else "waiting"))
        if self.mode == "outside":
            self.set_subtitle("Reading your playlists…" if self.scanning else
                              f"{fmt.counted(total, 'wallpaper')} in no playlist")
        else:
            self.set_subtitle("Checking your Workshop library…" if self.scanning else
                              f"{fmt.counted(total, 'wallpaper')} to process"
                              + (f" · {fmt.count(self._checked)} checked" if self._checked is not None else ""))
        self.footer.set_note(FOOTER[self.mode])
        self.footer.set_text(f"{fmt.count(shown)} shown · {fmt.count(total)} "
                             + ("in no playlist" if self.mode == "outside" else "to process")
                             + (f" · {fmt.count(len(self.selected))} selected" if self.selected else ""))
        self.content.setCurrentWidget(self.table if shown else self.empty)
        if self.mode == "outside":
            self.empty.set_title("Reading your playlists" if self.scanning else
                                 "Not read yet" if self._checked is None else
                                 "Every wallpaper here is in a playlist")
            self.empty.set_body("Subscribed wallpapers in no Wallpaper Engine playlist are listed here.")
        elif self.scanning:
            self.empty.set_title("Checking the Workshop library")
            self.empty.set_body("You can keep using the app while this check runs.")
        elif self._checked is None:
            self.empty.set_title("Not checked yet")
            self.empty.set_body("Check again to look for unavailable Workshop wallpapers.")
        else:
            self.empty.set_title("Nothing to process" if not total else "Nothing in this folder")
            self.empty.set_body("No unavailable Workshop wallpapers were found here.")

    def _open(self, index):
        if index.column() == 0:
            if not self.scanning and not self.acting:
                row = self.model.item_at(index.row())
                if row is not None:
                    if row.folder in self.selected:
                        self.selected.remove(row.folder)
                    else:
                        self.selected.add(row.folder)
                    self._render()
            return
        if self._fixture is not None:
            return
        row = self.model.item_at(index.row())
        if row is None:
            return
        now = time.monotonic()
        if self._last_open[0] == row.folder and now - self._last_open[1] < 1:
            return
        self._last_open = row.folder, now
        open_in_explorer(row.folder, self._open_failed.emit)

    def select_all(self, *_):
        if self.scanning or self.acting:
            return
        visible = {row.folder for row in self.model.items()}
        self.selected = set() if visible <= self.selected else visible
        self._render()

    def eventFilter(self, watched, event):
        if watched is self.table and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
                self.select_all()
                return True
            if event.key() == Qt.Key_Space:
                index = self.table.currentIndex()
                if index.isValid():
                    self._open(self.model.index(index.row(), 0))
                return True
        return super().eventFilter(watched, event)

    def _selected_action(self, key):
        return self._process([row for row in self.model.items() if row.folder in self.selected], key)

    def _rotating(self):
        jobs = getattr(self.services, "jobs", None)
        page = getattr(self.window(), "pages", {}).get("rotator")
        return ((jobs is not None and any(jobs.is_running(k) for k in ("rotator", "reserve")))
                or (page is not None and (page.state == "running" or page._starting)))

    def _action(self, row, column, key):
        item = self.model.item_at(row)
        return self._process([item] if item is not None else [], key)

    def _process(self, items, key):
        if not items or key not in ("delete", "reserve") or self._fixture is not None:
            return False
        if key == "reserve" and self.mode == "outside":
            return False
        if self.scanning or self.acting:
            return False
        if self._rotating():
            self._say("warn", "Wait for the rotation or reserve copy to finish before processing wallpapers.")
            return False
        reserve = self.config.source
        config_path = self.settings.get("tracker", "we_config", None)
        name =f"“{items[0].title}”" if len(items) == 1 else fmt.counted(len(items), "selected wallpaper")
        folders = [item.folder for item in items]
        if key == "reserve":
            if not reserve or not reserve.strip():
                self._say("warn", "Set the reserve folder in Settings first.")
                return False
            dialog = ConfirmDialog(f"Move {name} to the reserve?",
                                   "Steam is asked to unsubscribe, and the folder moves into the "
                                   "reserve; if Steam will not, it is put back. On another drive "
                                   "a verified copy is made and the Workshop folder is recycled.",
                                   self.window(), lines=[f"To: {reserve}"] + folders,
                                   icon="plus", confirm_text="Move to reserve", safe_default=True)
        elif self.mode == "outside":
            saved = outside.saved_at(config_path)
            when = (f"Wallpaper Engine last saved its playlists at {saved:%Y-%m-%d %H:%M}"
                    if saved else "Wallpaper Engine has not saved its playlists yet")
            title = (f"Unsubscribe from {name} and delete it?" if len(items) == 1
                     else f"Unsubscribe from {name} and delete them?")
            dialog = ConfirmDialog(title, "Playlists are read again first: anything now in one is "
                                   "kept. Steam is then asked to unsubscribe, and each folder goes "
                                   "to the Recycle Bin.", self.window(), lines=[when] + folders,
                                   destructive=True, icon="trash", confirm_text="Unsubscribe and delete")
        elif len(items) == 1:
            dialog = delete_dialog(items[0].title, items[0].folder, self.window())
        else:
            dialog = ConfirmDialog(f"Unsubscribe from {name} and delete them?",
                                   "Steam is asked to unsubscribe from each item, then its folder "
                                   "goes to the Recycle Bin. Failed items stay in the table.",
                                   self.window(), lines=folders, destructive=True, icon="trash",
                                   confirm_text="Unsubscribe and delete")
        if not self._answer(dialog) or self._rotating():
            return False
        self.acting = True
        self._render()
        if key == "reserve":
            run = begin(self.key, f"Moving {name} to the reserve", activity="reserve_move")
        else:
            run = (begin(self.key, f"Deleting {name}", activity="outside_delete")
                   if self.mode == "outside" else
                   begin(self.key, f"Deleting {name}", activity="unavailable_delete"))
        run.update(done=0, total=len(items))
        mode = self.mode
        def work():
            # An unreadable config.json raises here and fails the batch before any delete.
            kept = outside.playlisted_ids(config_path) if mode == "outside" else set()
            todo = [item.folder for item in items if Path(item.folder).name not in kept]
            skipped = len(items) - len(todo)
            def progress(done, _total):
                self._action_progress.emit(run, skipped + done, len(items))
            # One Steam session for the whole selection (see wallpaper_delete).
            answers = dict(zip(todo, engine.move_many(todo, reserve, on_progress=progress)
                               if key == "reserve" else delete_wallpapers(todo, on_progress=progress)))
            return [(item, answers.get(item.folder, KEPT)) for item in items]
        self._offload.run(work, lambda result: self._acted(items, key, result, run))
        return True

    def _answer(self, dialog):
        return dialog.ask()

    def _acted(self, items, key, results, run):
        self.acting = False
        if isinstance(results, Exception):
            results = [(item, results) for item in items]
        succeeded, kept = set(), set()
        for item, result in results:
            if result is KEPT:
                kept.add(item.folder)
                tone, words = "ok", f"“{item.title}” is now in a playlist; it was kept."
            elif isinstance(result, Exception):
                tone, words = "danger", f"Could not process “{item.title}”: {result}"
            else:
                succeeded.add(item.folder)
                tone, words = ("ok", f"“{item.title}” is kept in the reserve: {result}") if key == "reserve" \
                    else deleted_words(item.title, result)
            run.log("error" if tone == "danger" else "info", words)
        # A wallpaper now in a playlist is no longer a candidate: its row goes too.
        self._rows = [r for r in self._rows if r.folder not in succeeded | kept]
        for parked in self._parked.values():    # a deleted folder is gone from the other tab too
            parked["rows"] = [r for r in parked["rows"] if r.folder not in succeeded]
        failed = len(items) - len(succeeded) - len(kept)
        if len(items) > 1:
            tone = ("warn" if succeeded or kept else "danger") if failed else "ok"
            words = (f"{len(succeeded)} of {len(items)} wallpapers "
                     + ("moved to reserve." if key == "reserve" else "unsubscribed and recycled.")
                     + (f" {len(kept)} now in a playlist, kept." if kept else "")
                     + (f" {failed} failed; see the Cleaner log." if failed else ""))
        run.update(done=len(items), total=len(items))
        run.finish("problems" if failed and succeeded else "failed" if failed else "clean", words)
        self._filter()
        self._say(tone, words)

    def _say(self, tone, words):
        self.messages.append((tone, words))
        toasts = getattr(self.window(), "toasts", None)
        if toasts is not None:
            toasts.show_toast(words, tone)

    def frame_fixture(self, state):
        return {"frame": "idle", "nav": {self.key: {"kind": "badge", "value": 2}
                                               if state == "found" else {"kind": "none"}}}

    def load_fixture(self, state):
        if state not in self.FIXTURES:
            return super().load_fixture(state)
        self._fixture = state
        if state == "outside":
            self.tabs.set_current_index(1)
            self._folders = {"all": ["9000000003", "9000000004"], "new": ["9000000004"]}
            self._rows = [engine.Wallpaper(r"X:\Workshop\431960\9000000003", "Paper lanterns", "Mira",
                                           "video", 31200000, datetime(2026, 11, 24, 9, 12)),
                          engine.Wallpaper(r"X:\Workshop\431960\9000000004", "Rain on glass", "", "scene",
                                           8400000, datetime(2026, 11, 24, 9, 14))]
            self._checked = len(self._rows)
            self._fill_sources("")
            self._filter()
            return
        self._checked = 2400 if state in ("found", "empty") else None
        self.scanning = state == "checking"
        self._folders = {"new": ["9000000001"], "favorites": ["9000000002"]}
        self.source.blockSignals(True)
        self.source.clear()
        self.source.add_item("All wallpapers", "")
        for name in self._folders:
            self.source.add_item(name, name)
        self.source.blockSignals(False)
        self._rows = [engine.Wallpaper(r"X:\Workshop\431960\9000000001", "Moonlit garden", "", "video",
                                       2450000, datetime(2026, 9, 16, 23, 26)),
                      engine.Wallpaper(r"X:\Workshop\431960\9000000002", "A quiet evening", "Mira", "scene",
                                       1243000000, datetime(2026, 10, 2, 17, 22))] if state == "found" else []
        self.problem.setVisible(state == "failed")
        self.problem.set_body("Check failed: Steam is unreachable. Try again when your connection returns.")
        self._filter()
