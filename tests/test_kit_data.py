"""The kit's data display: formats, fields, tags, progress, cards, tables, thumbs.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_kit_data.py

Beyond drawing every class in its states, it holds the rules that matter on
this machine: every number written one way (`33 421`, `1.1 GB`, `≈6 min
left`, `~12:44`), a folder checked off the GUI thread and answered by signal,
a per-clip TagSelect's three states, a MonitorView mapped to the words a card
shows, a 33 000-row model that builds in well under 100 ms and a table that
paints only the rows on screen, thumbnails asked for only for those rows,
kept on disk by path and time, and not one file-system call on the GUI
thread while a table of thumbs and a folder field are shown.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# The labels carry ≈ and →, which a Windows console's code page (cp1252 on CI)
# cannot print; a check must not fail for the way its name is shown.
sys.stdout.reconfigure(errors="replace")

from PySide6.QtCore import QElapsedTimer, QEventLoop, QMimeData, QPointF, QSize, Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QDropEvent, QImage, QPainter                  # noqa: E402
from PySide6.QtTest import QTest                                               # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget                # noqa: E402

app = QApplication(sys.argv)

from app import animations, theme                                              # noqa: E402

theme.apply(app)
animations.ENABLED = True

from app.engines import creator                                                # noqa: E402
from app.ui import kit                                                         # noqa: E402
from app.ui.kit import (                                                       # noqa: E402
    ActivityLine, Cell, ChipCell, Column, EmptyState, Group, MonitorCard, MonitorView,
    PathField, ProgressBar, ProgressRing, StatCard, StepList, Table, TableFooter, TableModel,
    TableSummary, TagSelect, Thumb, ThumbLoader,
)
from app.ui.kit import base, thumbs                                            # noqa: E402
from app.ui.kit import format as fmt                                           # noqa: E402
from app.ui.kit.tags import TagPopup                                           # noqa: E402

NB = chr(0xA0)
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


def wait_for(condition, ms: int = 5000) -> bool:
    clock = QElapsedTimer()
    clock.start()
    while clock.elapsed() < ms:
        if condition():
            return True
        wait(20)
    return condition()


class Host(QWidget):
    """A surface like the app's, away from the offscreen pointer at (0, 0)."""

    def __init__(self, width: int = 900, height: int = 600):
        super().__init__()
        base.declare(self)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(24, 24, 24, 24)
        self.setGeometry(400, 400, width, height)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        theme.paint_app_background(painter, self.rect())
        base.paint(painter, self, event.rect())


def drawn(widget: QWidget) -> bool:
    image = widget.grab().toImage().convertToFormat(QImage.Format_ARGB32)
    if image.isNull() or image.width() == 0:
        return False
    first = image.pixelColor(0, 0)
    step = max(1, image.width() // 40)
    return any(image.pixelColor(x, y) != first
               for x in range(0, image.width(), step) for y in range(image.height()))


scratch = Path(tempfile.mkdtemp(prefix="kit-data-"))


def solid_image(path: Path, colour: str, size=QSize(64, 36)) -> None:
    image = QImage(size, QImage.Format_RGB32)
    image.fill(QColor(colour))
    image.save(str(path))


# ---- format ---------------------------------------------------------------------------------

check("counts group thousands with a no-break space",
      fmt.count(0) == "0" and fmt.count(1000) == f"1{NB}000"
      and fmt.count(33421) == f"33{NB}421" and fmt.count(1_234_567) == f"1{NB}234{NB}567")
check("and a count can carry its noun", fmt.counted(1, "folder") == "1 folder"
      and fmt.counted(33421, "folder") == f"33{NB}421 folders"
      and fmt.counted(2, "copy", "copies") == "2 copies")
MB, GB, TB = 1024 ** 2, 1024 ** 3, 1024 ** 4
check("sizes are whole below a gigabyte",
      fmt.size(0) == "0 B" and fmt.size(512) == "512 B" and fmt.size(1024) == "1 KB"
      and fmt.size(214 * MB) == "214 MB")
check("and carry one decimal from GB up",
      fmt.size(1.1 * GB) == "1.1 GB" and fmt.size(4.2 * TB) == "4.2 TB")
check("a size that rounds up to the next unit is written in it",
      fmt.size(1023.7 * MB) == "1.0 GB" and fmt.size(1023.96 * GB) == "1.0 TB")
check("durations: seconds, minutes and seconds under ten minutes, minutes after",
      fmt.duration(0) == "0 s" and fmt.duration(42) == "42 s"
      and fmt.duration(252) == "4 min 12 s" and fmt.duration(240) == "4 min"
      and fmt.duration(840) == "14 min" and fmt.duration(960) == "16 min")
check("hours and days",
      fmt.duration(3599) == "1 h" and fmt.duration(7500) == "2 h 5 min"
      and fmt.duration(3 * 86400 + 4 * 3600) == "3 d 4 h")
check("time left is an estimate, never to the second",
      fmt.left(372) == "≈6 min left" and fmt.left(30) == "≈30 s left")
check("estimates are marked ≈, once",
      fmt.approx("21 Sep") == "≈21 Sep" and fmt.approx("≈21 Sep") == "≈21 Sep")
check("reconstructed times are marked ~",
      fmt.reconstructed("12:44") == "~12:44"
      and fmt.reconstructed(datetime(2026, 9, 19, 12, 44)) == "~12:44")
check("and the mark comes back off for a painter",
      fmt.split_qualifier("≈6 min") == ("≈", "6 min")
      and fmt.split_qualifier("~12:44") == ("~", "12:44")
      and fmt.split_qualifier("6 min") == ("", "6 min"))
NOW = datetime(2026, 9, 25, 15, 0)              # a Friday
check("a table writes a moment as day, month and time",
      fmt.date_table(datetime(2026, 9, 19, 12, 44), NOW) == "19 Sep 12:44")
check("with the year when it is not this one",
      fmt.date_table(datetime(2025, 7, 3, 8, 5), NOW) == "3 Jul 2025 08:05")
check("recent activity today is just the time",
      fmt.date_activity(datetime(2026, 9, 25, 13, 47), NOW) == "13:47")
check("this week, the weekday and the time",
      fmt.date_activity(datetime(2026, 9, 24, 21, 32), NOW) == "Thu 21:32"
      and fmt.date_activity(datetime(2026, 9, 19, 9, 10), NOW) == "Sat 09:10")
check("older than that, the table's form",
      fmt.date_activity(datetime(2026, 9, 12, 9, 10), NOW) == "12 Sep 09:10")
check("the page header's long form",
      fmt.date_long(datetime(2026, 9, 19, 13, 44)) == "Saturday 19 September, 13:44")
check("an expected day is an estimate",
      fmt.estimate_day(datetime(2026, 9, 21, 9, 10), NOW) == "≈21 Sep")
check("a timestamp reads the same as its datetime",
      fmt.clock(datetime(2026, 9, 19, 12, 44).timestamp()) == "12:44")
check("ratios: card, nav and prose",
      fmt.ratio(4, 201) == "4 / 201" and fmt.ratio(4, 201, "nav") == "4/201"
      and fmt.ratio(412, 1000, "prose") == f"412 of 1{NB}000"
      and raises(lambda: fmt.ratio(1, 2, "fancy"), ValueError))
check("a percentage is honest at both ends",
      fmt.percent(4, 201) == "2%" and fmt.percent(412, 1000) == "41%"
      and fmt.percent(999, 1000) == "99%" and fmt.percent(1, 1000) == "1%"
      and fmt.percent(1000, 1000) == "100%" and fmt.percent(0, 10) == "0%"
      and fmt.percent(0, 0) == "0%")
check("the kit's grouped() is format.count", base.grouped(33421) == fmt.count(33421))


# ---- PathField -------------------------------------------------------------------------------

host = Host()
there = scratch / "reserve"
there.mkdir()
field = PathField(placeholder="Choose a folder")
field.setFixedWidth(300)
host.column.addWidget(field)
host.show()
answers: list = []
field.validity_changed.connect(answers.append)
chosen: list = []
field.path_changed.connect(chosen.append)
check("an empty PathField is the full field with Browse…",
      field.variant() == "empty" and field.valid() is None and field.path_state() == "empty")
field.set_path(there)
check("a new path shows at once, without a verdict yet",
      field.path() == str(there) and field.valid() is None and field.variant() == "compact")
check("the verdict arrives by signal, from a worker",
      wait_for(lambda: field.valid() is True) and answers[-1] is True)
check("a folder that is there is compact, and a path set from code is not the user's choice",
      field.variant() == "compact" and chosen == [])
plain = field.height()
field.set_path(scratch / "gone")
check("a folder that is not there is invalid",
      wait_for(lambda: field.valid() is False) and field.variant() == "invalid")
check("and makes room for its message", field.height() == plain + theme.FIELD_ERROR_HEIGHT)
field.set_path(scratch / "gone-too")
field.set_path(there)
check("an answer for a path since replaced is dropped",
      wait_for(lambda: field.valid() is True) and field.path() == str(there))
wait(200)
check("and a late answer does not overwrite it", field.valid() is True)
mime = QMimeData()
mime.setUrls([QUrl.fromLocalFile(str(there))])
field.dropEvent(QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
check("a folder dropped on it is the user's choice", chosen == [os.path.normpath(str(there))])
field.set_path("")
field._edit.setText(str(there))
field._edit.editingFinished.emit()
check("so is a path typed into the empty field", chosen[-1] == str(there)
      and wait_for(lambda: field.valid() is True))
field.setEnabled(False)
check("a disabled PathField still draws", drawn(field))
field.setEnabled(True)
readonly = PathField(str(there), editable=False)
check("a read-only one has no button to change it", not readonly._change.isVisibleTo(readonly))


# ---- TagSelect -------------------------------------------------------------------------------

tags = TagSelect()
check("the tags are the Creator's own list, not a copy", tags.options() is creator.WE_TAGS
      and len(tags.options()) == 25)
changes: list = []
tags.changed.connect(lambda: changes.append(tags.value()))
check("a batch select starts with none", tags.value() == [] and tags.count_text() == "0 / 25")
for tag in ("Game", "Nature", "Girls"):
    tags.toggle(tag)
check("ticked tags keep the order they were ticked in",
      tags.value() == ["Game", "Nature", "Girls"] and tags.count_text() == "3 / 25")
tags.toggle("Nature")
check("ticking one again takes it off", tags.value() == ["Game", "Girls"])
check("every change is said", len(changes) == 4)
tags.clear()
check("Clear leaves none", tags.value() == [] and tags.mode() == "none")
tags.set_value(["Anime", "Anime", " Retro ", None])
check("a value is cleaned as the engine cleans it", tags.value() == ["Anime", "Retro"])

clip = TagSelect(per_file=True)
clip.set_batch_tags(["Game", "Nature"])
check("a clip follows the batch until told otherwise",
      clip.value() is None and clip.mode() == "batch" and clip.tags() == ["Game", "Nature"]
      and clip.count_text() == "batch")
clip.toggle("Anime")
check("ticking a box starts its own list from the batch's",
      clip.value() == ["Game", "Nature", "Anime"] and clip.mode() == "own")
clip.set_batch_tags(["Music"])
check("which the batch no longer moves", clip.tags() == ["Game", "Nature", "Anime"])
for tag in ("Game", "Nature", "Anime"):
    clip.toggle(tag)
check("unticking the last leaves it at none, not back on the batch",
      clip.value() == [] and clip.mode() == "none" and clip.count_text() == "none")
clip.follow_batch()
check("Follow batch goes back", clip.value() is None and clip.tags() == ["Music"])
clip.set_mode("own")
check("choosing Own tags starts from the batch's",
      clip.value() == ["Music"] and clip.mode() == "own")
clip.set_mode("none")
check("choosing None is none", clip.value() == [])
check("an unknown mode is refused", raises(lambda: clip.set_mode("some"), ValueError))
popup = TagPopup(clip, embedded=True)
check("a per-file popup offers the three states first",
      popup.modes is not None and popup.modes.labels() == ["Follow batch", "Own tags", "None"])
popup.modes.set_current_index(0)
check("and choosing one there moves the select", clip.mode() == "batch")
clip.toggle("Retro")
check("a popup follows its select's changes", popup.modes.current_index() == 1)
batch_popup = TagPopup(tags, embedded=True)
check("a batch popup has no modes, and lays the 25 out in four columns",
      batch_popup.modes is None
      and batch_popup.grid.cell_rect(4).top() > batch_popup.grid.cell_rect(3).top()
      and batch_popup.grid.cell_rect(1).left() > batch_popup.grid.cell_rect(0).left())
host.column.addWidget(batch_popup)
batch_popup.grid.setFocus()
batch_popup.grid.set_hot(0)
QTest.keyClick(batch_popup.grid, Qt.Key_Right)
QTest.keyClick(batch_popup.grid, Qt.Key_Space)
check("the keyboard moves through the grid and ticks", tags.value() == ["Anime", "Retro", "Animal"])
check("the footer counts them", batch_popup._selected.text() == "3 selected")
batch_popup.clear_button.click()
check("and Clear there clears", tags.value() == [])
check("a TagSelect draws closed and open", drawn(tags) and drawn(batch_popup))


# ---- progress -----------------------------------------------------------------------------------

bar = ProgressBar(caption=True)
bar.set_value(412, 1000)
check("a hidden bar lands at once", abs(bar.shown_fraction() - 0.412) < 1e-9)
check("its caption is the count and the share", bar.caption() == (f"412 / 1{NB}000", "41%"))
bar.set_state("error")
bar.set_value(620, 1000)
check("an error bar is danger and says where it stopped",
      bar.colour_token() == "danger" and bar.caption()[0] == f"stopped at 620 / 1{NB}000")
bar.set_state("success")
check("success fills it in ok", bar.fraction() == 1.0 and bar.colour_token() == "ok")
check("a bar's height is one of the design's",
      raises(lambda: ProgressBar(height=7), ValueError)
      and all(ProgressBar(height=h).height() == h for h in theme.PROGRESS_HEIGHTS))
moving = ProgressBar()
host.column.addWidget(moving)
moving.show()
QApplication.processEvents()
moving.set_value(700, 1000)
check("a shown bar eases to its new width over motion.slow",
      moving.shown_fraction() < 0.7 and wait_for(lambda: abs(moving.shown_fraction() - 0.7) < 1e-6,
                                                  animations.SLOW * 4))
moving.set_value(100, 1000)
check("and goes backwards at once", abs(moving.shown_fraction() - 0.1) < 1e-9)
moving.set_state("indeterminate")
wait(40)
check("an indeterminate bar runs the shared sweep", animations.loop("indeterminate").running)
moving.set_state("determinate")
ring = ProgressRing(58)
ring.set_value(4, 201)
check("a ring's centre is the honest percentage", ring.label() == "2%")
ring.set_done()
check("a done ring says so with a tick, not a number", ring.state() == "done" and ring.label() == "")
check("rings come in the design's sizes", raises(lambda: ProgressRing(40), ValueError))
for size in theme.RING:
    for state in ("determinate", "indeterminate", "done"):
        r = ProgressRing(size)
        r.set_value(412, 1000)
        if state == "indeterminate":
            r.set_indeterminate()
        elif state == "done":
            r.set_done()
        check(f"a {size} px ring draws {state}", drawn(r))


# ---- cards ----------------------------------------------------------------------------------------

card = StatCard("Reserve", 33421, "8 204 never used", clickable=True)
host.column.addWidget(card)
check("a StatCard writes its number grouped", card.value() == f"33{NB}421"
      and card.card_state() == "default")
card.force_state = "hover"
check("hover shows the way to the page", card.card_state() == "hover" and card._ext.isVisibleTo(card))
card.force_state = None
clicks: list = []
card.clicked.connect(lambda: clicks.append(1))
QTest.mouseClick(card, Qt.LeftButton)
check("a clickable card says it was clicked", clicks == [1])
empty_card = StatCard("Duplicates set aside")
check("with no number it is empty, and says why",
      empty_card.card_state() == "empty" and empty_card.value() == fmt.DASH
      and empty_card.caption() == "no run yet")
empty_card.set_loading()
check("loading hides the words behind the skeleton", empty_card.card_state() == "loading")
warn = StatCard("New since last review", 89, tone="warn")
check("a value can take a tone", warn._value.property("tone") == "warn"
      and raises(lambda: warn.set_value(3, "loud"), KeyError))
for c in (card, empty_card, warn):
    check(f"a StatCard in state {c.card_state()} draws", drawn(c))

started = datetime(2026, 9, 19, 12, 44)
leading = MonitorView("Monitor1", "leading", resolution="2560×1440", title="Harbour Lights Loop",
                      author="Marlow", author_chip="Known", position=4, total=201,
                      shown_for=14 * 60 + 20, remaining=26 * 60, cycle_started=started)
check("a leading monitor wears LEADING in the accent", leading.badge() == ("Leading", "accent")
      and leading.bar_tone() == "accent")
check("its meta line is the author and time on screen",
      leading.meta_text(NOW) == "Marlow · 14 min in")
check("its count is the playlist position", leading.count_text() == "4 / 201"
      and abs(leading.fraction() - 4 / 201) < 1e-9)
check("its facts are the design's three",
      leading.facts(NOW) == [("Shown for", "14 min"), ("Remaining", "26 min"),
                             ("Cycle started", "19 Sep 12:44")])
paused = MonitorView("Monitor1", "paused", remaining=26 * 60, position=4, total=201)
check("a paused one says so in warn", paused.badge() == ("Paused", "warn")
      and paused.remaining_parts() == ("26 min", "paused", "warn") and paused.bar_tone() == "warn")
gone = MonitorView("Monitor1", "disconnected", title="Harbour Lights Loop", position=4, total=201,
                   last_seen=datetime(2026, 9, 25, 11, 2), cycle_started=started,
                   reconstructed=True)
check("a disconnected one is no signal, last seen, in danger",
      gone.title_text() == "no signal" and gone.meta_text(NOW) == "last seen 11:02"
      and gone.icon_tone() == "danger" and gone.remaining_parts()[1:] == ("disconnected", "danger"))
check("a rebuilt cycle start is marked ~", gone.cycle_text(NOW) == "~19 Sep 12:44")
summary = MonitorView("Monitor2", total=201, position=2)
check("a summary monitor wears no badge and a muted bar",
      summary.badge() is None and summary.bar_tone() == "muted")
check("an unknown state is refused", raises(lambda: MonitorView("M", "sleeping"), ValueError))
check("nothing known is a dash, not a zero",
      MonitorView("M").count_text() == fmt.DASH and MonitorView("M").title_text() == fmt.DASH)
compact = MonitorCard(leading)
compact.set_view(leading, NOW)
said = compact.texts()
check("a compact card shows what its view says",
      said["name"] == "Monitor1" and said["badge"] == "LEADING" and said["count"] == "4 / 201"
      and said["meta"] == "Marlow · 14 min in" and said["bar"] == "accent")
detail = MonitorCard(gone, detail=True)
detail.set_view(gone, NOW)
said = detail.texts()
check("a detailed card shows the ring, the count and the facts",
      said["count"] == "4 / 201" and said["ring"] == "2%" and said["title"] == "no signal"
      and said["meta"] == "last seen 11:02"
      and said["facts"] == "— | — disconnected | ~19 Sep 12:44")
detail.set_view(leading, NOW)
check("and follows its view when it changes",
      detail.texts()["chip"] == "KNOWN" and detail.texts()["badge"] == "LEADING"
      and detail.texts()["meta"] == "Marlow")
for view in (leading, paused, gone, summary):
    for is_detail in (False, True):
        c = MonitorCard(view, detail=is_detail)
        check(f"a {'detail' if is_detail else 'compact'} MonitorCard draws {view.state}", drawn(c))


# ---- empty states, steps, activity ----------------------------------------------------------------

empty = EmptyState("Nothing scanned since the last review", "A scan checks all 118 authors.",
                   icon="review", meta="last scan Friday 09:10")
check("an EmptyState says what and why", empty.title().startswith("Nothing")
      and empty.meta() == "last scan Friday 09:10" and not empty._actions_row.isVisibleTo(empty))
empty.add_action(kit.AccentButton("Scan for new items"))
check("its actions row appears with its first action", empty._actions_row.isVisibleTo(empty))
check("its tones are neutral, ok and danger",
      raises(lambda: EmptyState("x", tone="warn"), KeyError))
drop = EmptyState("Drop folders here", icon="copier", drop_zone=True)
dropped: list = []
drop.dropped.connect(dropped.append)
drop.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
check("a drop zone hands over what was dropped", drop.is_drop_zone()
      and dropped == [[os.path.normpath(str(there))]])
for tone in ("neutral", "ok", "danger"):
    e = EmptyState("Title", "Body", tone=tone)
    e.resize(500, 320)
    check(f"an EmptyState draws {tone}", drawn(e))
steps = StepList([("Return", "998 returned", "done"), ("Move", "412 / 1 000", "active"),
                  ("Check", "waiting")])
check("a StepList holds its steps and their states",
      len(steps) == 3 and [steps.step_state(i) for i in range(3)] == ["done", "active", "pending"])
check("an active step's caption is in the accent, a pending one's title quieter",
      steps.caption_tone(1) == "accent" and steps._rows[2][2].property("tone") == "mid")
steps.set_step(2, state="failed", caption="Wallpaper Engine did not restart")
check("a failed step's caption turns danger", steps.caption_tone(2) == "danger"
      and steps.caption(2) == "Wallpaper Engine did not restart")
steps.set_step(0, tone="warn")
check("a caption can take a tone of its own", steps.caption_tone(0) == "warn")
check("an unknown step state is refused",
      raises(lambda: steps.set_step(0, state="half"), ValueError))
line = ActivityLine("1234567890 → myprojects")
check("an ActivityLine leads with the spinner", line.lead() == "spinner"
      and line.shown_text() == "1234567890 → myprojects")
animations.ENABLED = False
check("with motion off it says working, since the spinner cannot",
      line.shown_text() == "working · 1234567890 → myprojects")
animations.ENABLED = True
line.set_thumb(pixmap=None)
check("or with a thumb instead", line.lead() == "thumb")


# ---- the table model ---------------------------------------------------------------------------------

COLUMNS = [Column("#", 32, "right", mono=True), Column("Wallpaper", None),
           Column("Author", 88), Column("Shown", 52, "right", mono=True),
           Column("State", 64, "right", sortable=False)]


class Rows(TableModel):
    def cell(self, item, column):
        return (f"{item[0]:03d}", item[1], item[2], fmt.duration(item[3]) if item[3] else fmt.DASH,
                Cell("on screen", "accent", True) if item[0] == 0 else ChipCell("Queued"))[column]

    def sort_key(self, item, column):
        return item[3] if column == 3 else super().sort_key(item, column)


rows = [(i, f"Wallpaper {i}", ("Marlow", "tidewright", "orbit_lab")[i % 3],
         (60 * (i + 1)) if i < 4 else None) for i in range(12)]
model = Rows(COLUMNS, rows, groups=[Group("shown", "Already shown this cycle"),
                                    Group("queue", "Queue"), Group("never", "Never")],
             group_of=lambda r: "shown" if r[0] < 4 else "queue")
check("a group is a header row over its items, and an empty group is left out",
      model.rowCount() == 14 and model.group_rows() == [0, 5])
check("a header's note is its count out of all rows", model.group_note(0) == "4 of 12"
      and model.group_note(5) == "8 of 12")
check("a header is never selected or enabled", model.flags(model.index(0, 0)) == Qt.NoItemFlags
      and bool(model.flags(model.index(1, 0)) & Qt.ItemIsSelectable))
check("a header reads as its title and note",
      model.data(model.index(0, 0)) == "Already shown this cycle · 4 of 12")
check("an item row reads as its cells", model.data(model.index(1, 1)) == "Wallpaper 0"
      and model.data(model.index(1, 4)) == "on screen" and model.data(model.index(6, 4)) == "Queued")
check("zebra stripes every second row, and starts again under each header",
      [model.zebra(r) for r in range(9)] == [False, False, True, False, True,
                                              False, False, True, False])
model.sort(3, Qt.DescendingOrder)
check("sorting stays inside each group", [model.item_at(r)[0] for r in range(1, 5)] == [3, 2, 1, 0]
      and model.is_group_row(5) and {model.item_at(r)[0] for r in range(6, 14)} == set(range(4, 12)))
check("items with nothing to sort by sort last", model.item_at(6)[3] is None)
model.sort(4)
check("a column that does not sort is ignored", model.sort_column() == 3)
model.sort(1)
check("text sorts by its text", [model.item_at(r)[1] for r in range(6, 9)]
      == ["Wallpaper 10", "Wallpaper 11", "Wallpaper 4"])
model.sort(-1)
check("-1 goes back to the order given", [model.item_at(r)[0] for r in range(1, 5)] == [0, 1, 2, 3])
model.set_filter(lambda r: r[2] == "Marlow")
check("a filter keeps the groups and their counts honest", model.group_note(0) == "2 of 12"
      and model.item_rows() == 4 and model.row_of_item(3) == 2 and model.row_of_item(1) == -1)
model.set_filter(None)
model.set_rows(rows, groups=[Group("shown", "Shown", show_empty=True),
                             Group("never", "Never", show_empty=True)],
               group_of=lambda r: "shown" if r[0] < 0 else "never")
check("show_empty keeps an empty group's header", model.group_rows() == [0, 1]
      and model.group_note(0) == "0 of 12")
check("a column spec refuses what it cannot draw",
      raises(lambda: Column("x", align="justify"), ValueError)
      and raises(lambda: Column("x", thumb="huge"), KeyError)
      and raises(lambda: Column("x", elide="middle"), ValueError))

# 33 000 rows, as the reserve has
BIG = 33_000
big_rows = [(i, f"Wallpaper {i}", ("Marlow", "tidewright", "orbit_lab")[i % 3], i * 7 % 3600)
            for i in range(BIG)]
start = time.perf_counter()
big = Rows(COLUMNS, big_rows, groups=[Group("shown", "Shown"), Group("queue", "Queue")],
           group_of=lambda r: "shown" if r[0] < 4 else "queue")
built_ms = (time.perf_counter() - start) * 1000
check(f"a {fmt.count(BIG)}-row model builds in under 100 ms (took {built_ms:.1f} ms)",
      built_ms < 100 and big.rowCount() == BIG + 2)
start = time.perf_counter()
big.sort(3)
sorted_ms = (time.perf_counter() - start) * 1000
check(f"and sorts in well under a second ({sorted_ms:.1f} ms)", sorted_ms < 500)

table = Table()
table.setModel(big)
table_host = Host(900, 520)
table_host.column.addWidget(table)
table_host.show()
QApplication.processEvents()
painted_rows: list = []
real_paint = table._delegate.paint_row


def counting(painter, rect, row, **flags):
    painted_rows.append(row)
    real_paint(painter, rect, row, **flags)


table._delegate.paint_row = counting
table.viewport().repaint()
visible = table.visible_rows()
check(f"a full repaint paints the rows on screen and no others ({len(painted_rows)} rows)",
      len(visible) > 3 and sorted(painted_rows) == list(visible))
painted_rows.clear()
table.verticalScrollBar().setValue(table.verticalScrollBar().value() + 40)
table.viewport().repaint()
check("after a scroll, still only rows on screen", painted_rows
      and set(painted_rows) <= set(table.visible_rows()))
painted_rows.clear()
table.verticalScrollBar().setValue(table.verticalScrollBar().maximum() // 2)
table.viewport().repaint()
check("in the middle of 33 000 rows, the same few", len(painted_rows) == len(table.visible_rows())
      and len(painted_rows) < 40)
table._delegate.paint_row = real_paint
updated: list = []
real_update = table._update_row
table._update_row = lambda row: updated.append(row) if row >= 0 else None
row_a, row_b = table.visible_rows()[2], table.visible_rows()[4]
table._hover_to(row_a)
table._hover_to(row_a)
check("the pointer staying on a row repaints nothing more", updated == [row_a])
table._hover_to(row_b)
check("moving to another row repaints exactly the two rows", updated == [row_a, row_a, row_b])
table._hover_to(big.group_rows()[1])
check("a group header is never the hovered row", table.hovered_row() == -1)
table._update_row = real_update
table.verticalScrollBar().setValue(0)
table.selectRow(3)
chosen_item = big.item_at(3)
table.sort_by(1, Qt.DescendingOrder)
check("sorting from the header keeps the same item selected",
      table.selected_items() == [chosen_item])
check("the header draws the sorted column", big.sort_column() == 1 and drawn(table.horizontalHeader()))
footer = TableFooter(f"virtualised · {fmt.count(BIG)} rows")
footer.set_note("Wallpaper Engine decides the order")
summary_strip = TableSummary(["33 421 folders", "8 204 never used", "4.2 TB"])
check("a table's footer and summary carry their words",
      footer.text().endswith("rows") and summary_strip.items()[0] == "33 421 folders")
table_host.close()


# ---- thumbnails: local previews ----------------------------------------------------------------------

cache = scratch / "cache"
loader = ThumbLoader(local_root=cache)
arrived: dict = {}
loader.local_done.connect(lambda key, image: arrived.__setitem__(key, image))
folder = scratch / "wallpaper_a"
folder.mkdir()
solid_image(folder / "preview.png", "#ff0000", QSize(640, 360))
loader.request_local("a", str(folder), QSize(120, 68))
check("a folder's preview is read on a worker and arrives by signal",
      wait_for(lambda: "a" in arrived) and not arrived["a"].isNull())
check("fitted into the box asked for", arrived["a"].width() <= 120 and arrived["a"].height() <= 68)
found = thumbs.find_preview(str(folder))
kept = thumbs.cache_file(cache, *found)
check("and kept in the cache under its path, time and size", kept.exists()
      and kept.parent.parent == cache)
check("a key is asked for once until the loader is retargeted",
      not loader.request_local("a", str(folder)))
# prove the cache is read: replace the kept still with another colour, and
# see that colour come back
solid_image(kept, "#0000ff", QSize(64, 36))
loader.retarget_local()
arrived.clear()
loader.request_local("a", str(folder))
check("an unchanged preview comes from the cache",
      wait_for(lambda: "a" in arrived) and arrived["a"].pixelColor(5, 5).blue() > 200)
os.utime(folder / "preview.png", ns=(time.time_ns(), time.time_ns() + 10_000_000_000))
loader.retarget_local()
arrived.clear()
loader.request_local("a", str(folder))
check("a preview whose time changed is read again",
      wait_for(lambda: "a" in arrived) and arrived["a"].pixelColor(5, 5).red() > 200)
fresh = thumbs.cache_file(cache, *thumbs.find_preview(str(folder)))
check("into a new still, and the old one is gone", fresh != kept and fresh.exists()
      and not kept.exists())
bare = scratch / "wallpaper_bare"
bare.mkdir()
(bare / "project.json").write_text("{}", encoding="utf-8")
loader.request_local("bare", str(bare))
check("a folder with no preview answers with nothing",
      wait_for(lambda: "bare" in arrived) and arrived["bare"].isNull())
loader.request_local("missing", str(scratch / "not-there"))
check("and so does a folder that is not there",
      wait_for(lambda: "missing" in arrived) and arrived["missing"].isNull())


def two_frame_gif() -> bytes:
    """A black frame, then a white one: a wallpaper that fades in."""
    head = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
            b"!\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00")
    frame = b"!\xf9\x04\x04\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02"
    return head + frame + b"D\x01\x00" + frame + b"L\x01\x00" + b";"


gif_folder = scratch / "wallpaper_gif"
gif_folder.mkdir()
(gif_folder / "preview.gif").write_bytes(two_frame_gif())
solid_image(gif_folder / "preview.jpg", "#00ff00")
loader.request_local("gif", str(gif_folder))
check("a GIF is preferred, and its first bright frame is the still",
      wait_for(lambda: "gif" in arrived) and arrived["gif"].pixelColor(0, 0).lightness() > 240)
loader.stop()

thumb = Thumb("row", loader=ThumbLoader(local_root=cache))
thumb_host = Host(300, 200)
thumb_host.column.addWidget(thumb)
thumb_host.show()
check("a Thumb with nothing is the placeholder", thumb.state() == "placeholder" and drawn(thumb))
thumb.set_source(str(folder))
check("given a folder it loads, then shows the picture",
      thumb.state() == "loading" and wait_for(lambda: thumb.state() == "image"))
thumb.set_source(str(bare))
check("and a folder with none is the placeholder again",
      wait_for(lambda: thumb.state() == "placeholder"))
check("sizes are the design's", raises(lambda: Thumb("poster"), KeyError)
      and Thumb("card").size() == QSize(72, 41) and Thumb("wide").size() == QSize(298, 84))
thumb_host.close()


# ---- a table of thumbs: only the rows on screen, and nothing on the GUI thread ------------------------

# enough folders that the middle of the table shows previews the top did not
folders = []
for i in range(200):
    f = scratch / f"library_{i:03d}"
    f.mkdir()
    solid_image(f / ("preview.jpg" if i % 2 else "preview.png"), "#446688")
    folders.append(str(f))


class Library(TableModel):
    def thumb_source(self, item):
        return folders[item[0] % len(folders)]

    def cell(self, item, column):
        return (item[1], item[2])[column]


library_rows = [(i, f"folder_{i:05d}", ("scene", "video", "web")[i % 3]) for i in range(BIG)]
library = Library([Column("Folder", None, thumb="row"), Column("Type", 60)], library_rows)
thumb_loader = ThumbLoader(local_root=scratch / "table-cache")

GUARDED = [(os, "stat"), (os, "lstat"), (os, "scandir"), (os, "listdir"), (os.path, "isdir"),
           (os.path, "isfile"), (os.path, "exists"), (Path, "stat"), (Path, "exists"),
           (Path, "is_dir"), (Path, "iterdir")]
on_gui_thread: list = []
originals = {}


def guard(owner, name):
    real = getattr(owner, name)
    originals[(owner, name)] = real

    def guarded(*args, **kwargs):
        if threading.current_thread() is threading.main_thread():
            on_gui_thread.append(f"{getattr(owner, '__name__', owner)}.{name}")
            raise OSError(f"{name} called on the GUI thread")
        return real(*args, **kwargs)
    setattr(owner, name, guarded)


for owner, name in GUARDED:
    guard(owner, name)
try:
    check("the guard does catch a GUI-thread call",
          raises(lambda: os.path.isdir(folders[0]), OSError)
          and raises(lambda: Path(folders[0]).exists(), OSError) and len(on_gui_thread) == 2)
    on_gui_thread.clear()
    library_table = Table(loader=thumb_loader)
    library_table.setModel(library)
    watched = PathField()
    shown = Host(900, 560)
    shown.column.addWidget(watched)
    shown.column.addWidget(library_table)
    shown.show()
    watched.set_path(folders[0])
    check("with the disk off limits to the GUI thread, the folder is still checked",
          wait_for(lambda: watched.valid() is True))
    on_screen = {library.thumb_source(library.item_at(r)) for r in library_table.visible_rows()}
    check("and the thumbs of the rows on screen arrive",
          wait_for(lambda: all(library_table.thumb_state(s)[0] == "image" for s in on_screen)))
    check("having been asked for those rows and no others",
          set(thumb_loader.asked_local()) == on_screen)
    library_table.verticalScrollBar().setValue(library_table.verticalScrollBar().maximum() // 2)
    wait(Table.THUMB_SETTLE + 60)
    now_shown = {library.thumb_source(library.item_at(r)) for r in library_table.visible_rows()}
    check("after a scroll, the new rows are asked for and the queue is dropped",
          now_shown.isdisjoint(on_screen) and set(thumb_loader.asked_local()) == now_shown)
    wait_for(lambda: all(library_table.thumb_state(s)[0] == "image" for s in now_shown))
    library_table.viewport().repaint()
    QApplication.processEvents()
finally:
    for (owner, name), real in originals.items():
        setattr(owner, name, real)
check(f"no file-system call was made on the GUI thread ({len(on_gui_thread)} were)",
      on_gui_thread == [])
thumb_loader.stop()
shown.close()

check("the kit exports what the pages will use",
      all(hasattr(kit, name) for name in kit.__all__))
check("the stylesheet's new rules all resolve", not theme.unresolved(theme.stylesheet()))

host.close()
print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
