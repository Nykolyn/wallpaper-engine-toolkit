# Tray

Open **Tools → Tray** to customize the Toolkit tray icon. Changes are saved
immediately and applied by the running tracker on its next tick.

- **Main rectangle color** controls the front rectangle's running fill.
- **Additional rectangle color** controls the back rectangle's running fill.
- Enter a `#RRGGBB` color or use **Choose…**.
- **Progress** defaults to the original single fill across both rectangles,
  measuring time until the next wallpaper. Alternatively assign wallpaper
  progress to either rectangle and playlist progress to the other. Each
  rectangle then fills independently from left to right.
- **Reset to defaults** restores the original colors and combined fill.

The preview uses example values, with labeled running, paused, timer unknown
and finished states. Paused, unknown and finished keep their status colors
and badges. The monitor selected in Settings supplies both progress readings.
Playlist progress uses wallpapers shown out of the playlist total.

Preferences are stored under `tray` in `suite.json`: `front_color`,
`back_color`, and `progress` (`combined`, `front_wallpaper`, `back_wallpaper`).

In separate modes, progress sweeps the visible width: inside the front
rectangle border, and across the rear outline. For example, 7:30 remaining
of a 10:00 interval is 25% elapsed. Each supported tray size is rounded to
whole display pixels (at most one pixel from the exact position); 0% stays
empty and 100% fills completely. The rear outline has a different colored
area because it is a frame partially covered by the front rectangle.
