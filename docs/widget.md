# Widget

Open **Utilities > Widget** (Ctrl+7) and turn on **Show widget**. The widget
starts the background tracker if needed and keeps working with the main
Toolkit window closed. Hiding it leaves the tracker counting.

**Compact** is the default. It shows the monitor selected for the tray in
**Settings > Tracker and tray**, with the monitor name, playlist name, number
shown, percentage and a numeric countdown. **Expanded** shows the same
information for every monitor known to Wallpaper Engine. There are no previews.

The countdown ring starts full and empties towards the next wallpaper change.
It freezes while paused. Running, paused, unknown and finished use the same
states and colours as the tray. `≈` marks an estimate; an unavailable timer
shows a dash. A completed playlist retains its green finished state.

Change mode from this page or the widget's header. The header also opens its
controls and hides the widget. Drag the header to move it between monitors.
Windows handles the drag and display scaling; the stored position is relative
to the screen's available area. Resolution or scaling changes keep it within
that area, and removing its screen returns it to an available one. A long
expanded view scrolls to keep its controls reachable.

**Start with Windows** is independent of the tracker's existing startup
switch. It registers a per-user Run entry for `--widget-startup`, which
shows the widget only while **Show widget** is enabled. Opening Toolkit also
restores an enabled widget. Closing the widget turns **Show widget** off.

From source, `python run_app.py --widget` opens the widget without opening
the main window. It shares the tray's existing tracker and timer; another
launch asks that process to show its widget. Updating or uninstalling Toolkit
closes both together and removes this copy's widget startup entry.
