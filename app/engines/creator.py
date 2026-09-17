"""creator.py — Build Wallpaper Engine projects from videos alone.

A Wallpaper Engine video wallpaper is a folder holding three things: the video,
a preview image, and a project.json describing them. This builds them in bulk,
and *generates* the preview straight from the video with ffmpeg — so a folder of
clips needs nothing else to become a folder of working wallpapers.

For every video in the source folder it:
  1. Creates  target/<name>-<random_suffix>
  2. Renders  preview.gif  from the video (square 1:1, 5 s, skipping the first second)
  3. Moves (or copies) the video inside
  4. Writes   project.json  in Wallpaper Engine format

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
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 — package missing or binary not downloaded
        return None


def _run(args):
    """Run ffmpeg quietly; return the CompletedProcess."""
    return subprocess.run(
        args, capture_output=True, text=True, errors="replace",
        creationflags=_NO_WINDOW,
    )


def probe_duration(ffmpeg, video_path):
    """Video length in seconds, parsed from ffmpeg's banner. None if unknown."""
    proc = _run([ffmpeg, "-i", video_path])
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", proc.stderr or "")
    if not m:
        return None
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)


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
        """Every readable video is buildable — the preview is generated."""
        return os.path.isfile(self.video_path)


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

    Callbacks (optional, called from the worker thread) — same shape as
    creator.BuildEngine so the UI layer stays uniform:
        log(text)               — a log line
        progress(done, total)   — overall progress by video
        item_done(name, status) — per-video result ('ok' | 'failed')
        finished(report)        — completion (report = list of dict)
    """

    def __init__(self, log=None, progress=None, item_done=None, finished=None):
        self._log = log or (lambda *_: None)
        self._progress = progress or (lambda *_: None)
        self._item_done = item_done or (lambda *_: None)
        self._finished = finished or (lambda *_: None)

        self._cancel = threading.Event()
        self._thread = None

    # ----- thread control -----------------------------------------------------

    def start(self, items, target_dir, move=True, tags=None):
        if self.is_running():
            return False
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run, args=(items, target_dir, move, clean_tags(tags)),
            daemon=True,
        )
        self._thread.start()
        return True

    def cancel(self):
        self._cancel.set()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ----- main logic ---------------------------------------------------------

    def _run(self, items, target_dir, move, tags):
        report = []
        total = len(items)
        self._progress(0, total)

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            self._log("[ERROR] ffmpeg not found. Install it or add the "
                      "imageio-ffmpeg package (pip install imageio-ffmpeg).")
            self._finished(report)
            return

        self._log(
            f"[START] Building {total} video(s). Mode: "
            f"{'move' if move else 'copy'}. Preview: {GIF_SIZE}×{GIF_SIZE} (1:1) "
            f"@ {GIF_FPS}fps, {GIF_DURATION:g}s from {GIF_SKIP:g}s."
        )
        self._log(f"[TAGS]  {', '.join(tags) if tags else 'none'}"
                  + (" (clips with their own tags override this)"
                     if any(getattr(i, "tags", None) is not None for i in items) else ""))

        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError as exc:
            self._log(f"[ERROR] Could not create target folder: {exc}")
            self._finished(report)
            return

        done = 0
        for it in items:
            if self._cancel.is_set():
                self._log("[CANCEL] Operation cancelled by user.")
                break
            entry = self._process_item(it, target_dir, move, ffmpeg, tags)
            report.append(entry)
            self._item_done(it.basename, entry["status"])
            done += 1
            self._progress(done, total)

        self._print_summary(report)
        self._finished(report)

    def _process_item(self, item, target_dir, move, ffmpeg, tags=()):
        entry = {"name": item.basename, "status": "failed", "folder": None,
                 "preview": None}
        folder = None
        try:
            folder_name = f"{item.filename}-{generate_suffix()}"
            folder = os.path.join(target_dir, folder_name)
            os.makedirs(folder, exist_ok=True)
            entry["folder"] = folder_name

            # --- preview: GIF, falling back to a still frame ---
            self._log(f"[GIF]   {item.basename} → rendering preview…")
            duration = probe_duration(ffmpeg, item.video_path)
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
                return entry
            entry["preview"] = preview_name

            if self._cancel.is_set():
                self._cleanup(folder)
                self._log(f"[CANCEL] {item.basename}: rolled back.")
                return entry

            # --- video ---
            dst_video = os.path.join(folder, item.basename)
            if move:
                shutil.move(item.video_path, dst_video)
            else:
                shutil.copy2(item.video_path, dst_video)

            # --- project.json ---
            # A clip that was decided about on its own keeps its own answer,
            # including an empty one.
            own = getattr(item, "tags", None)
            data = build_project_json(item.basename, item.filename, preview_name,
                                      tags if own is None else own)
            with open(os.path.join(folder, "project.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent="\t")

            entry["status"] = "ok"
            self._log(f"[OK]    {folder_name}")
        except Exception as exc:  # noqa: BLE001 — one failure must not stop the run
            self._log(f"[ERROR] {item.basename}: {exc}")
            if folder:
                self._cleanup(folder)
        return entry

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
        gifs = sum(1 for e in report if e["preview"] == "preview.gif")
        stills = sum(1 for e in report if e["preview"] == "preview.jpg")
        self._log("")
        self._log("=" * 50)
        self._log(f"RESULT: created {ok}, failed {failed} "
                  f"(previews: {gifs} gif, {stills} still).")
        self._log("=" * 50)
