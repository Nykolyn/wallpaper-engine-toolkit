"""Where the data lives, and moving it out of the program folder.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_data_location.py

Every folder here is made up under a temporary directory: the old ``data\\``
beside a pretend exe and the new one in a pretend ``%LOCALAPPDATA%``. The
Recycle Bin is stood in for by a function that records what it was handed, so
running this puts nothing in yours.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import data_location as dl     # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_data_location_test_"))

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def scene(name: str) -> tuple[Path, Path]:
    """A pretend install's data\\ and a pretend %LOCALAPPDATA% folder."""
    base = TMP / name
    return base / "install" / "data", base / "local" / dl.APP_FOLDER


def fill(folder: Path) -> dict[str, bytes]:
    """Data the way a real install has it: files at the top, nested, binary."""
    files = {
        "history.json": b'{"runs": []}',
        "secrets.json": b'{"steam_api_key": "dpapi:AAAA"}',
        "authors.sqlite": bytes(range(256)) * 400,
        "authors_backup/journal.jsonl": b'{"op": "add"}\n',
        "thumbs/12345.jpg": b"\xff\xd8" + os.urandom(5000),
    }
    for rel, content in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (folder / "playlist-refresh").mkdir()            # an empty folder too
    return files


class Bin:
    """Stands in for the Recycle Bin: remembers, and removes the folder."""

    def __init__(self, works: bool = True):
        self.works = works
        self.got: list[Path] = []

    def __call__(self, path: Path) -> bool:
        self.got.append(path)
        if self.works:
            shutil.rmtree(path)
        return self.works


def same(folder: Path, files: dict[str, bytes]) -> bool:
    return all((folder / rel).read_bytes() == content for rel, content in files.items())


def marker(folder: Path) -> dict:
    return json.loads((folder / dl.MARKER).read_text(encoding="utf-8"))


# ---- a first start ----------------------------------------------------------

print("-- nothing to move --")
old, new = scene("fresh")
got = dl.settle(old, new, recycle=Bin())
check("a first start uses the new folder", got.folder == new and new.is_dir())
check("and marks it as the data folder, moved from nowhere",
      marker(new)["moved_from"] is None and "(new)" in got.report)
check("and does not make a data folder beside the exe", not old.exists())
again = dl.settle(old, new, recycle=Bin())
check("the next start finds it marked and uses it", again.folder == new
      and again.report == str(new))

# ---- the move ---------------------------------------------------------------

print("-- moving data\\ out --")
old, new = scene("move")
files = fill(old)
bin_ = Bin()
got = dl.settle(old, new, recycle=bin_)
check("the new folder is the one to use", got.folder == new)
check("every file arrived byte for byte", same(new, files))
check("empty folders come along too", (new / "playlist-refresh").is_dir())
m = marker(new)
check("the marker says where from, and how much",
      m["moved_from"] == str(old) and m["files"] == len(files)
      and m["bytes"] == sum(len(c) for c in files.values()))
check("only then does the old folder go to the Recycle Bin",
      bin_.got == [old] and not old.exists())
check("the report says it was moved and verified",
      "moved from" in got.report and "verified" in got.report
      and "Recycle Bin" in got.report)
check("no staging folder is left behind",
      not new.with_name(new.name + dl.STAGING_SUFFIX).exists())

print("-- a copy that does not match --")
old, new = scene("mismatch")
files = fill(old)
real_sha = dl._sha256
dl._sha256 = lambda p: real_sha(p) + ("x" if dl.STAGING_SUFFIX in str(p) else "")
bin_ = Bin()
try:
    got = dl.settle(old, new, recycle=bin_)
finally:
    dl._sha256 = real_sha
check("a file that differs keeps the old folder in use", got.folder == old)
check("the old folder is untouched and nothing is recycled",
      same(old, files) and bin_.got == [])
check("the half-made copy is removed, and no new folder appears",
      not new.exists() and not new.with_name(new.name + dl.STAGING_SUFFIX).exists())
check("the report says the move failed", "failed" in got.report)
got = dl.settle(old, new, recycle=bin_)
check("the next start tries again, and succeeds",
      got.folder == new and same(new, files) and bin_.got == [old])

print("-- a move that died half way --")
old, new = scene("resume")
files = fill(old)
staging = new.with_name(new.name + dl.STAGING_SUFFIX)
(staging / "thumbs").mkdir(parents=True)
(staging / "thumbs" / "half.jpg").write_bytes(b"half")
got = dl.settle(old, new, recycle=Bin())
check("its staging folder is cleared and the move completes",
      got.folder == new and same(new, files) and not (new / "thumbs" / "half.jpg").exists()
      and not staging.exists())

print("-- the Recycle Bin says no --")
old, new = scene("no-bin")
files = fill(old)
got = dl.settle(old, new, recycle=Bin(works=False))
check("the verified copy is still used", got.folder == new and same(new, files))
check("the old folder is left, and the report says where",
      same(old, files) and "still at" in got.report)
got = dl.settle(old, new, recycle=Bin())
check("later starts use the new folder and point at the old one, deleting nothing",
      got.folder == new and same(old, files) and "not used" in got.report)

# ---- folders that are not what they seem -------------------------------------

print("-- a new folder nobody marked --")
old, new = scene("foreign")
files = fill(old)
new.mkdir(parents=True)
(new / "something.txt").write_text("not ours to overwrite")
got = dl.settle(old, new, recycle=Bin())
check("with data beside the exe too, nothing moves and the old folder stays in use",
      got.folder == old and same(old, files)
      and (new / "something.txt").read_text() == "not ours to overwrite"
      and not (new / dl.MARKER).exists())

old, new = scene("adopt")
new.mkdir(parents=True)
(new / "history.json").write_text('{"runs": []}')
got = dl.settle(old, new, recycle=Bin())
check("with nothing beside the exe, it is adopted rather than writing beside the exe",
      got.folder == new and (new / dl.MARKER).exists() and "adopted" in got.report
      and not old.exists())

# ---- inside another app's sandbox -----------------------------------------------
#
# A process started from a Store app's terminal writes %LOCALAPPDATA% into that
# app's private copy. The first start of 3.0.0 was such a process, and moved the
# data where the tray tracker could not see it.

print("-- inside another app's sandbox --")
old, new = scene("sandbox-move")
files = fill(old)
bin_ = Bin()
got = dl.settle(old, new, recycle=bin_, sandbox="Some.App_1234")
check("data beside the exe is not moved; it stays in use",
      got.folder == old and same(old, files) and bin_.got == [])
check("no new folder and no marker are made",
      not new.exists())
check("the report names the app and says the next start outside it moves it",
      "Some.App_1234" in got.report and "next start outside" in got.report)
got = dl.settle(old, new, recycle=bin_)
check("that next start, outside, does move it", got.folder == new and same(new, files))

old, new = scene("sandbox-moved")
dl.settle(old, new, recycle=Bin())
before = sorted(p.name for p in new.iterdir())
got = dl.settle(old, new, recycle=Bin(), sandbox="Some.App_1234")
check("a folder already marked is used, and nothing is written to it",
      got.folder == new and sorted(p.name for p in new.iterdir()) == before
      and "changed in place" in got.report)

old, new = scene("sandbox-fresh")
got = dl.settle(old, new, recycle=Bin(), sandbox="Some.App_1234")
check("with no data anywhere, nothing is marked",
      not (new / dl.MARKER).exists() and "not set up yet" in got.report)

old, new = scene("sandbox-copy")
copy = dl.sandbox_copy(new, "Some.App_1234")
check("the app's copy is looked for under Packages\\<app>\\LocalCache\\Local",
      copy == new.parent / "Packages" / "Some.App_1234" / "LocalCache" / "Local" / new.name)
copy.mkdir(parents=True)
(copy / "selfcheck.txt").write_text("version: 3.0.1")
got = dl.settle(old, new, recycle=Bin(), sandbox="Some.App_1234")
check("a selfcheck report left in the app's copy is not taken for data",
      "WARNING" not in got.report)
(copy / "history.json").write_text('{"runs": []}')
got = dl.settle(old, new, recycle=Bin(), sandbox="Some.App_1234")
check("a copy of the data inside the app's sandbox is warned about, by path",
      "WARNING" in got.report and str(copy) in got.report)

print("-- finding the sandbox --")
where = dl.redirected_into()
local = dl.installed_dir().parent
check("it names an app with a private copy, or none — whichever this process is in",
      where == "" or (local / "Packages" / where / "LocalCache").is_dir())
check("and leaves no probe folder behind",
      not any(p.name.startswith(dl.APP_FOLDER + ".write-probe")
              for p in local.iterdir()))
print(f"     (this process: {where or 'not in a sandbox'})")

# ---- how the folder is chosen -------------------------------------------------

print("-- choosing the folder --")
saved_env = os.environ.get(dl.OVERRIDE_ENV)
os.environ[dl.OVERRIDE_ENV] = str(TMP / "override")
dl._resolved = None
check("WALLPAPER_TOOLKIT_DATA names the folder outright",
      dl.data_dir() == TMP / "override" and (TMP / "override").is_dir())
del os.environ[dl.OVERRIDE_ENV]
dl._resolved = None
check("a source run keeps its data beside run_app.py",
      not getattr(sys, "frozen", False)
      and dl.data_dir() == Path(dl.__file__).resolve().parent.parent / "data")
if saved_env is not None:
    os.environ[dl.OVERRIDE_ENV] = saved_env
dl._resolved = None

saved_local = os.environ.get("LOCALAPPDATA")
os.environ["LOCALAPPDATA"] = str(TMP / "appdata")
check("a build's folder is %LOCALAPPDATA%\\WallpaperEngineToolkit",
      dl.installed_dir() == TMP / "appdata" / "WallpaperEngineToolkit")
if saved_local is None:
    del os.environ["LOCALAPPDATA"]
else:
    os.environ["LOCALAPPDATA"] = saved_local

print("-- two processes at once --")
if sys.platform == "win32":
    order: list[str] = []
    holding = threading.Event()

    def first():
        with dl._exclusive():
            holding.set()
            time.sleep(0.4)
            order.append("first done")

    def second():
        holding.wait()
        with dl._exclusive():
            order.append("second in")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    check("the second waits for the first to finish",
          order == ["first done", "second in"])
else:
    check("(the mutex is Windows only)", True)

shutil.rmtree(TMP, ignore_errors=True)

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
