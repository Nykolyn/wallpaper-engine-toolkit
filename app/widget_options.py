"""Validated widget profiles shared by the tray, desktop windows and settings page."""
from __future__ import annotations

import math
import re

DEFAULTS = {
    "mode": "compact", "size": 144, "opacity": 100, "layer": "desktop",
    "locked": False, "outer_color": "#4C8DFF", "inner_color": "#22C3B0",
    "monitor": "tray", "enabled": True,
}
SIZES = (112, 144, 176)
LAYERS = ("desktop", "normal", "top")


def _value(name: str, value):
    if name == "mode" and value in ("compact", "expanded"):
        return value
    if name == "size" and type(value) is int and value in SIZES:
        return value
    if name == "opacity" and type(value) is int and 40 <= value <= 100:
        return value
    if name == "layer" and value in LAYERS:
        return value
    if name in ("locked", "enabled") and type(value) is bool:
        return value
    if name in ("outer_color", "inner_color") and isinstance(value, str):
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return value.upper()
    if name == "monitor" and isinstance(value, str) and value.strip():
        return value
    if name == "position" and isinstance(value, dict) and isinstance(value.get("screen"), str):
        if all(type(value.get(axis)) in (int, float) and 0 <= value[axis] <= 1
               and math.isfinite(value[axis]) for axis in ("x", "y")):
            return {key: value[key] for key in ("screen", "x", "y")}
    raise ValueError(f"Invalid widget option {name}: {value!r}")


def _profiles(settings) -> dict:
    values = settings.get("widget", "profiles", {})
    return values if isinstance(values, dict) else {}


def profile(settings, key: str = "default") -> dict:
    """A fresh normalized copy; reading never rewrites old or malformed settings."""
    result = dict(DEFAULTS)
    raw = _profiles(settings).get(key, {})
    values = dict(raw) if isinstance(raw, dict) else {}
    if key == "default":
        for name in ("mode", "position"):
            values.setdefault(name, settings.get("widget", name, result.get(name)))
    for name, value in values.items():
        if name not in DEFAULTS and name != "position":
            continue
        try:
            result[name] = _value(name, value)
        except ValueError:
            pass
    if key != "default":
        result["monitor"] = key
    return result


def set_option(settings, key: str, name: str, value) -> None:
    value = _value(name, value)
    if key != "default" and name == "monitor" and value != key:
        raise ValueError("A monitor widget stays bound to its own monitor.")
    settings.reload_if_changed()
    profiles = dict(_profiles(settings))
    raw = profiles.get(key, {})
    profiles[key] = {**(raw if isinstance(raw, dict) else {}), name: value}
    settings.set("widget", "profiles", profiles)
    settings.save()


def set_layout(settings, value: str) -> None:
    if value not in ("single", "per_monitor"):
        raise ValueError(f"Invalid widget layout: {value!r}")
    settings.reload_if_changed()
    settings.set("widget", "layout", value)
    settings.save()


def chosen_profiles(settings, monitors) -> list[str]:
    keys = (list(dict.fromkeys(m for m in monitors if isinstance(m, str) and m))
            if settings.get("widget", "layout", "single") == "per_monitor" else ["default"])
    return [key for key in keys if profile(settings, key)["enabled"]]


def reset_profile(settings, key: str = "default") -> None:
    """Reset this appearance and position while preserving monitor binding and visibility."""
    settings.reload_if_changed()
    current = profile(settings, key)
    profiles = dict(_profiles(settings))
    profiles[key] = {name: current[name] for name in ("monitor", "enabled")}
    settings.set("widget", "profiles", profiles)
    if key == "default":
        for name in ("mode", "position"):
            settings.section("widget").pop(name, None)
    settings.save()
