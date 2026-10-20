# Widget

Open **Utilities > Widget** (Ctrl+7) and turn on **Show widgets**. The widget
starts the background tracker if needed and keeps working with the main
Toolkit window closed. Hiding widgets leaves the tracker counting. The tray
also offers **Show widgets / Hide widgets** and **Widget…** to open the controls.

**Compact** is the default view. Its default **Rectangles** design uses two
overlapping Liquid Glass lenses. The rear shows the percentage of the playlist
viewed and the front the countdown to the next wallpaper. Both fill from left
to right with a tint as progress elapses, while the countdown decreases.

Choose **Rings** for a circular glass lens and numeric countdown. Its outer ring
shows time remaining until the next wallpaper; the inner ring shows the share
of the playlist still unseen. Both empty towards completion. Design and
compact/expanded view are separate choices.

Running, paused, unknown and finished follow the tray's states. `≈` marks an
estimate; an unavailable timer shows a dash. A paused timer freezes.
Compact keeps the state text in its tooltip and accessibility description;
Expanded also shows the state beside each monitor.

**Expanded** uses compact monitor rows: monitor and playlist names, countdown,
playlist progress, and the current wallpaper's name and file size. Size refers
to the actual wallpaper file in the playlist, not its folder. Missing file
information is marked as unknown. Names and sizes load on a worker and are cached for
the session; there are no previews.

Both designs share the rounded **Glass Panel** in expanded mode, with a status
dot beside each monitor and more room for the wallpaper title and size.

The glass is painted by the widget itself: translucent smoke, light rims, a
highlight along the top and a soft shadow, with smooth edges at any display
scaling. Only the lenses, circle or panel are drawn; everything around them is
fully transparent and lets clicks through. Windows cannot blur behind a shape
like two overlapping lenses, so the desktop shows through the glass unblurred;
the smoke is dense enough to keep the widget readable over bright wallpapers.
The widget does not take desktop screenshots.

The material uses a fixed pearl-blue and lilac palette, with muted paused and
green completed states. Colours and material transparency are fixed across all
profiles; old saved colour and opacity values no longer affect rendering.
Countdowns use bold proportional numerals, and soft text shadows keep labels
legible on bright wallpapers.

Choose a **single widget** or **one per monitor**. The single widget follows
the monitor selected for the tray by default; it can also be bound to a
specific monitor. In expanded mode it can show all monitors. Each per-monitor
widget stays bound to its own monitor and has independent visibility,
appearance and saved position. Turning off global visibility keeps those
individual preferences for the next time widgets are shown.

For each widget, choose a design, compact/expanded view, small/medium/large
compact size and a movement lock. Each monitor remembers its own choices. Reset restores
the default rectangle design, appearance and position while retaining its
monitor binding and visibility.

The window layer defaults to **On desktop**, behind ordinary application
windows. **Normal** behaves like a normal window; **Always on top** stays above
ordinary windows. These choices are independent for each widget.

Right-click a widget for its controls, or open the same menu with the Menu key
or Shift+F10. Double-click switches compact/expanded mode. Drag an unlocked
widget to move it, or use Alt+arrow keys. Positions are stored relative to a screen's available area;
Windows handles display scaling. Resolution or scaling changes keep the widget
reachable, and removing its screen returns it to an available one.

**Start with Windows** is independent of the tracker's existing startup
switch. It registers a per-user Run entry for `--widget-startup`, which
shows widgets only while **Show widgets** is enabled. Opening Toolkit also
restores enabled widgets. Hiding a per-monitor widget affects only that monitor;
hiding the single widget turns off global visibility.

From source, `python run_app.py --widget` shows widgets without opening
the main window. It shares the tray's existing tracker and timer; another
launch asks that process to show its widgets. Updating or uninstalling Toolkit
closes both together and removes this copy's widget startup entry.
