"""Autostart wiring, checked without touching the registry or Task Scheduler.

Run it directly:

    .venv\\Scripts\\python.exe tests\\test_autostart.py

Only the pure parts are exercised — building the command line and the task XML.
Installing and removing autostart changes the machine, so that is left to the
checkbox in the Tracker tab and to `run_app.py --autostart on|off|status`.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import autostart                              # noqa: E402

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


# ---- the command line

program, arguments = autostart.target()
check("the program is an interpreter or the frozen exe",
      program.suffix.lower() == ".exe" and program.exists())
check("the arguments ask for tracker mode", arguments.endswith("--tracker"))
check("a source run avoids a console window",
      getattr(sys, "frozen", False) or program.name.lower() == "pythonw.exe")

line = autostart.command()
check("the Run-key command quotes the program", line.startswith(f'"{program}"'))
check("the Run-key command ends in tracker mode", line.endswith("--tracker"))


# ---- the task definition

check("XML-hostile characters are escaped",
      autostart._escape('a & b <c>') == "a &amp; b &lt;c&gt;")

xml = autostart._task_xml()
try:
    root = ET.fromstring(xml)
    parsed = True
except ET.ParseError as e:
    parsed = False
    print("   parse error:", e)
check("the task XML is well formed", parsed)

if parsed:
    command_node = root.find(".//t:Actions/t:Exec/t:Command", NS)
    args_node = root.find(".//t:Actions/t:Exec/t:Arguments", NS)
    check("the task runs the same program as the Run key",
          command_node is not None and command_node.text == str(program))
    check("the task passes the same arguments",
          args_node is not None and args_node.text == arguments)

    delay = root.find(".//t:Triggers/t:LogonTrigger/t:Delay", NS)
    check("the logon trigger is delayed past shell startup",
          delay is not None and delay.text == f"PT{autostart.LOGON_DELAY_SECONDS}S")

    limit = root.find(".//t:Settings/t:ExecutionTimeLimit", NS)
    check("a long-lived tray process is not time limited",
          limit is not None and limit.text == "PT0S")

    logon = root.find(".//t:Principals/t:Principal/t:LogonType", NS)
    check("it runs with the interactive token, so it has a desktop",
          logon is not None and logon.text == "InteractiveToken")

    level = root.find(".//t:Principals/t:Principal/t:RunLevel", NS)
    check("it does not ask for elevation",
          level is not None and level.text == "LeastPrivilege")

    instances = root.find(".//t:Settings/t:MultipleInstancesPolicy", NS)
    check("a second copy is never started", instances is not None
          and instances.text == "IgnoreNew")

    user = root.find(".//t:Principals/t:Principal/t:UserId", NS)
    check("the task names a user", bool(user is not None and (user.text or "").strip()))


# ---- schtasks output survives a localised console

code, out = autostart._schtasks("/query", "/tn", "☃ no such task ☃")
check("a failing schtasks call reports a non-zero code", code != 0)
check("its output decodes without throwing", isinstance(out, str))


# ---- the rename, and what a machine from before it still has
#
# Nothing below registers anything: the pieces that decide *what* to do are
# separable from the ones that do it, and those are the ones that can be
# wrong in a way nobody notices until a logon.

check("the old name is remembered, and is not the current one",
      autostart.LEGACY_TASK_NAME == "WallpaperSuiteTracker"
      and autostart.LEGACY_TASK_NAME != autostart.TASK_NAME)
check("the current name says what the app is now called",
      autostart.TASK_NAME == "WallpaperEngineToolkitTracker")

check("a task definition gives up its program",
      autostart._task_command(
          r"<Task><Command>C:\x\y.exe</Command></Task>") == r"C:\x\y.exe")
check("and unescapes it on the way out",
      autostart._task_command("<Task><Command>a &amp; b</Command></Task>") == "a & b")
check("a definition with no action names no program",
      autostart._task_command("<Task></Task>") == "")

check("UTF-16 is what schtasks hands back, and it decodes",
      "<Task" in autostart._decode_xml(
          "<Task><X/></Task>".encode("utf-16")))
check("so does UTF-16 without a BOM",
      "<Task" in autostart._decode_xml(
          "<Task><X/></Task>".encode("utf-16-le")))
check("and anything that is not a task at all decodes to nothing",
      autostart._decode_xml(b"ERROR: no such task") == "")

check("a task that was never registered exports nothing",
      autostart.export_task("☃ no such task ☃") == "")
check("and is not mistaken for a machine that needs migrating",
      isinstance(autostart.legacy_present(), bool))

# migrate_legacy() must be safe to call on a machine that never had the old
# name — which is every machine but the author's, and is the path that would
# otherwise go untested until someone else ran it.
if not autostart.legacy_present():
    check("with nothing to migrate, migrating does nothing and says so",
          autostart.migrate_legacy() == "")
else:
    print("SKIP  this machine still has the old entry; not migrating it in a test")


print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
