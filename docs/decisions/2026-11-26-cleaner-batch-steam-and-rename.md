---
date: 2026-11-26
status: accepted
source: "owner, in chat"
tags: [engine, steam]
---

# Cleaner asks Steam once per selection, and moves to the reserve by renaming

**Decision.** Cleaner's selection actions open Steam once for the whole
selection: every unsubscription is requested first, then they are awaited
together. Deleted folders still go to the Recycle Bin, one by one.
**Move to reserve** renames the folder when the reserve is on the Workshop
folder's volume: out of the Workshop folder into a holding folder beside the
reserve, then Steam is asked to unsubscribe, then the folder is renamed into
the reserve. If Steam will not let go, the folder is renamed back. On another
volume the verified copy and recycle stay as they were.

**Why.** Deleting 66 wallpapers took 44 s (~0.7 s each), almost all of it in
opening and closing Steam and waiting for it per item. A move on the same disk
copied every byte only to recycle the original: a slow copy of gigabytes on a
hard disk, for a result a rename gives at once. Leaving deletion to Steam
instead of recycling was considered and rejected (owner): Steam removes an
unsubscribed item only after Wallpaper Engine quits, so the folder would
reappear in Cleaner's lists until then, and the Recycle Bin is the only way
back for a wallpaper Workshop no longer has.

**Consequences.** Steam still drops the items one after another (~0.4 s
each), so the wait ends only after 5 s in which Steam dropped none: a first
cap that grew with the selection gave up on 29 of 57. A move on one volume leaves nothing in the Recycle Bin: the
wallpaper is in the reserve. A folder Wallpaper Engine is showing refuses the
rename before Steam is asked anything, so nothing is unsubscribed. If putting
a folder back or into the reserve fails, the error names the holding folder
that keeps it. The Tracker's and Rotator's single deletes are unchanged. See
[Cleaner](../cleaner.md).
