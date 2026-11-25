# Cleaner: outside playlists — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A second Cleaner tab that lists subscribed wallpapers of a folder that are in no playlist, and unsubscribes and recycles them.

**Architecture:** A local-only engine module reads `config.json` (playlists, folders) and the Workshop folder; the Cleaner page gets a `SegmentedControl` that switches its one table between the existing Steam check and the new scan. The delete worker re-reads playlists right before each batch.

**Tech Stack:** Python, PySide6, the project's kit widgets; tests are plain `unittest` scripts run with `.venv\Scripts\python.exe`.

**Spec:** [2026-11-25-cleaner-outside-playlists-design.md](../specs/2026-11-25-cleaner-outside-playlists-design.md)

## Global Constraints

- Playlists counted: every user section's `general.playlists[].items` and `general.wallpaperconfig.selectedwallpapers.*.playlist.items`. A monitor's single `file` does **not** count.
- Only subscribed folders: `workshop/content/431960/<id>` with `project.json`. Reserve and `myprojects` never scanned.
- No Steam call in the scan; no Wallpaper Engine restart.
- This tab has Trash and Delete selected only — no reserve button.
- Remembered folder: Settings `cleaner.outside_folder`; `""` = All wallpapers.
- Default when nothing remembered (or remembered folder gone): Review scope `folder:X` → `X` if it exists, else `""`.
- Sidebar badge counts unavailable wallpapers only.
- Docs, decision note and CHANGELOG `## [Unreleased]` in the same PR; no version bump; build only via `build.cmd`.

## Review Focus

- A wallpaper added to a playlist after the scan → skipped at delete time, reported, row stays gone from the candidates only after a rescan. (Task 2 test `test_readded_to_playlist_is_skipped`.)
- `config.json` missing or unreadable at delete time → nothing is unsubscribed; error shown. (Task 2 test `test_unreadable_config_deletes_nothing`.)
- Playlist item paths with backslashes or `.pkg` stems (`…/431960/<id>/<stem>.pkg`) → id still recognised. (Task 1 test `test_item_path_forms`.)
- Remembered folder renamed/deleted in Wallpaper Engine → falls back to the Review default. (Task 1 test `test_default_folder`.)
- Switching tabs while a scan or delete runs → tab switch disabled. (Task 2 test `test_tabs_locked_while_busy`.)

---

### Task 1: Engine `app/engines/outside_playlists.py`

**Files:**
- Create: `app/engines/outside_playlists.py`
- Modify: `app/engines/unavailable.py` — extract the per-folder row builder used by `scan` into `wallpaper_row(folder: Path, author: str) -> Wallpaper` and use it there.
- Test: `tests/test_outside_playlists.py`

**Interfaces:**
- Produces:
  - `playlisted_ids(config_path: str | Path | None) -> set[str]` — raises `OSError` when the file is missing/unreadable/not JSON (the delete path must not proceed on a guess). `None` → `review.find_we_config()`.
  - `saved_at(config_path) -> datetime | None` — `config.json` mtime.
  - `@dataclass OutsideScan: rows: list[Wallpaper]; folders: dict[str, list[str]]; saved: datetime | None`
  - `scan(*, config_path=None, workshop=None, cache_path=None) -> OutsideScan` — rows = subscribed ids not in `playlisted_ids`, sorted by id; authors via `wallpaper_meta.author_names(ids, cache_path)`; `folders` via `review.we_folders`.
  - `default_folder(remembered: str | None, review_scope: str, folders: Iterable[str]) -> str`

- [ ] **Step 1: Write failing tests** on a temp Workshop tree + fabricated `config.json` (two users, saved playlist, running playlist, a monitor with only `file`):
  - `test_scan_lists_only_unplaylisted_subscribed`: ids `1`(saved playlist), `2`(running playlist), `3`(only monitor `file`), `4`(none), `5`(in folder `all` but no folder on disk) → `[r.folder name for r in rows] == ["3", "4"]`; `folders["all"]` contains `"5"`.
  - `test_item_path_forms`: items `"X:/a/431960/7/project.json"`, `"X:\\a\\431960\\8\\scene.pkg"`, `"myprojects/9/project.json"` → `playlisted_ids == {"7", "8"}` (a non-Workshop path is not an id).
  - `test_unreadable_config_raises`: missing path and `"{"` content → `OSError`.
  - `test_default_folder`: `("all", "folder:new", ["all","new"]) == "all"`; `("gone", "folder:new", ["new"]) == "new"`; `(None, "@folders", ["new"]) == ""`; `(None, "folder:x", []) == ""`; `("", "folder:new", ["new"]) == ""` (an explicit All is remembered).
- [ ] **Step 2:** Run `.venv\Scripts\python.exe tests\test_outside_playlists.py` — expect ImportError.
- [ ] **Step 3:** Implement. Id from an item path: normalise `\` → `/`, take the segment after `431960`, keep it if `isdigit()`.
- [ ] **Step 4:** Run the new test and `tests\test_cleaner.py` — all pass.
- [ ] **Step 5:** Commit `Add the outside-playlists scan`.

### Task 2: Cleaner tabs

**Files:**
- Modify: `app/pages/cleaner.py`
- Test: `tests/test_cleaner.py` (new test class)

**Interfaces:**
- Consumes: Task 1's `scan`, `playlisted_ids`, `saved_at`, `default_folder`.
- Produces: `CleanerPage.tabs: SegmentedControl(("Unavailable", "Outside playlists"))`, `CleanerPage.mode -> "unavailable" | "outside"`.

- [ ] **Step 1: Write failing tests** (patch `outside_playlists.scan` / `playlisted_ids`, `_answer` → True, `delete_wallpaper` → fake):
  - `test_outside_tab_scans_once_and_filters_by_remembered_folder`: settings `cleaner.outside_folder="all"`; switching tab runs scan once; dropdown current data `"all"`; choosing `"new"` stores `"new"` in settings and does not rescan.
  - `test_outside_rows_have_no_reserve_button`.
  - `test_readded_to_playlist_is_skipped`: rows `3`,`4`; `playlisted_ids` returns `{"3"}` at delete → only `4` deleted; message contains `"1 now in a playlist"`; row `3` stays.
  - `test_unreadable_config_deletes_nothing`: `playlisted_ids` raises `OSError` → fake delete never called; danger message.
  - `test_tabs_locked_while_busy`: during scan/act `tabs.isEnabled()` is False.
  - `test_badge_counts_unavailable_only`: outside rows don't change the nav badge.
- [ ] **Step 2:** Run `tests\test_cleaner.py` — new tests fail.
- [ ] **Step 3:** Implement:
  - Tabs in the toolbar left of the dropdown. Each mode keeps its own rows, folders, checked state and selected folder; `_filter`/`_render` read the active mode's. Unavailable dropdown keeps "All wallpapers" + folders, not remembered. Outside dropdown starts at `default_folder(settings.get("cleaner","outside_folder",None), review scope from settings ("review","scope"), folders)`; changes are saved with `settings.set`.
  - First switch to Outside starts the scan; **Check again** rescans the active mode.
  - `reserve_selected` hidden on Outside; `WallpaperModel` gives only the trash button there.
  - Callout and empty-state words per mode (Outside: "Subscribed wallpapers in this folder that are in no Wallpaper Engine playlist.").
  - Delete confirm on Outside adds the line `Wallpaper Engine last saved its settings at <YYYY-MM-DD HH:MM>` (or `…has not saved them yet` if `None`).
  - Worker on Outside: first `playlisted_ids(config_path)` (an `OSError` fails the whole batch before any delete), skip ids in it, delete the rest; final words append `"<n> now in a playlist, kept."`.
  - Fixtures: add `"outside"` state with two made-up rows.
- [ ] **Step 4:** Run `tests\test_cleaner.py`, `tests\test_outside_playlists.py`, `.venv\Scripts\python.exe run_app.py --selfcheck` — all pass.
- [ ] **Step 5:** Commit `Cleaner: Outside playlists tab`.

### Task 3: Vault and changelog

**Files:**
- Modify: `docs/cleaner.md` (new section "Outside playlists"), `docs/configuration.md` (setting `cleaner.outside_folder`), `docs/README.md` (Cleaner row wording), `CHANGELOG.md` (`## [Unreleased]`)
- Create: `docs/decisions/2026-11-25-reread-playlists-before-unsubscribe.md` + row in `docs/decisions/README.md`

- [ ] **Step 1:** Write the pages: what counts as a playlist, the folder default and memory, the re-read, the save time line, that a monitor's single wallpaper is not kept. Decision note: re-read chosen over restarting Wallpaper Engine; why; consequence (cannot help before WE writes). No personal paths or names.
- [ ] **Step 2:** Run all tests in `tests\` and `--selfcheck` — pass.
- [ ] **Step 3:** Commit `Document the Outside playlists tab`.
