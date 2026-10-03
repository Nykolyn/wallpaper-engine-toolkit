"""Sequential wallpaper duplication, with detached callback snapshots and safe retry."""
from __future__ import annotations

import copy
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

LARGE_FILE_THRESHOLD = 50 * 1024 * 1024
CHUNK_SIZE = 4 * 1024 * 1024
REPORT_INTERVAL = .15


class CopyJob:
    """One or more sources, N copies each; CopyJob(source, count) still works."""

    def __init__(self, source, count=3, destination=None, *, copies=None):
        sources = [source] if isinstance(source, (str, os.PathLike)) else list(source)
        if not sources or any(not str(p).strip() for p in sources):
            raise ValueError("Choose at least one source folder")
        self.sources = tuple(dict.fromkeys(os.path.normpath(str(p)) for p in sources))
        self.count = int(count if copies is None else copies)
        if self.count < 1:
            raise ValueError("Copies must be at least 1")
        self.destination = str(destination) if destination else None
        self.id = uuid.uuid4().hex
        self.state, self.reason = "queued", ""
        self.completed = {}  # (source index, copy ordinal) -> (output, files, bytes)
        self.files = self.size = None
        self.done_files = self.done_bytes = 0
        self.elapsed = self.speed = 0.0

    @property
    def source(self):
        return self.sources[0]

    @property
    def copies(self):
        return self.count

    @property
    def name(self):
        name = os.path.basename(self.source.rstrip("\\/")) or self.source
        return name if len(self.sources) == 1 else f"{name} + {len(self.sources) - 1} folders"

    @property
    def requested(self):
        return len(self.sources) * self.count

    def snapshot(self):
        return dict(id=self.id, name=self.name, sources=self.sources, copies=self.count,
                    destination=self.destination, state=self.state, reason=self.reason,
                    files=self.files, size=self.size, done_files=self.done_files,
                    done_bytes=self.done_bytes, elapsed=self.elapsed, speed=self.speed,
                    requested=self.requested, ok=len(self.completed),
                    failed=int(self.state == "failed"), outputs=[v[0] for v in self.completed.values()])


def parse_line(line: str, default=3) -> tuple[str, int] | None:
    """Accept today's path-with-spaces + count syntax, and quoted paths."""
    line = line.strip()
    if not line:
        return None
    match = re.fullmatch(r'"(.+)"(?:\s+(\d+))?', line)
    if match:
        path, count = match.group(1), int(match.group(2) or default)
    else:
        parts = line.rsplit(None, 1)
        path, count = ((parts[0].strip('"'), int(parts[1]))
                       if len(parts) == 2 and parts[1].isdigit()
                       else (line.strip('"'), default))
    if not path.strip() or count < 1:
        raise ValueError("Copies must be at least 1 and the path must not be empty")
    return os.path.normpath(path), count


def _raise(error):
    raise error


def manifest(folder: str) -> tuple[dict[str, int], list[str]]:
    """Do not silently turn an unreadable folder into an empty successful copy."""
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"Source folder not found: {folder}")
    if os.path.islink(folder) or os.path.isjunction(folder):
        raise ValueError(f"Linked source folders are not supported: {folder}")
    files, directories = {}, []
    for root, dirs, names in os.walk(folder, onerror=_raise):
        for name in dirs + names:
            full = os.path.join(root, name)
            if os.path.islink(full) or os.path.isjunction(full):
                raise ValueError(f"Linked files or folders are not supported: {full}")
        directories.extend(os.path.relpath(os.path.join(root, d), folder) for d in dirs)
        for name in names:
            full = os.path.join(root, name)
            files[os.path.relpath(full, folder)] = os.path.getsize(full)
    return files, directories


def destination_info(destination: str) -> dict:
    """Find the actual filesystem behind the nearest existing ancestor, on a worker."""
    if not destination or not destination.strip():
        raise ValueError("Choose a destination folder")
    ancestor = Path(destination).absolute()
    while not ancestor.exists():
        parent = ancestor.parent
        if parent == ancestor:
            raise FileNotFoundError(f"Destination drive not found: {destination}")
        ancestor = parent
    if not ancestor.is_dir():
        raise NotADirectoryError(f"Destination is not a folder: {ancestor}")
    usage = shutil.disk_usage(ancestor)
    return dict(drive=ancestor.anchor or str(ancestor), device=ancestor.stat().st_dev,
                free=usage.free, path=destination)


def _check_destination(sources, destination):
    if not destination:
        raise ValueError("Choose a destination folder")
    target = os.path.realpath(destination)
    for source in sources:
        source = os.path.realpath(source)
        try:
            nested = os.path.commonpath([target, source]) == source
        except ValueError:
            nested = False
        if nested:
            raise ValueError("The destination must be outside every source folder")


def inspect_queue(jobs, destination="") -> dict:
    """Measure sizes and aggregate required space by filesystem, not by folder.

    Called on a worker on queue changes and immediately before Start. An
    inaccessible job is reported independently so the others can still run.
    """
    rows, drives = {}, {}
    for job in jobs:
        target = job.destination or destination
        row = dict(files=job.files, size=job.size, reason="", destination=target)
        if job.state not in ("queued", "copying"):
            rows[job.id] = row
            continue
        try:
            manifests = [manifest(source)[0] for source in job.sources]
            row.update(files=sum(len(m) for m in manifests) * job.count,
                       size=sum(sum(m.values()) for m in manifests) * job.count)
            _check_destination(job.sources, target)
            info = destination_info(target)
            drive = drives.setdefault(info["device"], dict(info, needed=0))
            drive["needed"] += sum(sum(m.values()) for i, m in enumerate(manifests)
                                   for n in range(job.count) if (i, n) not in job.completed)
        except (OSError, ValueError) as exc:
            row["reason"] = str(exc)
        rows[job.id] = row
    return dict(jobs=rows, drives=list(drives.values()))


class _Stopped(Exception):
    pass


class CopyEngine:
    def __init__(self, log=None, progress=None, speed=None, finished=None, *,
                 job_changed=None, updated=None):
        self._log = log or (lambda *_: None)
        self._progress = progress or (lambda *_: None)
        self._speed = speed or (lambda *_: None)
        self._finished = finished or (lambda *_: None)
        self._job_changed = job_changed or (lambda *_: None)
        self._updated = updated or (lambda *_: None)
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._ready = threading.Event()
        self._ready.set()
        self._thread = None
        self._running = False
        self._accepting = False
        self.jobs = []
        self._pending = []
        self._current = None
        self._rate_bytes = 0
        self._rate_time = self._last_emit = time.monotonic()
        self.rate = 0.0

    def is_running(self):
        with self._lock:
            return self._running

    def is_paused(self):
        return not self._ready.is_set()

    def pause(self):
        self._ready.clear()

    def resume(self):
        self._ready.set()

    def cancel(self):
        self._cancel.set()
        self._ready.set()

    def start(self, jobs, dest_root="", *, verify=False):
        with self._lock:
            if self._running:
                return False
            self.jobs = copy.deepcopy(list(jobs))
            self._pending = [j for j in self.jobs if j.state == "queued"]
            self.destination, self.verify = dest_root, verify
            self._cancel.clear()
            self._ready.set()
            self._running = self._accepting = True
            self._current = None
            self.rate = self._rate_bytes = 0
            self._rate_time = self._last_emit = time.monotonic()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True

    def retry(self, job) -> bool:
        """Queue only unfinished copies. When idle, call start(engine.jobs) next."""
        key = job if isinstance(job, str) else job.id
        with self._lock:
            row = next((j for j in self.jobs if j.id == key), None)
            if (row is None or row.state not in ("failed", "stopped", "skipped")
                    or self._running and (self._cancel.is_set() or not self._accepting)):
                return False
            row.state, row.reason = "queued", ""
            if self._running:
                self._pending.append(row)
            self._job_changed(row.snapshot())
            return True

    def skip(self, job) -> bool:
        key = job if isinstance(job, str) else job.id
        with self._lock:
            row = next((j for j in self.jobs if j.id == key), None)
            if row is None or row.state not in ("queued", "failed", "stopped"):
                return False
            row.state = "skipped"
            self._pending = [j for j in self._pending if j.id != key]
            self._job_changed(row.snapshot())
            return True

    def _checkpoint(self):
        if not self._ready.is_set():
            self.rate = 0
            self._emit(force=True)
            self._ready.wait()
            self._rate_time = time.monotonic()
            self._rate_bytes = 0
        if self._cancel.is_set():
            raise _Stopped()

    def _emit(self, *, force=False):
        now = time.monotonic()
        if not force and now - self._last_emit < REPORT_INTERVAL:
            return
        if now > self._rate_time and now - self._rate_time >= REPORT_INTERVAL:
            self.rate = self._rate_bytes / (now - self._rate_time)
            self._rate_bytes, self._rate_time = 0, now
        if self.is_paused() or self._cancel.is_set():
            self.rate = 0
        self._last_emit = now
        with self._lock:
            current = self._current
            if current:
                current.speed = self.rate
                self._job_changed(current.snapshot())
            rows = [j.snapshot() for j in self.jobs]
        done = sum(r["done_bytes"] for r in rows)
        total = sum(r["size"] or 0 for r in rows)
        self._updated(dict(done=done, total=total, files=sum(r["done_files"] for r in rows),
                           total_files=sum(r["files"] or 0 for r in rows), speed=self.rate,
                           current=current.id if current else None, paused=self.is_paused()))
        self._progress(sum(r["ok"] for r in rows), sum(r["requested"] for r in rows))
        self._speed(self.rate / (1024 * 1024))

    def _run(self):
        try:
            measured = inspect_queue(self.jobs, self.destination)
            for job in self.jobs:
                row = measured["jobs"][job.id]
                job.files, job.size = row["files"], row["size"]
                self._job_changed(job.snapshot())
            self._emit(force=True)
            while True:
                with self._lock:
                    if not self._pending or self._cancel.is_set():
                        # Atomic with retry: no retry can join a closing queue.
                        self._accepting = False
                        break
                    job = self._pending.pop(0)
                    self._current = job
                    job.state = "copying"
                self._process_job(job)
                self._current = None
                self._emit(force=True)
            if self._cancel.is_set():
                for job in self.jobs:
                    if job.state == "queued":
                        job.state, job.reason = "stopped", "Stopped before this job started"
                        self._job_changed(job.snapshot())
        except Exception as exc:
            self._log(f"[ERROR] Queue: {exc}")
            for job in self.jobs:
                if job.state in ("queued", "copying"):
                    job.state, job.reason = "failed", str(exc)
                    self._job_changed(job.snapshot())
        finally:
            with self._lock:
                self._running = False
                self._current = None
                self.rate = 0
                report = [j.snapshot() for j in self.jobs]
            self._speed(0)
            self._finished(report)

    def _process_job(self, job):
        started = time.monotonic()
        target = job.destination or self.destination
        try:
            self._checkpoint()
            _check_destination(job.sources, target)
            sources = [manifest(p) for p in job.sources]
            job.files = sum(len(m) for m, _ in sources) * job.count
            job.size = sum(sum(m.values()) for m, _ in sources) * job.count
            remaining = sum(sum(m.values()) for i, (m, _) in enumerate(sources)
                            for n in range(job.count) if (i, n) not in job.completed)
            if remaining > destination_info(target)["free"]:
                raise OSError("Not enough free space at the destination")
            os.makedirs(target, exist_ok=True)
            self._log(f"[START] {job.name}: {job.count} copies into {target}")
            for i, source in enumerate(job.sources):
                files, directories = sources[i]
                for n in range(job.count):
                    if (i, n) in job.completed:
                        continue
                    self._checkpoint()
                    output = self._reserve_copy(target, os.path.basename(source.rstrip("\\/")))
                    try:
                        for directory in directories:
                            os.makedirs(os.path.join(output, directory), exist_ok=True)
                        for rel in sorted(files, key=lambda p: files[p] > LARGE_FILE_THRESHOLD):
                            self._checkpoint()
                            if files[rel] > LARGE_FILE_THRESHOLD:
                                self._copy_large(source, output, rel)
                            else:
                                self._copy_small(source, output, rel)
                                job.done_bytes += files[rel]
                                self._rate_bytes += files[rel]
                            job.done_files += 1
                            self._emit()
                        self._checkpoint()
                        if self.verify:
                            self._verify(source, output, files)
                        job.completed[i, n] = (output, len(files), sum(files.values()))
                        self._log(f"[COPY] {os.path.basename(output)}: written"
                                  + (" and verified" if self.verify else ""))
                        self._emit()
                    except BaseException as exc:
                        # Only the just-reserved copy belongs to us. Existing
                        # numbered folders are never merged into or removed.
                        try:
                            self._discard(output, target)
                        except OSError as cleanup:
                            raise OSError(f"{exc or 'Stopped'}; partial output remains at {output}: {cleanup}") from exc
                        finally:
                            job.done_files = sum(v[1] for v in job.completed.values())
                            job.done_bytes = sum(v[2] for v in job.completed.values())
                        raise
            job.state, job.reason = "done", ""
        except _Stopped:
            job.state, job.reason = "stopped", "Stopped; unfinished copy removed"
        except Exception as exc:
            job.state, job.reason = "failed", str(exc)
            self._log(f"[ERROR] {job.name}: {exc}")
        finally:
            job.elapsed += time.monotonic() - started
            job.speed = 0
            self._job_changed(job.snapshot())

    @staticmethod
    def _next_copy_index(dest_root, base_name):
        pattern = re.compile(re.escape(base_name) + r"_copy(\d+)$", re.IGNORECASE)
        return max((int(m.group(1)) for name in os.listdir(dest_root)
                    if (m := pattern.fullmatch(name))), default=0) + 1

    def _reserve_copy(self, target, name):
        index = self._next_copy_index(target, name)
        while True:
            output = os.path.join(target, f"{name}_copy{index}")
            try:
                os.mkdir(output)
                return output
            except FileExistsError:
                index += 1

    @staticmethod
    def _discard(output, target):
        resolved, parent = os.path.realpath(output), os.path.realpath(target)
        if os.path.dirname(resolved) != parent or os.path.islink(output) or os.path.isjunction(output):
            raise OSError("Partial output changed location; cleanup refused")
        shutil.rmtree(output)

    @staticmethod
    def _verify(source, output, expected):
        current, _ = manifest(source)
        actual, _ = manifest(output)
        if current != expected:
            raise OSError("Verification failed: the source changed during copying")
        if actual.keys() != expected.keys():
            raise OSError(f"Verification failed: file list differs ({len(actual)} of {len(expected)} files)")
        for name, size in expected.items():
            if actual[name] != size:
                raise OSError(f"Verification failed: size differs for {name} ({actual[name]} of {size} bytes)")

    @staticmethod
    def _copy_small(source, dest, rel):
        shutil.copy2(os.path.join(source, rel), os.path.join(dest, rel))

    def _copy_large(self, source, dest, rel):
        src, dst = os.path.join(source, rel), os.path.join(dest, rel)
        with open(src, "rb") as reader, open(dst, "xb") as writer:
            while True:
                if self._cancel.is_set():
                    raise _Stopped()
                chunk = reader.read(CHUNK_SIZE)
                if not chunk:
                    break
                writer.write(chunk)
                self._current.done_bytes += len(chunk)
                self._rate_bytes += len(chunk)
                self._emit()
        shutil.copystat(src, dst)
