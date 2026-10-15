"""Tray appearance shared by the settings page and tracker process."""
import re

DEFAULTS = {"front_color": "#4C8DFF", "back_color": "#4C8DFF", "progress": "combined"}
MODES = ("combined", "front_wallpaper", "back_wallpaper")


def value(name, raw):
    if name in ("front_color", "back_color") and isinstance(raw, str):
        if re.fullmatch(r"#[0-9a-fA-F]{6}", raw):
            return raw.upper()
    if name == "progress" and raw in MODES:
        return raw
    raise ValueError(f"Invalid tray option {name}: {raw!r}")


def read(settings):
    options = dict(DEFAULTS)
    for name in options:
        try:
            options[name] = value(name, settings.get("tray", name, options[name]))
        except ValueError:
            pass
    return options


def set_option(settings, name, raw):
    normalized = value(name, raw)
    settings.reload_if_changed()
    settings.set("tray", name, normalized)
    settings.save()
