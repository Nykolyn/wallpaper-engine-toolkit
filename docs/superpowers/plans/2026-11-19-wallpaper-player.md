# Wallpaper player Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pause/Play, Next and a per-row Pick for Wallpaper Engine, from the Tracker page, the tray menu and the desktop widget.

**Architecture:** One engine module, `app/engines/player.py`, builds and runs WE's `-control` command lines (the exe of the running WE from `engine_control.running()`) and keeps the Toolkit's own pause flag in `data/player.json`. The page, tray and widget only call it, always off the GUI thread.

**Tech Stack:** Python 3.14, PySide6, the kit in `app/ui/kit`. Tests are plain scripts with `check(label, condition)`, run as `.venv\Scripts\python.exe tests\test_x.py` with `QT_QPA_PLATFORM=offscreen`.

**Spec:** `docs/superpowers/specs/2026-11-19-wallpaper-player-design.md`. Mockups: a private design canvas (four boards: Tracker card + rows, Pick dialog, tray menu, widget).

## Global Constraints

- Pause/Play are global: `-control pause` / `-control play`, no monitor argument. Every label says so: "Pause all monitors" / "Play all monitors".
- Next: `-control nextWallpaper -location <MonitorN>`. Pick: `-control openWallpaper -file <project.json path> -location <MonitorN>`.
- The `-control` process is never run on a GUI thread (the tray once hung for 90 s on GUI-thread I/O).
- Active monitor: Tracker page → `table_monitor()`; tray → the tray's primary monitor; widget → the monitor of the row/widget.
- Work happens in a worktree `.claude/worktrees/wallpaper-player` from `origin/main`; PR adds CHANGELOG lines under `## [Unreleased]`, `__version__` untouched.
- Copy uses no real wallpaper names, authors or paths (public repo); tests use invented ones.

## Review Focus

1. WE not running → every control is disabled or says "Wallpaper Engine is not running", never a traceback or a silent no-op. (Task 1 `send` returns the words; Tasks 3–5 test the disabled state.)
2. `-control` call fails or hangs → finishes within 5 s (`timeout=5`), reported as a toast/log line. (Task 1 test with a fake `run` raising `TimeoutExpired`.)
3. Pause flag left from a WE that has since restarted → ignored (pid differs). (Task 1.)
4. Pick on the row already on screen → button disabled. (Task 3.)
5. Pick on a stale `[protected]`-renamed row (its playlist entry points at the old folder) → disabled with the reason in the tip. (Task 3.)

---

### Task 1: The player engine

**Files:**
- Create: `app/engines/player.py`
- Test: `tests/test_player.py`

**Interfaces:**
- Produces:
  - `PLAYER_FILE = "player.json"`; `TIMEOUT_S = 5`
  - `control_args(exe: str, command: str, monitor: str | None = None, file: str | None = None) -> list[str]`
  - `send(command: str, monitor: str | None = None, file: str | None = None, *, find=engine_control.running, run=external.run) -> str | None` — None when sent, else the words of why not ("Wallpaper Engine is not running.", "Wallpaper Engine did not answer.", the `OSError` text).
  - `pause(**kw) -> str | None`, `play(**kw) -> str | None` (each also writes the flag on success), `next_wallpaper(monitor, **kw)`, `pick(monitor, item, **kw)` (`item` → `os.path.normpath`).
  - `is_paused(pid: int | None, folder: Path | None = None) -> bool`; `set_paused(paused: bool, pid: int, folder: Path | None = None) -> None` (folder defaults to `data_location.data_dir()`; unreadable file → False).
  - `in_background(work: Callable[[], str | None], done: Callable[[str | None], None] | None = None) -> None` — a daemon thread for the tray and widget; `done` is called on that thread (callers marshal with `QTimer.singleShot(0, …)`).

- [ ] **Step 1: Write the failing tests** in `tests/test_player.py` (style of `tests/test_tray_icon.py`, data dir in a temp folder):
  - `control_args("C:/we/wallpaper64.exe", "nextWallpaper", "Monitor1") == ["C:/we/wallpaper64.exe", "-control", "nextWallpaper", "-location", "Monitor1"]`
  - `control_args(exe, "openWallpaper", "Monitor0", "D:/w/1/project.json")` ends `["-file", "D:/w/1/project.json", "-location", "Monitor0"]`; `control_args(exe, "pause")` has no `-location`.
  - `send("pause", find=lambda: None) == "Wallpaper Engine is not running."`
  - with a fake `run` that records its args and kwargs: `send` passes `timeout=5`, `cwd` = the exe's folder, returns None.
  - fake `run` raising `subprocess.TimeoutExpired` → "Wallpaper Engine did not answer."
  - `pause(find=…, run=…)` then `is_paused(engine.pid)` is True, `is_paused(engine.pid + 1)` is False; `play(...)` → False; a garbage `player.json` → False.
- [ ] **Step 2:** Run `.venv\Scripts\python.exe tests\test_player.py` — FAIL (no module).
- [ ] **Step 3:** Implement `app/engines/player.py` per the interfaces; `send` uses `external.run(args, cwd=…, timeout=TIMEOUT_S, capture_output=True)`; a non-zero exit is not an error (WE's control process exits 0/1 inconsistently — confirmed in Task 2, adjust if it reports).
- [ ] **Step 4:** Run the test — all PASS; run `run_app.py --selfcheck`.
- [ ] **Step 5:** Commit `Add the player engine: WE -control commands and the pause flag`.

### Task 2: Live check on the real Wallpaper Engine (needs the user's OK first)

No code unless a finding asks for it. Run from the worktree with the main `.venv` python, one command at a time, and write each result into the PR body.

- [ ] **Step 1:** `player.pause()` → both monitors stop (watch 20 s); `player.play()` → they run. Note whether the tray's countdown froze (its memory timer should).
- [ ] **Step 2:** `player.next_wallpaper("Monitor0")` → only Monitor0 changes; `playliststate.bin` instance for Monitor0 unchanged; the tracker counts it as shown.
- [ ] **Step 3 (only with the user's explicit OK, on Monitor0 "subscribed" whose auto-restore is off):** `player.pick("Monitor0", <an item of its playlist>)` → record: does the monitor keep its playlist; what `playliststate.bin`, `config.json` (`selectedwallpapers.Monitor0`) and `data/tracker.json` say afterwards; does the tray log a reset / schedule a restore.
- [ ] **Step 4:** If Step 3 shows the tracker reading a Pick as a reset, add Task 2b: `player.pick` writes `data/player.json` `{"picked": {"Monitor0": <time>}}` and `pass_restore.find_restorable` / the tray's `_restore_unasked` skip a monitor picked within `AUTO_WINDOW_S`, with a test in `tests/test_pass_restore.py`. If it does not, note "no reset" in the PR and skip 2b.

### Task 3: Tracker page — card controls, card menus, row Pick

**Files:**
- Modify: `app/ui/kit/icons.py` (add `"next"`: `<polygon points="3,2.5 11,8 3,13.5"></polygon><line x1="13" y1="3" x2="13" y2="13"></line>`; `"pick"`: the `monitor` body plus `<polygon points="6.5,4.8 10.2,7 6.5,9.2" fill="currentColor" stroke="none"></polygon>`)
- Modify: `app/ui/kit/cards.py` — `MonitorCard.set_controls(widget: QWidget | None) -> None`, detail cards only, placed above `_facts` with a hairline above it (as in mockup board 1).
- Modify: `app/pages/tracker.py`, `app/engines/wallpaper_timer.py`
- Test: `tests/test_tracker_page.py`, `tests/test_icons.py`

**Interfaces:**
- Consumes: Task 1 (`pause`, `play`, `next_wallpaper`, `pick`, `is_paused`).
- Produces: `PICK = "pick"` cell key; `TrackerPage.toggle_pause() -> bool`, `TrackerPage.next_wallpaper(monitor: str) -> bool`, `TrackerPage.pick(row: PlaylistRow) -> bool`, each False when not done; `TrackerPage.player_texts() -> dict` for tests (`{"pause": "Pause", "next": "Next", "enabled": True}`); `WallpaperTimer(..., held: Callable[[int], bool] | None = None)`.

- [ ] **Step 1: Write failing tests** in `tests/test_tracker_page.py` (fixture page; replace `page._player` — an object with the four functions — by a recorder):
  - lead card shows the controls: `player_texts() == {"pause": "Pause", "next": "Next", "enabled": True}`; after `toggle_pause()` the recorder saw `pause` and the button reads "Play"; again → `play`.
  - `next_wallpaper` called from the lead card targets `table_monitor()`; a summary card's menu `menu_actions(m)` now starts with `("Pause all monitors", False, True)` and `("Next wallpaper", False, True)`.
  - action cell of a queued row has `PICK` first; on the on-screen row it is disabled with tip "Already on screen"; on a `stale` row disabled with tip "Its playlist entry points at the old name".
  - `pick(row)` asks (`_answer` replaced): no → recorder empty; yes → `pick(table_monitor(), row.item)`.
  - engine disconnected (fixture with no engine) → `player_texts()["enabled"] is False`.
- [ ] **Step 2:** Run `tests\test_tracker_page.py` — the new checks FAIL.
- [ ] **Step 3:** Implement:
  - `action_cell` gains a first slot: `CellButton(PICK, "pick", f"Put on {monitor} now")` (`monitor` passed in by `PlaylistModel`), disabled cases above; `_action_clicked` routes `PICK` → `pick`.
  - `pick`: `ConfirmDialog(f"Put {title} on {monitor}?", body, note=("warn", "{monitor} leaves its playlist, as when you click a wallpaper in Wallpaper Engine. Its pass stops counting until the playlist is applied again."), confirm_text="Put on screen")`, then `self._offload.run(lambda: player.pick(...), self._player_done)`; `_player_done(error)` toasts danger with the words, or nothing on success. "Don't ask again" from the spec is dropped: `ConfirmDialog` has no single checkbox, and Delete/Protect ask every time too.
  - Controls row on the lead card: a `SecondaryButton` Pause/Play (icon pause/play, tip "Pause all monitors"/"Play all monitors") + `SecondaryButton` Next (icon next, tip `f"Next wallpaper on {monitor}"`) + a `type.monoXs` lo label "pause: all monitors"; the toggle reads `player.is_paused(engine pid)` each `_render_cards`; disabled while `self._engine()` says WE is not running.
  - `_fill_menu`: first group "Pause all monitors"/"Play all monitors" and "Next wallpaper" for that card's monitor, then a separator, then the existing rows.
  - `WallpaperTimer.tick`: in the estimate branch `paused = (not settings.update_on_pause) and (is_paused(self.rules, screen) or held)` where `held = bool(engine) and self.held is not None and self.held(engine[0])`; `page_timer` passes `held=player.is_paused`.
- [ ] **Step 4:** Run `tests\test_tracker_page.py`, `tests\test_icons.py`, `tests\test_tracker.py` — all PASS. Snapshot the page with `tools/ui_snapshot.py` and compare with mockup board 1.
- [ ] **Step 5:** Commit `Tracker: Pause/Play and Next on the monitor card, Pick on each row`.

### Task 4: Tray menu

**Files:**
- Modify: `app/tray_menu.py`, `app/tracker_tray.py`
- Test: `tests/test_tray_icon.py`

**Interfaces:**
- Consumes: Task 1; the new kit icons (copy `pause`, `play`, `next` bodies into `ICON_BODIES`, as the module already does for its four).
- Produces: keys `PAUSE, NEXT = "pause", "next"`; `build_model(..., paused: bool = False, monitor: str = "")`.

- [ ] **Step 1: Update/add tests:** `model.keys() == ["open", "pause", "next", "rotate", "review", "widget_visible", "widget", "settings", "quit"]`; hints list gains `"", monitor`; `build_model(..., paused=True).rows()[1].label == "Play all monitors"`; `rows()[2].hint == "Monitor1"` for `monitor="Monitor1"`; choosing `pause` on a tray built with a fake player calls it via `in_background` (replace `player.in_background` with an immediate call).
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3:** Implement: second group `(Row(PAUSE, "Play all monitors" if paused else "Pause all monitors", "", "play" if paused else "pause"), Row(NEXT, "Next wallpaper", monitor, "next"))`; `_rebuild_menu` passes `paused=player.is_paused(pid)` (pid from `self.clock.engine`) and the primary's monitor; `_on_chosen` runs `player.in_background(...)` and logs a returned error with `log("player: …")`. NEXT is disabled-equivalent when no primary: skip the call and log.
- [ ] **Step 4:** Run — PASS.
- [ ] **Step 5:** Commit `Tray: Pause all monitors and Next wallpaper`.

### Task 5: Desktop widget

**Files:**
- Modify: `app/desktop_widget.py`, `app/tracker_tray.py`
- Test: `tests/test_desktop_widget.py`

**Interfaces:**
- Consumes: Task 1, Task 4's handling.
- Produces: `DesktopWidget.player_requested = Signal(str, str)` — (`"pause"`|`"play"`|`"next"`, monitor); the tray connects it to the same code path as the menu (`_player(action, monitor)`).

- [ ] **Step 1: Tests:** expanded widget: each `MonitorRow` has two visible buttons with accessible names "Pause all monitors" and f"Next wallpaper on {monitor}"; compact: buttons hidden; clicking Next emits `("next", "Monitor1")`; `build_menu()` actions start with "Pause all monitors", "Next wallpaper", separator; paused flag set → first reads "Play all monitors" and emits `"play"`.
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3:** Implement: two round 30 px `QToolButton`s per `MonitorRow` (glass fill `rgba(255,255,255,.08)`, rim `widget.edge`, icons from `icon_pixmap`), placed at the right of the row in `resizeEvent`, shown only when `not compact`; the row's title/size text is laid out to stop 76 px short of the right edge in expanded mode; pause state from `player.is_paused` read in `DesktopWidget.render`.
- [ ] **Step 4:** Run `tests\test_desktop_widget.py` and `tests\test_tray_icon.py` — PASS.
- [ ] **Step 5:** Commit `Widget: player buttons on expanded rows and in the menu`.

### Task 6: Docs, changelog, PR, release

**Files:** `docs/tracker.md`, `docs/tray.md`, `docs/widget.md`, `CHANGELOG.md`, the spec and this plan.

- [ ] **Step 1:** Docs: one short section each ("Player controls": what each does, that pause is global, that Pick leaves the playlist and the pass stops counting). CHANGELOG `## [Unreleased]` → `### Added` lines for the three surfaces.
- [ ] **Step 2:** Run every `tests\test_*.py` and `run_app.py --selfcheck`; `tools/release.py check --base origin/main`.
- [ ] **Step 3:** Audit staged content (credentials by shape, real names/paths), squash, push, open the PR with Task 2's findings; wait for the `tests` workflow; squash-merge on green.
- [ ] **Step 4:** Release PR: bump `__version__` 4.11.1 → 4.12.0 (new feature), move Unreleased to `## [4.12.0] - <date>`; merge on green; `gh workflow run tests.yml --ref main`; confirm the `v4.12.0` release appears.
