"""The LogStore: each tool's log lines, in files kept for 30 days.

    data/logs/<tool>/YYYY-MM-DD.log      a tool's lines, a file a day
    data/logs/rotator/run-<id>.log       one rotation's lines, a file a run

A line is `HH:MM:SS<TAB>kind<TAB>message`: the three columns a LogPanel
shows, in the same words, so a file read back looks as the panel did. The
kinds are the LogPanel's (moved, done, skip, dupe, fail, error, step, start,
info, warn …); the date is in the file's name.

- `open(tool, run_id=None)` gives a `LogWriter`; `write(kind, message)` adds a
  line, `write_text("[WARN]  …")` one in the callback engines' own format.
- `tail(tool, n)` is the last lines for a collapsed LogPanel.
- `folder(tool)` / `open_folder(tool)` for "Open log folder".
- `sweep()` deletes the files older than `KEEP_DAYS`, by the day in a daily
  file's name and by the last write of a run file; `sweep_in_background()`
  does it on a thread, as the window does once at start-up. Only files named
  as this store names them are ever deleted, never a folder.

`data/tracker.log` is not here and stays where it is: the tray writes it, it
is what to read when the tray did not come up, and the docs point at it.
"""
from __future__ import annotations

import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import external
from . import textfile

FOLDER_NAME = "logs"
KEEP_DAYS = 30
DAILY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.log$")
RUN_FILE = re.compile(r"^run-[A-Za-z0-9_-]+\.log$")
_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}$")

# The callback engines (Copier, Creator) write "[TAG]   message". The tag
# becomes the line's kind: the LogPanel's own word where there is one that
# says the same, so it is coloured the same.
TAG_KINDS = {
    "OK": "done", "DONE": "done", "FINISH": "done", "COPY": "done",
    "FIX": "fixed", "WARN": "warn", "ERROR": "error", "START": "start",
    "INFO": "info", "TAGS": "info", "GIF": "step", "CANCEL": "stop",
}
_TAGGED = re.compile(r"^\s*\[([A-Za-z]+)\]\s?(.*)$")
_RULE = re.compile(r"^[\s=\-]*$")      # "=====" and "-----" between report parts


def parse_text(text: str) -> list[tuple[str, str]]:
    """(kind, message) for each line a callback engine logged at once.

    Empty lines and the rules around a report are dropped; a line with no tag
    is `info`. A copy the Copier marks ❌ is a `fail`, whatever its tag.
    """
    found = []
    for line in str(text).splitlines():
        if _RULE.match(line):
            continue
        match = _TAGGED.match(line)
        if match:
            tag, message = match.group(1).upper(), match.group(2).strip()
            kind = TAG_KINDS.get(tag, tag.lower())
        else:
            kind, message = "info", line.strip()
        if "❌" in message and kind in ("done", "info"):
            kind = "fail"
        found.append((kind, message))
    return found


def parse_line(line: str) -> tuple[str, str, str]:
    """(time, kind, message) from a line of a log file.

    A line not in the three-column shape — written by hand, or cut short — is
    kept whole as the message of an `info` line with no time.
    """
    parts = line.split("\t", 2)
    if len(parts) == 3 and _TIME.match(parts[0]):
        return parts[0], parts[1] or "info", parts[2]
    return "", "info", line


class LogWriter:
    """Appends one tool's lines to its file. Made by `LogStore.open`.

    The file is held open between lines — a rotation writes a line per folder
    moved — and every line goes to the disk as it is written, so a crash loses
    nothing already logged. A daily log moves to the next day's file at
    midnight. A write that fails drops that line only; `error` says why.
    """

    def __init__(self, store: "LogStore", tool: str, run_id: str | None = None):
        self._store = store
        self.tool = tool
        self.run_id = run_id
        self.error = ""
        self._file = None
        self._day: date | None = None
        self.path = store.path_for(tool, run_id)

    @property
    def name(self) -> str:
        """`rotator/run-3f9a0c1e.log`: the file, as the LogPanel header names it."""
        return f"{self.tool}/{self.path.name}"

    def write(self, kind: str, message: str, when: datetime | None = None) -> None:
        when = when or datetime.now()
        kind = " ".join(str(kind or "info").split()).casefold() or "info"
        lines = [part for part in str(message).splitlines() if part.strip()] or [""]
        text = "".join(f"{when:%H:%M:%S}\t{kind}\t{part}\n" for part in lines)
        try:
            self._file_for(when).write(text)
            self.error = ""
        except OSError as err:
            self.error = f"{self.name} could not be written: {err}"

    def write_text(self, text: str, when: datetime | None = None) -> None:
        for kind, message in parse_text(text):
            self.write(kind, message, when)

    def _file_for(self, when: datetime):
        if self.run_id is None and self._day != when.date():
            self.close()
            self.path = self._store.path_for(self.tool, None, when.date())
        if self._file is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Line-buffered: each line reaches the disk as it is written.
            self._file = open(self.path, "a", encoding="utf-8", newline="\n", buffering=1)
            self._day = when.date()
        return self._file

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None

    def __enter__(self) -> "LogWriter":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def __del__(self):
        self.close()


class LogStore:
    """`data/logs/`: where every tool's log files are, and how long they stay."""

    def __init__(self, root: str | Path, *, keep_days: int = KEEP_DAYS):
        self.root = Path(root)
        self.keep_days = keep_days

    # -- writing

    def open(self, tool: str, run_id: str | None = None) -> LogWriter:
        """A writer for this tool's log: today's file, or a rotation run's own."""
        return LogWriter(self, tool, run_id)

    def path_for(self, tool: str, run_id: str | None = None,
                 day: date | None = None) -> Path:
        if not _NAME.match(tool or ""):
            raise ValueError(f"a tool's log is named by a plain word, not {tool!r}")
        if run_id is not None:
            if not _NAME.match(str(run_id)):
                raise ValueError(f"a run id is a plain word, not {run_id!r}")
            return self.root / tool / f"run-{run_id}.log"
        return self.root / tool / f"{(day or date.today()).isoformat()}.log"

    # -- reading

    def folder(self, tool: str | None = None) -> Path:
        """The tool's log folder (every tool's with None), made if missing."""
        folder = self.root if tool is None else self.path_for(tool).parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return folder

    def open_folder(self, tool: str | None = None) -> None:
        """"Open log folder": the tool's log folder in Explorer."""
        external.popen(["explorer", str(self.folder(tool))])

    def files(self, tool: str | None = None) -> list[Path]:
        """This tool's log files (every tool's with None), the last written first."""
        folders = [self.root / tool] if tool else (
            [p for p in self.root.iterdir() if p.is_dir()] if self.root.is_dir() else [])
        found = []
        for folder in folders:
            try:
                for p in folder.iterdir():
                    if p.is_file() and (DAILY_FILE.match(p.name) or RUN_FILE.match(p.name)):
                        try:
                            found.append((p.stat().st_mtime, p))
                        except OSError:
                            pass
            except OSError:
                continue
        found.sort(key=lambda pair: pair[0], reverse=True)
        return [p for _, p in found]

    def tail(self, tool: str | None, n: int) -> list[tuple[str, str, str]]:
        """The last `n` lines of this tool's log (any tool's with None), oldest
        first, as (time, kind, message) — what `LogPanel.extend` takes.

        Starts in the file written last and reaches back into older ones only
        when that one is shorter than `n`.
        """
        lines: list[str] = []
        for path in self.files(tool):
            lines = textfile.tail(path, n - len(lines)) + lines
            if len(lines) >= n:
                break
        return [parse_line(line) for line in lines]

    # -- keeping

    def sweep(self, now: datetime | None = None) -> list[Path]:
        """Delete the log files older than `keep_days`. Returns what went.

        A daily file's age is the day in its name — the day it holds — and a
        run file's is its last write. Files named any other way are left alone.
        """
        now = now or datetime.now()
        cutoff = now - timedelta(days=self.keep_days)
        gone = []
        for path in self.files(None):
            daily = DAILY_FILE.match(path.name)
            try:
                if daily:
                    old = datetime.fromisoformat(daily.group(1)) < cutoff.replace(
                        hour=0, minute=0, second=0, microsecond=0)
                else:
                    old = datetime.fromtimestamp(path.stat().st_mtime) < cutoff
                if old:
                    path.unlink()
                    gone.append(path)
            except (OSError, ValueError):
                continue
        return gone

    def sweep_in_background(self, now: datetime | None = None) -> threading.Thread:
        """`sweep()` on a thread of its own: listing a folder is no work for the
        GUI thread. The thread is returned for tests to join."""
        thread = threading.Thread(target=self.sweep, args=(now,), daemon=True,
                                  name="log retention sweep")
        thread.start()
        return thread
