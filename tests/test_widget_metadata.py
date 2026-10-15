"""Wallpaper metadata is cached, cancellable and read away from the GUI thread."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
scratch = tempfile.TemporaryDirectory(prefix="widget_metadata_")
os.environ["WALLPAPER_TOOLKIT_DATA"] = scratch.name
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication
from app import widget_metadata as module

app = QApplication([])
folder = Path(scratch.name)
item = folder / "scene.pkg"
item.write_bytes(b"wallpaper" * 10)
(folder / "project.json").write_text(json.dumps({"title": "Night sky"}), encoding="utf-8")
assert module.read_current(str(item)) == {"title": "Night sky", "size": 90}
assert module.read_current(str(folder))["size"] is None, "directories are not wallpaper file sizes"
(folder / "project.json").unlink()
assert module.read_current(str(folder / "missing.pkg"), "Saved title") == {
    "title": "Saved title", "size": None}


def wait_for(condition):
    deadline = time.monotonic() + 3
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition(), "worker failed to deliver metadata"


cache = module.WidgetMetadata()
main_thread = threading.get_ident()
reads = []
entered, release = threading.Event(), threading.Event()


def read(path, title):
    assert threading.get_ident() != main_thread, "wallpaper disk read on GUI thread"
    reads.append(path)
    if path == "first":
        entered.set()
        release.wait(2)
    return {"title": title, "size": len(path)}


with patch.object(module, "read_current", side_effect=read):
    cache.request([SimpleNamespace(current="first", current_title="One")])
    assert entered.wait(2)
    cache.request([SimpleNamespace(current="stale", current_title="Stale")])
    cache.request([SimpleNamespace(current="last", current_title="Two")])
    release.set()
    wait_for(lambda: "last" in cache.data)
    assert reads == ["first", "last"], "pending work follows the latest wallpapers"
    assert cache.data["last"] == {"title": "Two", "size": 4}
    cache.request([SimpleNamespace(current="last", current_title="Two")])
    app.processEvents()
    assert reads == ["first", "last"], "clock ticks must not reread sleeping disks"
    cache.stop()
    cache.request([SimpleNamespace(current="after stop")])
    assert reads == ["first", "last"]

cache = module.WidgetMetadata()
entered.clear()
release.clear()
with patch.object(module, "read_current", side_effect=read):
    cache.request([SimpleNamespace(current="first", current_title="One")])
    assert entered.wait(2)
    cache.stop()
    release.set()
    wait_for(lambda: not any(t.name == "widget metadata" for t in threading.enumerate()))
    app.processEvents()
    assert cache.data == {}, "shutdown discards late callbacks"

print("PASS current file sizes, missing files, worker reads, latest request, cache and shutdown")
