"""Review's two dialogs: Review settings, and the authors database.

**Review settings** (frame 16, a FormDialog) is what is set once for Review:

- REVIEW SOURCE — what a scan reads the authors from: one of Wallpaper
  Engine's folders (read from its config.json, with how many wallpapers each
  holds), all of them, none of them, or everything (`engines/review` scopes).
- SUBSCRIBE BY — not in the design, kept from the old tab. Steam directly is
  `ISteamUGC::SubscribeItem` through Wallpaper Engine's own Steamworks
  library, from this window; Steam's page opens the wallpaper in Steam to
  press Subscribe there. The first declares Wallpaper Engine's app id to
  Steam, which Valve does not sanction and an update could close, so the
  second stays.
- STEAM WEB API KEY — optional. Without one Steam treats the toolkit as a
  signed-out visitor: mature and questionable wallpapers are left out of
  every author's list, and names come from a cache up to two weeks old. The
  key can be shown, tested before it is saved, and is kept encrypted for this
  Windows account (`app.secrets`, DPAPI) — not in Windows Credential Manager,
  as the design says (§7.2).
- SECOND COPY OF THE AUTHORS BACKUPS — `review.backup_mirror`: a folder every
  backup of the authors database is copied to as well (§7.3).
- "Authors database…" opens the other dialog.

**Authors database** (not designed; built from the kit): how many authors,
where the file is, every backup in both places (a Table), "Back up now", and
"Restore selected…" through a destructive ConfirmDialog. A restore backs up
what is there first, so it is undone the same way it is done. Everything it
reads — the count, the file sizes, the folders of backups, one of which may be
on another drive — is read on a thread.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLineEdit, QSizePolicy, QVBoxLayout, QWidget,
)

from .. import external, secrets, theme
from ..engines import review as rv
from ..engines.authors_store import (
    KEEP_DAILY, KEEP_MONTHLY, KEEP_RECENT, KEEP_WEEKLY, AuthorsStore, DbError, StoreDamaged,
)
from ..settings import Settings
from ..ui.kit import (
    Column, ConfirmDialog, Dropdown, FormDialog, GhostButton, Glyph, LinkButton, OverlayDialog,
    PathField, SecondaryButton, SegmentedControl, Table, TableModel, TextInput, format as fmt,
    label,
)
from ..ui.kit.base import set_tone

SECTION = "review"
SCOPE = "scope"
SUBSCRIBE = "subscribe"
MIRROR = "backup_mirror"
KEYLESS_HIDDEN = "keyless_banner_hidden"

# How a wallpaper gets subscribed to.
BY_STEAM = "steam"        # ISteamUGC, from this window
BY_PAGE = "page"          # open Steam's own page and let the user press it
SUBSCRIBE_MODES = (BY_STEAM, BY_PAGE)
SUBSCRIBE_TITLES = ("Steam directly", "Steam's page")

KEY_PAGE = "https://steamcommunity.com/dev/apikey"
KEY_PATTERN = re.compile(r"[0-9A-Fa-f]{32}")

# Testing a key needs an author to ask about, and the answer is only
# convincing if that author has published something. A public workshop account
# picked for being prolific — nothing about it is particular to whoever runs
# this, and it is read, never written.
PROBE_AUTHOR = "76561198344659208"

# What the page and this dialog say when there is no key: one wording, so the
# banner and the dialog cannot drift apart.
WITHOUT_KEY = (
    "Without a key Steam treats the toolkit as a signed-out visitor: mature and "
    "questionable wallpapers are left out of every author's list — 43% of the "
    "library this was built on — and author names can be up to two weeks old.")
WITH_KEY = ("With a key, author lists are complete — mature wallpapers included — and "
            "names are fetched fresh on every scan.")
STORED_NOTE = ("The key is kept encrypted for this Windows account: the file is useless "
               "to another account or on another machine.")


def has_key() -> bool:
    return secrets.has(secrets.STEAM_API_KEY)


def mirror_folder(settings: Settings) -> Path | None:
    """The second backup folder, or None when none is chosen."""
    chosen = str(settings.get(SECTION, MIRROR, "") or "").strip()
    return Path(chosen) if chosen else None


def open_store(settings: Settings) -> AuthorsStore:
    """The store as the settings describe it, opened."""
    return AuthorsStore(mirror=mirror_folder(settings)).open()


def key_problem(text: str) -> str | None:
    """What is wrong with a key as typed; None when it is right or empty."""
    text = (text or "").strip()
    if not text or KEY_PATTERN.fullmatch(text):
        return None
    return "a key is 32 characters, the digits 0–9 and the letters A–F"


def test_key(value: str) -> str:
    """Ask Steam with the key; the answer in words, or an exception saying
    why not. Seconds, on the network: never on the window's thread."""
    from ..engines.steam_api import SteamClient
    client = SteamClient(api_key=value, cache_path=None)
    client.check_key()
    author = client.author_items(PROBE_AUTHOR, max_pages=1)
    return (f"Steam accepts the key — it answered with {fmt.count(author.total)} "
            "wallpapers for a test author")


def steamworks_note() -> str:
    """Whether Wallpaper Engine's Steamworks library is there to subscribe
    through. It looks on Wallpaper Engine's disk: call it on a thread."""
    from ..engines.steam_ugc import SteamUgc
    found = SteamUgc()
    if found.available:
        return ("Steam directly subscribes from this window through Wallpaper Engine's own "
                "Steamworks library; Steam must be running.")
    return ("Wallpaper Engine's Steamworks library was not found, so a click can only open "
            "the wallpaper's page in Steam.")


# ---- what is set ----------------------------------------------------------------------------

@dataclass
class ReviewSettings:
    """What the dialog shows and gives back."""
    scope: str = rv.DEFAULT_SCOPE
    subscribe: str = BY_STEAM
    key: str = ""
    mirror: str = ""


def read_settings(settings: Settings, key: str = "") -> ReviewSettings:
    """The settings as stored; the key is passed in (it is read from the
    secrets file, which the caller does once)."""
    mode = settings.get(SECTION, SUBSCRIBE, BY_STEAM)
    return ReviewSettings(scope=str(settings.get(SECTION, SCOPE, rv.DEFAULT_SCOPE) or rv.DEFAULT_SCOPE),
                          subscribe=mode if mode in SUBSCRIBE_MODES else BY_STEAM,
                          key=key, mirror=str(settings.get(SECTION, MIRROR, "") or ""))


def save_settings(settings: Settings, before: ReviewSettings, after: ReviewSettings) -> set[str]:
    """Write what changed — the settings file, and the key through `secrets` —
    and say which: "scope", "subscribe", "key", "mirror"."""
    changed: set[str] = set()
    for name, key in (("scope", SCOPE), ("subscribe", SUBSCRIBE), ("mirror", MIRROR)):
        if getattr(before, name) != getattr(after, name):
            settings.set(SECTION, key, getattr(after, name))
            changed.add(name)
    if before.key.strip() != after.key.strip():
        secrets.put(secrets.STEAM_API_KEY, after.key.strip())
        changed.add("key")
        if after.key.strip():
            # A key added after the banner was hidden: if it is removed again
            # one day, the banner comes back and says so.
            settings.set(SECTION, KEYLESS_HIDDEN, False)
    if changed:
        settings.save()
    return changed


def scope_rows(folders: dict[str, int] | None, current: str) -> list[tuple[str, str, str | None, int | None]]:
    """The REVIEW SOURCE list, as (kind, text, scope, count): "section",
    "item" or "separator". Wallpaper Engine's folders first with how many
    wallpapers each holds, then the three that cross them. With the folders not
    read yet, the current choice stands in for them; a folder deleted since it
    was chosen is still listed, marked, rather than swapped silently."""
    rows: list[tuple[str, str, str | None, int | None]] = []
    named = rv.folder_of(current)
    listed = dict(folders or {})
    if named and named not in listed:
        listed[named] = None
    if listed:
        rows.append(("section", "Your folders", None, None))
        for title, count in listed.items():
            missing = folders is not None and title not in folders
            rows.append(("item", f"{title} (not found)" if missing else title,
                         rv.FOLDER + title, count))
        rows.append(("separator", "", None, None))
    rows += [("item", "All folders", rv.SCOPE_FOLDERS, None),
             ("item", "Not in any folder", rv.SCOPE_LOOSE, None),
             ("item", "Everything you have", rv.SCOPE_EVERYTHING, None)]
    return rows


class _Answer(QObject):
    """Carries a thread's answer back to the window's thread."""
    done = Signal(str, object)


def _ask(answer: _Answer, what: str, work: Callable[[], object]) -> None:
    def run() -> None:
        try:
            found = work()
        except Exception as err:  # noqa: BLE001 — handed over, not raised on a thread
            found = err
        try:
            answer.done.emit(what, found)
        except RuntimeError:
            pass                # the dialog went while Steam was answering
    threading.Thread(target=run, daemon=True, name=f"review-{what}").start()


# ---- Review settings -------------------------------------------------------------------------

class KeyField(QWidget):
    """The key: masked, with Show and Test, a line saying where things stand
    (stored, tested, refused) and "Get a key"."""

    test_requested = Signal()

    def __init__(self, value: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.SP_6)
        row = QHBoxLayout()
        row.setSpacing(theme.SP_6)
        self.edit = TextInput(value, placeholder="32 characters, 0–9 and A–F")
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setAccessibleName("Steam Web API key")
        self.show_button = SecondaryButton("Show")
        self.show_button.setAutoDefault(False)
        self.show_button.clicked.connect(self.toggle_shown)
        self.test_button = SecondaryButton("Test")
        self.test_button.setAutoDefault(False)
        self.test_button.clicked.connect(self.test_requested)
        row.addWidget(self.edit, 1)
        row.addWidget(self.show_button)
        row.addWidget(self.test_button)
        column.addLayout(row)
        line = QHBoxLayout()
        line.setSpacing(theme.SP_8)
        self.verdict = label("", "type.caption", "lo")
        self.verdict.setWordWrap(True)
        self.get_key = LinkButton("Get a key", font="type.label")
        self.get_key.clicked.connect(lambda: external.open_url(KEY_PAGE))
        line.addWidget(self.verdict, 1)
        line.addWidget(self.get_key, 0, Qt.AlignTop)
        column.addLayout(line)
        self.meaning = label("", "type.caption", "lo")
        self.meaning.setWordWrap(True)
        column.addWidget(self.meaning)
        self.textChanged = self.edit.textChanged      # what FormDialog listens to
        self.edit.textChanged.connect(self._describe)
        self._describe()

    def value(self) -> str:
        return self.edit.text().strip()

    def shown(self) -> bool:
        return self.edit.echoMode() == QLineEdit.Normal

    def toggle_shown(self) -> None:
        self.edit.setEchoMode(QLineEdit.Password if self.shown() else QLineEdit.Normal)
        self.show_button.setText("Hide" if self.shown() else "Show")

    def say(self, text: str, tone: str = "lo") -> None:
        self.verdict.setText(text)
        set_tone(self.verdict, tone)

    def _describe(self, *_args) -> None:
        if self.value():
            self.meaning.setText(WITH_KEY)
            set_tone(self.meaning, "lo")
        else:
            self.meaning.setText(WITHOUT_KEY)
            set_tone(self.meaning, "warn")


class ReviewSettingsDialog(FormDialog):
    """Frame 16. `ask()` is True when saved; `values()` is what to save."""

    def __init__(self, values: ReviewSettings, parent: QWidget | None = None, *,
                 folders: dict[str, int] | None = None, stored: str = "",
                 on_authors: Callable[[], None] | None = None,
                 tester: Callable[[str], str] = test_key, embedded: bool = False):
        super().__init__("Review settings", parent, icon="review", tone="accent",
                         body="What a scan reads, how a click subscribes, and the Steam key "
                              "Review asks Steam with.", embedded=embedded)
        self._values = values
        self._tester = tester
        self._answer = _Answer(self)
        self._answer.done.connect(self._answered)

        self.source = Dropdown()
        self.source.setAccessibleName("Review source")
        self.add_row("Review source", self.source,
                     "The Wallpaper Engine folder a scan reads the authors from.")
        self.set_folders(folders)

        self.mode = SegmentedControl(SUBSCRIBE_TITLES,
                                     current=SUBSCRIBE_MODES.index(values.subscribe))
        self.mode.setAccessibleName("Subscribe by")
        holder = QWidget()
        line = QHBoxLayout(holder)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(self.mode)
        line.addStretch(1)
        self.add_row("Subscribe by", holder,
                     "Steam's page opens the wallpaper in Steam, to press Subscribe there.")
        self._mode_row = self._rows[-1]

        self.key = KeyField(values.key)
        self.key.test_requested.connect(self.test)
        self.add_row("Steam Web API key", self.key,
                     check=lambda field: key_problem(field.value()))
        self.key.say(stored or "not set — Review works without one, with less")

        self.mirror = PathField(values.mirror, placeholder="Not set",
                                dialog_title="A second folder for the authors backups")
        self.mirror.setAccessibleName("Second copy of the authors backups")
        self.add_row("Second copy of the authors backups", self.mirror,
                     "Optional. Every backup of the authors database is copied here too — "
                     "another drive, or a folder OneDrive syncs.",
                     check=self._mirror_problem)

        note = QWidget()
        row = QHBoxLayout(note)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SP_6)
        row.addWidget(Glyph("lock", "text.lo", theme.EMPTY_META_ICON), 0, Qt.AlignTop)
        words = label(STORED_NOTE, "type.caption", "lo")
        words.setWordWrap(True)
        row.addWidget(words, 1)
        self.add_widget(note)

        self.authors_button = None
        if on_authors is not None:
            self.authors_button = self.add_action("Authors database…", on_authors)
        self.revalidate()

    # -- the source list

    def set_folders(self, folders: dict[str, int] | None) -> None:
        """Wallpaper Engine's folders and their counts, once read (None: not yet)."""
        wanted = self.source.currentData() if self.source.count() else self._values.scope
        self.source.blockSignals(True)
        self.source.clear()
        for kind, text, scope, count in scope_rows(folders, wanted):
            if kind == "section":
                self.source.add_section(text)
            elif kind == "separator":
                self.source.add_separator()
            else:
                self.source.add_item(text, scope, count=count)
        found = self.source.findData(wanted)
        self.source.setCurrentIndex(found if found >= 0
                                    else self.source.findData(rv.SCOPE_EVERYTHING))
        self.source.blockSignals(False)

    def set_steamworks(self, note: str) -> None:
        """Whether Steam directly can work here, once looked."""
        row = self._mode_row
        row.note_text = (f"{note} Steam's page opens the wallpaper in Steam, to press "
                         "Subscribe there.")
        row.note.setText(row.note_text)
        row.note.show()

    def mode_note(self) -> str:
        return self._mode_row.note.text()

    # -- the key

    def test(self) -> None:
        value = self.key.value()
        if not value:
            self.key.say("Nothing to test — leave it empty to go without a key.")
            return
        problem = key_problem(value)
        if problem:
            self.key.say(problem[:1].upper() + problem[1:] + ".", "danger")
            return
        self.key.say("Asking Steam…")
        self.key.test_button.setEnabled(False)
        _ask(self._answer, "test", lambda: self._tester(value))

    def _answered(self, what: str, found) -> None:
        if what != "test":
            return
        self.key.test_button.setEnabled(True)
        if isinstance(found, Exception):
            self.key.say(f"Steam did not accept it: {found}", "danger")
        else:
            self.key.say(str(found), "ok")

    # -- the second folder

    @staticmethod
    def _mirror_problem(field: PathField) -> str | None:
        chosen = field.path().strip()
        if not chosen:
            return None
        backups = AuthorsStore().backup_dir
        if os.path.normcase(os.path.abspath(chosen)) == os.path.normcase(os.path.abspath(backups)):
            return "That is the backup folder itself — pick another, ideally on another drive."
        return None

    # -- the answer

    def values(self) -> ReviewSettings:
        return ReviewSettings(scope=self.source.currentData() or rv.DEFAULT_SCOPE,
                              subscribe=SUBSCRIBE_MODES[self.mode.current_index()],
                              key=self.key.value(), mirror=self.mirror.path().strip())


def stored_key_words() -> str:
    """Where the key stands, for the dialog's line under it. Reads the secrets
    file in the data folder."""
    name = secrets.STEAM_API_KEY
    if not secrets.has(name):
        return "not set — Review works without one, with less"
    if secrets.is_protected(name):
        return f"stored, encrypted ({secrets.masked(name)})"
    return "stored in the clear — DPAPI was unavailable"


# ---- the authors database ---------------------------------------------------------------------

@dataclass(frozen=True)
class Backup:
    """One backup file, as the dialog lists it."""
    path: Path
    taken: datetime
    count: int
    where: str
    size: int


@dataclass(frozen=True)
class StoreFacts:
    """What the dialog reads on its thread."""
    count: int | None
    problem: str
    damaged: bool
    path: Path
    size: int
    backups: tuple[Backup, ...]


def read_store(store: AuthorsStore) -> StoreFacts:
    """Count the authors, measure the file, list the backups. On a thread."""
    count, problem, damaged = None, "", False
    try:
        with AuthorsStore(store.path, mirror=store.mirror) as opened:
            count = opened.count()
    except StoreDamaged as err:
        problem, damaged = f"{err}. Pick the newest backup and restore it.", True
    except DbError as err:
        problem = str(err)
    try:
        size = store.path.stat().st_size
    except OSError:
        size = 0
    backups = tuple(Backup(s.path, s.taken, s.count, s.where, s.size)
                    for s in store.snapshots())
    return StoreFacts(count, problem, damaged, store.path, size, backups)


def policy_text() -> str:
    return ("After every change the whole database is saved as a snapshot (gzipped JSON, "
            f"readable without this app). Kept: the last {KEEP_RECENT}, then one a day for "
            f"{KEEP_DAILY} days, one a week for {KEEP_WEEKLY} weeks and one a month for "
            f"{KEEP_MONTHLY} months. Each change is also written to journal.jsonl, as it was "
            "and as it became.")


def mirror_text(mirror: Path | None) -> tuple[str, str]:
    """(tone, words) about the second copy."""
    if mirror is None:
        return ("warn", "No second copy: the backups are on the same disk as the database, "
                        "which protects against mistakes but not against that disk failing. "
                        "Choose a second folder in Review settings.")
    return ("lo", f"Every backup is copied to {mirror} as well, and pruned by the same rules.")


def restore_question(now_count: int | None, backup: Backup) -> tuple[str, str, str]:
    """(title, body, button) of the restore confirmation."""
    now = (f"the {fmt.count(now_count)} authors in the database now" if now_count is not None
           else "whatever is left of the damaged database")
    return (f"Restore the backup of {fmt.date_table(backup.taken)}?",
            f"Replace {now} with the {fmt.count(backup.count)} in this backup. Everything "
            "written since — visit dates, new authors, renames — goes back to how it was "
            "then. What is there now is backed up first, so this can be undone the same way.",
            f"Restore {fmt.count(backup.count)} authors")


class BackupModel(TableModel):
    COLUMNS = (Column("Taken", theme.AUTHORS_COLUMNS["taken"], mono=True),
               Column("Authors", theme.AUTHORS_COLUMNS["authors"], align="right", mono=True),
               Column("Size", theme.AUTHORS_COLUMNS["size"], align="right", mono=True),
               Column("Where", None, tone="text.mid"))

    def __init__(self, parent=None):
        super().__init__(self.COLUMNS, (), parent)

    def cell(self, b: Backup, column: int):
        return (fmt.date_table(b.taken), fmt.count(b.count), fmt.size(b.size), b.where)[column]

    def sort_key(self, b: Backup, column: int):
        return (b.taken, b.count, b.size, b.where)[column]


class AuthorsDialog(OverlayDialog):
    """The authors database and its backups. `restored` says whether a
    backup was put back, so the page drops what it read before."""

    def __init__(self, settings: Settings, parent: QWidget | None = None, *,
                 answer: Callable | None = None, read: Callable | None = None,
                 embedded: bool = False):
        super().__init__(parent, width=theme.AUTHORS_WIDTH, embedded=embedded)
        self.settings = settings
        self.restored = False
        self.messages: list[tuple[str, str]] = []
        self._answer_fn = answer or (lambda dialog: dialog.ask())
        self._read = read or read_store
        self._busy = False
        self.facts: StoreFacts | None = None
        self.store = AuthorsStore(mirror=mirror_folder(settings))
        self._set_head("Authors database", "Reading the database…", "review", "neutral")
        self._answers = _Answer(self)
        self._answers.done.connect(self._answered)

        policy = label(policy_text(), "type.caption", "lo")
        policy.setWordWrap(True)
        self.body_column.addWidget(policy)
        tone, words = mirror_text(self.store.mirror)
        self.mirror_note = label(words, "type.caption", tone)
        self.mirror_note.setWordWrap(True)
        self.body_column.addWidget(self.mirror_note)

        self.model = BackupModel(self)
        self.table = Table()
        self.table.setAccessibleName("Backups of the authors database")
        self.table.setModel(self.model)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAccessibleName("Backups")
        self.table.setFixedHeight(theme.AUTHORS_TABLE)
        self.table.selectionModel().selectionChanged.connect(self._selected)
        self.table.doubleClicked.connect(lambda _index: self.restore_selected())
        self.body_column.addWidget(self.table)

        for text, callback in (("Open folder", self._open_file),
                               ("Show backup folder", self._open_backups)):
            ghost = GhostButton(text)
            ghost.setAutoDefault(False)
            ghost.clicked.connect(lambda _=False, fn=callback: fn())
            self.footer.actions.addWidget(ghost)
        self.backup_button = SecondaryButton("Back up now")
        self.backup_button.clicked.connect(self.back_up_now)
        self.restore_button = SecondaryButton("Restore selected…")
        self.restore_button.clicked.connect(self.restore_selected)
        close = SecondaryButton("Close")
        close.clicked.connect(self.reject)
        for button in (self.backup_button, self.restore_button, close):
            self.footer.buttons.addWidget(button)
        self._initial = close
        self._selected()

    # -- reading

    def start(self) -> None:
        """Read what there is, on a thread."""
        store, read = self.store, self._read
        _ask(self._answers, "read", lambda: read(store))

    def take(self, facts: StoreFacts) -> None:
        self.facts = facts
        where = f"{facts.path} · {fmt.size(facts.size)}" if facts.size else \
            f"{facts.path} · not made yet"
        if facts.count is not None:
            self._body.setText(f"{fmt.counted(facts.count, 'author')} · {where}")
            set_tone(self._body, "mid")
        else:
            self._body.setText(f"{'The database file is damaged' if facts.damaged else 'The database cannot be read'}"
                               f" — {facts.problem} · {where}")
            set_tone(self._body, "danger")
        self.model.set_rows(list(facts.backups))
        if not facts.backups:
            self.footer.summary.set_text("No backups yet — the first is made with the first "
                                         "change, or by “Back up now”.")
        self._selected()
        if self.isVisible():
            self.place()

    def _answered(self, what: str, found) -> None:
        if what == "read":
            if isinstance(found, Exception):
                self._say("danger", f"Could not read the backups: {found}")
            else:
                self.take(found)
            return
        self._busy = False
        if isinstance(found, Exception):
            self._say("danger", str(found))
        elif what == "backup":
            path, warnings = found
            if path is None:
                self._say("lo", "The database is empty; there is nothing to back up yet.")
            elif warnings:
                self._say("warn", f"Saved {Path(path).name}, but " + "; ".join(warnings))
            else:
                self._say("ok", f"Saved {Path(path).name}"
                          + (" and copied it to the second folder." if self.store.mirror else "."))
        elif what == "restore":
            self.restored = True
            self._say("ok", f"Restored {fmt.counted(found['written'], 'author')} from "
                            f"{found['name']}.")
        self.start()

    # -- choosing

    def selected(self) -> Backup | None:
        chosen = self.table.selected_items()
        return chosen[0] if chosen else None

    def _selected(self, *_args) -> None:
        self.restore_button.setEnabled(self.selected() is not None and not self._busy)
        self.backup_button.setEnabled(not self._busy)

    def _say(self, tone: str, words: str) -> None:
        self.messages.append((tone, words))
        self.footer.summary.set_text(words)
        self.footer.summary.set_tone({"ok": "ok", "warn": "warn", "danger": "danger"}.get(tone, "lo"))

    # -- backing up and restoring

    def back_up_now(self) -> None:
        if self._busy:
            return
        store_path, mirror = self.store.path, self.store.mirror

        def work():
            with AuthorsStore(store_path, mirror=mirror) as store:
                return store.snapshot(), list(store.warnings)

        self._busy = True
        self._selected()
        self._say("lo", "Backing up…")
        _ask(self._answers, "backup", work)

    def restore_selected(self) -> None:
        backup = self.selected()
        if backup is None or self._busy:
            return
        title, body, button = restore_question(self.facts.count if self.facts else None, backup)
        confirm = ConfirmDialog(title, body, self, destructive=True, icon="refresh",
                                confirm_text=button)
        self.confirmation = confirm
        if not self._answer_fn(confirm):
            return
        store_path, mirror = self.store.path, self.store.mirror
        damaged = bool(self.facts and self.facts.damaged)

        def work():
            if damaged:
                set_aside(store_path)
            with AuthorsStore(store_path, mirror=mirror) as store:
                report = store.restore(backup.path)
            return {**report, "name": backup.path.name}

        self._busy = True
        self._selected()
        self._say("lo", f"Restoring {backup.path.name}…")
        _ask(self._answers, "restore", work)

    # -- Explorer

    def _open_file(self) -> None:
        path = self.store.path
        if path.exists():
            external.popen(f'explorer /select,"{path}"')
        else:
            external.popen(["explorer", str(path.parent)])

    def _open_backups(self) -> None:
        folder = self.store.backup_dir
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            self._say("warn", f"Could not open {folder}: {err}")
            return
        external.popen(["explorer", str(folder)])

    def result_value(self) -> bool:
        return self.restored


def set_aside(store_path: Path) -> None:
    """A damaged file cannot be opened to be replaced: it is set aside whole,
    never deleted, and a fresh one takes its place. Its journal goes with it —
    SQLite would otherwise take a leftover journal for the new file's and
    "roll it back" in."""
    n = 1
    while store_path.with_name(f"{store_path.name}.damaged{n}").exists():
        n += 1
    for suffix in ("", "-journal", "-wal", "-shm"):
        part = store_path.with_name(store_path.name + suffix)
        if part.exists():
            os.replace(part, part.with_name(f"{part.name}.damaged{n}"))
