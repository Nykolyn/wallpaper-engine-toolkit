"""Quota persistence, weighted rotations, small tails and successful retry debits."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = Path(tempfile.mkdtemp(prefix="multiplier_test_"))
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir()

from app.engines.multipliers import MultiplierStore                       # noqa: E402
from app.engines.rotator import config as rc, core                         # noqa: E402
from app.engines.rotator.config import Config, History, RunRecord          # noqa: E402


def world(tag, reserve=("a", "b"), current=(), count=5, runs=None):
    root = TMP / tag
    src, dst, dup = root / "reserve", root / "myprojects", root / "duplicates"
    for folder in (src, dst, dup):
        folder.mkdir(parents=True)
    for folder, names in ((src, reserve), (dst, current)):
        for name in names:
            (folder / name).mkdir()
            (folder / name / "project.json").write_text("{}", encoding="utf-8")
    rc.HISTORY_PATH = root / "history.json"
    history = History([] if runs is None else runs)
    history.save()
    return Config(source=str(src), destination=str(dst), duplicates=str(dup),
                  count=count, refresh_playlist=False), history, MultiplierStore()


def run_record(run_id, moved=(), reset=False, failed=()):
    return RunRecord(run_id, "2026-10-01 10:00:00", list(moved),
                     failed=list(failed), history_reset=reset)


cfg, history, store = world("tail", reserve=("b",), current=("a",),
                            runs=[run_record("old", ["a"])])
store.set_multiplier("a", 3)
usage = store.usage(history.runs)
assert (usage.multiplier("a"), usage.used("a"), usage.remaining("a")) == (3, 1, 2)
assert usage.remaining("b") == 1
assert history.last_used("[protected] a") == history.last_used("a")
assert store.multiplier("[PROTECTED] A") == 3
other = MultiplierStore()
other.set_multiplier("b", 2)
store.set_multiplier("a", 4)
assert MultiplierStore().multiplier("b") == 2       # a stale writer keeps the other edit
store.set_multiplier("a", 3)
store.set_multiplier("b", 1)

rates = []


def race(weight):
    rates.append(weight)
    return 1 / weight


with patch.object(core.random, "expovariate", race):
    first = core.Rotator(cfg, history).run()
assert set(first.moved) == {"a", "b"} and len(first.moved) == 2
assert sorted(rates) == [1, 2]                      # weight is remaining, not the configured 3
assert not first.history_reset
assert store.usage(history.runs).remaining("a") == 1
second = core.Rotator(cfg, history).run()
assert second.moved == ["a"] and not second.history_reset
assert core.Rotator(cfg, history).preview()["will_reset"]
third = core.Rotator(cfg, history).run()
assert set(third.moved) == {"a", "b"} and third.history_reset
assert store.usage(history.runs).remaining("a") == 2
assert set(asdict(third)) == {"id", "timestamp", "moved", "duplicates", "returned",
                              "failed", "history_reset"}

cfg, history, store = world("active-edit", reserve=("b",), current=("a",),
                            runs=[run_record("old", ["a"])])


def change_during_return(event):
    if event.phase == "return" and event.kind == "returned":
        store.set_multiplier("a", 20)


current = core.Rotator(cfg, history).run(change_during_return)
assert current.moved == ["b"]                      # running selection kept its original snapshot
store.reload()
assert store.usage(history.runs).remaining("a") == 19
assert core.Rotator(cfg, history).run().moved == ["a"]
assert store.usage(history.runs).remaining("a") == 18
store.set_multiplier("a", 1)
assert store.usage(history.runs).remaining("a") == 0
store.set_multiplier("a", 5)
assert store.usage(history.runs).remaining("a") == 3

cfg, history, store = world("failed-move", reserve=("a", "b", "c"))
real_move = shutil.move


def fail_b(src, dst):
    if Path(src).name == "b":
        raise PermissionError("locked")
    return real_move(src, dst)


with patch.object(core.shutil, "move", fail_b):
    rotation = core.Rotator(cfg, history)
    record = rotation.run()
assert set(record.moved) == {"a", "c"} and record.failed == ["b"]
assert store.usage(history.runs).remaining("b") == 1
assert store.usage(history.runs).remaining("a") == 0
assert rotation.retry(record, [], ["b"]).moved == ["b"]
assert store.usage(history.runs).remaining("b") == 0
assert rotation.retry(record, [], []).moved == []
assert store.usage(history.runs).used("b") == 1

old = run_record("locked-old", failed=["b"])
cfg, history, store = world("failed-old-retry", reserve=("b",),
                            runs=[run_record("reset", ["a"], True), old])
with patch.object(core.shutil, "move", fail_b):
    result = core.Rotator(cfg, history).retry(old, [], ["b"])
store.reload()
assert result.still_failed == ["b"] and store.usage(history.runs).used("b") == 0
assert core.Rotator(cfg, history).retry(old, [], ["b"]).moved == ["b"]
store.reload()
assert store.usage(history.runs).used("b") == 1

old = run_record("ledger-locked-old", failed=["b"])
cfg, history, store = world("failed-ledger-save", reserve=("b",),
                            runs=[run_record("reset", ["a"], True), old])
real_write = rc._write_atomically


def fail_quota_write(path, text):
    if path.name == "multipliers.json":
        raise PermissionError("read-only quota data")
    return real_write(path, text)


with patch.object(rc, "_write_atomically", fail_quota_write):
    result = core.Rotator(cfg, history).retry(old, [], ["b"])
assert result.still_failed == ["b"] and old.moved == []
assert (Path(cfg.source) / "b").is_dir()
assert store.usage(history.runs).remaining("b") == 1

old = run_record("old-failure", failed=["b"])
cfg, history, store = world("old-retry", reserve=("b",), current=("a",),
                            runs=[run_record("reset", ["a"], True), old])
store.set_multiplier("b", 2)
assert core.Rotator(cfg, history).retry(old, [], ["b"]).moved == ["b"]
store.reload()
assert store.usage(history.runs).remaining("b") == 1
assert MultiplierStore().usage(rc.read_runs()).used("b") == 1
history.runs.insert(0, run_record("next-reset", ["a"], True))
assert store.usage(history.runs).used("b") == 0      # the historical retry belongs to its cycle

old = run_record("exhausted-failure", failed=["b"])
cfg, history, store = world("exhausted-retry", reserve=("b",),
                            runs=[run_record("reset", ["b"], True), old])
result = core.Rotator(cfg, history).retry(old, [], ["b"])
assert result.moved == [] and result.still_failed == ["b"]
assert (Path(cfg.source) / "b").is_dir()

old = run_record("manual", failed=["b"])
cfg, history, store = world("manual-retry", reserve=(), current=("b",), runs=[old])
result = core.Rotator(cfg, history).retry(old, [], ["b"])
assert result.resolved == 1 and result.moved == [] and old.moved == []
assert store.usage(history.runs).remaining("b") == 1

cfg, history, store = world("protected", reserve=("a", "b"), current=("[protected] a",))
assert core.Rotator(cfg, history).preview()["available_unique"] == 1
assert core.Rotator(cfg, history).run().moved == ["b"]
assert (Path(cfg.destination) / "[protected] a").is_dir()

cfg, history, store = world("invalid")
store.path.write_text('{"version":1,"multipliers":{"a":0}}', encoding="utf-8")
before = store.path.read_bytes()
broken = MultiplierStore()
assert broken.problem and core.Rotator(cfg, history).validate()
for action in (lambda: broken.multiplier("a"), lambda: broken.usage(history.runs),
               lambda: broken.set_multiplier("a", 20),
               lambda: core.Rotator(cfg, history).run()):
    try:
        action()
        raise AssertionError("Malformed persistence was accepted")
    except ValueError:
        pass
assert store.path.read_bytes() == before
assert (Path(cfg.source) / "a").is_dir() and not (Path(cfg.destination) / "a").exists()

store.path.unlink()
store.reload()
store.path.write_text(json.dumps({"version": 1, "multipliers": {"a": 20},
    "baseline": {"run_id": "", "moved_count": 0, "active": ["a"]}, "retries": []}), encoding="utf-8")
store.reload()
assert store.usage(history.runs).remaining("a") == 19
history.runs = [run_record("new", ["b"])]
assert store.usage(history.runs).remaining("a") == 19

cfg, history, store = world("missing-baseline", runs=[run_record("baseline", ["a"])])
store.path.write_text(json.dumps({"version": 1, "multipliers": {"a": 2},
    "baseline": {"run_id": "baseline", "moved_count": 1, "active": ["a"]}, "retries": []}), encoding="utf-8")
store.reload()
try:
    store.usage([])
    raise AssertionError("Lost baseline was accepted")
except ValueError:
    pass

store.set_multiplier("a", 2)
with patch.object(rc, "_write_atomically", side_effect=PermissionError("read-only")):
    try:
        store.set_multiplier("a", 8)
        raise AssertionError("Failed save was accepted")
    except OSError:
        pass
assert store.multiplier("a") == 2

shutil.rmtree(TMP)
print("PASS multiplier persistence, quotas, tail cycles, weighted uniqueness and retries")
