"""The kit's feedback: the log, toasts, the status line and the dialogs.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_kit_feedback.py

Beyond drawing each class, it holds the rules the design states and a page
would otherwise have to remember: a log that keeps 5 000 lines and no more,
its Problems filter, a console that stops following the newest line the
moment you scroll up and starts again at the bottom; a destructive dialog
whose default and first focus is Cancel, whose Danger button says the count
it will delete, whose group box ticks rows not yet built, and which returns
no rows when cancelled; a form whose Save waits for valid fields; toasts that
stack, go after 6 s, and stay when they are danger; and a status line whose
count never elides.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# The labels carry → and ·, which a Windows console's code page (cp1252 on CI)
# cannot always print; a check must not fail for the way its name is shown.
sys.stdout.reconfigure(errors="replace")

from PySide6.QtCore import QElapsedTimer, QEvent, QPoint, Qt, QTimer           # noqa: E402
from PySide6.QtGui import QImage, QPainter                                    # noqa: E402
from PySide6.QtTest import QTest                                              # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget     # noqa: E402

app = QApplication(sys.argv)

from app import animations, theme                                             # noqa: E402

theme.apply(app)
animations.ENABLED = True

from app.ui import kit                                                        # noqa: E402
from app.ui.kit import (                                                      # noqa: E402
    CheckGroup, CheckRow, ConfirmDialog, ConfirmResult, Dropdown, Elided, FormDialog, LinkButton,
    LiveDot, LogModel, LogPanel, SpinBox, StatusLine, TextInput, Toast, ToastHost,
)
from app.ui.kit import base                                                   # noqa: E402
from app.ui.kit import format as fmt                                          # noqa: E402
from app.ui.kit.log import ProblemsFilter, kind_tone                          # noqa: E402

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
    QTest.qWait(ms)


def wait_for(condition, ms: int = 3000) -> bool:
    clock = QElapsedTimer()
    clock.start()
    while clock.elapsed() < ms:
        if condition():
            return True
        wait(20)
    return condition()


class Host(QWidget):
    """A surface like the app's, away from the offscreen pointer at (0, 0)."""

    def __init__(self, width: int = 900, height: int = 560):
        super().__init__()
        base.declare(self)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(24, 24, 24, 24)
        self.setGeometry(300, 200, width, height)

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
    step = max(1, image.width() // 60)
    return any(image.pixelColor(x, y) != first
               for x in range(0, image.width(), step) for y in range(0, image.height(), 2))


# ---- the log's model ---------------------------------------------------------------------

check("kinds take the console's colours: moved/done ok, skip/dupe warn, fail/error err, "
      "step/start/info mid",
      [kind_tone(k) for k in ("moved", "done", "skip", "dupe", "fail", "error", "step",
                              "start", "info")]
      == ["ok", "ok", "warn", "warn", "err", "err", "mid", "mid", "mid"])
check("any other kind is shown in mid, and case does not matter",
      kind_tone("whatever") == "mid" and kind_tone("MOVED") == "ok" and kind_tone("WARN") == "warn")

model = LogModel()
check("a log keeps 5 000 lines", model.cap() == theme.LOG_CAP == 5000)
removed = []
model.rowsRemoved.connect(lambda _p, first, last: removed.append((first, last)))
for n in range(5003):
    model.append("13:47:02", "error" if n < 2 else "moved", f"line {n}")
check("past the cap the oldest lines go, and the newest stay",
      len(model) == 5000 and model.rowCount() == 5000
      and model.line(0).message == "line 3" and model.line(4999).message == "line 5002")
check("each line past the cap took one off the front", removed == [(0, 0)] * 3)
check("the problems counted leave with their lines", model.problem_count() == 0
      and model.error_count() == 0)
batch = LogModel(cap=10)
inserted = []
batch.rowsInserted.connect(lambda _p, first, last: inserted.append((first, last)))
batch.extend([(None, "skip", f"b{n}") for n in range(25)])
check("extend adds many lines in one insert, keeping the newest `cap`",
      inserted == [(0, 9)] and [line.message for line in batch.lines()] == [f"b{n}" for n in range(15, 25)])
check("a datetime is stamped to the second",
      LogModel.make_line(datetime(2026, 9, 28, 13, 47, 2), "moved", "x").time == "13:47:02")
check("a line copies as time · kind · message",
      LogModel.make_line("13:47:02", "Moved", "1234567890 → myprojects").text()
      == "13:47:02  moved  1234567890 → myprojects")
check("a time that is not one is refused", raises(lambda: LogModel.make_line([], "info", "x"), TypeError))

mixed = LogModel()
for kind in ("start", "moved", "skip", "moved", "fail", "dupe", "done"):
    mixed.append(None, kind, kind)
problems = ProblemsFilter()
problems.setSourceModel(mixed)
check("the filter passes everything until asked", problems.rowCount() == 7)
problems.set_problems_only(True)
check("Problems keeps the warn and err lines only",
      [problems.index(r, 0).data(Qt.DisplayRole).split()[-1] for r in range(problems.rowCount())]
      == ["skip", "fail", "dupe"] and mixed.problem_count() == 3 and mixed.error_count() == 1)
mixed.append(None, "error", "late")
check("and follows new lines as they come", problems.rowCount() == 4)

# ---- the log panel ------------------------------------------------------------------------

host = Host(900, 620)
panel = LogPanel("Log", file="rotator.log", on_open_folder=lambda: opened.append(True))
opened: list[bool] = []
host.column.addWidget(panel)
host.column.addStretch(1)
host.show()
wait(30)
check("the header says which file the job writes", panel.file_text() == "writing to rotator.log")
panel.set_file(None)
check("and nothing when it writes none", panel.file_text() == "")
panel.set_file("rotator.log")
for n in range(300):
    panel.append(None, "moved" if n % 10 else "skip", f"1234{n:06d} → myprojects")
wait(40)
bar = panel.view.verticalScrollBar()
check("the console sits at the newest line while lines arrive",
      bar.maximum() > 0 and bar.value() == bar.maximum() and panel.view.following())
bar.setValue(bar.maximum() // 3)                   # the reader scrolls up
reading = bar.value()
first_row = panel.view.indexAt(QPoint(4, 4)).data(Qt.DisplayRole)
wait(20)
for n in range(40):
    panel.append(None, "moved", f"new {n}")
wait(40)
check("scrolled up, it stops following: new lines do not move what is being read",
      not panel.view.following() and bar.value() == reading
      and panel.view.indexAt(QPoint(4, 4)).data(Qt.DisplayRole) == first_row)
bar.setValue(bar.maximum())
for n in range(5):
    panel.append(None, "moved", f"after {n}")
wait(40)
check("back at the bottom, it follows again", panel.view.following()
      and bar.value() == bar.maximum()
      and panel.view.indexAt(QPoint(4, panel.view.viewport().height() - 4)).data(Qt.DisplayRole)
      .endswith("after 4"))

full = LogPanel("Log", cap=100)
host.column.insertWidget(1, full)
wait(30)
full.extend([("13:00:00", "moved", f"r{n}") for n in range(100)])
wait(30)
full_bar = full.view.verticalScrollBar()
full_bar.setValue(full_bar.maximum() // 2)
wait(20)
kept = full.view.indexAt(QPoint(4, 4)).data(Qt.DisplayRole)
for n in range(10):
    full.append("13:00:01", "moved", f"s{n}")
wait(40)
check("with the ring full, the lines being read stay put as the oldest leave",
      len(full.model()) == 100 and not full.view.following()
      and full.view.indexAt(QPoint(4, 4)).data(Qt.DisplayRole) == kept)
full.setParent(None)
full.deleteLater()

mixed_panel = LogPanel("Log")
host.column.insertWidget(1, mixed_panel)
mixed_panel.extend([("13:00:00", "start" if n == 0 else "skip" if n % 5 == 0 else "moved", f"m{n}")
                    for n in range(200)])
wait(30)
mixed_bar = mixed_panel.view.verticalScrollBar()
mixed_bar.setValue(mixed_bar.maximum() // 2)
wait(20)
reading = mixed_bar.value()
mixed_panel.set_problems_only(True)
mixed_panel.set_problems_only(False)
wait(30)
check("switching to Problems and back is not lines leaving: the position is not shifted",
      not mixed_panel.view.following() and mixed_bar.value() == reading)
mixed_panel.setParent(None)
mixed_panel.deleteLater()

check("the panel counts the problems it holds", panel.problem_count() == 30)
panel.set_problems_only(True)
wait(20)
check("Problems shows those lines only, and the switch says so",
      len(panel.shown_lines()) == 30 and all(l.tone in ("warn", "err") for l in panel.shown_lines())
      and panel._switch.current_index() == 1)
panel._switch.set_current_index(0)
check("and the switch turns it back", not panel.problems_only() and len(panel.shown_lines()) == 345)

QApplication.clipboard().clear()
panel.view.clearSelection()
copied = panel.copy()
check("copy without a selection takes every line shown",
      copied.count("\n") == 344 and QApplication.clipboard().text() == copied)
selection = panel.view.selectionModel()
filtered = panel.view.model()
selection.select(filtered.index(5, 0), selection.SelectionFlag.Select)
selection.select(filtered.index(2, 0), selection.SelectionFlag.Select)
copied = panel.copy()
check("with a selection, just those lines, oldest first",
      copied.splitlines() == [panel.shown_lines()[2].text(), panel.shown_lines()[5].text()])
panel.view.clearSelection()
QTest.keyClick(panel.view, Qt.Key_C, Qt.ControlModifier)
check("Ctrl+C in the console copies too", QApplication.clipboard().text().count("\n") == 344)
panel._open.click()
check("Open log folder calls the page back", opened == [True])
check("without a callback there is no Open log folder",
      not LogPanel("Log")._open.isVisibleTo(host) and not LogPanel("Log")._open.isVisible())

panel.set_live(True)
check("the live dot pulses while the job runs",
      panel.live() and animations.loop("pulse").running)
panel.set_live(False)

opened_height = panel.height()
panel.set_expanded(False)
wait(animations.SLOW // 3)
middle = (panel.expansion(), panel.chevron_angle(), panel.height())
wait(animations.SLOW + 80)
check("closing moves the height and the chevron together over motion.slow",
      0 < middle[0] < 1 and 0 < middle[1] < 180 and middle[2] < opened_height
      and panel.expansion() == 0 and panel.chevron_angle() == 0)
check("closed, it is the header alone, with the problems in a badge",
      not panel._body.isVisible() and panel.badge_text() == "30 problems"
      and panel._badge.tone() == "warn")
panel.append(None, "error", "access denied")
check("an error among them turns the badge to danger",
      panel.badge_text() == "31 problems" and panel._badge.tone() == "danger")
panel.set_expanded(True)
wait(animations.SLOW + 80)
check("open again: body back, chevron up, no badge",
      panel.expansion() == 1 and panel.chevron_angle() == 180 and panel.badge_text() == ""
      and panel.height() == opened_height)
animations.ENABLED = False
panel.set_expanded(False)
check("with Windows' animations off it closes at once", panel.expansion() == 0)
panel.set_expanded(True)
animations.ENABLED = True
check("the log panel draws", drawn(panel))
QTest.mouseClick(panel._head, Qt.LeftButton, Qt.NoModifier, QPoint(panel._head.width() // 2, 6))
check("a click on the header closes it", not panel.expanded())
host.close()

# ---- the status line -------------------------------------------------------------------------

line_host = Host(900, 120)
status = StatusLine()
line_host.column.addWidget(status)
line_host.show()
wait(30)
shown: list[bool] = []
status.set_running("Rotating · moving folders into myprojects", 412, 1000,
                   on_show=lambda: shown.append(True))
check("running: the pulse, the bar, the count and Show",
      status.state() == "running" and status._dot.live() and status.bar().isVisible()
      and status.count_text() == f"412 / 1{fmt.NBSP}000"
      and status.link_text() == "Show" and status.bar().fraction() == 0.412
      and status.height() == theme.STATUS_HEIGHT)
status._link.click()
check("Show calls the page back", shown == [True])
status.set_running("Scanning", 0, 0)
check("with no total the bar sweeps and there is no count",
      status.bar().state() == "indeterminate" and status.count_text() == ""
      and status.link_text() == "")
status.set_idle("Idle · next rotation Saturday")
check("idle: words and a still dot, nothing else",
      status.state() == "idle" and not status._dot.live() and not status.bar().isVisible()
      and status.count_text() == "" and status.link_text() == ""
      and status._text.tone() == "mid")
acted: list[str] = []
status.set_warn("Finished with 2 problems", "Open log", lambda: acted.append("warn"))
check("warn: the warn glyph and a link that does something about it",
      status.state() == "warn" and status._glyph.isVisible() and status._glyph._colour == "warn"
      and status.link_text() == "Open log" and not status._dot.isVisible())
status._link.click()
status.set_error("The rotation stopped", "Open log", lambda: acted.append("error"))
status._link.click()
check("error: the glyph in danger, and the link calls its own callback",
      status.state() == "error" and status._glyph._colour == "danger" and acted == ["warn", "error"])
status.set_error("The rotation stopped")
check("a state with no action shows no link", status.link_text() == "")
long_words = "Rotating · moving 1 000 folders from W:\\wallpaper_reserve into myprojects " * 3
status.set_running(long_words, 412, 1000, on_show=lambda: None)
line_host.resize(520, 120)
wait(40)
check("short of room the words elide and the count does not",
      status.shown_text().endswith("…") and status._count.width() >= status._count.sizeHint().width()
      and status._count.text() == fmt.ratio(412, 1000))
check("the status line draws", drawn(status))
check("and its link's ring goes on it", status._link._kit_surface is status)
line_host.close()

# ---- toasts ------------------------------------------------------------------------------------

page = Host(900, 600)
toasts = ToastHost(page)
page.show()
wait(30)
check("an info toast goes after 6 s, a danger one stays",
      Toast("x", "info").timeout() == 6000 and Toast("x", "danger").timeout() is None
      and theme.TOAST_TIMEOUT == 6000)
check("an unknown variant is refused", raises(lambda: Toast("x", "loud"), KeyError))
check("the toast's motion is anim.toastIn: 180 in, 120 out",
      (animations.TOAST_IN, animations.TOAST_OUT) == (180, 120))
first = toasts.show_toast("37 wallpapers created.", "ok", action="Open folder",
                          on_action=lambda: acted.append("toast"))
wait(animations.TOAST_IN // 3)
check("a toast fades in rising", 0 < first.appearance() < 1
      and first.graphicsEffect() is not None)
wait(animations.TOAST_IN + 60)
check("then is fully there, the effect gone and its clock running",
      first.appearance() == 1 and first.graphicsEffect() is None and first.timer_running())
second = toasts.show_toast("2 folders stayed in the reserve.", "warn")
third = toasts.show_toast("The rotation stopped.", "danger")
wait(animations.TOAST_IN + 60)
x = page.width() - theme.TOAST_MARGIN - theme.TOAST_WIDTH
check("toasts stack bottom-right, the newest lowest",
      third.geometry().bottom() == page.height() - theme.TOAST_MARGIN - 1
      and second.geometry().bottom() == third.y() - theme.TOAST_STACK_GAP - 1
      and first.geometry().bottom() == second.y() - theme.TOAST_STACK_GAP - 1
      and {first.x(), second.x(), third.x()} == {x})
check("over the page's content, and the host lets clicks through",
      toasts.testAttribute(Qt.WA_TransparentForMouseEvents)
      and page.childAt(page.width() - 40, page.height() - 30) is not toasts)
check("the danger toast has no clock", not third.timer_running())
first._link.click()
wait(animations.TOAST_OUT + 80)
check("its action runs, and it goes", acted[-1] == "toast" and first not in toasts.toasts())
wait(40)
check("the rest close up the gap",
      second.geometry().bottom() == third.y() - theme.TOAST_STACK_GAP - 1)
quick = toasts.show_toast("Copied.", "info", timeout=300)
wait(animations.TOAST_IN + 300 + animations.TOAST_OUT + 150)
check("a toast goes by itself when its time is up", quick not in toasts.toasts())
held = toasts.show_toast("Held.", "info", timeout=300)
wait(animations.TOAST_IN + 40)
QApplication.sendEvent(held, QEvent(QEvent.Enter))
wait(450)
check("the pointer on a toast holds it", held in toasts.toasts() and not held.leaving())
QApplication.sendEvent(held, QEvent(QEvent.Leave))
wait(300 + animations.TOAST_OUT + 150)
check("and it goes once the pointer moves off", held not in toasts.toasts())
check("danger stays until it is closed", third in toasts.toasts() and third.isVisible())
third._close.click()
wait(animations.TOAST_OUT + 80)
check("and goes when it is", third not in toasts.toasts())
second.dismiss()
many = [toasts.show_toast(f"toast {n}", "info") for n in range(3)]
alarm = toasts.show_toast("danger 1", "danger")
many.append(toasts.show_toast("toast 3", "info"))
wait(animations.TOAST_OUT + 80)
staying = [t for t in toasts.toasts() if not t.leaving()]
check("at most four at once: the oldest that may go makes room, never a danger one",
      len(staying) == theme.TOAST_LIMIT and many[0] not in staying and alarm in staying)
toasts.clear()
wait(animations.TOAST_OUT + 80)
check("clear takes them all", toasts.toasts() == [])
animations.ENABLED = False
instant = toasts.show_toast("no motion", "ok")
check("with Windows' animations off a toast is there at once", instant.appearance() == 1)
instant.dismiss()
check("and gone at once", instant.gone())
animations.ENABLED = True
specimen = toasts.show_toast("The rotation stopped: access denied.", "danger", action="Open log")
wait(animations.TOAST_IN + 60)
check("a toast draws, and the host its shadow", drawn(specimen) and drawn(page))
check("a toast is the surface its buttons draw their rings on",
      specimen._close._kit_surface is specimen and specimen._link._kit_surface is specimen)
page.close()

# ---- ConfirmDialog --------------------------------------------------------------------------------

window = Host(1000, 700)
window.show()
wait(30)
safe = [CheckRow(f"12345000{n:02d}", "no project.json · empty", 0, data=n) for n in range(9)]
media = [CheckRow(f"media_{n}", "no project.json · holds 3 videos", (n + 1) * 1024 ** 2,
                  data=100 + n) for n in range(8)]
opened_folder: list[bool] = []


def destructive(**kwargs) -> ConfirmDialog:
    return ConfirmDialog(
        "Delete 9 unusable folders?", "Deleting is permanent.", window, destructive=True,
        icon="trash",
        groups=[CheckGroup("Safe to delete", safe, tone="ok"),
                CheckGroup("Hold media", media, tone="warn", initially_checked=False)],
        confirm_text=lambda rows: f"Delete {len(rows)} permanently",
        actions=[("Open folder", lambda: opened_folder.append(True))], **kwargs)


dialog = destructive()
dialog.show()
wait(30)
check("the scrim covers the whole window, the panel in its middle",
      dialog.geometry() == window.geometry()
      and abs(dialog.panel.geometry().center().x() - window.width() // 2) <= 1
      and abs(dialog.panel.geometry().center().y() - window.height() // 2) <= 1
      and dialog.panel.width() == theme.DIALOG_WIDE)
check("a destructive dialog leads with the warn tile", dialog.tile() == ("trash", "warn"))
check("its default button, and the one focused when it opens, is Cancel",
      dialog.cancel_button().isDefault() and dialog.focusWidget() is dialog.cancel_button()
      and not dialog.confirm_button().isDefault() and dialog.confirm_button().variant == "danger")
check("groups start as asked: the first ticked, the second clear",
      dialog.group_state(0) == Qt.Checked and dialog.group_state(1) == Qt.Unchecked
      and [row.data for row in dialog.checked_rows()] == list(range(9)))
check("a group's header says what it holds, in overline",
      dialog.group_header(0) == "Safe to delete — 9 folders · 0 B"
      and dialog.group_header(1) == "Hold media — 8 folders · 36 MB")
check("the footer counts what is ticked", dialog.summary_text() == "9 selected · 0 B")
check("and the Danger button says the same number", dialog.confirm_button().text()
      == "Delete 9 permanently")
check("a long group shows five rows and folds the rest",
      dialog.built_rows(0) == 5 and dialog.more_text(0) == "4 more like these"
      and dialog.built_rows(1) == 5 and dialog.more_text(1) == "3 more like these"
      and dialog.row_checkbox(0, 7) is None)
dialog.row_checkbox(0, 2).click()
check("clearing one row leaves its group partly ticked",
      dialog.group_state(0) == Qt.PartiallyChecked and dialog.summary_text() == "8 selected · 0 B"
      and dialog.confirm_button().text() == "Delete 8 permanently")
dialog.group_checkbox(0).click()
check("a click on a partly ticked group ticks all of it",
      dialog.group_state(0) == Qt.Checked and dialog.row_checkbox(0, 2).isChecked()
      and len(dialog.checked_rows()) == 9)
dialog.group_checkbox(1).click()
check("ticking a group ticks the rows folded under “more” too",
      dialog.group_state(1) == Qt.Checked and len(dialog.checked_rows()) == 17
      and dialog.summary_text() == "17 selected · 36 MB"
      and dialog.confirm_button().text() == "Delete 17 permanently")
before = dialog.panel.height()
dialog.expand_group(1)
check("“more like these” builds the rest, ticked as the group is",
      dialog.built_rows(1) == 8 and dialog.more_text(1) == ""
      and all(dialog.row_checkbox(1, r).isChecked() for r in range(8))
      and dialog.panel.height() >= before)
dialog.set_row_checked(1, 7, False)
check("a built row cleared from the page clears its box",
      not dialog.row_checkbox(1, 7).isChecked() and dialog.group_state(1) == Qt.PartiallyChecked)
dialog.group_checkbox(0).click()
dialog.group_checkbox(1).click()
dialog.group_checkbox(1).click()
check("a click on a ticked group clears it; with nothing ticked the Danger button is off",
      dialog.checked_rows() == [] and not dialog.confirm_button().isEnabled()
      and dialog.summary_text() == "0 selected · 0 B")
dialog.set_group_checked(0, True)
dialog.action_buttons()[0].click()
check("a ghost action runs and leaves the dialog open", opened_folder == [True] and dialog.isVisible())
QTest.keyClick(dialog, Qt.Key_Return)
check("Enter cancels a destructive dialog", not dialog.isVisible()
      and not dialog.result_value() and dialog.result_value().checked == ())

dialog = destructive()
QTimer.singleShot(60, lambda: QTest.keyClick(dialog, Qt.Key_Escape))
answer = dialog.ask()
check("Esc returns cancel, with no rows",
      isinstance(answer, ConfirmResult) and not answer and answer.checked == ()
      and dialog.result() == QDialog.Rejected)

dialog = destructive()
QTimer.singleShot(60, lambda: dialog.confirm_button().click())
answer = dialog.ask()
check("confirmed, it returns the rows ticked",
      bool(answer) and [row.data for row in answer.checked] == list(range(9)))

neutral = ConfirmDialog("Start rotation 39?", "The Rotator does this, in this order.", window,
                        confirm_text="Start rotation",
                        steps=["Close Wallpaper Engine",
                               ("Move 1 000 new folders in", "drawn at random")])
neutral.show()
wait(30)
check("a neutral dialog: an accent tile, numbered steps, the Accent button default and focused",
      neutral.tile() == ("info", "accent") and neutral.step_titles()
      == ["Close Wallpaper Engine", "Move 1 000 new folders in"]
      and neutral.confirm_button().variant == "accent" and neutral.confirm_button().isDefault()
      and neutral.focusWidget() is neutral.confirm_button()
      and neutral.panel.width() == theme.DIALOG_WIDTH)
check("the dialog draws its scrim and panel", drawn(neutral))
QTest.keyClick(neutral, Qt.Key_Escape)
check("Esc cancels a neutral one too", not neutral.isVisible() and not neutral.result_value())
neutral = ConfirmDialog("Go?", "", window)
QTimer.singleShot(60, lambda: QTest.keyClick(neutral, Qt.Key_Return))
check("and Enter confirms it", bool(neutral.ask()))

sized = ConfirmDialog("x", groups=[CheckGroup("Mixed", [CheckRow("a", size=2048), CheckRow("b")])])
check("a size not measured makes the total a floor",
      sized.summary_text() == "2 selected · at least 2 KB")
unsized = ConfirmDialog("x", groups=[CheckGroup("Plain", [CheckRow("a"), CheckRow("b")],
                                                noun="author")])
check("rows without sizes are counted without one",
      unsized.summary_text() == "2 selected" and unsized.group_header(0) == "Plain — 2 authors")
custom = ConfirmDialog("x", groups=[CheckGroup("G", safe[:3])],
                       summary=lambda rows: f"{len(rows)} of 3 folders")
check("a page's own summary is used", custom.summary_text() == "3 of 3 folders")
check("an unknown group tone is refused",
      raises(lambda: ConfirmDialog("x", groups=[CheckGroup("G", safe, tone="loud")]), KeyError))
embedded = destructive(embedded=True)
window.column.addWidget(embedded)
wait(30)
check("embedded, it is a child with its own size and no scrim",
      not embedded.isWindow() and embedded.size() == embedded.panel.size())
embedded.cancel_button().click()
check("and answering leaves it where it is", embedded.isVisible() and not embedded.result_value())
embedded.setParent(None)

# ---- FormDialog ------------------------------------------------------------------------------------

form = FormDialog("Review settings", window)
every = Dropdown()
for option in ("Every week", "Every two weeks"):
    every.add_item(option)
form.add_row("Scan", every, note="The next scan is due Saturday.")
key = form.add_row("Steam Web API key", TextInput(), note="32 characters.", required=True,
                   check=lambda f: None if len(f.text().strip()) in (0, 32)
                   else "a key is 32 characters")
limit = form.add_row("New items per author", SpinBox(minimum=0, maximum=500, value=40),
                     check=lambda f: None if f.value() > 0 else "at least one")
form.show()
wait(30)
check("a form opens with its first field focused, and Save default",
      form.focusWidget() is every and form.save_button().isDefault())
check("Save is off while a required field is empty, and nothing is said yet",
      not form.is_valid() and not form.save_button().isEnabled() and key.error() is None)
QTest.keyClicks(key, "abc")
check("a wrong value keeps it off, and the field says why once touched",
      not form.is_valid() and key.error() == "a key is 32 characters"
      and form.problem(key) == "a key is 32 characters")
key.setText("k" * 32)
check("a right one turns it on, and the message goes", form.is_valid() and key.error() is None)
limit.setValue(0)
row = form._rows[2]
check("any kind of field is checked, its note turned into the problem",
      not form.is_valid() and row.note.text() == "at least one" and row.note.property("tone") == "danger")
limit.setValue(12)
check("and turned back", form.is_valid() and row.note.text() == "" and not row.note.isVisible())
form.set_check(lambda f: "not both" if every.currentIndex() == 1 and limit.value() > 10 else None)
every.setCurrentIndex(1)
check("the form as a whole is checked too, and says so in the footer",
      not form.is_valid() and form.footer.summary.text() == "not both"
      and form.footer.summary.tone() == "danger")
limit.setValue(5)
check("until it is right", form.is_valid() and form.footer.summary.text() == "")
QTest.keyClick(key, Qt.Key_Return)
check("Enter in a field saves a valid form", not form.isVisible() and form.result_value() is True)

form = FormDialog("x", window)
blank = form.add_row("Name", TextInput(), required=True)
QTimer.singleShot(60, lambda: (QTest.keyClick(blank, Qt.Key_Return),
                               QTest.keyClick(form, Qt.Key_Escape)))
check("Enter does nothing while it is not valid, and Esc cancels", form.ask() is False)

# ---- the small pieces ----------------------------------------------------------------------------------

words = Elided("A long line of words that does not fit", "type.label", "mid")
words.show()
words.resize(80, 20)
wait(10)
check("Elided cuts with an ellipsis and keeps the whole in its tool tip",
      words.shown_text().endswith("…") and words.toolTip() == words.text() and words.is_elided())
words.resize(900, 20)
wait(10)
check("and no tool tip once it fits", not words.is_elided() and words.toolTip() == "")
check("an unknown tone is refused", raises(lambda: Elided("x", tone="loud"), KeyError))
dot = LiveDot()
check("a LiveDot pulses while live", dot.live())
dot.set_live(False)
check("and stands still when not", not dot.live())
link = LinkButton("3 more like these")
clicks: list[bool] = []
link.clicked.connect(lambda: clicks.append(True))
link.click()
check("a LinkButton clicks, and is no dialog's default",
      clicks == [True] and not hasattr(link, "setDefault"))
check("fmt.clock can stamp seconds",
      fmt.clock(datetime(2026, 9, 28, 9, 5, 7), seconds=True) == "09:05:07"
      and fmt.clock(datetime(2026, 9, 28, 9, 5, 7)) == "09:05")

window.close()
check("the kit exports what the pages will use",
      all(hasattr(kit, name) for name in kit.__all__))
check("the stylesheet's new rules all resolve", not theme.unresolved(theme.stylesheet()))

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
