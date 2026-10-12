"""Review and run copy consolidation off the GUI thread."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject

from ..engines import engine_control, multiplier_migration as migration, steam_paths
from ..engines.rotator import config as rc
from ..engines.rotator.core import folder_is_set, list_subfolders
from ..services.runs import Run
from ..settings import SETTINGS_PATH
from ..ui.kit import ConfirmDialog
from .tracker import _Offload


class MigrationController(QObject):
    def __init__(self, page, settings, services, config, store, *, on_done: Callable, editors=()):
        super().__init__(page)
        self.page, self.settings, self.services = page, settings, services
        self.config, self.store, self.on_done = config, store, on_done
        self.editors = tuple(editors)
        self.offload = _Offload(self)
        self.busy = False
        self.page._migration_busy = False
        self.latest = None

    def _set_busy(self, busy: bool) -> None:
        self.busy = self.page._migration_busy = busy
        for editor in self.editors:
            editor.locked = busy
            editor._snapshot()
        render = getattr(self.page, "_render", None)
        if render is not None:
            render()

    def _blocked(self) -> bool:
        jobs = getattr(self.services, "jobs", None)
        return (self.busy or any(editor.pending for editor in self.editors)
                or self.page._fixture is not None or self.page._busy()
                or (jobs is not None and any(jobs.is_running(tool)
                                            for tool in ("rotator", "cleaner", "creator"))))

    def start(self) -> bool:
        if self._blocked():
            return False
        roots = [self.config.source, self.config.destination, self.config.duplicates,
                 self.settings.get("copier", "dest", "")]
        roots = list(dict.fromkeys(p for p in roots if folder_is_set(p)))
        if not roots or not folder_is_set(self.config.destination):
            self.page._say("warn", "Set the reserve and myprojects folders before migrating copies.")
            return False
        self._set_busy(True)
        run = Run(self.services, "rotator", "Inspecting physical copies")
        run.update("Checking copy contents; no folders changed")

        def inspect():
            plan = migration.plan_migration(roots, self.config.destination)
            manifests = sorted(self.store.path.parent.glob("multiplier-migration/*/manifest.json"),
                               key=lambda p: p.stat().st_mtime, reverse=True)
            pending = migration.pending_migrations(self.store.path.parent / "multiplier-migration")
            latest = pending[0] if pending else next((p for p in manifests
                if json.loads(p.read_text(encoding="utf-8")).get("status") == "complete"), None)
            return plan, latest

        self.offload.run(inspect, lambda result: self._planned(result, run))
        return True

    def _planned(self, result, run):
        self._set_busy(False)
        if isinstance(result, Exception):
            run.fail(f"Could not inspect copies: {result}")
            self.page._say("danger", f"Could not inspect copies: {result}")
            return
        plan, self.latest = result
        run.finish("clean", f"Found {plan.copy_count} verified copies", journal=False)
        status = (json.loads(self.latest.read_text(encoding="utf-8")).get("status")
                  if self.latest is not None else "")
        if status and status != "complete":
            if status not in ("prepared", "applying", "committed", "finalizing"):
                self.page._say("warn", "An interrupted migration must be restored before starting another.")
                self.rollback()
                return
            dialog = ConfirmDialog("Recover interrupted copy migration?",
                                   "The saved manifest can finish this migration or restore its previous folders.",
                                   self.page.window(), confirm_text="Resume migration", safe_default=True,
                                   actions=[("Restore previous folders", self.rollback)])
            if self._answer(dialog):
                self._execute(lambda: migration.resume_migration(self.latest, self.store))
            return
        lines = [f"{Path(g.survivor).name}: Multiplier {g.multiplier}; remove {g.multiplier - 1} copies"
                 for g in plan.groups]
        lines += plan.preserved
        body = ("Original wallpapers are retained, playlist paths are repaired, and current wallpapers "
                "consume one appearance in the new cycle. One verified payload per group is kept "
                "outside the library for rollback. Wallpaper Engine will close and restart.")
        dialog = ConfirmDialog(f"Migrate {plan.copy_count} verified copies?", body,
                               self.page.window(), lines=lines,
                               confirm_text="Migrate copies", safe_default=True,
                               actions=[("Restore last migration", self.rollback)] if self.latest else [])
        if not plan.groups:
            dialog = ConfirmDialog("No identical copies to migrate", "Changed copies and copies without an "
                                   "original are preserved. " + ("A previous migration can be restored." if self.latest else ""),
                                   self.page.window(), lines=plan.preserved,
                                   confirm_text="Close",
                                   actions=[("Restore last migration", self.rollback)] if self.latest else [])
            self._answer(dialog)
            return
        if self._answer(dialog):
            config_path = self.settings.get("tracker", "we_config", None)

            def apply():
                path = config_path or steam_paths.we_config()
                try:
                    runs = rc.read_runs()
                except FileNotFoundError:
                    runs = []
                return migration.apply_migration(plan, self.store, runs=runs,
                    current_names=list_subfolders(self.config.destination), config_path=path,
                    settings_paths=[SETTINGS_PATH, rc.CONFIG_PATH, rc.HISTORY_PATH],
                    backup_root=self.store.path.parent / "multiplier-migration")

            self._execute(apply)

    def _answer(self, dialog):
        return dialog.ask()

    def rollback(self) -> bool:
        if self.latest is None or self._blocked():
            return False
        dialog = ConfirmDialog("Restore the previous wallpaper folders?",
                               "The migration's saved paths and preferences are restored. Recovery refuses "
                               "to overwrite files changed since migration. Wallpaper Engine will restart.",
                               self.page.window(), lines=[str(self.latest)],
                               confirm_text="Restore migration", safe_default=True)
        if not self._answer(dialog):
            return False
        self._execute(lambda: migration.rollback_migration(self.latest, self.store))
        return True

    def _execute(self, work):
        if self._blocked():
            return
        self._set_busy(True)
        run = Run(self.services, "rotator", "Migrating wallpaper copies", activity="migration")
        run.update("Verifying folders and repairing playlist references")

        def perform():
            engine = engine_control.running()
            if engine is not None and not engine_control.close(engine):
                raise OSError("Wallpaper Engine could not close; no folders were migrated.")
            warning, error, restart = "", None, True
            try:
                manifest = work()
            except Exception as failure:
                error = failure
                try:
                    pending = migration.pending_migrations(self.store.path.parent / "multiplier-migration")
                    for path in pending:
                        status = json.loads(path.read_text(encoding="utf-8"))["status"]
                        if status not in ("committed", "finalizing"):
                            migration.rollback_migration(path, self.store)
                except Exception as recovery:
                    restart = False
                    warning = f" Recovery could not finish: {recovery}. Wallpaper Engine remains closed."
            finally:
                if engine is not None and restart and not engine_control.start(engine):
                    warning = " Wallpaper Engine could not restart; start it manually."
            if error is not None:
                raise RuntimeError(f"{error}{warning}") from error
            return manifest, warning

        self.offload.run(perform, lambda result: self._finished(result, run))

    def _finished(self, result, run):
        self._set_busy(False)
        if isinstance(result, Exception):
            words = f"Migration stopped: {result}. Use Migrate copies to inspect recovery."
            run.fail(words)
            self.page._say("danger", words)
        else:
            self.latest, warning = result
            words = f"Migration finished. Recovery manifest: {self.latest}.{warning}"
            run.finish("problems" if warning else "clean", words)
            self.page._say("warn" if warning else "ok", words)
        self.on_done()
