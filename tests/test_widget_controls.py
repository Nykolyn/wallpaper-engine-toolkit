"""Widget controls without launching processes or changing Windows startup."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
scratch = tempfile.TemporaryDirectory(prefix="toolkit_widget_controls_")
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(Path(scratch.name) / "data")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from app import autostart, widget_control, widget_startup, window_instance  # noqa: E402
from app.pages.widget import WidgetPage  # noqa: E402
from app.settings import Settings  # noqa: E402

app = QApplication.instance() or QApplication([])
results: list[bool] = []


def check(title: str, okay: bool) -> None:
    results.append(bool(okay))
    print(("PASS " if okay else "FAIL ") + title)


with patch.object(autostart, "target", return_value=(Path(r"C:\Python\pythonw.exe"),
                                                      r'"D:\Toolkit source\run_app.py" --tracker')):
    check("source startup keeps pythonw and the quoted script",
          widget_startup.command() == '"C:\\Python\\pythonw.exe" "D:\\Toolkit source\\run_app.py" --widget-startup')
with patch.object(autostart, "target", return_value=(Path(r"C:\Toolkit\WallpaperEngineToolkit.exe"),
                                                      "--tracker")):
    check("frozen startup launches the widget startup mode",
          widget_startup.command() == '"C:\\Toolkit\\WallpaperEngineToolkit.exe" --widget-startup')

values = {autostart.VALUE_NAME: "existing tracker command"}


class Key:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def query(key, name):
    if name not in values:
        raise FileNotFoundError(name)
    return values[name], 1


def delete(key, name):
    if name not in values:
        raise FileNotFoundError(name)
    del values[name]


registry = SimpleNamespace(HKEY_CURRENT_USER=1, KEY_SET_VALUE=2, REG_SZ=1,
                           OpenKey=lambda *a: Key(), CreateKeyEx=lambda *a: Key(),
                           QueryValueEx=query, DeleteValue=delete,
                           SetValueEx=lambda key, name, _, kind, value: values.update({name: value}))
with patch.dict(sys.modules, {"winreg": registry}):
    check("widget startup defaults to off", not widget_startup.enabled())
    widget_startup.set_enabled(True)
    check("enabling widget startup registers its own entry",
          widget_startup.enabled() and values[widget_startup.VALUE_NAME].endswith("--widget-startup"))
    check("tracker startup is independent", values[autostart.VALUE_NAME] == "existing tracker command")
    widget_startup.set_enabled(False)
    widget_startup.set_enabled(False)
    check("turning widget startup off removes only its entry",
          not widget_startup.enabled() and values == {autostart.VALUE_NAME: "existing tracker command"})
    with patch.object(registry, "CreateKeyEx", side_effect=PermissionError("denied")):
        try:
            widget_startup.set_enabled(True)
            raised = False
        except PermissionError:
            raised = True
        check("Windows startup errors reach the caller", raised)

gone = []
installed = r"C:\Toolkit\WallpaperEngineToolkit.exe"
other = r"D:\Other\WallpaperEngineToolkit.exe"
said = widget_startup.release(installed, run_value=lambda n: f'"{installed.upper()}" --widget-startup',
                              clear=gone.append)
check("uninstall removes a startup entry owned by this copy",
      gone == [widget_startup.VALUE_NAME] and "removed" in said)
gone.clear()
said = widget_startup.release(installed, run_value=lambda n: f'"{other}" --widget-startup', clear=gone.append)
check("uninstall preserves startup pointing at another copy", not gone and "left alone" in said)
with patch.object(autostart, "target", return_value=(Path(r"C:\Python\pythonw.exe"),
                                                      r'"D:\Current\run_app.py" --tracker')):
    source_line = widget_startup.command()
    widget_startup.release(run_value=lambda n: source_line, clear=gone.append)
    check("source uninstall removes this checkout's startup", gone == [widget_startup.VALUE_NAME])
    gone.clear()
    said = widget_startup.release(run_value=lambda n: source_line.replace("Current", "Another"), clear=gone.append)
    check("source uninstall preserves another checkout using the same Python",
          not gone and "left alone" in said)


def settings():
    store = Settings({"tracker": {"primary": "Monitor2"}})
    store.reload_if_changed = Mock(return_value=False)
    store.save = Mock()
    return store


store = settings()
with patch.object(window_instance, "send", return_value=True) as send, \
        patch.object(window_instance, "launch") as launch:
    widget_control.set_visible(store, True)
    check("show reaches the running tracker without starting another process",
          not launch.called and send.call_args.args == ("widget:show",)
          and ".tracker." in send.call_args.kwargs["name"])
    check("show reloads and saves settings preserving the tray monitor",
          store.reload_if_changed.called and store.save.called
          and store.get("widget", "enabled", False) and store.get("tracker", "primary", "") == "Monitor2")

with patch.object(window_instance, "send", return_value=False) as send, \
        patch.object(window_instance, "launch") as launch:
    widget_control.set_visible(store, False)
    check("hide never launches or kills the tracker",
          not launch.called and send.call_args.args == ("widget:hide",)
          and not store.get("widget", "enabled", True))

with patch.object(window_instance, "send", return_value=False), \
        patch.object(window_instance, "already_running", return_value=False), \
        patch.object(window_instance, "launch") as launch:
    widget_control.set_visible(store, True)
    check("show starts the background widget when no tracker exists",
          launch.call_args.kwargs == {"widget": True})

store.set("widget", "enabled", False)
with patch.object(window_instance, "send", side_effect=[False, True]) as send, \
        patch.object(window_instance, "already_running", return_value=True), \
        patch.object(window_instance, "launch") as launch:
    widget_control.set_visible(store, True)
    check("a starting tracker gets time to open its socket without a duplicate process",
          send.call_count == 2 and not launch.called)

store.set("widget", "enabled", False)
with patch.object(window_instance, "send", return_value=False), \
        patch.object(window_instance, "already_running", return_value=False), \
        patch.object(window_instance, "launch", side_effect=OSError("cannot launch")):
    try:
        widget_control.set_visible(store, True)
        raised = False
    except OSError:
        raised = True
    check("a launch failure restores visibility and reaches the caller",
          raised and not store.get("widget", "enabled", True))

with patch.object(window_instance, "send", return_value=False), \
        patch.object(window_instance, "already_running", return_value=True), \
        patch.object(window_instance, "launch") as launch:
    try:
        widget_control.set_visible(store, True)
        raised = False
    except OSError:
        raised = True
    check("an unresponsive tracker reports failure without starting another",
          raised and not launch.called and not store.get("widget", "enabled", True))

page = WidgetPage(store)
page._ask = lambda what, work: page._answered(what, work())
with patch.object(widget_startup, "enabled", return_value=False), \
        patch.object(widget_control, "set_visible") as show:
    page.startup()
    page.on_shown()
    check("the page defaults to compact and hidden", page.mode.currentData() == "compact" and not page.visible.isChecked())
    check("opening the page does not open a disabled widget", not show.called)
    page.mode.setCurrentIndex(1)
    check("the page saves the expanded view", store.get("widget", "mode", "") == "expanded")
    store.set("widget", "enabled", True)
    page._sync()
    check("the page follows a visibility change from another process", page.visible.isChecked())
    page.startup()
    check("window startup restores a shown widget", show.call_args.args == (store, True))
    store.set("widget", "enabled", False)
    page._sync()
    check("an external widget close updates the page", not page.visible.isChecked())

page._answered("visible", OSError("failure reason"))
page._sync()
check("visibility errors stay visible while settings are polled",
      "failure reason" in page.visible_note.text() and page.visible_note.property("tone") == "danger")
page._answered("startup", True)
page.startup_toggle.blockSignals(True)
page.startup_toggle.setChecked(False)
page.startup_toggle.blockSignals(False)
page._answered("startup", PermissionError("registry denied"))
check("a startup failure restores the last known toggle and displays its reason",
      page.startup_toggle.isChecked() and "registry denied" in page.startup_note.text())
page.on_hidden()
check("the settings poll stops when the page is hidden", not page._poll.isActive())

fixture = WidgetPage(store, live=False)
with patch.object(widget_control, "set_visible", side_effect=AssertionError("live widget")), \
        patch.object(widget_startup, "enabled", side_effect=AssertionError("live registry")), \
        patch.object(widget_startup, "set_enabled", side_effect=AssertionError("live registry")):
    saved = store.save.call_count
    fixture.load_fixture("expanded")
    fixture.on_shown()
    fixture.startup()
    check("snapshot fixtures use expanded controls without machine changes",
          fixture.visible.isChecked() and fixture.mode.currentData() == "expanded"
          and store.save.call_count == saved and not fixture._poll.isActive())
    check("refreshed toggles paint the checked state",
          fixture.visible.knob_position == 1 and fixture.startup_toggle.knob_position == 1)
    fixture.load_fixture("idle")
    check("the idle fixture shows the compact default",
          not fixture.visible.isChecked() and fixture.mode.currentData() == "compact")
    fixture.show()
    fixture.load_fixture("expanded")
    QTest.qWait(180)
    check("visible refreshed toggles finish their checked animation",
          fixture.visible.knob_position == 1 and fixture.startup_toggle.knob_position == 1
          and store.save.call_count == saved)

page.close()
fixture.close()
print("\nPASSED %d/%d" % (sum(results), len(results)))
scratch.cleanup()
sys.exit(0 if all(results) else 1)
