---
date: 2026-10-10
status: accepted
source: "#54, #55"
tags: [release, data]
---

# One installer, and it keeps the data

**Decision.** Every release ships `WallpaperEngineToolkit-Setup-X.Y.Z.exe`,
made by Inno Setup (`installer/WallpaperEngineToolkit.iss`) from the same
PyInstaller build. The installer never deletes, moves or overwrites the data
folder. Before an update's new version runs, the data is copied to
`update_backup\` and verified (newest five kept); if that fails, the new
version is not started. Uninstall keeps the data. A first install from an
earlier copy copies and verifies that copy's data, leaving the original alone.
Running copies are asked to quit and may refuse while a job runs; nothing is
killed.

**Why.** Download, run, done — no Python, clone or build — without any update
being able to cost data.

**Consequences.** The Release workflow builds and checks the installer
(`tools/check_installer.py`: install, update over a running copy, uninstall,
reinstall, data checked after each). See [Installing](../installing.md).
