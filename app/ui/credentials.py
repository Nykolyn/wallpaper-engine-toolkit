"""Where the Steam key and the database live, set from inside the app.

Both were put in place by hand while the Review tab was being built, which was
fine for building it and no good for using it. Everything the toolkit needs to
reach Steam and the authors database is set here instead, checked here, and
stored by :mod:`app.secrets` — DPAPI-encrypted, so the file is unreadable to
another Windows account and useless if it is copied off the machine.

The two **Test** buttons matter more than they look. A wrong Steam key and an
unreachable database fail identically from the Review tab's point of view —
"nothing happened" — and each takes a slow round trip to find out. Testing them
here, one at a time, with the answer in a sentence, turns a mystery into a
sentence about which of the two is wrong.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from .. import secrets, theme
from ..settings import Settings
from ..engines.authors_db import AuthorsDb, DbError, uri_from_env_file
from ..engines.steam_api import SteamClient
from ..engines.steam_ugc import SteamUgc

# A web service that already talks to the same database keeps its credentials
# in a `.env`, and reading them beats retyping them. Where that file lives is
# particular to whoever is running this, so it is never guessed: set
# ``WET_SERVER_ENV`` to pre-fill the dialog, and after one pick the chosen
# path is remembered anyway.
SERVER_ENV_VAR = "WET_SERVER_ENV"

# Testing the key needs an author to ask about, and the answer is only
# convincing if that author has published something. This is a public
# workshop account picked for being prolific — nothing about it is
# particular to whoever is running this, and it is read, never written.
PROBE_AUTHOR = "76561198344659208"
_LAST_ENV_SETTING = ("review", "server_env_path")


def _env_dialog_start() -> str:
    """Where the .env picker should open, or "" to let Qt decide."""
    settings = Settings.load()
    remembered = settings.get(*_LAST_ENV_SETTING, "")
    for candidate in (remembered, os.environ.get(SERVER_ENV_VAR, "")):
        if candidate and Path(candidate).exists():
            return candidate
    return ""


class _Check(QThread):
    """One test, off the GUI thread — both of them can take twenty seconds."""

    done = Signal(str, bool)

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self._work = work

    def run(self) -> None:
        try:
            self.done.emit(self._work(), True)
        except Exception as err:  # noqa: BLE001 — the message is the answer
            self.done.emit(str(err), False)


class SecretField(QWidget):
    """A masked line with a reveal box and a verdict underneath it."""

    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setPlaceholderText(placeholder)
        row.addWidget(self.edit, 1)
        self.reveal = QCheckBox("Show")
        self.reveal.toggled.connect(
            lambda on: self.edit.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password))
        row.addWidget(self.reveal)
        self.test = QPushButton("Test")
        row.addWidget(self.test)
        layout.addLayout(row)
        self.verdict = QLabel("")
        self.verdict.setWordWrap(True)
        self.verdict.setStyleSheet(theme.label_style("faint"))
        layout.addWidget(self.verdict)

    def say(self, message: str, good: bool | None = None) -> None:
        kind = "faint" if good is None else ("ok" if good else "danger")
        self.verdict.setStyleSheet(theme.label_style(kind))
        self.verdict.setText(message)

    @property
    def value(self) -> str:
        return self.edit.text().strip()

    @value.setter
    def value(self, text: str) -> None:
        self.edit.setText(text or "")


class CredentialsDialog(QDialog):
    """Steam key, database connection string, and whether either of them works."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Steam and the authors database")
        self.setMinimumWidth(620)
        self._checks: list[_Check] = []

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)

        self.key = SecretField("32 hex characters from steamcommunity.com/dev/apikey")
        self.key.value = secrets.get(secrets.STEAM_API_KEY)
        self.key.test.clicked.connect(self.test_key)
        form.addRow("Steam Web API key", self.key)

        self.uri = SecretField("mongodb+srv://user:password@host/database")
        self.uri.value = secrets.get(secrets.AUTHORS_DB_URI)
        self.uri.test.clicked.connect(self.test_db)
        form.addRow("Authors database", self.uri)
        layout.addLayout(form)

        note = QLabel(
            "The key is what makes an author's list complete: signed out, Steam "
            "hides mature wallpapers, and on this library that is 43% of them. "
            "Both values are stored encrypted for this Windows account.")
        note.setWordWrap(True)
        note.setStyleSheet(theme.label_style("faint"))
        layout.addWidget(note)

        row = QHBoxLayout()
        borrow = QPushButton("Take the connection string from a .env file…")
        borrow.clicked.connect(self.import_env)
        row.addWidget(borrow)
        row.addStretch()
        layout.addLayout(row)

        self.ugc = QLabel("")
        self.ugc.setWordWrap(True)
        self.ugc.setStyleSheet(theme.label_style("faint"))
        layout.addWidget(self.ugc)
        self._describe_ugc()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._say_stored()

    # -- what is already there --------------------------------------------

    def _say_stored(self) -> None:
        for field, name in ((self.key, secrets.STEAM_API_KEY),
                            (self.uri, secrets.AUTHORS_DB_URI)):
            if not secrets.has(name):
                field.say("not set")
            elif secrets.is_protected(name):
                field.say(f"stored, encrypted  ({secrets.masked(name)})")
            else:
                field.say("stored in the clear — DPAPI was unavailable", False)

    def _describe_ugc(self) -> None:
        found = SteamUgc()
        if found.available:
            self.ugc.setText(
                "Subscribing from the gallery uses Wallpaper Engine's own "
                f"Steamworks library ({found.path}). Steam must be running.")
        else:
            self.ugc.setText(
                "Wallpaper Engine's Steamworks library was not found, so the "
                "gallery can only open Steam's page for a wallpaper.")

    # -- testing -----------------------------------------------------------

    def _start(self, field: SecretField, work) -> None:
        field.say("checking…")
        field.test.setEnabled(False)
        check = _Check(work, self)

        def finished(message: str, good: bool) -> None:
            field.say(message, good)
            field.test.setEnabled(True)

        check.done.connect(finished)
        self._checks.append(check)
        check.start()

    def test_key(self) -> None:
        value = self.key.value
        if not value:
            self.key.say("nothing to test", False)
            return

        def work() -> str:
            client = SteamClient(api_key=value, cache_path=None)
            client.check_key()
            author = client.author_items(PROBE_AUTHOR, max_pages=1)
            return (f"Steam accepts the key — it answered with "
                    f"{author.total} wallpapers for a test author")

        self._start(self.key, work)

    def test_db(self) -> None:
        value = self.uri.value
        if not value:
            self.uri.say("nothing to test", False)
            return

        def work() -> str:
            with AuthorsDb(value) as db:
                return f"connected — {db.count()} authors in {db.database}"

        self._start(self.uri, work)

    # -- borrowing from the old server -------------------------------------

    def import_env(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "A service's .env", _env_dialog_start(),
            "Environment files (*.env .env);;All files (*)")
        if not chosen:
            return
        try:
            self.uri.value = uri_from_env_file(chosen)
            self.uri.say("read from the file — press Test to be sure")
        except (DbError, OSError) as err:
            self.uri.say(str(err), False)
            return
        # Only a file that parsed is worth returning to next time.
        settings = Settings.load()
        settings.set(*_LAST_ENV_SETTING, chosen)
        settings.save()

    # -- saving ------------------------------------------------------------

    def save(self) -> None:
        secrets.put(secrets.STEAM_API_KEY, self.key.value)
        secrets.put(secrets.AUTHORS_DB_URI, self.uri.value)
        self.accept()

    def closeEvent(self, event) -> None:
        for check in self._checks:
            check.wait(100)
        super().closeEvent(event)
