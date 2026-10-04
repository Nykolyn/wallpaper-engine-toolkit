"""A wallpaper folder's row, on the Tracker's and the Rotator's tables: Delete
(the Recycle Bin, a Workshop item unsubscribed first), Send to Copier and a
click that opens the folder; the tables' square previews, the ones that play,
and `#`, which undoes a sort; and the window opening on its LoadingCover with
no other window flashing up first.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_folder_actions.py

Nothing here unsubscribes from anything or empties anything into a real
Recycle Bin: Steam and the bin are stood in for, and every folder is made in a
temporary one.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_folder_actions_test_"))
# Before any app module: the data folder resolves when they are imported.
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(errors="replace")

from PySide6.QtCore import QEvent, QObject, Qt, Signal                    # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget          # noqa: E402

app = QApplication(sys.argv)

from app import animations, services, theme                               # noqa: E402
from app.engines import wallpaper_delete as wd                            # noqa: E402
from app.engines.library_index import FolderInfo                          # noqa: E402
from app.engines.rotator.config import Config                             # noqa: E402
from app.engines.wallpaper_delete import DeleteError, Deleted             # noqa: E402
from app.pages import folder_actions                                      # noqa: E402
from app.pages.folder_actions import (                                    # noqa: E402
    DELETE, SEND, delete_dialog, deleted_words,
)
from app.pages.rotator import LIBRARY_ACTIONS, RotatorPage                # noqa: E402
from app.pages.tracker import ACTIONS, PlaylistRow, TrackerPage           # noqa: E402
from app.services import snapshot as snapshot_module                      # noqa: E402
from app.settings import DEFAULT_COPIER_COUNT, Settings                   # noqa: E402
from app.ui.kit import Column, Table, TableModel, ThumbLoader             # noqa: E402
from app.ui.kit.tables import thumb_tile                                  # noqa: E402

theme.apply(app)
animations.ENABLED = True
snapshot_module.compute = lambda keys, data_dir: {}

NOW = datetime(2026, 10, 4, 16, 25)
results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def wait_for(condition, ms: int = 5000) -> bool:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return bool(condition())


def raises(fn, kind) -> bool:
    try:
        fn()
    except kind:
        return True
    return False


class Host(QWidget):
    def __init__(self, w=1600, h=900):
        super().__init__()
        self.resize(w, h)
        self.column = QVBoxLayout(self)
        self.move(300, 200)             # off the offscreen pointer at (0, 0)


def folder(*parts: str) -> Path:
    path = TMP.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    (path / "project.json").write_text("{}", encoding="utf-8")
    return path


# ---- the engine: what Delete does -----------------------------------------------------------

print("-- Delete: the Recycle Bin, a Workshop item unsubscribed first --")
WORKSHOP = TMP / "steamapps" / "workshop" / "content" / "431960"
MYPROJECTS = TMP / "projects" / "myprojects"

check("a folder under Steam's 431960 is a Workshop item, by its path alone",
      wd.workshop_id(WORKSHOP / "1700000005") == "1700000005"
      and wd.workshop_id(str(WORKSHOP / "1700000005") + os.sep) == "1700000005"
      and wd.workshop_id("W:\\Steam\\steamapps\\workshop\\content\\431960\\3001") == "3001")
check("a copy in myprojects is not, whatever it is called",
      wd.workshop_id(MYPROJECTS / "1700000005") == ""
      and wd.workshop_id(WORKSHOP / "not-an-id") == "" and wd.workshop_id("") == "")


class Bin:
    """The Recycle Bin, standing in: takes the folder away, or refuses."""

    def __init__(self, works: bool = True):
        self.works = works
        self.taken: list[Path] = []

    def __call__(self, path: Path) -> bool:
        if not self.works:
            return False
        self.taken.append(Path(path))
        for inner in sorted(Path(path).rglob("*"), reverse=True):
            inner.unlink() if inner.is_file() else inner.rmdir()
        Path(path).rmdir()
        return True


steam_asked: list[str] = []


def steam(answer=True, fails: Exception | None = None):
    def unsubscribe(item: str) -> bool:
        steam_asked.append(item)
        if fails is not None:
            raise fails
        return answer
    return unsubscribe


plain = folder("projects", "myprojects", "harbour")
bin_ = Bin()
done = wd.delete_wallpaper(plain, unsubscribe=steam(), recycle=bin_)
check("a myprojects folder goes to the Recycle Bin, and Steam is not asked",
      done == Deleted(str(plain)) and bin_.taken == [plain] and not plain.exists()
      and steam_asked == [])

item = folder("steamapps", "workshop", "content", "431960", "1700000005")
bin_ = Bin()
done = wd.delete_wallpaper(item, unsubscribe=steam(), recycle=bin_)
check("a Workshop item is unsubscribed, then goes to the Recycle Bin",
      done.workshop_id == "1700000005" and steam_asked == ["1700000005"]
      and bin_.taken == [item] and not item.exists())

steam_asked.clear()
item = folder("steamapps", "workshop", "content", "431960", "1700000006")
bin_ = Bin()
check("Steam not there: nothing is deleted, and it says why",
      raises(lambda: wd.delete_wallpaper(item, unsubscribe=steam(fails=RuntimeError(
          "Steam is not running")), recycle=bin_), DeleteError)
      and item.is_dir() and bin_.taken == [])
check("Steam still subscribed after asking: nothing is deleted either",
      raises(lambda: wd.delete_wallpaper(item, unsubscribe=steam(answer=False), recycle=bin_),
             DeleteError) and item.is_dir() and bin_.taken == [])
try:
    wd.delete_wallpaper(item, unsubscribe=steam(fails=RuntimeError("Steam is not running")),
                        recycle=bin_)
except DeleteError as err:
    said = str(err)
check("the words say Steam would download it again", "download it again" in said
      and "Steam is not running" in said)
busy = folder("projects", "myprojects", "busy")
check("a folder Windows will not move: an error, and it is still there",
      raises(lambda: wd.delete_wallpaper(busy, unsubscribe=steam(), recycle=Bin(works=False)),
             DeleteError) and busy.is_dir())
check("a folder that is gone already: an error, nothing asked",
      raises(lambda: wd.delete_wallpaper(TMP / "nowhere", unsubscribe=steam(), recycle=Bin()),
             DeleteError))

print("-- the question and the answer --")
question = delete_dialog("Harbour Lights", str(MYPROJECTS / "harbour"), None)
check("a plain folder: Delete, with Cancel the safe default",
      question._confirm.text() == "Delete" and question._cancel.isDefault())
workshop_question = delete_dialog("Moth", str(WORKSHOP / "1700000005"), None)
check("a Workshop item: unsubscribe and delete, by its id",
      workshop_question._confirm.text() == "Unsubscribe and delete")
check("the toasts: in the bin; unsubscribed and in the bin; and why not",
      deleted_words("Harbour", Deleted("x")) == ("ok", "“Harbour” is in the Recycle Bin.")
      and deleted_words("Moth", Deleted("x", "17")) == (
          "ok", "Unsubscribed from “Moth” and moved its folder to the Recycle Bin.")
      and deleted_words("Pine", DeleteError("it is not there any more.")) == (
          "danger", "Could not delete “Pine”: it is not there any more."))


# ---- the Tracker's rows ------------------------------------------------------------------------

print("-- the Tracker: Delete asks, runs on a worker, and the row goes --")


class Jobs:
    def __init__(self):
        self.rotating = False

    def is_running(self, tool):
        return tool == "rotator" and self.rotating


class Services:
    def __init__(self):
        self.jobs = Jobs()
        self.snapshot = None


tracker_services = Services()
tracker = TrackerPage(None, None, now=lambda: NOW, make_timer=lambda f: None,
                      load=lambda m: None, measure=lambda f: 0)
tracker._services = tracker_services
rows = [PlaylistRow(f"{MYPROJECTS}/w{n}/scene.pkg", "queue", n,
                    folder=str(MYPROJECTS / f"w{n}"), title=f"Wallpaper {n}") for n in range(1, 5)]
tracker._show_rows("Monitor1", rows, False, NOW)
asked: list = []
worked_on: list[bool] = []
answer = {"yes": True}


def tracker_answer(dialog):
    asked.append(dialog)
    return answer["yes"]


def tracker_delete(path):
    worked_on.append(threading.current_thread() is threading.main_thread())
    return Deleted(path)


tracker._answer = tracker_answer
tracker._delete_folder = tracker_delete
check("each row offers Delete last", tracker.model.cell(rows[0], ACTIONS).buttons[-1].key == DELETE)
answer["yes"] = False
check("not wanted: nothing is deleted", not tracker.delete(rows[0]) and worked_on == [])
answer["yes"] = True
check("wanted: it runs", tracker.delete(rows[1]))
check("and while it runs, the row's Delete is off",
      not tracker.model.cell(rows[1], ACTIONS).buttons[-1].enabled)
check("on a worker, and the row leaves the table",
      wait_for(lambda: tracker.model.item_rows() == 3) and worked_on == [False]
      and rows[1] not in tracker.model.items())
check("a toast says where it went", tracker.messages[-1] == ("ok", "“Wallpaper 2” is in the "
                                                                  "Recycle Bin."))
tracker._show_rows("Monitor1", list(rows), False, NOW)
check("read again before the tracker has noticed, the row stays gone",
      tracker.model.item_rows() == 3)
tracker_services.jobs.rotating = True
asked.clear()
check("never while a rotation runs, and without asking",
      not tracker.delete(rows[2]) and asked == [] and "rotation is running" in tracker.messages[-1][1])
tracker_services.jobs.rotating = False
tracker._delete_folder = lambda path: (_ for _ in ()).throw(DeleteError("it is in use."))
tracker.delete(rows[2])
check("one that fails stays, and says why",
      wait_for(lambda: tracker.messages[-1][0] == "danger")
      and rows[2] in tracker.model.items()
      and tracker.messages[-1][1] == "Could not delete “Wallpaper 3”: it is in use."
      and tracker.model.cell(rows[2], ACTIONS).buttons[-1].enabled)


# ---- the Rotator's rows ------------------------------------------------------------------------

print("-- the Rotator: a click opens the folder; Send to Copier; Delete --")
reserve = TMP / "reserve"
for name in ("alpha", "beta", "gamma"):
    folder("reserve", name)
config = Config(source=str(reserve), destination=str(MYPROJECTS), duplicates="")
rotator = RotatorPage(config, None)
host = Host()
host.column.addWidget(rotator)
host.show()
model = rotator.models["reserve"]
model.root = str(reserve)
model.infos = {"beta": FolderInfo("beta", title="Beta Wave", kind="scene")}
model.set_names(["alpha", "beta", "gamma"])
app.processEvents()
check("each row ends with Send to Copier and Delete",
      [b.key for b in model.cell("alpha", LIBRARY_ACTIONS).buttons] == [SEND, DELETE]
      and model.columns[LIBRARY_ACTIONS].title == "" and not model.columns[LIBRARY_ACTIONS].sortable)

opened: list = []
real_popen = folder_actions.external.popen
folder_actions.external.popen = lambda command, **kw: opened.append(command)
try:
    index = model.index(model.row_of_item(model.items().index("beta")), 0)
    rotator._folder_clicked("reserve", index)
    rotator._folder_clicked("reserve", index)       # the double-click's second half
    check("a click opens the folder in Explorer, once, off the window's thread",
          wait_for(lambda: len(opened) == 1)
          and opened[0] == ["explorer", os.path.normpath(str(reserve / "beta"))])
    rotator._opened_last = ("", 0.0)
    gone_index = model.index(model.row_of_item(model.items().index("gamma")), 0)
    (reserve / "gamma" / "project.json").unlink()
    (reserve / "gamma").rmdir()
    rotator._folder_clicked("reserve", gone_index)
    check("one that is gone says so instead",
          wait_for(lambda: rotator.messages and "not there any more" in rotator.messages[-1][1])
          and len(opened) == 1)
finally:
    folder_actions.external.popen = real_popen

sent: list = []


def copier(folders):
    sent.append(folders)
    rotator.copier_took(folders, 1 if len(sent) == 1 else 0)


rotator.copier_requested.connect(copier)
check("Send to Copier hands the folder over, and the toast names it by its title",
      rotator.send_to_copier("reserve", "beta") and sent == [[str(reserve / "beta")]]
      and rotator.messages[-1] == ("ok", f"“Beta Wave” is on the Copier's list, for "
                                         f"{DEFAULT_COPIER_COUNT} copies."))
rotator.send_to_copier("reserve", "beta")
check("sent again: on the list already", rotator.messages[-1] == (
    "info", "“Beta Wave” is on the Copier's list already."))
rotator.copier_requested.disconnect(copier)
check("with no Copier wired, it says so", not rotator.send_to_copier("reserve", "alpha")
      and rotator.messages[-1] == ("warn", "The Copier is not there to take it."))

rotator._answer = lambda dialog: True
rotator._delete_folder = lambda path: Deleted(path)
check("Delete runs on a worker and the row goes",
      rotator.delete_folder("reserve", "alpha")
      and wait_for(lambda: "alpha" not in model.items())
      and rotator.messages[-1] == ("ok", "“alpha” is in the Recycle Bin."))
rotator.state = "running"
check("never while its run is under way", not rotator.delete_folder("reserve", "beta")
      and "rotation is running" in rotator.messages[-1][1] and "beta" in model.items())
rotator.state = "idle"


# ---- the tables: square previews, the ones that play, and # --------------------------------------

print("-- the tables: # undoes a sort --")


class Numbers(TableModel):
    pass


numbers = Numbers([Column("#", 40, natural=True), Column("Name", None)],
                  [(3, "c"), (1, "a"), (2, "b")])
numbers_table = Table()
numbers_table.setModel(numbers)


def order() -> list:
    return [numbers.item_at(r)[1] for r in range(numbers.rowCount())]


numbers_table._header_clicked(1)
check("a column sorts", order() == ["a", "b", "c"])
numbers_table._header_clicked(1)
check("and the other way", order() == ["c", "b", "a"])
numbers_table._header_clicked(0)
check("# goes back to the order given, not a sort of its own",
      order() == ["c", "a", "b"] and numbers.sort_column() == -1)

print("-- square previews, cropped to fill --")
check("a table's thumb is 200 × 200, square", theme.THUMB["row"][:2] == (200, 200)
      and "row" in theme.THUMB_COVER)
from PySide6.QtGui import QColor, QPixmap                                 # noqa: E402

wide = QPixmap(320, 180)
wide.fill(QColor("#ff0000"))
tile = thumb_tile(wide, "image", "row", 1.0).toImage()
corner = tile.pixelColor(30, 30)
check("a 16:9 picture fills the square's corners rather than leaving them empty",
      corner.red() > 200 and corner.green() < 60)
check("narrow tables step down through smaller squares",
      theme.THUMB_NARROWER["row"] == "rowsm" and theme.THUMB["rowsm"][0] == theme.THUMB["rowsm"][1]
      and theme.THUMB["rowxs"][0] == theme.THUMB["rowxs"][1])

print("-- an animated preview plays, on screen only --")
from PIL import Image                                                     # noqa: E402

gif_folder = TMP / "animated"
gif_folder.mkdir()
frames = [Image.new("RGB", (64, 64), colour) for colour in ("red", "green", "blue")]
frames[0].save(gif_folder / "preview.gif", save_all=True, append_images=frames[1:], duration=40,
               loop=0)
still_folder = TMP / "still"
still_folder.mkdir()
Image.new("RGB", (64, 64), "white").save(still_folder / "preview.jpg")


class Previews(TableModel):
    def thumb_source(self, item):
        return item[1]


previews = Previews([Column("Wallpaper", None, thumb="row"), Column("Type", 60)],
                    [("Animated", str(gif_folder)), ("Still", str(still_folder))])
loader = ThumbLoader(local_root=TMP / "thumbs")
preview_table = Table(loader=loader)
preview_table.setModel(previews)
preview_host = Host()
preview_host.column.addWidget(preview_table)
preview_host.show()
app.processEvents()
preview_table.request_visible_thumbs()
check("the GIF plays: a player, and frames drawn over its row",
      wait_for(lambda: preview_table.players() == [str(gif_folder)])
      and wait_for(lambda: preview_table.frame_tile(str(gif_folder), "row") is not None))
check("the JPEG does not", str(still_folder) not in preview_table.players()
      and preview_table.frame_tile(str(still_folder), "row") is None)
first = preview_table.frame_tile(str(gif_folder), "row").toImage().pixelColor(100, 100)
check("and the frame moves on", wait_for(
    lambda: preview_table.frame_tile(str(gif_folder), "row").toImage().pixelColor(100, 100)
    != first, 3000))
preview_host.hide()
app.processEvents()
check("off screen, nothing plays", preview_table.players() == [])
animations.ENABLED = False
preview_host.show()
app.processEvents()
preview_table.request_visible_thumbs()
app.processEvents()
check("with motion off, nothing plays either", preview_table.players() == [])
animations.ENABLED = True
preview_host.hide()


# ---- opening the window ----------------------------------------------------------------------------

print("-- the window opens on its LoadingCover, with nothing flashing up first --")
from app.main_window import LoadingCover, MainWindow                      # noqa: E402


class Feed(QObject):
    updated = Signal()
    config_changed = Signal()

    def __init__(self):
        super().__init__()
        self.results, self.error, self.config_path, self.atime_ok = [], None, "", True
        self.files = None

    def refresh(self):
        pass

    def use_config(self, path):
        pass

    def set_heartbeat(self, seconds):
        pass


class Windows(QObject):
    """Every top-level widget that comes on screen."""

    def __init__(self):
        super().__init__()
        self.shown: list[str] = []

    def eventFilter(self, obj, event):          # noqa: N802 - Qt's name
        if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
            self.shown.append(type(obj).__name__)
        return False


windows = Windows()
app.installEventFilter(windows)
settings = Settings({})
svc = services.Services(data_dir=TMP / "data", settings=settings)
window = MainWindow(settings=settings, feed=Feed(), services_=svc, start=False,
                    initial="tracker", defer=True)
check("made at once, with no pages yet", not window.ready() and window.pages == {}
      and isinstance(window.loading, LoadingCover))
window.handle_command("show", "rotator")
check("a command asked meanwhile waits for them", window.current_page() == "")
window.move(300, 200)
window.show()
check("once on screen, the pages are made", wait_for(window.ready)
      and len(window.pages) == 7 and window.loading is None)
check("and the command is carried out", window.current_page() == "rotator")
check("no window but the main one came on screen while it was all made",
      windows.shown == ["MainWindow"])
app.removeEventFilter(windows)
window.close()

print()
passed = sum(results)
print(f"{'OK' if passed == len(results) else 'FAILED'} {passed}/{len(results)}")
sys.exit(0 if passed == len(results) else 1)
