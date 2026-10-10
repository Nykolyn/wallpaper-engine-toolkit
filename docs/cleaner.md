# Cleaner

**Downloaded Workshop wallpapers whose Steam records are unavailable.**

Cleaner is the first page under **Utilities** (Ctrl+5). Its sidebar chip is
the total number of wallpapers still to process, including those hidden by
the current folder filter. A completed check with no matches shows **0**.

The check starts automatically when the application window opens. It lists
downloaded Workshop folders with `project.json`, then asks Steam for item
details in batches of up to 200. Cached answers are reused for at most one
day; **Check again** forces a fresh check. No Steam Web API key is needed.
Disk and network work runs off the window's thread; local metadata and sizes
are read only for matches, and previews are loaded only for visible rows.

Steam must explicitly report FileNotFound, AccessDenied or ItemDeleted to
put an item in this table. These codes mean the record is unavailable to the
public API; they do not establish why it disappeared. Timeouts, connection
failures and other inconclusive answers do not become cleanup candidates.
If a check fails, previous results remain visible with an error notice.

Choose **All wallpapers** (the default) or a Wallpaper Engine browser folder
above the table. Folder membership comes from Wallpaper Engine's saved
`config.json`; it may lag changes made before Wallpaper Engine saves it.
The reserve and `myprojects` copies are not scanned: they do not need a live
Workshop record to remain usable.

| Column | Meaning |
|---|---|
| FOLDER | Local preview, wallpaper title and Workshop folder ID. Hover for its full path. |
| AUTHOR | The author's last known name from the shared Steam cache, or — when unknown. |
| TYPE | Type from the local `project.json`. |
| SIZE | Total size of the folder's files, including its preview. |
| DATE | Windows folder creation date and time in your local timezone; not a verified subscription date. |
| Actions | Trash unsubscribes and recycles; + moves to the reserve. |

Click a row, or focus it and press Enter, to open its folder in Explorer.
Column headings sort the table; SIZE and DATE sort by their actual values.

**Trash** asks before unsubscribing through the running Steam client and
sending the folder to the Windows Recycle Bin, as Tracker and Rotator do.
If Steam cannot unsubscribe, the source stays on disk.

**+** uses the Rotator's reserve folder, configured in Settings. It asks
first, checks available space, copies into a temporary sibling folder,
verifies the source and destination file lists and sizes, then publishes
the copy under its Workshop ID. Only then is the Workshop subscription
dropped and the source recycled. A matching destination name is an error;
nothing is overwritten. If removing the source fails after copying, the
verified reserve copy is kept and the error reports its path.

Only one Cleaner action runs at a time. Rotation and Cleaner actions wait
for each other, so the Rotator cannot take a wallpaper while it is moving.
Successful actions remove the row and decrease the sidebar count.

Steam result meanings: [Steamworks EResult documentation](https://partner.steamgames.com/doc/api/steam_api#EResult).
