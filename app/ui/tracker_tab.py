"""Tracker tab — how far Wallpaper Engine has got through the active playlist.

One card per monitor showing `seen/total`, and, underneath, the two lists that
card is made of: what has already been shown and what is still waiting. The
counting itself lives in ``engines/tracker.py``; this is only its face.

The tab shows a `TrackerFeed`: the window's, which the snapshot and the
activity journal also listen to (app/services), or one of its own when the
tab is built alone. A separate tray process looks too, and both write the
same ``data/tracker.json``.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QComboBox, QSplitter,
    QMessageBox, QFormLayout, QAbstractItemView,
)

from .. import animations, external, theme
from ..engines.tracker import (
    ANCHOR_NONE, Progress, Tracker, elapsed_since, format_minutes, pick_primary,
    title_for)
from ..tracker_feed import TrackerFeed, heartbeat_setting
from .kit import LinkButton
from .widgets import FolderListPanel


class MonitorCard(QGroupBox):
    """The `seen/total` headline for a single monitor's playlist."""

    reset_requested = Signal(str)   # monitor

    def __init__(self, monitor: str, parent=None):
        super().__init__(monitor, parent)
        self.monitor = monitor
        self._last_seen: int | None = None

        layout = QVBoxLayout(self)

        head = QHBoxLayout()
        self.count = QLabel("—")
        f = QFont()
        f.setPointSize(22)
        f.setBold(True)
        self.count.setFont(f)
        head.addWidget(self.count)
        head.addSpacing(16)
        self.percent = QLabel("")
        self.percent.setStyleSheet(theme.label_style("muted", size=15))
        head.addWidget(self.percent)
        head.addStretch()
        self.reset_btn = QPushButton("New cycle")
        self.reset_btn.setToolTip(
            "Reset the counter and start counting this playlist from scratch.\n"
            "Rarely needed — a playlist swapped in by a rotation is detected on its own.")
        self.reset_btn.clicked.connect(lambda: self.reset_requested.emit(self.monitor))
        head.addWidget(self.reset_btn)
        layout.addLayout(head)

        self.bar = animations.SmoothProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setMinimumHeight(14)
        layout.addWidget(self.bar)

        self.stats = QLabel("")
        self.stats.setStyleSheet(theme.label_style("muted"))
        layout.addWidget(self.stats)

        self.origin = QLabel("")
        self.origin.setStyleSheet(theme.label_style("faint", size=11))
        self.origin.setWordWrap(True)
        layout.addWidget(self.origin)

        self.now = QLabel("")
        self.now.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.now.setWordWrap(True)
        layout.addWidget(self.now)

        self.cycle_info = QLabel("")
        self.cycle_info.setStyleSheet(theme.label_style("faint", size=11))
        layout.addWidget(self.cycle_info)

    def update_from(self, p: Progress) -> None:
        self.setTitle(f"{p.monitor}  ·  playlist “{p.playlist}”")
        # The count moves once every ten minutes, on a card nobody is watching
        # at that moment. Colour it briefly so the change is not missed.
        if self._last_seen is not None and p.seen != self._last_seen:
            animations.flash(self.count, "ok" if p.seen >= p.total else "accent")
        self._last_seen = p.seen

        self.count.setText(p.label)
        self.percent.setText(f"{p.percent}%")
        self.bar.setMaximum(max(p.total, 1))
        self.bar.setValue(p.seen)

        if p.remaining:
            left = f"~{format_minutes(p.eta_minutes)} of screen time"
            if p.finish_estimate:
                left += f", so ~{p.finish_estimate} at this cycle's pace"
            parts = [f"{p.remaining} left", left]
        else:
            parts = ["whole playlist shown — time to rotate"]
        parts.append(f"changes: {p.changes}")
        if p.repeats:
            parts.append(f"repeats: {p.repeats}")
        parts.append(f"{p.order}, every {p.delay} min")
        self.stats.setText("  ·  ".join(parts))

        if p.restarted_from and p.previous_finished:
            origin = (f"Wallpaper Engine began the next pass at "
                      f"{(p.restarted_at or '')[11:16]}, after all "
                      f"{p.restarted_from.split('/')[-1]} of the last one were shown")
        elif p.restarted_from:
            origin = (f"Wallpaper Engine started the playlist over at "
                      f"{(p.restarted_at or '')[11:16]} — the previous count had reached "
                      f"{p.restarted_from}")
        else:
            origin = ("cycle counted from when tracking started" if p.anchor == ANCHOR_NONE
                      else f"cycle dated from {p.anchor}")
        if p.from_engine:
            origin += "  ·  following Wallpaper Engine's own record of the pass"
        if p.inferred:
            origin += (f"  ·  {p.inferred} drawn while nothing was watching"
                       if p.from_engine else
                       f"  ·  {p.inferred} restored from file access times")
        if p.gone:
            # Deleted wallpapers Wallpaper Engine still lists. Held out of the
            # total, because otherwise they stall the count short of the end.
            origin += (f"  ·  {p.gone} deleted since the playlist was built, "
                       f"not counted")
        self.origin.setText(origin)

        if p.current:
            stale = ("" if p.live else
                     "  (Wallpaper Engine is not running — last known)" if p.from_engine else
                     "  (nothing open — last known)")
            self.now.setText(f"<b>Now:</b> {p.current_title}  "
                             f"<span style='color:{theme.css('text.lo')}'>— for "
                             f"{elapsed_since(p.current_since)}{stale}</span>")
            self.now.setToolTip(p.current)
        else:
            self.now.setText("<b>Now:</b> nothing found — Wallpaper Engine is not running, "
                             "or is showing something outside the playlist")
            self.now.setToolTip("")

        self.cycle_info.setText(
            f"cycle started {p.started} · running {elapsed_since(p.started)}")


class TrackerTab(QWidget):
    # "Change in Settings": the window goes to the Settings page.
    settings_requested = Signal()

    def __init__(self, settings, feed: TrackerFeed | None = None):
        super().__init__()
        self.settings = settings
        self.feed = feed or TrackerFeed(settings.get("tracker", "we_config", None),
                                        heartbeat_setting(settings), parent=self)
        self.cards: dict[str, MonitorCard] = {}
        self._list_key: tuple | None = None

        layout = QVBoxLayout(self)

        layout.addWidget(self._build_config_box())

        self.cards_box = QVBoxLayout()
        layout.addLayout(self.cards_box)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(theme.label_style("danger"))
        self.status.hide()
        layout.addWidget(self.status)

        layout.addWidget(self._build_lists(), 1)

        self.feed.updated.connect(self._show)
        # A config.json chosen on the Settings page: the old monitors go first.
        self.feed.config_changed.connect(self._clear_cards)
        if self.feed.results:
            self._show()          # the tray's feed has looked already

    @property
    def tracker(self) -> Tracker:
        return self.feed.tracker

    # ------------------------------------------------------------- building
    def _build_config_box(self) -> QGroupBox:
        box = QGroupBox("Source")
        form = QFormLayout(box)

        controls = QWidget()
        c = QHBoxLayout(controls)
        c.setContentsMargins(0, 0, 0, 0)
        # config.json, how often to look, and counting in the background are
        # set on the Settings page.
        where = QLabel("Wallpaper Engine's config.json, how often to check, and counting "
                       "in the background are on the Settings page.")
        where.setWordWrap(True)
        c.addWidget(where, 1)
        change = LinkButton("Change in Settings")
        change.clicked.connect(self.settings_requested.emit)
        c.addWidget(change)
        c.addSpacing(16)

        self.rebuild_btn = QPushButton("Rebuild from file times")
        self.rebuild_btn.setToolTip(
            "Re-derive every count from the wallpapers' last-access times.\n"
            "Runs by itself when a playlist is adopted or polling has been away,\n"
            "so this is only needed to force a recount.")
        self.rebuild_btn.clicked.connect(self._rebuild)
        c.addWidget(self.rebuild_btn)

        refresh_btn = QPushButton("Refresh now")
        refresh_btn.clicked.connect(lambda: self.feed.refresh())
        c.addWidget(refresh_btn)
        form.addRow("", controls)

        if self.tracker.atime_ok is False:
            warn = QLabel(
                "NTFS last-access updates are off on this machine, so time when nothing "
                "was polling cannot be recovered — keep the background tracker running.")
            warn.setWordWrap(True)
            warn.setStyleSheet(theme.label_style("warn"))
            form.addRow("", warn)
            self.rebuild_btn.setEnabled(False)
        return box

    def _rebuild(self):
        recovered = self.tracker.rebuild()
        self._list_key = None
        self.feed.refresh()
        QMessageBox.information(
            self, "Rebuild from file times",
            f"Recovered {recovered} wallpaper(s) that had been shown without being watched."
            if recovered else
            "Nothing to recover — the counts already match the wallpapers' file times.")

    def _build_lists(self) -> QWidget:
        box = QGroupBox("Playlist contents")
        outer = QVBoxLayout(box)

        head = QHBoxLayout()
        head.addWidget(QLabel("Monitor:"))
        self.monitor_pick = QComboBox()
        self.monitor_pick.currentTextChanged.connect(lambda _: self._refresh_lists())
        head.addWidget(self.monitor_pick)
        head.addStretch()
        outer.addLayout(head)

        hint = QLabel("Click a row to open its folder in Explorer.")
        hint.setStyleSheet(theme.label_style("faint", size=11))
        outer.addWidget(hint)

        split = QSplitter(Qt.Horizontal)
        self.shown_panel = FolderListPanel("Shown")
        self.left_panel = FolderListPanel("Not yet shown")
        self._shown_paths: list[str] = []
        self._left_paths: list[str] = []
        for panel, paths in ((self.shown_panel, lambda: self._shown_paths),
                             (self.left_panel, lambda: self._left_paths)):
            panel.refresh_btn.hide()
            panel.view.setSelectionMode(QAbstractItemView.SingleSelection)
            panel.view.clicked.connect(
                lambda index, p=panel, get=paths: self._reveal(p, get(), index))
            split.addWidget(panel)
        split.setSizes([1, 1])
        outer.addWidget(split)
        return box

    def _reveal(self, panel: FolderListPanel, paths: list[str], index) -> None:
        """Open Explorer on the wallpaper behind the clicked row."""
        row = panel.proxy.mapToSource(index).row()
        if not 0 <= row < len(paths):
            return
        target = Path(paths[row].replace("/", "\\"))
        try:
            if target.exists():
                # /select opens the containing folder with the file highlighted.
                external.popen(f'explorer /select,"{target}"')
                return
            # A playlist outlives its files: a later rotation carries folders back
            # to the reserve, and Wallpaper Engine keeps listing them. Open the
            # nearest folder that is still there instead of a dead end.
            folder = next((p for p in target.parents if p.is_dir()), None)
            if folder is not None:
                external.popen(f'explorer "{folder}"')
            else:
                QMessageBox.information(
                    self, "Open folder", f"Nothing of this path is left on disk:\n{target}")
        except OSError as e:
            QMessageBox.warning(self, "Open folder", f"Could not open Explorer:\n{e}")

    # -------------------------------------------------------------- actions
    def _reset(self, monitor: str):
        answer = QMessageBox.question(
            self, "New cycle",
            f"Reset the counter for {monitor} and start the playlist over?\n"
            "The current one is kept in the archive.")
        if answer == QMessageBox.Yes:
            self.tracker.reset(monitor)
            self.feed.refresh()

    # -------------------------------------------------------------- refresh
    def _clear_cards(self):
        for card in self.cards.values():
            card.setParent(None)
        self.cards.clear()
        self.monitor_pick.clear()
        self._list_key = None

    def _show(self):
        results = self.feed.results

        if self.tracker.error:
            self.status.setText(self.tracker.error)
            self.status.show()
        else:
            self.status.hide()

        # The leading monitor comes first and is what the lists below show, so
        # the playlist whose end matters is the one in front.
        primary = pick_primary(results, self.settings.get("tracker", "primary", None))
        if primary is not None:
            results = [primary] + [p for p in results if p is not primary]

        for p in results:
            card = self.cards.get(p.monitor)
            if card is None:
                card = MonitorCard(p.monitor)
                card.reset_requested.connect(self._reset)
                self.cards[p.monitor] = card
                self.cards_box.addWidget(card)
                self.monitor_pick.addItem(p.monitor)
            card.update_from(p)

        self._refresh_lists()

    def _refresh_lists(self):
        monitor = self.monitor_pick.currentText()
        if not monitor:
            self.shown_panel.set_names([])
            self.left_panel.set_names([])
            return
        shown, remaining = self.tracker.split_items(monitor)
        in_order = self.tracker.queue_known(monitor)
        # Refilling the models scrolls both views back to the top, so only do it
        # when something actually changed. In a sorted playlist the head of the
        # queue moves with the wallpaper on screen even when the counts do not,
        # so the head is part of what counts as a change.
        key = (monitor, len(shown), len(remaining), in_order,
               remaining[0] if remaining else None)
        if key == self._list_key:
            return
        self._list_key = key
        self.left_panel.set_title(
            "Up next, in playing order" if in_order else
            "Not yet shown — random order, any of these can be next")
        self.left_panel.setToolTip(
            "Wallpaper Engine plays this playlist sorted, so this is the queue: "
            "the top row comes next." if in_order else
            "This playlist plays in random order. Wallpaper Engine shuffles each "
            "pass and keeps the shuffle to itself, so every wallpaper here is "
            "equally likely to be next. Set the playlist's order to Sorted in "
            "Wallpaper Engine to see the real queue here.")
        self._shown_paths = [item for item, _when, _inferred in shown]
        self._left_paths = list(remaining)
        # "~" marks a row credited from its file's access time rather than watched.
        self.shown_panel.set_names(
            [f"{when} {'~' if inferred else ' '} {title_for(item)}"
             for item, when, inferred in shown])
        self.left_panel.set_names([title_for(item) for item in remaining])
