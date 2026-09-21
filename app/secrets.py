"""Local secret storage — the Steam Web API key.

The Review tab can use a credential that must not live in the source tree, in
`suite.json`, or in a log line: the Steam Web API key (optional — see
:mod:`app.ui.credentials` for what going without one costs). It is kept here
instead, in `data/secrets.json`, encrypted with **DPAPI** — Windows' own
per-user data protection.

DPAPI is the right size of answer for this. The key is derived from the logged-in
Windows account, so the file is unreadable by another user and useless if it is
copied off the machine, and nothing has to be typed at start-up or stored to
unlock it. It is not protection against someone already running as this user —
nothing local can be — it is protection against the file leaking on its own,
which is the failure that actually happens: a backup, a synced folder, a
screen-share of an editor.

There are no dependencies: `CryptProtectData` is called through `ctypes`. Where
DPAPI is unavailable (a non-Windows run, a stripped image), the value is stored
in the clear, marked as such, and :func:`is_protected` says so — so a UI can
report the difference rather than implying a safety it does not have.
"""
from __future__ import annotations

import base64
import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path

from .settings import app_data_dir

SECRETS_PATH = app_data_dir() / "secrets.json"

# Keys used by the toolkit.
STEAM_API_KEY = "steam_api_key"

_PLAIN = "plain:"
_DPAPI = "dpapi:"


# ---- DPAPI through ctypes --------------------------------------------------

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]

    @classmethod
    def of(cls, data: bytes) -> "_Blob":
        buffer = ctypes.create_string_buffer(data, len(data))
        return cls(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    def value(self) -> bytes:
        return ctypes.string_at(self.pbData, self.cbData)


def _crypt32():
    if sys.platform != "win32":
        return None
    try:
        return ctypes.WinDLL("crypt32.dll")
    except OSError:
        return None


def _protect(text: str) -> str:
    """Encrypt for this Windows user, or fall back to storing it as it is."""
    crypt32 = _crypt32()
    if crypt32 is None:
        return _PLAIN + text
    source = _Blob.of(text.encode("utf-8"))
    result = _Blob()
    ok = crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None,
                                  0, ctypes.byref(result))
    if not ok:
        return _PLAIN + text
    try:
        return _DPAPI + base64.b64encode(result.value()).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def _unprotect(stored: str) -> str:
    if stored.startswith(_PLAIN):
        return stored[len(_PLAIN):]
    if not stored.startswith(_DPAPI):
        return stored
    crypt32 = _crypt32()
    if crypt32 is None:
        return ""
    try:
        raw = base64.b64decode(stored[len(_DPAPI):])
    except (ValueError, TypeError):
        return ""
    source = _Blob.of(raw)
    result = _Blob()
    ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None,
                                    0, ctypes.byref(result))
    if not ok:
        # Written by a different Windows account, or the profile was rebuilt.
        return ""
    try:
        return result.value().decode("utf-8", "replace")
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


# ---- The store -------------------------------------------------------------

def _load(path: Path = SECRETS_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict, path: Path = SECRETS_PATH) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get(name: str, path: Path = SECRETS_PATH) -> str:
    """The stored secret, or an empty string if there is none to read."""
    stored = _load(path).get(name)
    return _unprotect(stored) if isinstance(stored, str) else ""


def put(name: str, value: str, path: Path = SECRETS_PATH) -> None:
    """Store a secret, or remove it when handed an empty value."""
    data = _load(path)
    if value:
        data[name] = _protect(value)
    else:
        data.pop(name, None)
    _save(data, path)


def has(name: str, path: Path = SECRETS_PATH) -> bool:
    return bool(get(name, path))


def is_protected(name: str, path: Path = SECRETS_PATH) -> bool:
    """False when the value had to be written in the clear."""
    stored = _load(path).get(name)
    return isinstance(stored, str) and stored.startswith(_DPAPI)


def masked(name: str, path: Path = SECRETS_PATH, keep: int = 4) -> str:
    """A form fit for a window or a log: ``0123…CDEF``."""
    value = get(name, path)
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}…{value[-keep:]}"
