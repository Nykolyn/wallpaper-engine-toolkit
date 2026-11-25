# Cleaner: wallpapers outside every playlist — design

**Why.** The owner subscribes to new wallpapers daily, adds the ones they like
to a playlist, and unsubscribes from the rest by hand. This automates the last
step: list the subscribed wallpapers of a folder that are in no playlist, and
unsubscribe and recycle them.

**Goals.**

- A second tab on the Cleaner page: **Unavailable** (today's table) and
  **Outside playlists**.
- Candidates = subscribed Workshop folders (`workshop/content/431960/<id>` with
  `project.json`) ∩ the chosen folder ∩ in no playlist.
- "In a playlist" = in any saved playlist (`general.playlists[].items`) or any
  monitor's running copy (`wallpaperconfig.selectedwallpapers.*.playlist`), for
  every user section of `config.json`. No playlist is named.
- Folder dropdown: Wallpaper Engine's folders plus **All wallpapers** (every
  subscribed one). Default from Review's source; changeable by hand.
- Same table, selection and Trash / Delete selected as the Unavailable tab, via
  `delete_wallpaper`; same one-action-at-a-time and rotation lock.
- **Re-read before unsubscribing** (owner's choice): right before acting, the
  worker re-reads `config.json` and drops every id that is now in a playlist,
  saying how many. The confirm dialog shows when Wallpaper Engine last saved
  `config.json`.

**Non-goals.** No "move to reserve" on this tab (these are unwanted). No
restart of Wallpaper Engine. No Steam call for the scan. Reserve and
`myprojects` are not scanned. Unsubscribed ids remembered by a folder are
ignored.

**Anti-goals.** Unsubscribing a wallpaper the owner had put in a playlist.

**Approaches considered.**

1. *Two tabs on Cleaner* — **picked**: reuses the table, selection, actions and
   log; the owner suggested it.
2. A separate Utilities page — duplicates the whole table and its actions.
3. A button in Review — Review is about authors; it has no table of folders.

**Constraint.** Wallpaper Engine writes `config.json` with a delay (seen: running
since 16:25, last write 20:02). The re-read narrows the window; it cannot help
when Wallpaper Engine has not written at all. That is why the save time is shown.

**My calls (correct me).**

- Review source `folder:X` → folder X; "All folders", "Not in any folder" and
  "Everything" → **All wallpapers**.
- **Owner:** the hand choice is remembered (Settings `cleaner.outside_folder`);
  Review's source is used only while nothing is remembered, or when the
  remembered folder no longer exists.
- **Owner:** a wallpaper a monitor shows without a playlist is *not* kept —
  only playlists count.
- The scan is local only (fast): it runs the first time the tab is opened and on
  **Check again**.
- The sidebar badge keeps counting only unavailable wallpapers.
- Docs: a section in `docs/cleaner.md`, a decision note for "re-read, not
  restart", a CHANGELOG line under Unreleased, one engine test script.
