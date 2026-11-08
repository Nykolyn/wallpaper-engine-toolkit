# Restore pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put a Wallpaper Engine pass that WE threw away back in place — from a new Restore page by button, and automatically from the tray per monitor.

**Architecture:** The tracker's archive starts keeping what a thrown-away pass had shown. A new engine module (`app/engines/pass_restore.py`) decides what is restorable, builds the deck, and runs close WE → rewrite `playliststate.bin` → hand the tracker its old cycle → start WE, reusing `engine_control` and the state-file reader/writer. A new page and a hook in the tray drive it.

**Tech Stack:** Python 3.12+, PySide6, the project's own UI kit (`app/ui/kit`). Tests are plain scripts with a `check(label, condition)` helper and `PASSED n/m` at the end — no pytest.

**Spec:** `docs/superpowers/specs/2026-11-08-pass-restore-design.md`

## Global Constraints

- Every change goes through a PR from branch `pass-restore`; CHANGELOG entry under `## [Unreleased]`, `__version__` untouched.
- Never leave WE closed: `engine_control.start` runs whenever `engine_control.close` succeeded, whatever failed in between.
- WE did not close → nothing is written (state file, tracker.json).
- `playliststate.bin` is backed up before it is rewritten, to `app_data_dir() / "pass-restore" / "playliststate.bin"`.
- Only the chosen monitor's record in `playliststate.bin` changes; the others round-trip byte-for-byte.
- Playlist still the same = same playlist name **and** ≥ 90% of the archived shown list still in the current items.
- Auto restore waits **60 s** after the reset is seen; a pass auto-restored is not auto-restored again within **600 s**.
- Auto restore switch per monitor in `suite.json`: section `"restore"`, key `"auto.<MonitorN>"`; default **on for a monitor whose current cycle has `from_rotation`**, off otherwise (on this machine: Monitor1 on, Monitor0 off).
- Rotator-made passes are never restorable (they are archived with no reason or another reason).
- Keep Python off per-frame GUI paths; WE work (close/rewrite/start, `scan_atimes` on W:) runs on a `QThread`, never the GUI thread.
- Run tests with `.venv\Scripts\python.exe tests\<file>.py`; expected last line `PASSED n/n`.

## Review Focus

1. **The tray tracker saving over the restore.** The tray polls on its own and saves tracker.json; a save in flight while the restore writes can put the post-reset cycle back. Expected: counts still end up right, because the next look follows the deck (everything not waiting is credited). Test in Task 3: after `restore`, a fresh `Tracker.poll()` over the rewritten deck reports the restored seen count.
2. **WE did not close / did not come back.** Expected: nothing written / backup path named in the message. Tests in Task 3.
3. **A second reset right after a restore** (monitor attached late again). Expected: no restore loop. Test in Task 4 (`AutoGuard`).
4. **Playlist edited since the reset** (items added/removed, renamed). Expected: refused with a reason, nothing touched. Test in Task 2.
5. **A pass already complete or already restored.** Expected: Restore disabled with a reason. Tests in Task 2.

---

### Task 1: Archive keeps the shown list on engine resets

**Files:**
- Modify: `app/engines/tracker.py` — `TrackerState.archive_cycle` (~line 1121) and its call in `Tracker.poll` (~line 1491)
- Test: `tests/test_pass_restore.py` (new; script style, header copied from `tests/test_tracker.py`: temp `tr.STATE_PATH`, `check`, `PASSED`)

**Interfaces:**
- Produces: `TrackerState.archive_cycle(cycle: Cycle, reason: str | None = None, *, keep_shown: bool = False) -> None`. With `keep_shown`, the entry gains `"shown": {item: when}` — `cycle.seen` restricted to `cycle.items` minus `cycle.missing`.
- Produces: module constant `RESET_REASON = "Wallpaper Engine started the playlist over"` in `tracker.py`, used at the call site and by Task 2.

- [ ] **Step 1: Write the failing tests** in `tests/test_pass_restore.py`:
  - `archive keeps shown on an engine reset`: a `Cycle(monitor="Monitor1", playlist="custom", started=..., items=["a","b","c"], seen={"a": t1, "b": t2, "x": t3}, missing=["c"])`, `state.archive_cycle(c, tr.RESET_REASON, keep_shown=True)` → `state.archive[0]["shown"] == {"a": t1, "b": t2}`.
  - `archive keeps no list otherwise`: same cycle, `archive_cycle(c, "shown to the end; …")` → `"shown" not in state.archive[0]`.
- [ ] **Step 2: Run** `.venv\Scripts\python.exe tests\test_pass_restore.py` — expected: FAIL (`RESET_REASON` missing / unexpected keyword).
- [ ] **Step 3: Implement** the parameter and constant; in `poll`, pass `keep_shown=not finished` and use `RESET_REASON` for the not-finished reason string (text unchanged).
- [ ] **Step 4: Run** `tests\test_pass_restore.py` and `tests\test_tracker.py` — expected: both `PASSED n/n`.
- [ ] **Step 5: Commit** `git commit -m "Keep a thrown-away pass's shown list in the tracker archive"`

### Task 2: What is restorable, and the new deck (pure logic)

**Files:**
- Create: `app/engines/pass_restore.py`
- Test: `tests/test_pass_restore.py`

**Interfaces:**
- Consumes: `TrackerState`, `Cycle`, `RESET_REASON`, `scan_atimes(items) -> dict[str, float]`, `TIME_FMT` from `tracker.py`; `read_state_file`, `write_state_file`, `StateSection`, `StateMonitor` from `wallpaper_timer.py`; `SECTION` from `playlist_refresh.py`.
- Produces:
  ```python
  @dataclass
  class Restorable:
      monitor: str
      entry: dict            # the archive entry
      current: Cycle         # the cycle the reset made
      shown: dict[str, str]  # item -> when, old pass ∪ current.seen, within current.items
      source: str            # "saved" | "access times"
      @property
      def total(self) -> int: ...       # live items: current.items minus current.missing
      @property
      def seen(self) -> int: ...        # len(shown)

  def shown_from_atimes(items: list[str], atimes: dict[str, float],
                        started: datetime, ended: datetime) -> dict[str, str]
  def find_restorable(state: TrackerState, monitor: str,
                      atimes: Callable[[list[str]], dict[str, float]] = scan_atimes,
                      atime_ok: bool | None = True) -> tuple[Restorable | None, str]
  def plan_deck(items: list[str], shown: Iterable[str], missing: Iterable[str],
                current: str) -> list[str]
  def restored_cycle(r: Restorable) -> Cycle
  def rewrite_state(data: bytes, monitor: str, current: str, waiting: list[str]) -> bytes
  ```
- Rules for `find_restorable` (the reason string is shown on the page when the first value is None):
  - no current cycle → `"Nothing is tracked on {monitor} yet."`
  - the archive entry with `id == current.previous_id`, `reason == RESET_REASON`, no `"restored"` key; none → `"Wallpaper Engine has not thrown a pass away on {monitor}."`; already restored → `"That pass was already restored."`
  - `entry["playlist"] != current.playlist` or < 90% of the old shown in `current.items` → `"The playlist has changed since the reset."`
  - old shown: `entry["shown"]` (source `"saved"`), else `shown_from_atimes(current.items, atimes(current.items), started, ended)` (source `"access times"`) when `atime_ok`; neither → `"No record of what that pass showed."`
  - union with `current.seen` leaves no live item unshown → `"That pass had already been shown to the end."`
- `restored_cycle`: a copy of `current` with `id=entry["id"]`, `started=entry["started"]`, `seen=r.shown`, `changes=entry["changes"] + current.changes`, `restarted_at/restarted_from/previous_id = None`, `anchor` unchanged.
- `rewrite_state`: in section `SECTION`, the monitor named `monitor` gets `current`, `waiting`, `mark=0`, `marks=[0]*len(waiting)`; raises `ValueError(f"{monitor} is not in playliststate.bin")` if absent.

- [ ] **Step 1: Write the failing tests** (today's real shape, with made-up paths `"p000"…"p193"` plus 7 missing):
  - `atimes pick only the pass window`: atimes before start, inside, after end → only inside returned, times formatted with `TIME_FMT`.
  - `today's pass is restorable from access times`: archive entry `{"id": "270bd142", "playlist": "custom", "reason": RESET_REASON, "started": S, "ended": E, "changes": 175, "seen": 168, "total": 194}`, current cycle with `previous_id="270bd142"`, 201 items, 7 missing, `seen` = 2 items not among the 168; atimes put 168 inside [S, E) → `r.source == "access times"`, `r.seen == 170`, `r.total == 194`.
  - `saved list wins over access times`: entry with `"shown"` → `r.source == "saved"` and the atimes callable is never called.
  - `plan keeps the current one out and drops shown and missing`: `plan_deck(items, shown(170), missing(7), current)` → 24 items, `current not in` result, order as in `items`.
  - `refuses a changed playlist`: rename current playlist → `(None, "The playlist has changed since the reset.")`; replace 20% of the shown items → same.
  - `refuses a restored pass` / `refuses a complete pass` / `refuses without a reset`: the three reasons above.
  - `restored cycle carries the old id`: `restored_cycle(r).id == "270bd142"`, `.started == S`, `.previous_id is None`, `len(.seen) == 170`.
  - `rewrite touches only that monitor`: a state built with `write_state_file` holding Monitor0 and Monitor1 → after `rewrite_state(..., "Monitor1", cur, deck)`, `read_state_file` shows Monitor1 current/waiting changed and Monitor0's record equal to before; unknown monitor raises `ValueError`.
- [ ] **Step 2: Run** `tests\test_pass_restore.py` — expected: FAIL (`ModuleNotFoundError: app.engines.pass_restore`).
- [ ] **Step 3: Implement** the module (module docstring: why WE throws passes away and how this puts one back, two short paragraphs).
- [ ] **Step 4: Run** `tests\test_pass_restore.py` — expected `PASSED n/n`.
- [ ] **Step 5: Commit** `git commit -m "Work out which thrown-away pass can be restored"`

### Task 3: Running a restore

**Files:**
- Modify: `app/engines/pass_restore.py`
- Test: `tests/test_pass_restore.py`

**Interfaces:**
- Consumes: Task 2; `engine_control.running() -> Engine | None`, `engine_control.close(engine) -> bool`, `engine_control.start(engine) -> bool`, `Engine.install_dir`; `playlist_refresh.STATE_FILE`, `playlist_refresh._replace`; `app_data_dir()`.
- Produces:
  ```python
  @dataclass
  class RestoreResult:
      ok: bool
      message: str           # one sentence for the page / balloon / journal
      seen: int = 0
      total: int = 0

  def restore(monitor: str, say: Callable[[str], None] = lambda _m: None) -> RestoreResult
  class RestoreWorker(QThread):      # finished_restore = Signal(object)  # RestoreResult
      def __init__(self, monitor: str): ...
  ```
- Order inside `restore`: `find_restorable` (refusal → `ok=False`, its reason) → `running()` (None → `"Wallpaper Engine is not running."`) → `close` (False → `"Wallpaper Engine did not close, so nothing was changed."`, return) → backup → `_replace(state_path, rewrite_state(...))` → `TrackerState.load()`, replace `cycles[monitor]` with `restored_cycle`, set `entry["restored"] = now` (in the loaded state's archive, matched by id), `save()` → `finally: start`. Start failing → `ok=False`, message names the backup path. Success message: `"Pass restored on {monitor}: {seen}/{total}."`
- Any exception between close and start → `ok=False`, `"Restoring failed: {e}"`, tracker.json untouched if the state file was not written.

- [ ] **Step 1: Write the failing tests** with `engine_control.running/close/start` and `pass_restore.app_data_dir` monkeypatched to fakes over a temp install dir holding `bin/playliststate.bin`:
  - `a restore rewrites the deck and gives the tracker its pass back`: result `ok`, `seen == 170`; the file's Monitor1 waiting == `plan_deck(...)`; `TrackerState.load().cycles["Monitor1"].id == "270bd142"`; archive entry has `"restored"`; backup file exists; `start` called once.
  - `WE that does not close changes nothing`: `close` → False: file bytes and tracker.json unchanged, `start` not called, message as above.
  - `WE is started even when the rewrite fails`: `rewrite_state` patched to raise → `start` called, tracker.json unchanged, `ok` False.
  - `a failed start names the backup`: `start` → False → `"pass-restore" in result.message`.
  - `the tracker follows the restored deck`: with `tests/test_tracker.py`'s `write_cfg` pattern (`items`, playlist `"custom"`, `monitor="Monitor1"`) and the rewritten state file next to it, one `Tracker(...).poll()` reports Monitor1 `seen == 170` (Review Focus 1).
- [ ] **Step 2: Run** — expected FAIL (`restore` missing).
- [ ] **Step 3: Implement** `restore`, `RestoreResult`, `RestoreWorker` (`run` calls `restore` and emits; an unexpected exception becomes `RestoreResult(False, f"Unexpected error: {e}")`).
- [ ] **Step 4: Run** `tests\test_pass_restore.py` — `PASSED n/n`.
- [ ] **Step 5: Commit** `git commit -m "Restore a thrown-away pass: close, rewrite the deck, start"`

### Task 4: Auto switch and loop guard

**Files:**
- Modify: `app/engines/pass_restore.py`
- Test: `tests/test_pass_restore.py`

**Interfaces:**
- Produces:
  ```python
  AUTO_DELAY_S = 60
  AUTO_WINDOW_S = 600
  def auto_enabled(settings, monitor: str, cycle: Cycle | None) -> bool
      # settings.get("restore", f"auto.{monitor}", None); None -> bool(cycle and cycle.from_rotation)
  def set_auto(settings, monitor: str, on: bool) -> None   # settings.set(...) then settings.save() as other pages do
  class AutoGuard:
      def __init__(self, window: float = AUTO_WINDOW_S): ...
      def allowed(self, monitor: str, now: float) -> bool
      def mark(self, monitor: str, now: float) -> None
  ```
- [ ] **Step 1: Write the failing tests:** default follows `from_rotation` (True/False); an explicit `False` beats a rotation cycle; `AutoGuard`: allowed → mark at t → not allowed at t+599 → allowed at t+600; other monitor unaffected.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** (check how `Settings` persists in `app/settings.py` and follow it).
- [ ] **Step 4: Run** — `PASSED n/n`.
- [ ] **Step 5: Commit** `git commit -m "Per-monitor automatic restore switch and loop guard"`

### Task 5: Automatic restore from the tray

**Files:**
- Modify: `app/tracker_tray.py` (`_announce_completions`, new `_auto_restore`, `_on_restored`)
- Modify: `app/services/activity.py` — `ACTIVITIES["restore"] = ("tracker", "putting back a pass Wallpaper Engine threw away")`
- Test: `tests/test_pass_restore.py` (logic only; the tray is checked live in Task 7)

**Interfaces:**
- Consumes: Task 3 `RestoreWorker`, Task 4 `auto_enabled`, `AutoGuard`, `AUTO_DELAY_S`; `ActivityJournal(app_data_dir() / JOURNAL_FILE).add(tool, kind, title, detail)`.
- Behaviour: in the not-finished branch, where `_say_restarted(p)` is called today: if `auto_enabled(self.settings, p.monitor, <current cycle>)` and `self._restore_guard.allowed(...)` → `mark`, `QTimer.singleShot(AUTO_DELAY_S * 1000, lambda m=p.monitor: self._auto_restore(m))` and skip the "started over" balloon; else `_say_restarted(p)` as now. `_auto_restore` keeps the worker on `self` until it finishes. `_on_restored(result)`: balloon titled `"Pass restored"` / `"Pass not restored"` with `result.message`, and a journal entry of kind `restore.clean` / `restore.failed`.
- The current cycle for `auto_enabled` comes from `TrackerState.load().cycles.get(p.monitor)`.

- [ ] **Step 1: Write the failing test:** `known_kind("restore.clean") and known_kind("restore.failed")`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** the activity kind and the tray hook.
- [ ] **Step 4: Run** `tests\test_pass_restore.py`, `tests\test_services.py`, `tests\test_tray_options.py` — all `PASSED`.
- [ ] **Step 5: Commit** `git commit -m "Restore a thrown-away pass automatically from the tray"`

### Task 6: The Restore page

**Files:**
- Create: `app/pages/restore.py`
- Modify: `app/pages/__init__.py` (`build_pages`: add `RestorePage(settings)` after `ReservePage`)
- Modify: `app/main_window.py` — `PAGE_ORDER` gets `"restore"` after `"reserve"`; `PAGE_SHORTCUTS` skips it (Ctrl+1…9 and Ctrl+0 unchanged); `TOOL_NAMES["restore"] = "Restore"`
- Test: `tests/test_restore_page.py` (new; offscreen Qt like `tests/test_tracker_page.py`)

**Interfaces:**
- Consumes: Tasks 2–4; kit `GlassPanel`, `CardTitle`, `label`, `AccentButton`, `Toggle`, `ConfirmDialog`; `services.runs.begin(tool="tracker", title=..., activity="restore")`.
- Produces: `class RestorePage(Page)`: `key = "restore"`, `title = "Restore"`, `icon = "refresh"`, `FIXTURES = ("idle",)`; `refresh()`; `_answer(dialog)` (tests answer); `texts() -> dict` with per monitor `{"current", "lost", "source", "button_enabled", "reason", "auto"}`.
- Layout (like `TrayPage`: a scroll with one `GlassPanel` per monitor of `TrackerState.load().cycles`): `CardTitle(monitor, playlist)`; line `"Now: {seen}/{total}"`; line `"Thrown away {ended}: {seen}/{total} · list {saved by the tracker|from file access times}"` or the refusal reason; `AccentButton("Restore")` with accessible name `"Restore the pass on {monitor}"`, disabled when not restorable; `Toggle("Restore automatically")` bound to `set_auto`. Restore asks `ConfirmDialog("Restore the pass on {monitor}?", "Wallpaper Engine closes for a moment, the pass it threw away is put back, and it starts again.", confirm_text="Restore")`, then runs a `RestoreWorker`; the button shows busy while it runs; the result goes to `run.finish("clean", msg)` / `run.fail(msg)` and `refresh()`.
- `find_restorable` reads access times on W: — call it in `refresh()` from a `QThread`/`threading.Thread`, not inline on the GUI thread; the panel shows "Checking…" until then.
- `load_fixture("idle")`: two made-up monitors (one restorable 168/194 from access times, one not), no machine data.

- [ ] **Step 1: Write the failing tests:** fixture state renders two panels with the expected `texts()`; the button disabled with the reason on the non-restorable one; toggling the switch writes `restore / auto.Monitor1` to a temp `Settings`; a refused `ConfirmDialog` (`_answer` → False) starts no worker; `PAGE_SHORTCUTS` has no `"restore"` and `"reserve" == "Ctrl+0"`.
- [ ] **Step 2: Run** `tests\test_restore_page.py` — FAIL.
- [ ] **Step 3: Implement** the page and the registrations.
- [ ] **Step 4: Run** `tests\test_restore_page.py`, `tests\test_keyboard.py`, `tests\test_shell.py`, then `python run_app.py --selfcheck` — all pass.
- [ ] **Step 5: Snapshot** `.venv\Scripts\python.exe tools\ui_snapshot.py --page restore --state idle` (see the Claude-design memory for offscreen fonts) and look at it.
- [ ] **Step 6: Commit** `git commit -m "Add the Restore page"`

### Task 7: Docs, changelog, PR

**Files:**
- Create: `docs/restore.md` (what WE does to passes, the button, the switch, the 60 s / 10 min rules, where the backup goes)
- Modify: `docs/README.md` (link it with the other Utilities), `CHANGELOG.md` (`### Added` under `## [Unreleased]`)

- [ ] **Step 1:** Write the docs and changelog entry.
- [ ] **Step 2:** Run every `tests\test_*.py` and `tools\release.py check --base origin/main` — all pass.
- [ ] **Step 3:** Safety audit of the diff (credentials, the design mock-ups' sample names), push `pass-restore`, open the PR, read CI.
- [ ] **Step 4:** After the user merges and installs: open Restore, press Restore on Monitor1 → expected `"Pass restored on Monitor1: 170/194."` (± what was shown meanwhile), cross-checked against `.tmp/monitor1-pass-270bd142.json`; confirm the deck in `playliststate.bin` and that Monitor0 is untouched.
