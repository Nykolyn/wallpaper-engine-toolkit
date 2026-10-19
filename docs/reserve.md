# To reserve

**Utilities → To reserve** (Ctrl+0) keeps copies of subscribed wallpapers in the
same reserve used by Rotator. Set that folder in Settings first, and keep Steam
running so the app can verify the current account's subscriptions.

Opening the page scans in the background. It looks at the Workshop library,
independently of Wallpaper Engine's folders and playlists. Only active,
fully downloaded wallpaper projects with readable `project.json` and nonempty
content are listed. Copies already in the reserve are excluded by Workshop ID,
including renamed folders. A folder named after the same Workshop ID also blocks
another copy, so nothing is overwritten.

The table shows a preview, title, author, type and folder size. Author names use
the existing Steam cache; missing names are looked up through the same Steam
client as Review. A name Steam cannot supply is shown as a dash and does not
prevent copying. The footer shows the total size and the size of the selection.

- **Copy all to reserve** copies the whole list.
- **Copy selected** copies checked rows; **Select all**, Ctrl+A and Space work as
  in the other tables.
- The **+** on a row copies that wallpaper. Clicking its title opens its folder.
- **Refresh** checks subscriptions and the reserve again.
- **Stop** interrupts scanning or copying. Completed copies remain in the reserve,
  and unfinished rows remain in the list.

Copying preserves the Workshop originals and subscriptions. Progress shows the
current wallpaper, bytes and wallpaper count, both on this page and in the shared
status line, refreshed up to five times a second. Scanning and copying use
background CPU, disk and memory priority. Copies are capped at **16 MiB/s** so
they leave room for games and other disk activity; animated previews pause during
the operation. Each copy is staged beside the reserve, checked against the source
file list and sizes, then published without replacing an existing folder. Failed
or cancelled copies are cleaned up; any cleanup failure reports the location.
Failed rows keep their error message for retry.

Rotator and Cleaner cannot change the reserve during a copy. Reserve settings
are read-only until that job finishes. The utility has no scheduling, rotation,
unsubscribe or source deletion options.
