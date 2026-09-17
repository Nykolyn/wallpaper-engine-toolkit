"""Motion, checked by watching it actually move.

Run it directly (needs Qt, but no Wallpaper Engine and no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_animations.py

Animations are the kind of thing that breaks silently — a property renamed, a
recursion guard lost, an effect left attached — and none of it shows up in a
screenshot. So each check samples the real widget over real time.
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
path = sample(bar, animations.NORMAL + 140)
check("a forward jump is interpolated, not applied at once",
      any(0 < v < 100 for v in path))
check("the values only ever move forward",
      all(b >= a for a, b in zip(path, path[1:])))
check("it lands exactly on the target", bar.value() == 100)

# Backwards is a run starting over — that must be immediate, not a slow rewind.
bar.setValue(0)
check("a reset to zero is immediate", bar.value() == 0)

bar.setValue(60)
wait(animations.NORMAL + 140)
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
      theme.C["text"].lower() in settled.lower())


# ---- tabs fade their pages in

was_fast = animations.FAST
animations.FAST = SLOW_FADE          # same reason: a window wide enough to see
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
    animations.FAST = was_fast


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
finally:
    animations.ENABLED = True


print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
