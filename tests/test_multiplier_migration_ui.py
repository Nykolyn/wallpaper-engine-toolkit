"""Migration UI starts only approved plans and blocks concurrent rotations."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

TMP = Path(tempfile.mkdtemp(prefix="quota_migration_ui_"))
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication, QWidget
from app.engines import multiplier_migration as migration
from app.engines.multipliers import MultiplierStore
from app.engines.rotator.config import Config
from app.pages.multiplier_migration import MigrationController
from app.settings import Settings

patch("app.pages.multiplier_migration.steam_paths.we_config",
      side_effect=AssertionError("Migration fixtures must never discover live Wallpaper Engine files")).start()
app = QApplication.instance() or QApplication([])

class Page(QWidget):
    _fixture = None
    _migration_busy = False
    def __init__(self):
        super().__init__()
        self.messages = []
    def _busy(self):
        return self._migration_busy
    def _say(self, tone, words):
        self.messages.append((tone, words))

def wait(fn):
    until = time.monotonic() + 10
    while time.monotonic() < until:
        app.processEvents()
        if fn():
            return
        time.sleep(0.01)
    raise AssertionError("Migration UI worker did not finish")

reserve, current = TMP / "reserve", TMP / "current"
reserve.mkdir(); current.mkdir()
for folder in (reserve / "garden", reserve / "garden_copy1", current / "garden_copy4"):
    folder.mkdir()
    (folder / "project.json").write_text(json.dumps({"type":"video","file":"wall.mp4"}))
    (folder / "wall.mp4").write_bytes(b"verified payload")
config = Config(source=str(reserve), destination=str(current), duplicates=str(TMP / "duplicates"))
store = MultiplierStore(TMP / "data" / "multipliers.json")
page, updates = Page(), []
controller = MigrationController(page, Settings({"tracker": {"we_config": str(TMP / "we" / "config.json")}}), None, config, store,
                                 on_done=lambda: updates.append(True))
answers = []
controller._answer = lambda dialog: answers.append(dialog) or True
with patch("app.pages.multiplier_migration.engine_control.running", return_value=None):
    assert controller.start()
    assert page._migration_busy
    assert not controller.start()
    wait(lambda: bool(updates))
assert not page._migration_busy and len(answers) == 1
assert (current / "garden" / "wall.mp4").read_bytes() == b"verified payload"
assert not (reserve / "garden_copy1").exists()
assert not (current / "garden_copy4").exists()
assert store.multiplier("garden") == 3 and store.usage([]).remaining("garden") == 2
assert controller.latest.is_file()
# Inspecting a completed migration without new copies must preserve the cycle.
before = store.path.read_bytes()
with patch("app.pages.multiplier_migration.engine_control.running", return_value=None):
    assert controller.start()
    wait(lambda: not controller.busy)
assert store.path.read_bytes() == before
# Refusal to close Wallpaper Engine prevents all filesystem work.
called = []
with patch("app.pages.multiplier_migration.engine_control.running", return_value=object()), \
     patch("app.pages.multiplier_migration.engine_control.close", return_value=False):
    controller._execute(lambda: called.append(True))
    wait(lambda: not controller.busy)
assert not called and "could not close" in page.messages[-1][1]
# Saves in flight block migration, and both page editors are locked for the worker.
class Editor:
    pending = set()
    locked = False
    def _snapshot(self):
        pass
editor = Editor()
controller.editors = (editor,)
editor.pending = {"garden"}
assert not controller.start()
editor.pending.clear()
engine = object()
with patch("app.pages.multiplier_migration.engine_control.running", return_value=engine), \
     patch("app.pages.multiplier_migration.engine_control.close", return_value=True) as close, \
     patch("app.pages.multiplier_migration.engine_control.start", return_value=True) as start:
    def approved_work():
        assert editor.locked
        return controller.latest
    controller._execute(approved_work)
    assert editor.locked
    wait(lambda: not controller.busy)
    close.assert_called_once_with(engine)
    start.assert_called_once_with(engine)
assert not editor.locked
# A restart failure is reported after successful migration.
with patch("app.pages.multiplier_migration.engine_control.running", return_value=engine), \
     patch("app.pages.multiplier_migration.engine_control.close", return_value=True), \
     patch("app.pages.multiplier_migration.engine_control.start", return_value=False) as start:
    controller._execute(lambda: controller.latest)
    wait(lambda: not controller.busy)
    start.assert_called_once_with(engine)
assert page.messages[-1][0] == "warn" and "start it manually" in page.messages[-1][1]
# Failure after quarantine must restore paths before the engine is restarted.
for name in ("lake", "lake_copy1"):
    folder = reserve / name
    folder.mkdir()
    (folder / "project.json").write_text(json.dumps({"type":"video","file":"payload"}))
    (folder / "payload").write_bytes(b"lake")
plan = migration.plan_migration([reserve, current], current)
assert len(plan.groups) == 1 and plan.copy_count == 1
replace = migration._replace
before = store.path.read_bytes()
def fail_store(path, payload):
    if Path(path) == store.path:
        raise OSError("quota installation failed")
    return replace(path, payload)
def paths_are_restored(_engine):
    assert (reserve / "lake").is_dir() and (reserve / "lake_copy1").is_dir()
    return True
with patch("app.pages.multiplier_migration.engine_control.running", return_value=engine), \
     patch("app.pages.multiplier_migration.engine_control.close", return_value=True), \
     patch("app.pages.multiplier_migration.engine_control.start", side_effect=paths_are_restored) as start, \
     patch("app.engines.multiplier_migration._replace", side_effect=fail_store):
    controller._execute(lambda: migration.apply_migration(plan, store))
    wait(lambda: not controller.busy)
    # Restoration also uses the failed store writer, so this first recovery cannot finish.
    start.assert_not_called()
assert "remains closed" in page.messages[-1][1]
pending = migration.pending_migrations(store.path.parent / "multiplier-migration")
assert len(pending) == 1
try:
    store.set_multiplier("garden", 20)
except ValueError:
    pass
else:
    raise AssertionError("An interrupted migration allowed quota edits")
migration.rollback_migration(pending[0], store)
assert store.path.read_bytes() == before
# A transient install error can roll back successfully before restarting.
failed_once = []
def fail_once(path, payload):
    if Path(path) == store.path and not failed_once:
        failed_once.append(True)
        raise OSError("temporary quota installation failure")
    return replace(path, payload)
with patch("app.pages.multiplier_migration.engine_control.running", return_value=engine), \
     patch("app.pages.multiplier_migration.engine_control.close", return_value=True), \
     patch("app.pages.multiplier_migration.engine_control.start", side_effect=paths_are_restored) as start, \
     patch("app.engines.multiplier_migration._replace", side_effect=fail_once):
    controller._execute(lambda: migration.apply_migration(plan, store))
    wait(lambda: not controller.busy)
    start.assert_called_once_with(engine)
assert store.path.read_bytes() == before and not migration.pending_migrations(store.path.parent / "multiplier-migration")
print("PASSED migration UI: approved plan, quotas, save locks, recovery before restart, restart failure, close refusal")
