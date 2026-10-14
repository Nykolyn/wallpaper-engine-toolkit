"""Show or hide the widget owned by the one background tracker process."""
from __future__ import annotations

from . import quit_request, window_instance
from .settings import Settings


def set_visible(settings: Settings, on: bool) -> None:
    settings.reload_if_changed()
    previous = settings.get("widget", "enabled", False)
    settings.set("widget", "enabled", bool(on))
    settings.save()
    message = "widget:show" if on else "widget:hide"
    if window_instance.send(message, wait=0, name=quit_request.tracker_server_name()) or not on:
        return
    # The mutex can exist before the new tray's socket is ready.
    if window_instance.already_running(quit_request.TRAY_MUTEX):
        if window_instance.send(message, wait=2, name=quit_request.tracker_server_name()):
            return
        settings.set("widget", "enabled", previous)
        settings.save()
        raise OSError("The background tracker is not responding. Restart it and try again.")
    try:
        window_instance.launch(widget=True)
    except OSError:
        settings.set("widget", "enabled", previous)
        settings.save()
        raise
