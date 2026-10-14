"""In-app updates: trusted metadata, complete downloads and safe installer handoff."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

TMP = Path(tempfile.mkdtemp(prefix="toolkit_updates_test_"))
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(errors="replace")

from app.engines import updates as u

results = []


def check(label, condition):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def raises(work, kind=ValueError):
    try:
        work()
    except kind:
        return True
    return False


with patch.object(sys, "frozen", False, create=True), patch.object(
        u.install_info, "installed_exe", side_effect=AssertionError("source mode must not probe installed files")):
    check("source update eligibility does not access an installed copy", not u.install_info.is_installed_copy())
with patch.object(sys, "frozen", True, create=True), patch.object(
        u.install_info, "installed_exe", return_value=Path(sys.executable)):
    check("the installed executable is eligible", u.install_info.is_installed_copy())
with patch.object(sys, "frozen", True, create=True), patch.object(
        u.install_info, "installed_exe", return_value=TMP / "other.exe"):
    check("a standalone build cannot update another installation", not u.install_info.is_installed_copy())


payload = b"a fabricated installer" * 30_000
digest = hashlib.sha256(payload).hexdigest()
metadata = {"tag_name": "v99.0.0", "body": "A new version", "draft": False, "prerelease": False,
            "assets": [{"name": "WallpaperEngineToolkit-Setup-99.0.0.exe", "size": len(payload),
                        "digest": "sha256:" + digest,
                        "browser_download_url": u.RELEASES + "/download/v99.0.0/WallpaperEngineToolkit-Setup-99.0.0.exe"}]}
release = u.parse(metadata)
check("numeric versions compare across double digits", u.version("v3.10.0") > u.version("3.9.9"))
check("same and older versions are not updates", u.parse(metadata, "99.0.0") is None
      and u.parse(metadata, "100.0.0") is None)
for field in ("draft", "prerelease"):
    data = dict(metadata, **{field: True})
    check(field + " releases are ignored", u.parse(data) is None)
for tag in ("v1.2.3-beta", "../setup", "1.2", None):
    check("invalid version is rejected: " + str(tag), raises(lambda: u.parse(dict(metadata, tag_name=tag))))
for field, value in (("digest", None), ("digest", "sha256:bad"), ("size", -1), ("size", True),
                     ("browser_download_url", "https://example.com/setup.exe"),
                     ("name", "other.exe")):
    data = copy.deepcopy(metadata)
    data["assets"][0][field] = value
    check("untrusted installer field rejected: " + field, raises(lambda: u.parse(data)))
check("incomplete release waits for its installer", raises(lambda: u.parse(dict(metadata, assets=[]))))
check("duplicate installers are rejected", raises(lambda: u.parse(dict(metadata, assets=metadata["assets"] * 2))))


class Response(io.BytesIO):
    def __init__(self, body=payload, url="https://release-assets.githubusercontent.com/github-production-release-asset/file"):
        super().__init__(body)
        self.url = url

    def geturl(self):
        return self.url


with patch.object(u, "_open", lambda url: Response(json.dumps(metadata).encode())):
    check("latest release metadata is read", u.check() == release)
with patch.object(u, "_open", lambda url: Response(b"x" * (u.MAX_METADATA + 1))):
    check("metadata is bounded", raises(u.check))

folder = TMP / "updates"
folder.mkdir()
old = folder / "WallpaperEngineToolkit-Setup-1.0.0.exe"
old.write_bytes(b"old setup")
(folder / "keep.txt").write_text("keep", encoding="utf-8")
cancel = threading.Event()
progress = []
with patch.object(u, "_open", lambda url: Response()):
    installer = u.download(release, folder, cancel, lambda done, total: progress.append((done, total)))
check("download is complete and verified", installer.read_bytes() == payload
      and progress[-1] == (len(payload), len(payload)) and not list(folder.glob("*.partial")))
check("only older setup downloads are removed after success", not old.exists()
      and (folder / "keep.txt").read_text(encoding="utf-8") == "keep")
for body, url in ((payload[:-1], release.url), (payload + b"x", release.url),
                  (b"x" * len(payload), release.url), (payload, "http://github.com/file"),
                  (payload, "https://example.com/file")):
    with patch.object(u, "_open", lambda address, b=body, final=url: Response(b, final)):
        check("failed download preserves the verified installer and removes the partial",
              raises(lambda: u.download(release, folder, cancel))
              and installer.read_bytes() == payload and not list(folder.glob("*.partial")))
with patch.object(u, "_open", side_effect=OSError("connection lost")):
    check("connection failure is reported", raises(lambda: u.download(release, folder, cancel), OSError))


class Interrupted(Response):
    def read(self, size=-1):
        if self.tell():
            raise OSError("connection lost halfway")
        return super().read(size)


with patch.object(u, "_open", lambda url: Interrupted()):
    check("interrupted transfer removes the partial and keeps the previous verified file",
          raises(lambda: u.download(release, folder, cancel), OSError)
          and not list(folder.glob("*.partial")) and installer.read_bytes() == payload)
with patch.object(u.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
    check("full disk is rejected before downloading", raises(lambda: u.download(release, folder, cancel), OSError))
with patch.object(u, "_open", lambda url: Response()):
    check("mid-download cancellation leaves no partial",
          raises(lambda: u.download(release, folder, cancel, lambda *_: cancel.set()), u.Cancelled)
          and not list(folder.glob("*.partial")) and installer.read_bytes() == payload)
check("cancelled download does not connect", raises(lambda: u.download(release, folder, cancel), u.Cancelled))
cancel.clear()

calls = []
with patch.object(u.install_info, "is_installed_copy", return_value=True), patch.object(
        u.external, "popen", lambda args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(wait=lambda: 20)):
    check("installer errors reach the caller", u.install(installer, release) == 20)
    check("setup receives update mode, logging and no Windows restart",
          "/UpdateFromApp=1" in calls[-1][0] and "/NORESTART" in calls[-1][0]
          and any(a.startswith("/LOG=") for a in calls[-1][0]))
    check("in-app updates hide the wizard, progress window and startup prompt",
          "/VERYSILENT" in calls[-1][0] and "/SP-" in calls[-1][0] and "/SILENT" not in calls[-1][0])
    check("installation failure messages remain visible after the app closes",
          "/SUPPRESSMSGBOXES" not in calls[-1][0])
    installer.write_bytes(b"x" * len(payload))
    check("tampering after download prevents execution", raises(lambda: u.install(installer, release)) and len(calls) == 1)
installer.write_bytes(payload)
with patch.object(u.install_info, "is_installed_copy", return_value=False):
    check("source builds cannot install over the installed copy", raises(lambda: u.install(installer, release)))

from PySide6.QtWidgets import QApplication
from app import theme
from app.pages.settings import SettingsPage
from app.engines.rotator.config import Config
from app.settings import Settings
from app.services.jobs import JobCenter
from app.ui.kit import ConfirmDialog

app = QApplication([])
theme.apply(app)
settings = Settings()
jobs = JobCenter()
page = SettingsPage(settings, Config(source="", destination=""), services=SimpleNamespace(jobs=jobs))
work = []
page._ask = lambda what, task: work.append((what, task))
with patch.object(u.install_info, "is_installed_copy", return_value=True):
    page.startup()
    page.check_update()
    check("startup checks once and duplicate checks are blocked", len(work) == 1 and work[0][0] == "update-check")
    page._show_update(release)
    check("available update has an action and a sidebar notice", not page.update_action.isHidden()
          and page.nav_state().text == "Update")
    page._update_action()
    check("download registers a job and runs on a worker", jobs.is_running("updates") and work[-1][0] == "update-download")
    page._downloaded_update(u.Cancelled())
    check("cancel ends the job and allows retry", not jobs.running() and page.update_action.isEnabled())
    page._update_action()
    page._downloaded_update(installer)
    check("verified download offers installation", page.update_action.text() == "Install and restart" and not jobs.running())
    with patch.object(ConfirmDialog, "ask", return_value=False):
        before = len(work)
        page._update_action()
        check("declining installation does nothing", len(work) == before and not page._install_requested)
    active = jobs.start("copier", "Copying folders")
    with patch.object(ConfirmDialog, "ask", return_value=True):
        page._update_action()
        check("installation waits for active work", page._install_requested and work[-1][0] != "update-install")
        page.cancel_update()
        active.finish("clean")
        check("postponed installation never launches", work[-1][0] != "update-install")
        active = jobs.start("creator", "Creating wallpapers")
        page._update_action()
        active.finish("clean")
        check("installation starts only once after jobs finish", work[-1][0] == "update-install"
              and sum(name == "update-install" for name, _ in work) == 1)
    page._installed_update(OSError("setup did not start"))
    check("launch failure leaves retry available", page.update_action.isEnabled() and not page._update_busy)
    page.auto_update_check.setChecked(False)
    check("startup preference is persisted", json.loads((TMP / "data" / "suite.json").read_text())["updates"]["check_on_startup"] is False)
    before = len(work)
    page.startup()
    check("disabled startup check stays quiet", len(work) == before)
    page._show_update(ValueError("offline"))
    check("check failure allows another try", page.update_check.isEnabled() and "offline" in page.update_note.text())
with patch.object(u.install_info, "is_installed_copy", return_value=False):
    page._show_update(release)
    check("source builds show the release without an install button", page.update_action.isHidden())

page.close()
print(f"PASSED {sum(results)}/{len(results)}")
sys.exit(0 if all(results) else 1)
