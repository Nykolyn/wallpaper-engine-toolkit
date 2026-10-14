"""Show or hide the widget owned by the one background tracker process."""
from __future__ import annotations

import time

from . import quit_request, window_instance
from .settings import Settings
from .tracker_tray import log


def set_visible(settings: Settings, on: bool, *, stopped=lambda: False) -> None:
    if stopped():
        return
    log(f"widget visibility requested: {on}")
    settings.reload_if_changed()
    previous = settings.get("widget", "enabled", False)
    settings.set("widget", "enabled", bool(on))
    settings.save()
    message = "widget:show" if on else "widget:hide"
    if window_instance.send(message, wait=0, name=quit_request.tracker_server_name()) or not on:
        log("widget visibility request delivered or already hidden")
        return
    if stopped():
        return
    # The mutex can exist before the new tray's socket is ready.
    if window_instance.already_running(quit_request.TRAY_MUTEX):
        if window_instance.send(message, wait=window_instance.REACH_SECONDS,
                                name=quit_request.tracker_server_name()):
            return
        log(f"existing tracker did not answer the widget request after {window_instance.REACH_SECONDS:g} s")
        settings.set("widget", "enabled", previous)
        settings.save()
        raise OSError("The background tracker is not responding. Restart it and try again.")
    try:
        if stopped():
            return
        child = window_instance.launch(widget=True)
        log(f"widget process started: pid {child.pid}")
        # Keep the parent alive until this child can hear the installer's quit request.
        while child.poll() is None:
            closing = stopped()
            request = quit_request.QUIT if closing else message
            if window_instance.send(request, wait=0.5, name=quit_request.tracker_server_name()):
                log(f"widget process reached: {request}")
                if closing:
                    while child.poll() is None:
                        time.sleep(0.05)
                    return
                if stopped():
                    continue
                return
            time.sleep(0.05)
        if not stopped():
            # A duplicate launch exits successfully after forwarding to the winning tray.
            if child.poll() == 0 and window_instance.send(
                    message, wait=2, name=quit_request.tracker_server_name()):
                log("widget request reached the existing tracker after duplicate launch exited")
                return
            raise OSError("The widget process stopped before it could open.")
    except OSError as err:
        log(f"widget could not open: {err}")
        settings.set("widget", "enabled", previous)
        settings.save()
        raise
