"""Settings: the things set once, in one place.

Not in the design; built from its parts (GlassPanel, PathField, SpinBox,
Toggle, Dropdown, the FormDialog's overline rows). Every change is saved as it
is made — there is no Save button — through the store it belongs to: the
Rotator's own `Config` (`data/config.json`) for its folders and batch size,
and `Settings` (`data/suite.json`) for everything else. The two stores stay
two (REDESIGN_PLAN §2.3).

- Folders: the reserve, myprojects, the duplicates folder and the folders per
  run (the Rotator's), the Creator's source and
  output. Each path is checked on a worker, never on the window's thread. The
  Rotator's are read-only while a rotation runs: the run is using them.
- Wallpaper Engine: its config.json, and how often to look when nothing
  announces a change.
- Tracker and tray: counting in the background (a logon task, asked about on a
  worker: `schtasks` takes a moment), and the monitor the tray leads with.
- Review and Steam: Review's own dialogs — Review settings (the source, how a
  click subscribes, the Steam key, the second backup folder) and the authors
  database.
- About: the name and version, the data and log folders, the selfcheck.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QGridLayout, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .. import __version__, autostart, external, install_info, selfcheck, theme
from ..branding import FULL_NAME
from ..engines.rotator.config import Config
from ..engines import updates
from ..services.snapshot import PLAYLIST, RESERVE, ROTATION
from ..settings import (
    DEFAULT_CREATOR_SOURCE, Settings, app_data_dir, default_creator_target,
)
from ..tracker_feed import heartbeat_setting
from ..ui.kit import (
    AccentButton, BrandMark, CardTitle, ConfirmDialog, Dropdown, GlassPanel, LinkButton,
    NavState, Overline, PathField, ProgressBar, SecondaryButton, SpinBox, Toggle, format as fmt, label,
)
from ..ui.kit.base import set_tone
from ..ui.kit.tables import set_row_thumb
from .base import Page

# The Rotator's batch: at least one folder, and no more than a reserve could hold.
_BATCH = (1, 100_000)
# "Also check every": minutes.
_HEARTBEAT = (1, 60)


class _Row(QWidget):
    """An overline, the field, and a note under it — the FormDialog's row."""

    def __init__(self, title: str, field: QWidget, note: str = "", parent=None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.SP_6)
        self.title = Overline(title)
        self.title.setBuddy(field)
        column.addWidget(self.title)
        column.addWidget(field)
        self.note = label(note, "type.caption", "lo")
        self.note.setWordWrap(True)
        if not note:
            self.note.hide()            # never shown while it has no parent: a flash on screen
        column.addWidget(self.note)
        self.field = field
        if not field.accessibleName():
            field.setAccessibleName(title.capitalize())

    def set_note(self, text: str, tone: str = "lo") -> None:
        self.note.setText(text)
        set_tone(self.note, tone)
        self.note.setVisible(bool(text))


def _left(widget: QWidget) -> QWidget:
    """A widget at its own width, at the left of a row."""
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(widget)
    row.addStretch(1)
    return holder


class _Answer(QObject):
    """Carries what a worker found back to the window's thread."""
    done = Signal(str, object)


class SettingsPage(Page):
    key = "settings"
    title = "Settings"
    icon = "settings"
    FIXTURES = ("default", "running", "update-available", "update-ready", "update-downloading")

    # Which store changed: "rotator", "creator" or "tracker".
    changed = Signal(str)
    _update_progress = Signal(int, int)

    def __init__(self, settings: Settings, config: Config, *, feed=None, services=None,
                 on_review_settings=None, on_authors=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.config = config
        self._feed = feed
        self._services = services
        self._answer = _Answer(self)
        self._answer.done.connect(self._answered)
        self._autostart_known = False
        self._checking = False
        self._update_busy = False
        self._release = self._installer = None
        self._update_job = None
        self._install_requested = False
        self._cancel_update = threading.Event()
        self._update_progress.connect(self._show_update_progress)
        self.set_subtitle("Set once, used everywhere · saved as you change them")

        scroll = QScrollArea()
        self._scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.NoFocus)       # its fields are Tab's stops, not it
        body = QWidget()
        grid = QGridLayout(body)
        pad_v, pad_h = theme.BODY_PAD
        grid.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        grid.setHorizontalSpacing(theme.PANEL_GAP)
        grid.setVerticalSpacing(theme.PANEL_GAP)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        left = QVBoxLayout()
        left.setSpacing(theme.PANEL_GAP)
        left.addWidget(self._folders_panel())
        left.addWidget(self._appearance_panel())
        left.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(theme.PANEL_GAP)
        right.addWidget(self._engine_panel())
        right.addWidget(self._tracker_panel())
        right.addWidget(self._review_panel())
        right.addWidget(self._updates_panel())
        right.addWidget(self._about_panel())
        right.addStretch(1)
        grid.addLayout(left, 0, 0)
        grid.addLayout(right, 0, 1)

        if services is not None:
            services.jobs.changed.connect(self._jobs_changed)
            services.jobs.changed.connect(self._try_update_install)
        if feed is not None:
            feed.updated.connect(self._fill_monitors)
            feed.config_changed.connect(self._feed_config_changed)
        self._jobs_changed()
        self._fill_monitors()
        self.connect_review(on_review_settings, on_authors)

    # ---- the panels ------------------------------------------------------------

    @staticmethod
    def _panel(title: str, subtitle: str = "") -> tuple[GlassPanel, QVBoxLayout]:
        panel = GlassPanel(padding="lg")
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.DIALOG_ROW_GAP)
        column.addWidget(CardTitle(title, subtitle))
        return panel, column

    def _folders_panel(self) -> GlassPanel:
        panel, column = self._panel("Folders", "Where the Rotator and the Creator work")
        c = self.config
        self.reserve = PathField(c.source, placeholder="Choose the reserve folder",
                                 dialog_title="The reserve")
        self.myprojects = PathField(c.destination, placeholder="Choose Wallpaper Engine's myprojects",
                                    dialog_title="myprojects")
        self.duplicates = PathField(c.duplicates, placeholder="Choose a folder for duplicates",
                                    dialog_title="Duplicates")
        self.batch = SpinBox(minimum=_BATCH[0], maximum=_BATCH[1], value=c.count)
        self.batch.setAccessibleName("Folders per run")
        self._rotator_rows = [
            _Row("Reserve", self.reserve,
                 "The Rotator draws each batch from here, and returns the last one here."),
            _Row("myprojects", self.myprojects,
                 "Wallpaper Engine's own projects folder: what is in rotation now."),
            _Row("Duplicates", self.duplicates,
                 "A folder coming back that the reserve already has is set aside here."),
            _Row("Folders per run", _left(self.batch)),
        ]
        self.rotator_note = label("", "type.caption", "warn")
        self.rotator_note.setWordWrap(True)
        self.rotator_note.hide()
        column.addWidget(self.rotator_note)
        for row in self._rotator_rows:
            column.addWidget(row)

        self.creator_source = PathField(
            self.settings.get("creator", "source", DEFAULT_CREATOR_SOURCE),
            placeholder="Choose a folder of video clips", dialog_title="Creator source")
        self.creator_target = PathField(
            self.settings.get("creator", "target", default_creator_target()),
            placeholder="Choose where new wallpapers go", dialog_title="Creator output")
        column.addWidget(_Row("Creator source", self.creator_source,
                              "The video clips the Creator builds wallpapers from."))
        column.addWidget(_Row("Creator output", self.creator_target,
                              "Where the Creator writes the wallpapers it builds."))

        self.reserve.path_changed.connect(lambda p: self._set_rotator("source", p))
        self.myprojects.path_changed.connect(lambda p: self._set_rotator("destination", p))
        self.duplicates.path_changed.connect(lambda p: self._set_rotator("duplicates", p))
        self.batch.valueChanged.connect(lambda n: self._set_rotator("count", int(n)))
        self.creator_source.path_changed.connect(lambda p: self._set("creator", "source", p))
        self.creator_target.path_changed.connect(lambda p: self._set("creator", "target", p))
        return panel

    def _engine_panel(self) -> GlassPanel:
        panel, column = self._panel("Wallpaper Engine")
        known = self.settings.get("tracker", "we_config", None) or (
            self._feed.config_path if self._feed is not None else "")
        self.we_config = PathField(known or "", kind="file", placeholder="Choose config.json",
                                   dialog_title="Wallpaper Engine's config.json",
                                   file_filter="config.json (config.json)")
        self.we_config.path_changed.connect(self._set_we_config)
        column.addWidget(_Row("config.json", self.we_config,
                              "Wallpaper Engine's settings file, where the Tracker reads "
                              "each monitor's playlist. Found by itself where Steam usually is."))
        self.heartbeat = SpinBox(minimum=_HEARTBEAT[0], maximum=_HEARTBEAT[1],
                                 value=heartbeat_setting(self.settings) // 60, suffix=" min")
        self.heartbeat.setAccessibleName("Also check every")
        self.heartbeat.valueChanged.connect(self._set_heartbeat)
        column.addWidget(_Row(
            "Also check every", _left(self.heartbeat),
            "A wallpaper change is seen within a second of Wallpaper Engine writing it "
            "down. This is only the safety check in between, for what nothing announces "
            "— a wallpaper deleted from disk, say."))
        return panel

    def _appearance_panel(self) -> GlassPanel:
        """How the window shows things. A new option is one more _Row here,
        saved under the "appearance" section."""
        panel, column = self._panel("Appearance", "Applied at once, in every page")
        self.preview_size = Dropdown()
        self.preview_size.setAccessibleName("Preview size")
        for side in theme.ROW_THUMB_SIDES:
            default = " (default)" if side == theme.ROW_THUMB_DEFAULT else ""
            self.preview_size.add_item(f"{side} × {side} px{default}", side)
        self.preview_size.setCurrentIndex(theme.ROW_THUMB_SIDES.index(
            theme.row_thumb_side(self.settings.get("appearance", "preview_size", None))))
        self.preview_size.activated.connect(self._set_preview_size)
        column.addWidget(_Row(
            "Preview size", _left(self.preview_size),
            "The wallpaper previews in the Tracker, Rotator, Review list and Creator "
            "tables, and the Tracker's grid."))
        return panel

    def _set_preview_size(self, row: int) -> None:
        side = self.preview_size.itemData(row)
        self.settings.reload_if_changed()
        self.settings.set("appearance", "preview_size", side)
        self.settings.save()
        set_row_thumb(side)

    def _tracker_panel(self) -> GlassPanel:
        panel, column = self._panel("Tracker and tray")
        self.background = Toggle("Keep counting in the background (tray, starts with Windows)")
        self.background.setEnabled(False)
        self.background.toggled.connect(self._set_background)
        self.background_note = label("autostart: checking…", "type.caption", "lo")
        self.background_note.setWordWrap(True)
        holder = QWidget()
        inner = QVBoxLayout(holder)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(theme.SP_6)
        inner.addWidget(self.background)
        inner.addWidget(self.background_note)
        column.addWidget(holder)
        self.lead = Dropdown()
        self.lead.setAccessibleName("Lead monitor")
        self.lead.activated.connect(self._set_lead)
        column.addWidget(_Row(
            "Lead monitor", self.lead,
            "The monitor the tray icon, the Tracker and \u201cNext in the loop\u201d lead "
            "with. Automatic picks the playlist a rotation built."))
        return panel

    def _review_panel(self) -> GlassPanel:
        panel, column = self._panel("Review and Steam")
        review = SecondaryButton("Review settings…")
        authors = SecondaryButton("Authors database…")
        column.addWidget(_Row("Review settings", _left(review),
                              "What a scan reads, how a click subscribes, and the Steam Web API "
                              "key Review asks Steam with — kept encrypted for this Windows "
                              "account."))
        column.addWidget(_Row("Authors database", _left(authors),
                              "The authors you have reviewed, and the backups of that list."))
        self.review_settings_button, self.authors_button = review, authors
        return panel

    def _about_panel(self) -> GlassPanel:
        panel, column = self._panel("About")
        head = QWidget()
        row = QHBoxLayout(head)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SP_12)
        row.addWidget(BrandMark(theme.DIALOG_TILE), 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(theme.SP_2)
        words.addWidget(label(FULL_NAME, "type.h3", "hi"))
        self.version = label(f"version {__version__}", "type.monoSm", "lo")
        words.addWidget(self.version)
        row.addLayout(words, 1)
        column.addWidget(head)

        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SP_8)
        data = SecondaryButton("Open data folder", icon="folder")
        data.clicked.connect(self.open_data_folder)
        logs = SecondaryButton("Open log folder", icon="folder")
        logs.clicked.connect(self.open_log_folder)
        self.selfcheck_button = SecondaryButton("Run selfcheck")
        self.selfcheck_button.clicked.connect(self.run_selfcheck)
        for button in (data, logs, self.selfcheck_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        column.addLayout(buttons)
        result = QHBoxLayout()
        result.setSpacing(theme.SP_8)
        self.selfcheck_result = label("", "type.caption", "lo")
        self.selfcheck_result.setWordWrap(True)
        self.selfcheck_result.hide()
        self.report_link = LinkButton("Open report")
        self.report_link.clicked.connect(self.open_report)
        self.report_link.hide()
        result.addWidget(self.selfcheck_result, 1)
        result.addWidget(self.report_link, 0, Qt.AlignTop)
        column.addLayout(result)
        return panel

    # ---- wiring the Review buttons once the window has made the Review tab ------------

    def _updates_panel(self) -> GlassPanel:
        panel, column = self._panel("Updates")
        self.auto_update_check = Toggle("Check for updates at startup")
        self.auto_update_check.setChecked(self.settings.get("updates", "check_on_startup", True))
        self.auto_update_check.toggled.connect(self._set_update_check)
        column.addWidget(self.auto_update_check)
        self.update_note = label("Check for a newer stable release.", "type.bodySm", "mid")
        self.update_note.setTextFormat(Qt.PlainText)
        self.update_note.setWordWrap(True)
        column.addWidget(self.update_note)
        self.update_notes = label("", "type.caption", "lo")
        self.update_notes.setTextFormat(Qt.PlainText)
        self.update_notes.setWordWrap(True)
        self.update_notes.hide()
        column.addWidget(self.update_notes)
        self.update_bar = ProgressBar(caption=True)
        self.update_bar.setAccessibleName("Update download progress")
        self.update_bar.hide()
        column.addWidget(self.update_bar)
        buttons = QHBoxLayout()
        self.update_check = SecondaryButton("Check for updates")
        self.update_check.clicked.connect(self.check_update)
        self.update_action = AccentButton("Download update")
        self.update_action.clicked.connect(self._update_action)
        self.update_action.hide()
        self.update_cancel = SecondaryButton("Cancel")
        self.update_cancel.clicked.connect(self.cancel_update)
        self.update_cancel.hide()
        for button in (self.update_check, self.update_action, self.update_cancel):
            buttons.addWidget(button)
        buttons.addStretch(1)
        column.addLayout(buttons)
        self.release_link = LinkButton("Release notes")
        self.release_link.clicked.connect(lambda: external.open_url(
            self._release.url.rsplit("/", 1)[0].replace("/download/", "/tag/")
            if self._release else updates.RELEASES))
        column.addWidget(self.release_link, 0, Qt.AlignLeft)
        return panel

    def startup(self) -> None:
        if (install_info.is_installed_copy()
                and self.settings.get("updates", "check_on_startup", True)):
            self.check_update()

    def _set_update_check(self, on: bool) -> None:
        self.settings.set("updates", "check_on_startup", on)
        self.settings.save()

    def check_update(self) -> None:
        if self._update_busy or self._install_requested:
            return
        self._update_busy = True
        self.update_check.setEnabled(False)
        self.update_action.hide()
        self.update_notes.hide()
        self._update_message("Checking for updates…")
        self._ask("update-check", updates.check)

    def _update_message(self, text: str, tone: str = "lo") -> None:
        self.update_note.setText(text)
        set_tone(self.update_note, tone)

    def _show_update(self, found) -> None:
        self._update_busy = False
        self.update_check.setEnabled(True)
        if isinstance(found, Exception):
            self._update_message(f"Could not check for updates: {found}", "danger")
            return
        self._release, self._installer = found, None
        if found is None:
            self.set_nav_state(NavState())
            self._update_message("You are using the latest version.", "ok")
            return
        self.set_nav_state(NavState.status("Update", "accent"))
        self._update_message(f"Version {found.version} is available · {fmt.size(found.size)}", "ok")
        self.update_notes.setText(found.notes[:1200])
        self.update_notes.setVisible(bool(found.notes))
        self.update_action.setText("Download update")
        self.update_action.setEnabled(True)
        self.update_action.setVisible(install_info.is_installed_copy())
        if not install_info.is_installed_copy():
            self._update_message(f"Version {found.version} is available. Install its setup to enable "
                                 "updates from the program; this source or standalone build stays as it is.")
        toasts = getattr(self.window(), "toasts", None)
        if toasts is not None:
            toasts.show_toast(f"Toolkit {found.version} is available.", action="Show update",
                              on_action=self.show_update, timeout=None)

    def show_update(self) -> None:
        self.navigate.emit(self.key)
        QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(self.update_action))

    def _update_action(self) -> None:
        if self._update_busy or self._release is None or self._install_requested:
            return
        if self._installer is not None:
            dialog = ConfirmDialog(f"Install Toolkit {self._release.version}?",
                                   "After active tasks finish, the window and tray will close. "
                                   "Toolkit will keep and back up your data, update in the background "
                                   "and open again.",
                                   self.window(), confirm_text="Install and restart", safe_default=True)
            if dialog.ask():
                self._install_requested = True
                self.update_action.setEnabled(False)
                self.update_check.setEnabled(False)
                self.update_cancel.show()
                self._try_update_install()
            return
        self._update_busy = True
        self._cancel_update.clear()
        self.update_check.setEnabled(False)
        self.update_action.setEnabled(False)
        self.update_cancel.setEnabled(True)
        self.update_cancel.show()
        self.update_bar.set_value(0, self._release.size)
        self.update_bar.set_caption(None)
        self.update_bar.set_state("determinate")
        self.update_bar.show()
        self._update_message("Downloading update…")
        if self._services is not None:
            self._update_job = self._services.jobs.start("updates", "Downloading update", self.key)
        release = self._release
        self._ask("update-download", lambda: updates.download(
            release, app_data_dir() / "updates",
            self._cancel_update, self._update_progress.emit))

    def _show_update_progress(self, done: int, total: int) -> None:
        self.update_bar.set_value(done, total)
        self.update_bar.set_caption(f"{fmt.size(done)} / {fmt.size(total)}", f"{int(done * 100 / total)}%")
        if self._update_job is not None:
            self._update_job.update("Downloading update", done, total)

    def _downloaded_update(self, found) -> None:
        self._update_busy = False
        self.update_cancel.hide()
        self.update_check.setEnabled(True)
        self.update_action.setEnabled(True)
        if isinstance(found, Exception):
            stopped = isinstance(found, updates.Cancelled)
            self._update_message("Download cancelled." if stopped else f"Could not download update: {found}",
                                 "lo" if stopped else "danger")
            self.update_bar.hide()
            result = "stopped" if stopped else "failed"
        else:
            self._installer = found
            self.update_bar.set_state("success")
            self._update_message("Update downloaded and verified. Ready to install.", "ok")
            self.update_action.setText("Install and restart")
            result = "clean"
        job, self._update_job = self._update_job, None
        if job is not None:
            job.finish(result, self.update_note.text())

    def cancel_update(self) -> None:
        if self._install_requested:
            self._install_requested = False
            self.update_cancel.hide()
            self.update_check.setEnabled(True)
            self.update_action.setEnabled(True)
            self._update_message("Installation postponed. The verified download is ready.")
        else:
            self._cancel_update.set()
            self.update_cancel.setEnabled(False)
            self._update_message("Cancelling download…")

    def _try_update_install(self, *_args) -> None:
        if not self._install_requested or self._update_busy:
            return
        if self._services is not None and self._services.jobs.running():
            self._update_message("Waiting for active tasks to finish before installing…", "warn")
            return
        if QApplication.activeModalWidget() is not None:
            QTimer.singleShot(500, self._try_update_install)
            return
        self._install_requested = False
        self._update_busy = True
        self.update_cancel.hide()
        self._update_message("Verifying the update and starting background installation…")
        self._ask("update-install", lambda: updates.install(self._installer, self._release))

    def _installed_update(self, found) -> None:
        # Normally this window has quit by now; setup shows errors after it closes.
        self._update_busy = False
        self.update_check.setEnabled(True)
        self.update_action.setEnabled(True)
        if found == 0:
            self._update_message("Update installed. Restart Toolkit to use the new version.", "ok")
        else:
            self._update_message(f"Setup could not finish: {found}. You can retry installing the update.", "danger")

    def connect_review(self, on_review_settings=None, on_authors=None) -> None:
        """What "Review settings…" and "Authors database…" open. They are the
        Review page's dialogs, so the page hears what changed."""
        for button, callback in ((self.review_settings_button, on_review_settings),
                                 (self.authors_button, on_authors)):
            button.setEnabled(callback is not None)
            if callback is not None:
                button.clicked.connect(lambda _c=False, f=callback: f())

    # ---- saving -------------------------------------------------------------------

    def _set_rotator(self, name: str, value) -> None:
        if self._rotator_busy():
            return
        setattr(self.config, name, value)
        try:
            self.config.save()
        except OSError as err:
            self.rotator_note.setText(f"Could not save the Rotator's settings: {err}")
            set_tone(self.rotator_note, "danger")
            self.rotator_note.show()
            return
        self.changed.emit("rotator")
        if self._services is not None:
            self._services.snapshot.refresh([RESERVE, ROTATION])

    def show_found_folders(self) -> None:
        """Steam's folders are known (steam_paths.when_found): the defaults that
        waited for them, in the fields where nothing is chosen."""
        for field, section, key, default in (
                (self.creator_target, "creator", "target", default_creator_target),):
            if not self.settings.get(section, key, None) and field.path() != default():
                field.set_path(default())
        self.show_rotator()

    def show_rotator(self) -> None:
        """Show the Rotator's settings again, as another page saved them."""
        c = self.config
        for field, path in ((self.reserve, c.source), (self.myprojects, c.destination),
                            (self.duplicates, c.duplicates)):
            if field.path() != path:
                field.set_path(path)
        if self.batch.value() != c.count:
            self.batch.blockSignals(True)
            self.batch.setValue(c.count)
            self.batch.blockSignals(False)

    def _set(self, section: str, key: str, value) -> None:
        self.settings.set(section, key, value)
        self.settings.save()
        self.changed.emit(section)
        if section == "creator" and self._services is not None:
            self._services.snapshot.refresh([ROTATION])

    def _set_we_config(self, path: str) -> None:
        self.settings.set("tracker", "we_config", path)
        self.settings.save()
        if self._feed is not None:
            self._feed.use_config(path)
        self.changed.emit("tracker")

    def _feed_config_changed(self) -> None:
        # With nothing chosen, the field shows where the feed found config.json,
        # which its worker may only have worked out after this page was built.
        if not self.settings.get("tracker", "we_config", None):
            self.we_config.set_path(self._feed.config_path)

    def _set_heartbeat(self, minutes: int) -> None:
        seconds = int(minutes) * 60
        tracker = self.settings.section("tracker")
        tracker["heartbeat"] = seconds
        # The old 30-second poll interval means nothing now; left in place it
        # would only suggest otherwise to whoever reads the file.
        tracker.pop("interval", None)
        self.settings.save()
        if self._feed is not None:
            self._feed.set_heartbeat(seconds)
        self.changed.emit("tracker")

    def _set_lead(self, row: int) -> None:
        monitor = self.lead.itemData(row)
        tracker = self.settings.section("tracker")
        if monitor:
            tracker["primary"] = monitor
        else:
            tracker.pop("primary", None)
        self.settings.save()
        self.changed.emit("tracker")
        if self._services is not None:
            self._services.snapshot.refresh([PLAYLIST])

    # ---- the Rotator's folders while it runs --------------------------------------------

    def _rotator_busy(self) -> bool:
        return (self._services is not None
                and any(self._services.jobs.is_running(k) for k in ("rotator", "reserve")))

    def _jobs_changed(self, *_args) -> None:
        busy = self._rotator_busy()
        for row in self._rotator_rows:
            row.field.setEnabled(not busy)
        if busy:
            self.rotator_note.setText("Read-only while the Rotator or reserve copy is at work: it is "
                                      "using these folders.")
            set_tone(self.rotator_note, "warn")
            self.rotator_note.show()
        elif self.rotator_note.property("tone") == "warn":
            self.rotator_note.hide()

    # ---- the lead monitor ---------------------------------------------------------------

    def _fill_monitors(self) -> None:
        chosen = self.settings.get("tracker", "primary", None)
        seen = [p.monitor for p in (self._feed.results if self._feed is not None else [])]
        if chosen and chosen not in seen:
            seen.append(chosen)
        self.lead.blockSignals(True)
        self.lead.clear()
        self.lead.add_item("Automatic", None)
        for monitor in seen:
            self.lead.add_item(monitor, monitor)
        self.lead.setCurrentIndex(seen.index(chosen) + 1 if chosen else 0)
        self.lead.blockSignals(False)

    # ---- counting in the background (schtasks, on a worker) --------------------------------

    def on_shown(self) -> None:
        self.settings.reload_if_changed()
        self._fill_monitors()
        if not self._autostart_known:
            self._ask("autostart", _autostart_state)

    def _set_background(self, on: bool) -> None:
        if not self._autostart_known:
            return
        self.background.setEnabled(False)
        self.background_note.setText("autostart: changing…")
        set_tone(self.background_note, "lo")
        self._ask("autostart", lambda: _change_autostart(on))

    def _ask(self, what: str, work) -> None:
        def run():
            try:
                found = work()
            except Exception as err:  # noqa: BLE001 — a worker reports, not dies
                found = err
            try:
                self._answer.done.emit(what, found)
            except RuntimeError:
                pass                    # the window went while Windows answered
        threading.Thread(target=run, daemon=True, name=f"settings-{what}").start()

    def _answered(self, what: str, found) -> None:
        if what == "autostart":
            self._show_autostart(found)
        elif what == "selfcheck":
            self._show_selfcheck(found)
        elif what == "update-check":
            self._show_update(found)
        elif what == "update-download":
            self._downloaded_update(found)
        elif what == "update-install":
            self._installed_update(found)

    def _show_autostart(self, found) -> None:
        if isinstance(found, Exception):
            self.background_note.setText(f"Could not change autostart: {found}")
            set_tone(self.background_note, "danger")
            self._autostart_known = False
            self._ask("autostart", _autostart_state)
            return
        on, method = found
        self._autostart_known = True
        self.background.blockSignals(True)
        self.background.setChecked(on)
        self.background.blockSignals(False)
        self.background.setEnabled(True)
        self.background_note.setText(_background_note(on, method))
        set_tone(self.background_note, "lo")

    # ---- About -----------------------------------------------------------------

    def open_data_folder(self) -> None:
        external.popen(["explorer", str(app_data_dir())])

    def open_log_folder(self) -> None:
        if self._services is not None:
            self._services.logs.open_folder()
        else:
            external.popen(["explorer", str(app_data_dir() / "logs")])

    def run_selfcheck(self) -> None:
        if self._checking:
            return
        self._checking = True
        self.selfcheck_button.setEnabled(False)
        self.selfcheck_result.setText("Checking what this build can do…")
        set_tone(self.selfcheck_result, "lo")
        self.selfcheck_result.show()
        self.report_link.hide()
        self._ask("selfcheck", selfcheck.run)

    def _show_selfcheck(self, found) -> None:
        self._checking = False
        self.selfcheck_button.setEnabled(True)
        if isinstance(found, Exception):
            self.selfcheck_result.setText(f"The selfcheck could not run: {found}")
            set_tone(self.selfcheck_result, "danger")
            return
        ok, report = found
        wrong = selfcheck.problems(report)
        if ok:
            checked = sum(1 for line in report.splitlines() if line.startswith("ok"))
            self.selfcheck_result.setText(f"All {checked} checks passed.")
            set_tone(self.selfcheck_result, "ok")
        else:
            self.selfcheck_result.setText(
                f"{fmt.counted(len(wrong), 'problem')}: " + "; ".join(wrong[:2]))
            set_tone(self.selfcheck_result, "danger")
        self.report_link.show()

    def open_report(self) -> None:
        external.popen(f'explorer /select,"{selfcheck.report_path()}"')

    # ---- snapshots ----------------------------------------------------------------

    def frame_fixture(self, state: str) -> dict | None:
        return {"frame": "idle"} if state.startswith("update-") else None

    def load_fixture(self, state: str) -> None:
        """Made-up folders on a made-up drive, never this machine's."""
        if state not in self.FIXTURES:
            super().load_fixture(state)
        self.reserve.set_path(r"X:\Toolkit\reserve")
        self.myprojects.set_path(r"X:\Steam\steamapps\common\wallpaper_engine\projects\myprojects")
        self.duplicates.set_path(r"X:\Toolkit\duplicates")
        self.batch.setValue(1000)
        self.creator_source.set_path(r"X:\Videos\clips")
        self.creator_target.set_path(r"X:\Steam\steamapps\common\wallpaper_engine\projects\myprojects")
        self.we_config.set_path(r"X:\Steam\steamapps\common\wallpaper_engine\config.json")
        self.heartbeat.setValue(5)
        self._show_autostart((True, f"scheduled task, {autostart.LOGON_DELAY_SECONDS}s after logon"))
        self.lead.blockSignals(True)
        self.lead.clear()
        self.lead.add_item("Automatic", None)
        for monitor in ("Monitor1", "Monitor2"):
            self.lead.add_item(monitor, monitor)
        self.lead.setCurrentIndex(0)
        self.lead.blockSignals(False)
        if state == "running":
            for row in self._rotator_rows:
                row.field.setEnabled(False)
            self.rotator_note.setText("Read-only while the Rotator is at work: it is "
                                      "using these folders.")
            set_tone(self.rotator_note, "warn")
            self.rotator_note.show()
        if state.startswith("update-"):
            self._release = updates.Release("9.9.0", "Faster library scans and clearer download progress.",
                                            "", 60_000_000, "0" * 64)
            self.set_nav_state(NavState.status("Update", "accent"))
            self.update_notes.setText(self._release.notes)
            self.update_notes.show()
            self.update_action.show()
            self._update_message("Version 9.9.0 is available · 57.2 MB", "ok")
            if state == "update-ready":
                self.update_action.setText("Install and restart")
                self._update_message("Update downloaded and verified. Ready to install.", "ok")
            if state == "update-downloading":
                self.update_check.setEnabled(False)
                self.update_action.setEnabled(False)
                self.update_cancel.show()
                self.update_bar.show()
                self._show_update_progress(24_000_000, 60_000_000)
                self._update_message("Downloading update…")
            QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(self.update_action))


def _autostart_state() -> tuple[bool, str]:
    return autostart.is_enabled(), autostart.method()


def _change_autostart(on: bool) -> tuple[bool, str]:
    autostart.set_enabled(on)
    return _autostart_state()


def _background_note(on: bool, method: str) -> str:
    if not on:
        return ("Off: the count moves only while this window or the tray is open. "
                "autostart: off")
    return (f"The tray starts with Windows and counts with the window closed. "
            f"autostart: {method}")
