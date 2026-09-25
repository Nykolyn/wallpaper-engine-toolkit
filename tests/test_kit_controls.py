"""The kit's controls: buttons, fields, selection controls, chips and panels.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_kit_controls.py

Every class is built in every state and drawn. Beyond that, the rules the
design states and a page would otherwise have to remember: the fourteen chip
variants and no more, the Pagination's four numbers, two or three segments,
a Toggle that jumps when motion is off, Dropdown rows that cannot be chosen,
a DangerButton that never takes Enter, and a focus ring for the keyboard only.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer              # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter                    # noqa: E402
from PySide6.QtTest import QTest                                       # noqa: E402
from PySide6.QtWidgets import (                                        # noqa: E402
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QVBoxLayout, QWidget,
)

app = QApplication(sys.argv)

from app import animations, theme                                      # noqa: E402

theme.apply(app)
animations.ENABLED = True

from app.ui import kit                                                 # noqa: E402
from app.ui.kit import (                                               # noqa: E402
    AccentButton, Callout, CardTitle, Checkbox, Chip, DangerButton, Dropdown,
    GhostButton, GlassPanel, IconButton, MetricStrip, Overline, Pagination,
    SecondaryButton, SegmentedControl, SpinBox, TextInput, Toggle, chip_pixmap,
    chip_size, page_numbers,
)
from app.ui.kit import base, chips                                     # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def raises(fn, error=Exception) -> bool:
    try:
        fn()
    except error:
        return True
    return False


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class Host(QWidget):
    """A surface to put controls on, painted like the app: bg.app, then what
    the controls draw outside themselves. Placed away from the offscreen
    pointer, which sits at (0, 0) and would hover whatever is there."""

    def __init__(self):
        super().__init__()
        base.declare(self)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(24, 24, 24, 24)
        self.setGeometry(400, 400, 520, 160)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        theme.paint_app_background(painter, self.rect())
        base.paint(painter, self, event.rect())


def drawn(widget: QWidget) -> bool:
    """The widget drew something: its picture is not one flat colour."""
    image = widget.grab().toImage().convertToFormat(QImage.Format_ARGB32)
    if image.isNull() or image.width() == 0:
        return False
    first = image.pixelColor(0, 0)
    step = max(1, image.width() // 40)
    return any(image.pixelColor(x, y) != first
               for x in range(0, image.width(), step) for y in range(image.height()))


def lightness_at(widget: QWidget, point: QPoint) -> QColor:
    return widget.grab().toImage().pixelColor(point)


# ---- every class, every state -------------------------------------------------------------

def dropdown() -> Dropdown:
    d = Dropdown(prefix="Sort")
    d.add_section("Your folders")
    d.add_item("all", count=12547)
    d.add_item("new", count=3366)
    d.add_separator()
    d.add_item("Everything you have")
    return d


INTERACTIVE = {
    "AccentButton": lambda: AccentButton("Start run", icon="play"),
    "SecondaryButton": lambda: SecondaryButton("Playlist settings"),
    "DangerButton": lambda: DangerButton("Delete 9"),
    "GhostButton": lambda: GhostButton("Open folder"),
    "GhostButton outlined": lambda: GhostButton("Skip for now", outlined=True),
    "GhostButton with key cap": lambda: GhostButton("Paste path", icon="clipboard", key="Ctrl+V"),
    "IconButton": lambda: IconButton("refresh", "Refresh"),
    "IconButton sm": lambda: IconButton("chevR", "Next page", size="sm"),
    "TextInput": lambda: TextInput(placeholder="Filter by author…"),
    "TextInput search": lambda: TextInput("dune", search=True),
    "SpinBox": lambda: SpinBox(maximum=100_000, value=1000),
    "Dropdown": dropdown,
    "Checkbox": lambda: Checkbox("Verify after move"),
    "Toggle": lambda: Toggle("Restart Wallpaper Engine"),
    "SegmentedControl": lambda: SegmentedControl(["Queue", "Shown"]),
    "Chip": lambda: Chip("Duplicated", clickable=True),
}

host = Host()
host.show()
for name, make in INTERACTIVE.items():
    ok = True
    for state in (None, "hover", "pressed", "focus", "disabled"):
        widget = make()
        host.row.addWidget(widget)
        if state == "disabled":
            widget.setEnabled(False)
        else:
            widget.force_state = state
        app.processEvents()
        ok = ok and drawn(widget) and drawn(host)
        widget.deleteLater()
        app.processEvents()
    check(f"{name} builds and draws in all five states", ok)

pages = Pagination(pages=6, current=2)
host.row.addWidget(pages)
ok = True
for state in (None, "hover", "pressed", "focus"):
    pages.force_state = state
    app.processEvents()
    ok = ok and drawn(pages)
check("Pagination builds and draws in every state", ok)
pages.deleteLater()

STILL = {
    "GlassPanel": lambda: GlassPanel(tone="warn"),
    "Overline": lambda: Overline("The four steps"),
    "CardTitle": lambda: CardTitle("The loop", "run 38 · started 13:41"),
    "Callout": lambda: Callout("Rebuild it from here.", tone="danger", title="Old playlist"),
    "MetricStrip": lambda: MetricStrip([(1000, "moved in"), (2, "failed", "danger")]),
}
for name, make in STILL.items():
    widget = make()
    host.row.addWidget(widget)
    if isinstance(widget, GlassPanel):
        QVBoxLayout(widget).addWidget(Overline("inside"))
    app.processEvents()
    check(f"{name} builds and draws", drawn(widget))
    widget.deleteLater()
app.processEvents()

for tone in (None, "ok", "warn", "danger", "accent"):
    GlassPanel(tone=tone)
for tone in ("neutral", "info", "warn", "danger", "ok"):
    Callout("x", tone=tone)
check("GlassPanel takes its five tones, Callout its five",
      raises(lambda: GlassPanel(tone="purple"), KeyError)
      and raises(lambda: Callout("x", tone="purple"), KeyError))
check("a state that is not one of the five is refused",
      raises(lambda: setattr(SecondaryButton("x"), "force_state", "active"), ValueError))


# ---- Chip: fourteen variants and no more ---------------------------------------------------

FOURTEEN = {"NewAuthor", "Known", "Duplicated", "Unidentified", "New", "Queued", "WasYours",
            "Subscribed", "Tagged", "NeedsTags", "AutoTagged", "Copying", "Done", "Failed"}
check("the Chip variants are exactly the design's fourteen", set(chips.VARIANTS) == FOURTEEN
      and len(chips.VARIANTS) == 14)
check("an unknown variant is an error, as a widget and as a pixmap",
      raises(lambda: Chip("Sparkly"), KeyError) and raises(lambda: chip_pixmap("Sparkly"), KeyError))
own = Chip("Duplicated", "Already have")
check("the label text is independent of the variant",
      own.text() == "ALREADY HAVE" and own.variant == "Duplicated"
      and Chip("Duplicated").text() == "DUPLICATED")
check("New leads with its dot, Queued with its glyph, Unidentified is dashed",
      chips.VARIANTS["New"].lead == "dot" and chips.VARIANTS["Queued"].lead == "clock"
      and chips.VARIANTS["Unidentified"].dashed
      and chip_size("New").width() > chip_size("Known", "New").width())
first = chip_pixmap("Done", None, "default", 1.5)
check("a chip is drawn once per variant, text, state and scale",
      chip_pixmap("Done", None, "default", 1.5) is first
      and chip_pixmap("Done", None, "hover", 1.5) is not first
      and chip_pixmap("Done", None, "default", 1.0) is not first)
ring = theme.FOCUS_RING
size = chip_size("Done")
check("its pixmap has room for the selected ring on every side",
      first.width() == round((size.width() + 2 * ring) * 1.5)
      and first.height() == round((size.height() + 2 * ring) * 1.5))
check("every chip is the same height", len({chip_size(v).height() for v in FOURTEEN}) == 1)


# ---- Pagination: the four-number rule ---------------------------------------------------------

CASES = {
    (1, 1): [],
    (0, 1): [],
    (2, 1): [1, 2],
    (3, 2): [1, 2, 3],
    (4, 4): [1, 2, 3, 4],
    (6, 1): [1, 2, 3, None, 6],
    (6, 2): [1, 2, 3, None, 6],
    (6, 3): [1, None, 3, 4, None, 6],
    (6, 5): [1, None, 4, 5, 6],
    (6, 6): [1, None, 4, 5, 6],
    (5, 3): [1, None, 3, 4, 5],
    (100, 50): [1, None, 50, 51, None, 100],
    (6, 99): [1, None, 4, 5, 6],
}
for (count, current), expected in CASES.items():
    check(f"page {current} of {count} shows {expected}", page_numbers(count, current) == expected)
check("never more than four numbers",
      all(sum(n is not None for n in page_numbers(p, c)) <= 4
          for p in range(0, 40) for c in range(1, p + 1)))

pager = Pagination(pages=6, current=1)
host.row.addWidget(pager)
app.processEvents()
moved: list[int] = []
pager.page_changed.connect(moved.append)
check("at the first page the back chevron is off, the next one on",
      not pager._previous.isEnabled() and pager._next.isEnabled())
pager._next.click()
check("the next chevron moves a page and says so", pager.current() == 2 and moved == [2])
pager.set_current(6)
check("a page set by code is not reported as the user's", moved == [2] and pager.current() == 6)
check("at the last page the next chevron is off", not pager._next.isEnabled())
pager.set_pages(1)
check("below two pages it is hidden, not disabled", pager.isHidden() and pager.isEnabled())
pager.set_pages(4)
check("and comes back with a second page", not pager.isHidden())
pager.deleteLater()


# ---- SegmentedControl: two or three -------------------------------------------------------------

check("a SegmentedControl rejects one segment",
      raises(lambda: SegmentedControl(["Queue"]), ValueError))
check("and four", raises(lambda: SegmentedControl(["a", "b", "c", "d"]), ValueError))
segments = SegmentedControl(["All", "Problems", "Shown"])
host.row.addWidget(segments)
app.processEvents()
chosen: list[int] = []
segments.changed.connect(chosen.append)
segments.setFocus(Qt.TabFocusReason)
QTest.keyClick(segments, Qt.Key_Right)
QTest.keyClick(segments, Qt.Key_End)
QTest.keyClick(segments, Qt.Key_Right)
check("takes two or three, and the arrow keys move the choice within them",
      chosen == [1, 2] and segments.current_index() == 2)
segments.deleteLater()


# ---- Toggle: instant when motion is off -----------------------------------------------------------

switch = Toggle("Restart Wallpaper Engine")
host.row.addWidget(switch)
app.processEvents()
animations.ENABLED = False
switch.setChecked(True)
check("with motion off, the knob is at the end the moment it is switched",
      switch.knob_position == 1.0 and not switch.sliding)
animations.ENABLED = True
switch.setChecked(False)
check("with motion on, it slides", switch.sliding and switch.knob_position > 0.5)
wait(animations.BASE + 120)
check("and lands over motion.base", switch.knob_position == 0.0 and not switch.sliding)
switch.deleteLater()


# ---- Dropdown: section rows cannot be chosen ---------------------------------------------------------

d = dropdown()
host.row.addWidget(d)
app.processEvents()
check("a Dropdown starting with a section chooses its first real row",
      d.currentIndex() == 1 and d.currentText() == "all")
check("section and separator rows are not selectable",
      not d.is_selectable(0) and not d.is_selectable(3) and d.selectable_rows() == [1, 2, 4])
check("their items carry no flags a view could choose them by",
      d.model().item(0).flags() == Qt.NoItemFlags and d.model().item(3).flags() == Qt.NoItemFlags)
check("setting a section row is an error", raises(lambda: d.setCurrentIndex(0), ValueError)
      and raises(lambda: d.setCurrentIndex(3), ValueError) and d.currentIndex() == 1)
d.choose(0)
check("a click on a section row does nothing", d.currentIndex() == 1)
d.setFocus(Qt.TabFocusReason)
QTest.keyClick(d, Qt.Key_Down)
QTest.keyClick(d, Qt.Key_Down)
check("the arrow keys step over the separator", d.currentIndex() == 4)
QTest.keyClick(d, Qt.Key_Home)
check("Home lands on the first row that can be chosen, not the heading", d.currentIndex() == 1)
check("the chosen row's count is shown in mono, apart by a no-break space",
      d.count_text(d.currentIndex()) == "12\u00a0547")

d.showPopup()
app.processEvents()
popup = d._popup
check("the list opens under the box", popup is not None and popup.isVisible() and d.popup_open())
check("it shows focus while its list is open", d.focus_visible())
rows = popup._rows
rows.setFocus()
QTest.keyClick(rows, Qt.Key_Up)
check("in the list, the arrow keys skip the heading too", popup.hot_row() == 1)
QTest.keyClick(rows, Qt.Key_Down)
QTest.keyClick(rows, Qt.Key_Down)
check("and the separator", popup.hot_row() == 4)
activated: list[int] = []
d.activated.connect(activated.append)
QTest.keyClick(rows, Qt.Key_Return)
app.processEvents()
check("Enter chooses the highlighted row, says so and closes the list",
      d.currentIndex() == 4 and activated == [4] and not popup.isVisible())
d.deleteLater()


# ---- DangerButton: never the default ----------------------------------------------------------------

danger = DangerButton("Delete 9 permanently")
check("a DangerButton refuses to be the default button",
      raises(lambda: danger.setDefault(True), ValueError) and not danger.isDefault())
check("or to become it on focus",
      raises(lambda: danger.setAutoDefault(True), ValueError) and not danger.autoDefault())

dialog = QDialog()
dialog.setGeometry(400, 400, 360, 120)
buttons = QDialogButtonBox()
destroy = DangerButton("Delete 9 permanently")
buttons.addButton(destroy, QDialogButtonBox.AcceptRole)
QVBoxLayout(dialog).addWidget(buttons)
clicked: list[bool] = []
destroy.clicked.connect(lambda: clicked.append(True))
dialog.show()
app.processEvents()
check("a dialog's button box cannot make it the default either", not destroy.isDefault())
QTest.keyClick(dialog, Qt.Key_Return)
app.processEvents()
check("so Enter in the dialog does not press it", clicked == [])
dialog.close()


# ---- the focus ring: the keyboard's only ---------------------------------------------------------------

panel = GlassPanel(padding="lg")
QVBoxLayout(panel).addWidget(button := SecondaryButton("Playlist settings"))
host.row.addWidget(panel)
host.activateWindow()
app.processEvents()
button.setFocus(Qt.MouseFocusReason)
app.processEvents()
check("focus from a click shows no ring", button.hasFocus() and not button.focus_visible())
button.clearFocus()
button.setFocus(Qt.TabFocusReason)
app.processEvents()
check("focus from Tab does", button.hasFocus() and button.focus_visible())

# The ring is drawn by the panel behind the button, outside the button's box.
outside = button.mapTo(panel, QPoint(-1, button.height() // 2))
with_ring = lightness_at(panel, outside)
button.clearFocus()
app.processEvents()
without = lightness_at(panel, outside)
check("the panel draws the ring outside the button, in the ring's blue",
      with_ring.blue() > without.blue() + 30 and with_ring.blue() > with_ring.red())
field = TextInput()
host.row.addWidget(field)
app.processEvents()
field.setFocus(Qt.MouseFocusReason)
app.processEvents()
check("a field you type into shows its ring after a click", field.focus_visible())
panel.deleteLater()
field.deleteLater()


# ---- smaller rules ---------------------------------------------------------------------------------------

check("an IconButton needs a tool tip", raises(lambda: IconButton("refresh", ""), ValueError)
      and raises(lambda: IconButton("refresh", "  "), ValueError))
icon_button = IconButton("refresh", "Refresh the list")
check("and the tool tip is its accessible name",
      icon_button.accessibleName() == "Refresh the list"
      and raises(lambda: icon_button.setToolTip(""), ValueError))
check("an unknown icon fails when the button is made, not when it paints",
      raises(lambda: AccentButton("x", icon="sparkles"), KeyError)
      and raises(lambda: IconButton("sparkles", "x"), KeyError))
check("every variant of a size is one height, so a row of them lines up",
      len({b.sizeHint().height() for b in (AccentButton("a"), SecondaryButton("b"),
                                            DangerButton("c"), GhostButton("d"),
                                            GhostButton("e", outlined=True))}) == 1
      and AccentButton("a").sizeHint().height() == theme.CONTROL_HEIGHT)
check("a key cap widens its button", GhostButton("Paste", key="Ctrl+V").sizeHint().width()
      > GhostButton("Paste").sizeHint().width())

hover = SecondaryButton("Playlist settings")
host.row.addWidget(hover)
app.processEvents()
QTest.mouseMove(hover)
app.processEvents()
check("the fill eases into hover over motion.base",
      hover.visual_state() == "hover" and hover._fade.running)
animations.ENABLED = False
QTest.mouseMove(host, QPoint(2, 2))
app.processEvents()
check("and changes at once with motion off",
      hover.visual_state() == "default" and not hover._fade.running)
animations.ENABLED = True
hover.deleteLater()

spin = SpinBox(maximum=100_000, value=12345)
check("a SpinBox writes thousands apart by a no-break space",
      spin.text() == "12\u00a0345" and spin.textFromValue(1000) == "1\u00a0000")
check("and reads them back, with a plain space too",
      spin.valueFromText("1\u00a0000") == 1000 and spin.valueFromText("45 000") == 45000)
from PySide6.QtGui import QValidator                                   # noqa: E402
check("typing a grouped number is acceptable, letters are not",
      spin.validate("45 000", 0)[0] == QValidator.Acceptable
      and spin.validate("4x", 0)[0] == QValidator.Invalid)

wrong = TextInput("rain?")
plain_height = wrong.sizeHint().height()
wrong.set_error("no author by that name")
check("an error turns the field red and makes room for its message",
      wrong.property("error") is True and wrong.error() == "no author by that name"
      and wrong.sizeHint().height() == plain_height + theme.FIELD_ERROR_HEIGHT
      and wrong.height() == plain_height + theme.FIELD_ERROR_HEIGHT)
wrong.set_error(None)
check("and clearing it gives the room back", wrong.property("error") is False
      and wrong.height() == plain_height)

check("a MetricStrip holds two to four numbers",
      raises(lambda: MetricStrip([(1, "a")]), ValueError)
      and raises(lambda: MetricStrip([(i, "x") for i in range(5)]), ValueError))
strip = MetricStrip([(1000, "moved in"), (998, "returned"), (2, "failed", "danger")])
check("and writes its numbers as the design does", strip.value(0) == "1\u00a0000")
strip.set_value(2, 0, None)
check("a value's tone can change with it", strip._values[2].property("tone") == "hi")

check("the kit's stylesheet rules all resolve", not theme.unresolved(theme.stylesheet()))
check("the kit exports what the pages will use",
      all(hasattr(kit, name) for name in kit.__all__))

host.close()
print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
