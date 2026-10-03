"""creator.py — Build Wallpaper Engine projects from videos alone.

A Wallpaper Engine video wallpaper is a folder holding three things: the video,
a preview image, and a project.json describing them. This builds them in bulk,
and *generates* the preview straight from the video with ffmpeg — so a folder of
clips needs nothing else to become a folder of working wallpapers.

For every video in the source folder it:
  1. Creates  target/<name>-<random_suffix>
  2. Renders  preview.gif  from the video (square 1:1, 5 s, skipping the first second)
  3. Copies the video inside
  4. Writes and verifies project.json, the video and the preview
  5. Removes the source only after verification, when Move is selected

An earlier version of this engine required a matching preview file to already
exist for every clip, and skipped the ones without. That turned out to be the
whole of the work — nobody has forty previews lying around — so the requirement
is gone and the rendering below replaced it. The folder naming and the
project.json shape are carried over from it unchanged, so wallpapers built by
either version are indistinguishable.

ffmpeg is located on PATH, or falls back to the binary bundled with the
imageio-ffmpeg package. If the GIF cannot be produced the engine falls back to
a single still frame (preview.jpg); if even that fails the item is skipped and
its video is left untouched in the source folder.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import random
import threading
import time

from .. import external

# Folder naming + project.json come from the Creator engine unchanged.

# Video extensions Wallpaper Engine can use.

# ---- Naming a project, and describing it -----------------------------------
#
# Both of these came from the earlier preview-matching engine and are kept
# exactly as they were, so a project built now is byte-identical in shape to
# one built then.

# Keeps two suffixes made in the same second from colliding.
_suffix_counter = 0


def generate_suffix():
    """A folder suffix: epoch, pid, six random digits, and a counter.

    Wallpaper Engine's own editor suffixes project folders the same way, and two
    clips of the same name have to land in two folders. The counter is the part
    that is not in the original recipe: epoch plus pid plus randint collides
    often enough to matter when forty projects are built in one second.
    """
    global _suffix_counter
    _suffix_counter += 1
    return (f"{int(time.time())}{os.getpid()}"
            f"{random.randint(0, 999999):06d}{_suffix_counter}")


# Wallpaper Engine's genre tags.
#
# Measured rather than recalled. 21 distinct values appear across 1 529
# project.json files in a real workshop library, and 23 of the 25 below are
# present verbatim in Wallpaper Engine's own UI bundle. The two that are not —
# "Sci-Fi" and "Television" — were found in real projects instead, so this list
# is the union of both sources.
#
# It is an offer, not a rule: project.json takes any string, so a UI can show
# these and still let anything be typed.
WE_TAGS = (
    "Abstract", "Animal", "Anime", "Cartoon", "CGI", "Cyberpunk", "Fantasy",
    "Game", "Girls", "Guys", "Landscape", "Medieval", "Memes", "MMD", "Music",
    "Nature", "Pixel art", "Relaxing", "Retro", "Sci-Fi", "Sports",
    "Technology", "Television", "Unspecified", "Vehicle",
)


def clean_tags(tags):
    """Whatever was handed in, as a list of distinct non-empty strings, in order.

    The order is kept because it is the order somebody chose, and duplicates go
    because Wallpaper Engine shows a repeated tag twice.
    """
    out = []
    for tag in tags or ():
        # str() is not applied blindly: str(None) is "None", which would put the
        # word None in a wallpaper's tags.
        if not isinstance(tag, str):
            continue
        text = tag.strip()
        if text and text not in out:
            out.append(text)
    return out


def build_project_json(basename, filename, preview_name, tags=None):
    """The project.json Wallpaper Engine expects for a video wallpaper.

    ``tags`` is whatever the caller chose; nothing is invented when it is empty.
    An earlier version of this function hard-coded a single genre, which was
    right for the one library it was written in and wrong for every other.
    """
    return {
        "file": basename,
        "general": {
            "properties": {
                "schemecolor": {
                    "order": 0,
                    "text": "ui_browse_properties_scheme_color",
                    "type": "color",
                    "value": "0.20392 0.14902 0.10196",
                }
            }
        },
        "preview": preview_name,
        "snapshotformat": -1,
        "snapshotoverlay": "",
        "tags": clean_tags(tags),
        "title": filename,
        "type": "video",
        "version": 0,
    }


VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v")

# --- Preview GIF settings -------------------------------------------------
GIF_SIZE = 480        # px; previews are square (1:1)
GIF_FPS = 15
GIF_DURATION = 5.0    # seconds of video captured
GIF_SKIP = 1.0        # seconds skipped at the start (avoids fade-ins/black frames)


def _scale_filter(size=GIF_SIZE):
    """ffmpeg filter producing a square (1:1) frame.

    A centre square of the smaller dimension is cropped first, then scaled to
    size x size — so nothing is stretched and no black bars are added. The
    commas inside min() must stay escaped or ffmpeg reads them as filter
    separators.
    """
    return (r"crop=min(iw\,ih):min(iw\,ih)," f"scale={size}:{size}:flags=lanczos")

# Hide the console window ffmpeg would otherwise flash in a windowed build.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def find_ffmpeg():
    """Return a path to an ffmpeg executable, or None if none is available."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        # It runs the binary once to see that it works, with subprocess and this
        # process's environment. Only the DLL folder can be kept from it, and that
        # stays cleared for the whole run (once per process: the answer is cached).
        with external.clean_dll_search():
            return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 — package missing or binary not downloaded
        return None


def _run(args):
    """Run ffmpeg quietly; return the CompletedProcess."""
    return external.run(
        args, capture_output=True, text=True, errors="replace",
        creationflags=_NO_WINDOW,
    )


def parse_probe_output(stderr):
    """Read length and video dimensions from a single ffmpeg input banner.

    ``ffmpeg -i`` normally returns a nonzero code because it has no output;
    stream metadata, rather than that code, tells us whether a video was read.
    Restrict the resolution match to a Video stream so codec ids and audio
    stream values cannot accidentally become dimensions.
    """
    metadata = {"duration": None, "width": None, "height": None, "video": False}
    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr or "")
    if duration:
        hours, minutes, seconds = duration.groups()
        metadata["duration"] = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    for line in (stderr or "").splitlines():
        if not re.search(r"Stream\s.*?Video:", line):
            continue
        if "attached pic" in line:
            continue
        metadata["video"] = True
        dimensions = re.search(r"(?:^|[\s,])(\d+)x(\d+)(?=[\s,\[]|$)",
                               line.split("Video:", 1)[1])
        if dimensions and "attached pic" not in line:
            width, height = map(int, dimensions.groups())
            if width > 0 and height > 0:
                metadata.update(width=width, height=height)
                break
    return metadata


def probe_metadata(ffmpeg, video_path):
    """Probe a video once, returning its length and resolution."""
    proc = _run([ffmpeg, "-i", video_path])
    return parse_probe_output(proc.stderr)


def probe_duration(ffmpeg, video_path):
    """Backwards-compatible duration helper; source reads use probe_metadata."""
    return probe_metadata(ffmpeg, video_path)["duration"]


def _segment(duration):
    """Pick (start, length) for the preview given the clip's duration."""
    if duration is None:
        return GIF_SKIP, GIF_DURATION
    if duration <= GIF_SKIP + 1.0:
        # Too short to skip anything — take it from the top.
        return 0.0, max(min(GIF_DURATION, duration), 0.5)
    return GIF_SKIP, max(min(GIF_DURATION, duration - GIF_SKIP), 0.5)


def make_gif(ffmpeg, video_path, out_path, duration=None,
             size=GIF_SIZE, fps=GIF_FPS):
    """Render a square (1:1) animated GIF preview. Returns True on success.

    Two-pass palettegen/paletteuse — a single pass produces badly banded GIFs.
    """
    start, length = _segment(duration)
    vf = f"fps={fps}," + _scale_filter(size)
    palette = out_path + ".palette.png"

    try:
        r1 = _run([
            ffmpeg, "-y", "-ss", str(start), "-t", str(length), "-i", video_path,
            "-vf", vf + ",palettegen=stats_mode=diff", palette,
        ])
        if r1.returncode != 0 or not os.path.isfile(palette):
            return False

        r2 = _run([
            ffmpeg, "-y", "-ss", str(start), "-t", str(length), "-i", video_path,
            "-i", palette,
            "-lavfi", vf + "[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5"
                           ":diff_mode=rectangle",
            "-loop", "0", out_path,
        ])
        ok = r2.returncode == 0 and os.path.isfile(out_path) \
            and os.path.getsize(out_path) > 0
        return ok
    finally:
        try:
            os.remove(palette)
        except OSError:
            pass


def make_still(ffmpeg, video_path, out_path, duration=None, size=GIF_SIZE):
    """Fallback: extract a single square frame as a static preview."""
    start, _ = _segment(duration)
    r = _run([
        ffmpeg, "-y", "-ss", str(start), "-i", video_path,
        "-frames:v", "1", "-vf", _scale_filter(size), out_path,
    ])
    return r.returncode == 0 and os.path.isfile(out_path) \
        and os.path.getsize(out_path) > 0


class VideoItem:
    """One video in the source folder."""

    def __init__(self, video_path):
        self.video_path = os.path.normpath(video_path)
        self.basename = os.path.basename(self.video_path)          # clip.mp4
        self.filename = os.path.splitext(self.basename)[0]         # clip
        self.size = self._safe_size(self.video_path)
        self._exists = os.path.isfile(self.video_path)
        self.width = None
        self.height = None
        self.duration = None
        self.metadata_read = False
        self.reason = None
        # None means "whatever the batch is tagged with"; a list — even an empty
        # one — means this clip was decided about on its own. The difference
        # matters: clearing a clip's tags has to survive the batch changing.
        self.tags = None

    @staticmethod
    def _safe_size(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    @property
    def valid(self):
        """Cached scan result, safe for a table to read on the GUI thread."""
        return self._exists and self.reason is None

    @property
    def resolution(self):
        return f"{self.width}×{self.height}" if self.width and self.height else None


def resolve_tags(item, batch_tags=()):
    """Follow the batch only when the clip has no tag decision of its own."""
    return clean_tags(batch_tags if item.tags is None else item.tags)


def needs_tags(item, batch_tags=()):
    return not resolve_tags(item, batch_tags)


def _cancelled(cancelled):
    if cancelled is None:
        return False
    return cancelled.is_set() if hasattr(cancelled, "is_set") else bool(cancelled())


def read_source(source_dir, on_item=None, cancelled=None, ffmpeg=None, on_check=None):
    """Read a folder on a worker, streaming each file with cached metadata.

    Return the files checked so far if stopped. Unsupported files remain in the
    result with a reason, so the page can explain the usable/unsupported count.
    ``on_item(item, checked, total)`` runs on the caller's worker thread. Nothing
    is written, and each supported file uses exactly one ffmpeg probe.
    """
    if _cancelled(cancelled):
        return []
    if not os.path.isdir(source_dir):
        raise FileNotFoundError(f"Source folder not found: {source_dir}")
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Install ffmpeg or imageio-ffmpeg.")
    paths = []
    with os.scandir(source_dir) as entries:
        for entry in entries:
            if _cancelled(cancelled):
                return []
            if entry.is_file():
                paths.append(entry.path)
    paths.sort(key=lambda path: (os.path.basename(path).casefold(), path))
    total = len(paths)
    items = []
    for path in paths:
        if _cancelled(cancelled):
            break
        if on_check:
            on_check(os.path.basename(path), len(items), total)
        item = VideoItem(path)
        extension = os.path.splitext(path)[1].lower()
        if extension not in VIDEO_EXTS:
            item.reason = f"unsupported — {extension or 'no file extension'}"
        else:
            try:
                # Stat alone does not establish that the worker can read it.
                with open(path, "rb") as source:
                    source.read(1)
                metadata = probe_metadata(ffmpeg, path)
                item.metadata_read = True
                item.duration = metadata["duration"]
                item.width = metadata["width"]
                item.height = metadata["height"]
                if not metadata["video"] or item.size <= 0:
                    item.reason = "could not be read — file may be damaged"
            except (OSError, subprocess.SubprocessError):
                item.reason = "could not be read — file may be damaged"
        if _cancelled(cancelled):
            break
        items.append(item)
        if on_item:
            on_item(item, len(items), total)
    return items


def scan_source(source_dir):
    """Scan the source folder and return VideoItems, sorted by name."""
    items = []
    if not os.path.isdir(source_dir):
        return items
    for entry in sorted(os.listdir(source_dir)):
        if entry.lower().endswith(VIDEO_EXTS):
            full = os.path.join(source_dir, entry)
            if os.path.isfile(full):
                items.append(VideoItem(full))
    return items


def human_size(num_bytes):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


class BuildEngine:
    """
    Background engine that turns bare videos into Wallpaper Engine projects.

    Optional callbacks run on the worker thread:
        log(text)                              — a log line
        progress(done, total)                  — overall progress by video
        item_done(name, status, folder, preview_path) — per-video result
        finished(report)                       — completion (list of dict)
    """

    def __init__(self, log=None, progress=None, item_done=None, finished=None,
                 item_progress=None, item_result=None):
        self._log = log or (lambda *_: None)
        self._progress = progress or (lambda *_: None)
        self._item_done = item_done or (lambda *_: None)
        self._finished = finished or (lambda *_: None)
        self._item_progress = item_progress or (lambda *_: None)
        self._item_result = item_result or (lambda *_: None)

        self._cancel = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._thread = None

    # ----- thread control -----------------------------------------------------

    def start(self, items, target_dir, move=True, tags=None, skip_needs_tags=False):
        if self.is_running():
            return False
        self._cancel.clear()
        self._resume.set()
        # Freeze the selected subset for this run.
        items = list(items)
        self._thread = threading.Thread(
            target=self._run,
            args=(items, target_dir, move, clean_tags(tags), skip_needs_tags),
            daemon=True,
        )
        self._thread.start()
        return True

    def cancel(self):
        self._cancel.set()
        self._resume.set()

    def pause(self):
        """Let the current wallpaper finish, then wait before the next one."""
        self._resume.clear()

    def resume(self):
        self._resume.set()

    def is_paused(self):
        return not self._resume.is_set()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ----- main logic ---------------------------------------------------------

    def _wait_until_ready(self):
        while not self._resume.wait(0.1):
            if self._cancel.is_set():
                return False
        return not self._cancel.is_set()

    @staticmethod
    def _entry(item, status="failed", reason=None):
        return {"name": item.basename, "status": status, "folder": None,
                "preview": None, "preview_path": None, "reason": reason,
                "size": item.size, "bytes_written": 0, "video_path": item.video_path}

    def _run(self, items, target_dir, move, tags, skip_needs_tags=False):
        report = []
        total = len(items)
        self._progress(0, total)

        ffmpeg = find_ffmpeg()
        run_error = None
        if not ffmpeg:
            run_error = "ffmpeg not found; install ffmpeg or imageio-ffmpeg"
            self._log(f"[ERROR] {run_error}")

        self._log(
            f"[START] Building {total} video(s). Mode: "
            f"{'move' if move else 'copy'}. Preview: {GIF_SIZE}×{GIF_SIZE} (1:1) "
            f"@ {GIF_FPS}fps, {GIF_DURATION:g}s from {GIF_SKIP:g}s."
        )
        self._log(f"[TAGS]  {', '.join(tags) if tags else 'none'}"
                  + (" (clips with their own tags override this)"
                     if any(getattr(i, "tags", None) is not None for i in items) else ""))

        if not run_error:
            try:
                os.makedirs(target_dir, exist_ok=True)
            except OSError as exc:
                run_error = f"could not create target folder: {exc}"
                self._log(f"[ERROR] {run_error}")

        done = 0
        for it in items:
            if not self._wait_until_ready():
                entry = self._entry(it, "skipped", "stopped — not built")
            elif getattr(it, "reason", None):
                status = "skipped" if it.reason.startswith("unsupported") else "failed"
                entry = self._entry(it, status, it.reason)
            elif skip_needs_tags and needs_tags(it, tags):
                entry = self._entry(it, "skipped", "skipped — no tags")
            elif run_error:
                entry = self._entry(it, "failed", f"failed — {run_error}")
            else:
                entry = self._process_item(it, target_dir, move, ffmpeg, tags)
            report.append(entry)
            self._item_result(entry)
            self._item_done(it.basename, entry["status"], entry["folder"],
                            entry["preview_path"])
            if entry["reason"]:
                self._log(f"[{entry['status'].upper()}] {it.basename}: {entry['reason']}")
            done += 1
            self._progress(done, total)

        self._print_summary(report)
        self._finished(report)

    def _process_item(self, item, target_dir, move, ffmpeg, tags=()):
        entry = self._entry(item)
        folder = None
        committed = False
        try:
            if not os.path.isfile(item.video_path):
                entry["reason"] = "could not be read — file may be damaged"
                return entry
            folder_name = f"{item.filename}-{generate_suffix()}"
            candidate = os.path.join(target_dir, folder_name)
            os.makedirs(candidate)
            folder = candidate

            # --- preview: GIF, falling back to a still frame ---
            self._item_progress(item.basename, "making preview", None)
            self._log(f"[GIF]   {item.basename} → rendering preview…")
            duration = (item.duration if item.metadata_read
                        else probe_duration(ffmpeg, item.video_path))
            gif_path = os.path.join(folder, "preview.gif")
            preview_name = None

            if make_gif(ffmpeg, item.video_path, gif_path, duration):
                preview_name = "preview.gif"
                self._log(
                    f"        ⤷ preview.gif ({human_size(os.path.getsize(gif_path))})")
            else:
                self._log(f"[WARN]  {item.basename}: GIF failed, trying a still frame…")
                jpg_path = os.path.join(folder, "preview.jpg")
                if make_still(ffmpeg, item.video_path, jpg_path, duration):
                    preview_name = "preview.jpg"
                    self._log("        ⤷ preview.jpg (static fallback)")

            if not preview_name:
                self._log(f"[ERROR] {item.basename}: could not create any preview.")
                self._cleanup(folder)
                entry["reason"] = "could not be read — file may be damaged"
                return entry

            if self._cancel.is_set():
                self._cleanup(folder)
                self._log(f"[CANCEL] {item.basename}: rolled back.")
                entry.update(status="skipped", reason="stopped — not built")
                return entry

            # --- video ---
            dst_video = os.path.join(folder, item.basename)
            # Move is copy + verify + remove. Removing the source earlier would
            # make a later JSON write failure delete the only copy at cleanup.
            source_size = os.path.getsize(item.video_path)
            self._item_progress(item.basename, "copying video", os.path.join(folder, preview_name))
            shutil.copy2(item.video_path, dst_video)

            # --- project.json ---
            # A clip that was decided about on its own keeps its own answer,
            # including an empty one.
            data = build_project_json(item.basename, item.filename, preview_name,
                                      resolve_tags(item, tags))
            project_path = os.path.join(folder, "project.json")
            self._item_progress(item.basename, "writing project.json", os.path.join(folder, preview_name))
            with open(project_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent="\t")

            self._item_progress(item.basename, "verifying wallpaper", os.path.join(folder, preview_name))
            self._verify_project(folder, item.basename, preview_name, data,
                                 source_size)
            if self._cancel.is_set():
                self._cleanup(folder)
                self._log(f"[CANCEL] {item.basename}: rolled back.")
                entry.update(status="skipped", reason="stopped — not built")
                return entry
            # Measure everything that the report needs before removing input.
            bytes_written = sum(os.path.getsize(os.path.join(folder, name))
                                for name in (item.basename, preview_name, "project.json"))
            if move:
                os.remove(item.video_path)
            committed = True
            entry.update(status="ok", folder=folder_name, preview=preview_name,
                         preview_path=os.path.join(folder, preview_name),
                         bytes_written=bytes_written)
            self._log(f"[OK]    {folder_name}")
        except Exception as exc:  # noqa: BLE001 — one failure must not stop the run
            if committed:
                # A notification failure after a successful Move must never
                # remove the now-only copy of the video.
                return entry
            entry["reason"] = f"failed — {exc}"
            if folder and os.path.isdir(folder):
                self._cleanup(folder)
            self._log(f"[ERROR] {item.basename}: {exc}")
        return entry

    @staticmethod
    def _verify_project(folder, basename, preview_name, expected, source_size):
        """Read back a complete project before Move can remove its source."""
        if source_size <= 0:
            raise OSError("source video is empty")
        if os.path.getsize(os.path.join(folder, basename)) != source_size:
            raise OSError("copied video size does not match the source")
        if os.path.getsize(os.path.join(folder, preview_name)) <= 0:
            raise OSError("preview is empty")
        with open(os.path.join(folder, "project.json"), encoding="utf-8") as f:
            actual = json.load(f)
        if actual != expected:
            raise OSError("project.json did not read back correctly")

    @staticmethod
    def _cleanup(folder):
        """Remove a half-built project folder so nothing broken is left behind."""
        try:
            shutil.rmtree(folder)
        except OSError:
            pass

    def _print_summary(self, report):
        ok = sum(1 for e in report if e["status"] == "ok")
        failed = sum(1 for e in report if e["status"] == "failed")
        skipped = sum(1 for e in report if e["status"] == "skipped")
        gifs = sum(1 for e in report if e["preview"] == "preview.gif")
        stills = sum(1 for e in report if e["preview"] == "preview.jpg")
        self._log("")
        self._log("=" * 50)
        self._log(f"RESULT: created {ok}, skipped {skipped}, failed {failed} "
                  f"(previews: {gifs} gif, {stills} still).")
        self._log("=" * 50)
