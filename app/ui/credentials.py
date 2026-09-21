"""The Steam Web API key — optional, and honest about what it is for.

The Review tab works without a key. Steam then treats the toolkit as a
signed-out visitor, and that costs two things worth knowing before choosing:

* **Mature and questionable wallpapers are invisible** in an author's list —
  43% of the library this was built on. A review without a key recommends
  "nothing new" for exactly the authors it matters most for, and a visit date
  written from such a list moves past wallpapers that were never shown.
* **Each author's name is a page of its own**, so names come from a cache that
  is at most two weeks old instead of being fetched fresh every scan.

So the dialog says both, links to where a key comes from, and lets the key be
tested before it is saved: a wrong key and a missing one fail differently, and
a sentence saying which is worth more than a scan that quietly finds less.

The key is stored by :mod:`app.secrets` — DPAPI-encrypted, so the file is
unreadable to another Windows account and useless if copied off the machine.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget)

from .. import secrets, theme
from ..engines.steam_api import SteamClient
from ..engines.steam_ugc import SteamUgc

KEY_PAGE = "https://steamcommunity.com/dev/apikey"

# Testing the key needs an author to ask about, and the answer is only
# convincing if that author has published something. This is a public
# workshop account picked for being prolific — nothing about it is
# particular to whoever is running this, and it is read, never written.
PROBE_AUTHOR = "76561198344659208"

# What the tab and this dialog both say when there is no key. One wording, so
# the banner and the dialog cannot drift apart.
WITHOUT_KEY = (
    "Without a key Steam treats the toolkit as a signed-out visitor: mature and "
    "questionable wallpapers are left out of every author's list — 43% of the "
    "library this was built on — and author names can be up to two weeks old.")


def has_key() -> bool:
    return secrets.has(secrets.STEAM_API_KEY)


class _Check(QThread):
    """One test, off the GUI thread — it can take several seconds."""

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
    """The Steam key: optional, testable, and clear about the difference."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Steam Web API key")
        self.setMinimumWidth(620)
        self._checks: list[_Check] = []

        layout = QVBoxLayout(self)
        title = QLabel("Steam Web API key  —  optional")
        title.setStyleSheet(theme.label_style("text", size=13, weight=600))
        layout.addWidget(title)

        self.key = SecretField("32 hex characters")
        self.key.value = secrets.get(secrets.STEAM_API_KEY)
        self.key.test.clicked.connect(self.test_key)
        self.key.edit.textChanged.connect(self._describe_choice)
        layout.addWidget(self.key)

        where = QLabel(
            f'A key is free: sign in at <a href="{KEY_PAGE}">{KEY_PAGE}</a>, enter '
            "any domain name, and copy the key it shows.")
        where.setOpenExternalLinks(True)
        where.setWordWrap(True)
        where.setTextInteractionFlags(Qt.TextBrowserInteraction)
        where.setStyleSheet(theme.label_style("muted"))
        layout.addWidget(where)

        # What the choice means, in the colour of the choice: amber while there
        # is no key, quiet once there is.
        self.meaning = QLabel("")
        self.meaning.setWordWrap(True)
        layout.addWidget(self.meaning)

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
        self._describe_choice()

    # -- what is already there --------------------------------------------

    def _say_stored(self) -> None:
        name = secrets.STEAM_API_KEY
        if not secrets.has(name):
            self.key.say("not set — the Review tab works without one, with less")
        elif secrets.is_protected(name):
            self.key.say(f"stored, encrypted  ({secrets.masked(name)})")
        else:
            self.key.say("stored in the clear — DPAPI was unavailable", False)

    def _describe_choice(self, *_args) -> None:
        if self.key.value:
            self.meaning.setStyleSheet(theme.label_style("faint"))
            self.meaning.setText(
                "With a key, author lists are complete — mature wallpapers "
                "included — and names are fetched fresh on every scan, a "
                "hundred authors to a request.")
        else:
            self.meaning.setStyleSheet(theme.label_style("warn"))
            self.meaning.setText(
                "⚠  " + WITHOUT_KEY + " A visit date written from such a list "
                "moves past the wallpapers that were left out, so they are not "
                "offered later either — not even after a key is added.")

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

    def test_key(self) -> None:
        value = self.key.value
        if not value:
            self.key.say("nothing to test — leave it empty to go without a key", None)
            return
        self.key.say("checking…")
        self.key.test.setEnabled(False)

        def work() -> str:
            client = SteamClient(api_key=value, cache_path=None)
            client.check_key()
            author = client.author_items(PROBE_AUTHOR, max_pages=1)
            return (f"Steam accepts the key — it answered with "
                    f"{author.total} wallpapers for a test author")

        check = _Check(work, self)

        def finished(message: str, good: bool) -> None:
            self.key.say(message, good)
            self.key.test.setEnabled(True)

        check.done.connect(finished)
        self._checks.append(check)
        check.start()

    # -- saving ------------------------------------------------------------

    def save(self) -> None:
        secrets.put(secrets.STEAM_API_KEY, self.key.value)
        self.accept()

    def closeEvent(self, event) -> None:
        for check in self._checks:
            check.wait(100)
        super().closeEvent(event)
