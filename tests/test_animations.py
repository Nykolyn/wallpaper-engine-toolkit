"""Motion, checked by watching it actually move.

Run it directly (needs Qt, but no Wallpaper Engine and no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_animations.py

Animations are the kind of thing that breaks silently — a property renamed, a
recursion guard lost, an effect left attached, a loop that keeps a timer running
for a page nobody is looking at — and none of it shows up in a screenshot. So
each check samples the real widget over real time.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

app = QApplication(sys.argv)

from app import animations, theme                     # noqa: E402

theme.apply(app)

# The machine running this may have Windows' animations off; the checks below
# are about the motion itself, so it is switched on for them.
animations.ENABLED = True

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def wait(ms: int) -> None:
    """Run the event loop for a while so animations get to advance."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def sample(widget, ms: int, every: int = 20) -> list[int]:
    """Values a progress bar passes through while it animates."""
    seen = []
    for _ in range(max(ms // every, 1)):
        seen.append(widget.value())
        wait(every)
    seen.append(widget.value())
    return seen


# ---- the tokens

check("the three durations are the design's 90 / 140 / 220 ms",
      (animations.FAST, animations.BASE, animations.SLOW) == (90, 140, 220))
check("a count's flash lasts four slow beats", animations.FLASH == 4 * animations.SLOW)
curve = animations.ease()
check("ease.standard starts at 0 and lands on 1",
      curve.valueForProgress(0.0) == 0.0 and abs(curve.valueForProgress(1.0) - 1.0) < 1e-6)
check("and is quick off the mark: most of the way there by the halfway point",
      curve.valueForProgress(0.5) > 0.8)
samples = [curve.valueForProgress(i / 20) for i in range(21)]
check("without overshooting or going back",
      all(b >= a - 1e-6 for a, b in zip(samples, samples[1:])) and max(samples) <= 1.0 + 1e-6)
check("Windows' animation switch can be read", isinstance(animations.reduced_motion(), bool))


host = QWidget()
host.resize(400, 200)
host.show()
wait(60)


# ---- the progress bar eases instead of jumping

bar = animations.SmoothProgressBar(host)
bar.setRange(0, 100)
bar.setValue(0)
bar.show()
wait(40)

bar.setValue(100)
path = sample(bar, animations.SLOW + 140)
check("a forward jump is interpolated, not applied at once",
      any(0 < v < 100 for v in path))
check("the values only ever move forward",
      all(b >= a for a, b in zip(path, path[1:])))
check("it lands exactly on the target", bar.value() == 100)

# Backwards is a run starting over — that must be immediate, not a slow rewind.
bar.setValue(0)
check("a reset to zero is immediate", bar.value() == 0)

bar.setValue(60)
wait(animations.SLOW + 140)
bar.setValue(61)
check("a one-step change does not bother animating", bar.value() == 61)

hidden = animations.SmoothProgressBar()
hidden.setRange(0, 100)
hidden.setValue(90)
check("a bar nobody can see is set outright", hidden.value() == 90)


# ---- fading in cleans up after itself

page = QWidget(host)
page.resize(200, 100)
page.show()

# Deliberately slow, so a check on what the fade is doing mid-flight cannot be
# missed just because the machine stalled for a moment under load.
SLOW_FADE = 400
animations.fade_in(page, duration=SLOW_FADE)
opacities = []
for _ in range(8):
    effect = page.graphicsEffect()
    opacities.append(effect.opacity() if effect else None)
    wait(SLOW_FADE // 10)

check("a fade attaches an opacity effect", any(o is not None for o in opacities))
seen_values = [o for o in opacities if o is not None]
check("and it comes up from transparent", bool(seen_values) and min(seen_values) < 1.0)
check("rising as it goes",
      all(b >= a for a, b in zip(seen_values, seen_values[1:])))
wait(SLOW_FADE)
check("the effect is dropped once the fade ends, so later repaints stay cheap",
      page.graphicsEffect() is None)


# ---- a flash settles back to the resting colour

label = QLabel("193/208", host)
label.show()
animations.flash(label, "accent", duration=260)
wait(40)
check("a flash colours the label", "color:" in label.styleSheet())
wait(500)
settled = label.styleSheet()
check("and it fades back to the resting text colour",
      theme.css("text.body").lower() in settled.lower())


# ---- tabs fade their pages in

was_base = animations.BASE
animations.BASE = SLOW_FADE          # same reason: a window wide enough to see
try:
    tabs = animations.FadingTabWidget()
    first, second = QWidget(), QWidget()
    tabs.addTab(first, "one")
    tabs.addTab(second, "two")
    tabs.resize(300, 200)
    tabs.show()
    wait(60)
    tabs.setCurrentIndex(1)
    wait(SLOW_FADE // 8)
    check("switching tabs fades the page that arrives",
          second.graphicsEffect() is not None)
    wait(SLOW_FADE * 2)
    check("and that effect is cleaned up too", second.graphicsEffect() is None)
finally:
    animations.BASE = was_base


# ---- loops share one clock, and only run while seen

class Watcher(QWidget):
    """Counts its repaints, the way a spinner would be asked to redraw."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.paints = 0
        self.resize(20, 20)

    def paintEvent(self, event):         # noqa: N802 - Qt's name
        self.paints += 1


spin = animations.loop("spin")
check("one driver per kind of loop", animations.loop("spin") is spin)
try:
    animations.loop("wobble")
    check("an unknown loop is refused", False)
except KeyError:
    check("an unknown loop is refused", True)
check("a driver with nobody watching has no timer running", not spin.running)

page = QWidget(host)
page.resize(100, 100)
page.show()
seen = Watcher(page)
seen.show()
wait(30)
spin.subscribe(seen)
check("a visible subscriber starts it", spin.running)
before = seen.paints
wait(250)
check("and is repainted while it runs", seen.paints - before >= 5)
angles = []
for _ in range(12):
    angles.append(spin.value())
    wait(40)
check("a spinner turns through the whole circle, and only that",
      all(0 <= a < 360 for a in angles) and len({round(a) for a in angles}) > 6)

page.hide()
check("hiding the page it is on stops the clock", not spin.running)
before = seen.paints
wait(150)
check("and nothing hidden is repainted", seen.paints == before)
page.show()
wait(30)
check("showing it again starts it", spin.running)
spin.subscribe(seen)
spin.unsubscribe(seen)
check("unsubscribing the last watcher stops it", not spin.running)

pulse, shimmer, sweep = (animations.loop(k) for k in ("pulse", "shimmer", "indeterminate"))
values = {"pulse": [], "shimmer": [], "indeterminate": []}
for _ in range(45):
    values["pulse"].append(pulse.value())
    values["shimmer"].append(shimmer.value())
    values["indeterminate"].append(sweep.value())
    wait(40)
check("the live dot breathes between .35 and full",
      min(values["pulse"]) >= 0.35 - 1e-6 and max(values["pulse"]) <= 1.0 + 1e-6
      and max(values["pulse"]) - min(values["pulse"]) > 0.4)
check("a skeleton shimmers between .4 and .85",
      min(values["shimmer"]) >= 0.4 - 1e-6 and max(values["shimmer"]) <= 0.85 + 1e-6
      and max(values["shimmer"]) - min(values["shimmer"]) > 0.25)
check("the indeterminate sweep travels its whole track",
      min(values["indeterminate"]) < 0.2 and max(values["indeterminate"]) > 0.8)
check("skeleton rows can be staggered against each other",
      abs(shimmer.value() - shimmer.value(delay=400)) > 0.01
      or abs(shimmer.value(delay=200) - shimmer.value(delay=600)) > 0.01)

gone = Watcher(page)
gone.show()
sweep.subscribe(gone)
check("a sweep runs for its watcher", sweep.running)
gone.deleteLater()
wait(80)
check("and stops once that watcher is deleted", not sweep.running)


# ---- motion can be switched off wholesale

animations.ENABLED = False
try:
    plain = animations.SmoothProgressBar(host)
    plain.setRange(0, 100)
    plain.show()
    wait(30)
    plain.setValue(100)
    check("with motion off the bar jumps straight there", plain.value() == 100)

    quiet = QWidget(host)
    quiet.show()
    animations.fade_in(quiet)
    check("with motion off nothing is faded", quiet.graphicsEffect() is None)

    still = Watcher(host)
    still.show()
    wait(30)
    pulse.subscribe(still)
    check("with motion off a loop does not run for a visible watcher", not pulse.running)
    check("and stands on its resting frame: a live dot fully lit",
          pulse.phase() == 0.0 and pulse.value() == 1.0)
    pulse.unsubscribe(still)
finally:
    animations.ENABLED = True


print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
