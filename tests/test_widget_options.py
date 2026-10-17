"""Widget profiles validate persisted data and keep each monitor's choices separate."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
scratch = tempfile.TemporaryDirectory(prefix="toolkit_widget_options_")
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(Path(scratch.name) / "data")

from app.settings import Settings  # noqa: E402
from app import widget_options as options  # noqa: E402

results = []


def check(title, value):
    results.append(bool(value))
    print(("PASS " if value else "FAIL ") + title)


store = Settings({"widget": {"enabled": True, "mode": "expanded", "position":
                            {"screen": "left", "x": 0.25, "y": 0.75}}})
store.reload_if_changed = Mock(return_value=False)
store.save = Mock()
check("legacy mode and normalized position are retained by the shared profile",
      options.profile(store)["mode"] == "expanded" and options.profile(store)["position"]["x"] == 0.25)
check("each monitor starts with independent compact defaults",
      options.profile(store, "Monitor2")["mode"] == "compact"
      and options.profile(store, "Monitor2")["monitor"] == "Monitor2")
check("profile reads never save settings", not store.save.called)
check("the compatible layout defaults to one widget", options.chosen_profiles(store, ["Monitor1", "Monitor2"]) == ["default"])
options.set_option(store, "Monitor1", "mode", "expanded")
options.set_option(store, "Monitor1", "outer_color", "#aBcDeF")
check("a profile edit is validated and kept separate from other monitors",
      options.profile(store, "Monitor1")["mode"] == "expanded"
      and options.profile(store, "Monitor1")["outer_color"] == "#ABCDEF"
      and options.profile(store, "Monitor2")["mode"] == "compact")
check("editing reloads before saving", store.reload_if_changed.call_count == 2 and store.save.call_count == 2)
check("new and legacy profiles default to the rectangle design",
      options.profile(store).get("design") == "rectangles"
      and options.profile(store, "Monitor2").get("design") == "rectangles")
options.set_option(store, "Monitor1", "design", "rings")
check("the selected design is saved independently for each monitor",
      options.profile(store, "Monitor1").get("design") == "rings"
      and store.get("widget", "profiles", {}).get("Monitor1", {}).get("design") == "rings"
      and options.profile(store, "Monitor2").get("design") == "rectangles")
options.set_layout(store, "per_monitor")
check("per-monitor layout follows discovered monitors in order without duplicates",
      options.chosen_profiles(store, ["Monitor2", "Monitor1", "Monitor2"]) == ["Monitor2", "Monitor1"])
options.set_option(store, "Monitor1", "enabled", False)
check("hiding one monitor preserves the others and global visibility",
      options.chosen_profiles(store, ["Monitor1", "Monitor2"]) == ["Monitor2"]
      and store.get("widget", "enabled", False))
check("a monitor profile cannot be rebound",
      options.profile(store, "Monitor1")["monitor"] == "Monitor1")
try:
    options.set_option(store, "Monitor1", "monitor", "Monitor2")
    rejected = False
except ValueError:
    rejected = True
check("a mismatched per-monitor binding is rejected", rejected)

bad = [("size", True), ("size", 143), ("opacity", 39), ("opacity", 101),
       ("opacity", "80"), ("locked", 1), ("enabled", "false"), ("mode", "wide"),
       ("layer", "background"), ("design", "wide"), ("design", None), ("design", ["rings"]),
       ("outer_color", "red"), ("inner_color", "#fff"),
       ("monitor", ""), ("position", {"screen": "x", "x": float("nan"), "y": 0}),
       ("position", {"screen": "x", "x": 10**1000, "y": 0}), ("unknown", True)]
before = store.save.call_count
rejected = 0
for name, value in bad:
    try:
        options.set_option(store, "Monitor2", name, value)
    except ValueError:
        rejected += 1
check("invalid persisted option values are rejected before any write",
      rejected == len(bad) and store.save.call_count == before)
for raw in (None, [], "broken", {"Monitor1": "broken"}):
    damaged = Settings({"widget": {"profiles": raw}})
    check(f"malformed profile storage falls back safely ({type(raw).__name__})",
          options.profile(damaged, "Monitor1")["size"] == 144)
damaged = Settings({"widget": {"profiles": {"default": {"size": -1, "opacity": None,
                                                       "layer": [], "outer_color": False,
                                                       "locked": "false"}}}})
check("individual malformed options use safe defaults", options.profile(damaged) == options.DEFAULTS)
for value in ("wide", None, ["rings"], {"design": "rings"}):
    damaged = Settings({"widget": {"profiles": {"default": {"design": value}}}})
    damaged.save = Mock()
    check(f"an invalid saved design falls back without rewriting it ({type(value).__name__})",
          options.profile(damaged).get("design") == "rectangles"
          and damaged.get("widget", "profiles", {})["default"]["design"] == value
          and not damaged.save.called)

options.set_option(store, "default", "monitor", "Monitor2")
options.set_option(store, "default", "enabled", False)
store.set("widget", "start_with_windows", True)
options.set_option(store, "default", "design", "rings")
options.reset_profile(store)
reset = options.profile(store)
check("reset removes legacy appearance and position but keeps the monitor and visibility",
      reset["mode"] == "compact" and reset.get("design") == "rectangles" and "position" not in reset
      and reset["monitor"] == "Monitor2" and not reset["enabled"])
check("reset preserves other profiles, global visibility, layout and startup",
      options.profile(store, "Monitor1")["outer_color"] == "#ABCDEF"
      and options.profile(store, "Monitor1").get("design") == "rings"
      and store.get("widget", "enabled", False) and store.get("widget", "layout", "") == "per_monitor"
      and store.get("widget", "start_with_windows", False))
snapshot = options.profile(store)
snapshot["mode"] = "expanded"
check("editing a returned profile does not mutate persisted settings", options.profile(store)["mode"] == "compact")

print("\nPASSED %d/%d" % (sum(results), len(results)))
scratch.cleanup()
sys.exit(0 if all(results) else 1)
