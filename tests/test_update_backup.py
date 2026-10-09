"""The copy of the data taken before an update.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_update_backup.py

Every folder is made up under a temporary directory, and the Recycle Bin is
stood in for by a function that records what it was handed.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import update_backup as ub     # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_update_backup_test_"))

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


class Bin:
    def __init__(self, works: bool = True):
        self.works = works
        self.got: list[Path] = []

    def __call__(self, path: Path) -> bool:
        self.got.append(path)
        if self.works:
            shutil.rmtree(path)
        return self.works


data = TMP / "WallpaperEngineToolkit"
kept = {
    "suite.json": b'{"rotator": {}}',
    "secrets.json": b'{"steam_api_key": "dpapi:AAAA"}',
    "authors.sqlite": bytes(range(256)) * 300,
    "history.json": b'{"runs": [1, 2]}',
    "playlist-refresh/config.json": b"{}",
}
left = {
    "thumbs/123.img": b"\xff" * 4000,
    "logs/rotation.txt": b"moved",
    "authors_backup/snap.json.gz": b"gz",
    "history_backup/history.json": b"{}",
    "tracker.log": b"started",
}
for rel, content in {**kept, **left}.items():
    (data / rel).parent.mkdir(parents=True, exist_ok=True)
    (data / rel).write_bytes(content)

start = datetime(2026, 10, 10, 15, 30, 0)
copy, said = ub.take(data, "before 3.19.0", now=start, recycle=Bin())
check("the copy is named for when it was taken and what came next",
      copy == data / "update_backup" / "2026-10-10 153000 before 3.19.0")
check("everything that cannot be had again is in it, byte for byte",
      all((copy / rel).read_bytes() == content for rel, content in kept.items()))
check("previews, logs and the app's own backups are left out",
      not any((copy / rel).exists() for rel in left))
manifest = json.loads((copy / ub.MANIFEST).read_text(encoding="utf-8"))
check("the manifest lists every file with its size and checksum",
      sorted(f["file"] for f in manifest["files"]) == sorted(kept)
      and all(len(f["sha256"]) == 64 for f in manifest["files"]))
check("and says what it was taken before", manifest["label"] == "before 3.19.0")
check("the report says how much was copied and that it was checked",
      "5 files" in said and "verified" in said)
check("the data itself is as it was",
      all((data / rel).read_bytes() == content for rel, content in {**kept, **left}.items()))
check("nothing half made is left beside it",
      not any(p.name.endswith(ub.PARTIAL) for p in (data / "update_backup").iterdir()))

copy2, _ = ub.take(data, "before 3.19.0", now=start, recycle=Bin())
check("a second copy in the same second gets a name of its own",
      copy2.name.endswith("(2)") and copy.exists())

bin_ = Bin()
for day in range(1, 6):
    ub.take(data, f"before 3.19.{day}", now=start + timedelta(days=day), recycle=bin_)
names = sorted(p.name for p in (data / "update_backup").iterdir())
check(f"only the newest {ub.KEEP} are kept", len(names) == ub.KEEP
      and names[-1].startswith("2026-10-15") and not any(n.startswith("2026-10-10") for n in names))
check("the older ones went to the Recycle Bin, oldest first",
      [p.name for p in bin_.got] == ["2026-10-10 153000 before 3.19.0",
                                     "2026-10-10 153000 before 3.19.0 (2)"])

stuck = Bin(works=False)
_, said = ub.take(data, "before 3.20.0", now=start + timedelta(days=9), recycle=stuck)
check("a copy the Recycle Bin will not take is left, and that is said",
      "could not" in said and len(list((data / "update_backup").iterdir())) == ub.KEEP + 1)


def broken_copy(*args, **kwargs):
    raise OSError("disk full")


real_copy = shutil.copy2
shutil.copy2 = broken_copy
try:
    ub.take(data, "before 4.0.0", now=start + timedelta(days=20), recycle=Bin())
    raised = False
except OSError:
    raised = True
finally:
    shutil.copy2 = real_copy
check("a copy that cannot be made raises, and leaves nothing half made",
      raised and not any(p.name.endswith(ub.PARTIAL) or "4.0.0" in p.name
                         for p in (data / "update_backup").iterdir()))

shutil.rmtree(TMP, ignore_errors=True)

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
