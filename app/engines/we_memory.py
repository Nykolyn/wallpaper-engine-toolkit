"""we_memory.py — reading Wallpaper Engine's own playlist timer, exactly. Read-only.

The countdown in `wallpaper_timer` is worked out from the outside, and the
outside is a second late at every pause and three seconds late at every change:
Wallpaper Engine resets its timer and writes playliststate.bin about three
seconds apart (measured 3.3 s and 3.1 s). The timer itself is in the process,
and it is simple to read once it has been found.

**What was found.** Scanning wallpaper64.exe's memory for float32 values that
grow exactly in step with real time turned up one per monitor, and each dropped
to 0.000 the moment its monitor changed wallpaper (599.808 -> 0.000 at
17:23:50.275; the state file followed at 17:23:53.595). Each sits in a playlist
object that describes itself, relative to the start of the monitor's name:

    +0    std::string "MonitorN"   (inline buffer, size 8 at +16, capacity 15 at +24)
    +88   float32  delay, in minutes            (10.0)
    +104  uint32   the playlist instance number, the same u32 that
                   playliststate.bin stores beside the wallpaper (467, 84)
    +108  float32  seconds this wallpaper has run   <- the timer
    +116  int32    transition time in ms         (1500)

Nothing else in memory matches all five, so the object is found by searching for
the name and checking the rest against config.json and the state file — no
guessing, and no pointer path that a Wallpaper Engine update would silently
move. If an update does change the layout, the search simply finds nothing and
the countdown falls back to its outside estimate.

**What a search costs the process being read.** ReadProcessMemory copies out of
the other process while attached to it, and how long one call takes is what
matters, not how many: on this machine a read runs at 76 MB/s whatever its size,
so a 16 MB piece holds Wallpaper Engine for about 220 ms — long enough to stall
a wallpaper visibly, and the reason the tray used to stutter the picture while
Wallpaper Engine was starting. Measured hold per piece:

    piece      64 KB    256 KB      1 MB      4 MB
    median   0.33 ms   1.32 ms   5.26 ms   55.3 ms
    max      3.42 ms  12.45 ms  48.16 ms  154.6 ms

So memory is read in 64 KB pieces, and the search rests after each one for as
long as the read took, leaving the process its own memory for half of every
second. The sweep also starts where the objects were last time and, once one is
found, looks next to it for the others, since a heap keeps them together: one
run needed 24 MB rather than 861 MB that way. None of that is relied on — what
is not found near by is found by reading everything, at about 35 s for 1.3 GB.
"""
from __future__ import annotations

import ctypes
import os
import struct
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Protocol, Sequence

# Offsets from the start of the monitor name, for the 64-bit build.
NAME_SIZE_OFF = 16
NAME_CAP_OFF = 24
DELAY_OFF = 88
INSTANCE_OFF = 104
TIMER_OFF = 108
TRANSITION_OFF = 116
BLOCK = 124

# Memory is read in pieces of this size, overlapping by a whole object so one
# lying across the boundary between two pieces is not missed. Small pieces cost
# nothing in throughput and keep every hold on the other process short.
CHUNK = 64 << 10
# After each piece the search waits this many times the seconds the read took.
REST = 1.0
# Regions bigger than this are video frames and textures, not playlist objects.
MAX_REGION = 256 << 20
# At most this many remembered places are checked before a sweep begins.
MAX_HINTS = 64

_MEM_COMMIT, _PAGE_READWRITE, _PAGE_GUARD, _MEM_PRIVATE = 0x1000, 0x04, 0x100, 0x20000


@dataclass(frozen=True)
class Expected:
    """What one monitor's playlist object must hold to be the right one."""
    monitor: str
    instance: int          # from playliststate.bin
    delay_minutes: float   # from config.json
    transition_ms: int     # from config.json

    @property
    def limit(self) -> float:
        """The largest value the timer can plausibly show."""
        return self.delay_minutes * 60 + 30


@dataclass(frozen=True)
class Reading:
    """What a playlist object says: the timer, and which pass it belongs to."""
    timer: float
    instance: int


@dataclass(frozen=True)
class Hint:
    """Where a monitor's object was the last time it was found."""
    monitor: str
    address: int
    region_size: int
    offset: int            # from the start of the region it was in


class Memory(Protocol):
    def regions(self) -> Iterable[tuple[int, int]]: ...
    def read(self, address: int, size: int) -> bytes: ...
    def close(self) -> None: ...


def signature(monitor: str) -> bytes:
    """The bytes of a short std::string holding the monitor's name."""
    raw = monitor.encode("ascii")
    if len(raw) > 15:
        raise ValueError("a name that long is not stored inline")
    return raw + b"\0" * (16 - len(raw)) + struct.pack("<QQ", len(raw), 15)


def check_block(block: bytes, want: Expected, *, instance: bool = True) -> Reading | None:
    """What `block` says, if it is this monitor's object; None if it is not.

    `instance=False` drops the one check that Wallpaper Engine itself changes
    while the object stays put — re-applying a playlist gives the pass a new
    number — so an object already found is not lost over it.
    """
    if len(block) < BLOCK or not block.startswith(signature(want.monitor)):
        return None
    delay = struct.unpack_from("<f", block, DELAY_OFF)[0]
    found = struct.unpack_from("<I", block, INSTANCE_OFF)[0]
    timer = struct.unpack_from("<f", block, TIMER_OFF)[0]
    transition = struct.unpack_from("<i", block, TRANSITION_OFF)[0]
    if (abs(delay - want.delay_minutes) > 1e-3 or transition != want.transition_ms
            or not 0.0 <= timer <= want.limit):
        return None
    if instance and found != want.instance:
        return None
    return Reading(timer, found)


def read_timer(memory: Memory, address: int, want: Expected,
               *, instance: bool = True) -> Reading | None:
    """The object at `address`, or None if it is no longer there."""
    return check_block(memory.read(address, BLOCK), want, instance=instance)


def remembered_places(regions: Sequence[tuple[int, int]], hint: Hint) -> list[int]:
    """Addresses worth one read each before any sweeping, for a known object.

    The same address, which is where it still is within one run of the process,
    and the same spot in any region shaped like the one it was in, which is
    where a heap laid out the same way would put it again.
    """
    places = [hint.address] if any(base <= hint.address < base + size
                                   for base, size in regions) else []
    places += [base + hint.offset for base, size in regions
               if size == hint.region_size and base + hint.offset != hint.address]
    return places[:MAX_HINTS]


def rank_regions(regions: Iterable[tuple[int, int]],
                 hints: Iterable[Hint] = ()) -> list[tuple[int, int]]:
    """The order to sweep memory in: nearest where an object was, else smallest first.

    Both are guesses that cost nothing and are never relied on — the sweep
    covers every region either way. Small regions first because a playlist
    object sits among a heap's small allocations rather than in the megabytes of
    video frames that make up most of the process.
    """
    near = [h.address for h in hints]
    if near:
        return sorted(regions, key=lambda r: min(abs(r[0] - a) for a in near))
    return sorted(regions, key=lambda r: r[1])


def find_objects(memory: Memory, wanted: list[Expected],
                 cancelled: Callable[[], bool] = lambda: False,
                 hints: Iterable[Hint] = (), rest: float = REST) -> dict[str, list[int]]:
    """Addresses of each monitor's playlist object (the start of its name).

    Stops as soon as every monitor has one, since a match has already passed
    five independent checks.
    """
    patterns = {w.monitor: (signature(w.monitor), w) for w in wanted}
    found: dict[str, list[int]] = {w.monitor: [] for w in wanted}
    regions = list(memory.regions())
    by_name = {w.monitor: w for w in wanted}

    for hint in hints:
        want = by_name.get(hint.monitor)
        if want is None or found[hint.monitor]:
            continue
        for address in remembered_places(regions, hint):
            if check_block(memory.read(address, BLOCK), want) is not None:
                found[hint.monitor].append(address)
                break
    if all(found.values()):
        return found

    queue = rank_regions(regions, hints)
    while queue:
        base, size = queue.pop(0)
        offset = 0
        while offset < size:
            if cancelled():
                return found
            started = time.perf_counter()
            data = memory.read(base + offset, min(CHUNK + BLOCK, size - offset))
            if rest:
                time.sleep((time.perf_counter() - started) * rest)
            for monitor, (pattern, want) in patterns.items():
                if found[monitor]:
                    continue
                i = data.find(pattern)
                while i != -1:
                    if i < CHUNK or offset + CHUNK >= size:      # overlap is the next piece's
                        block = data[i:i + BLOCK]
                        if len(block) < BLOCK:
                            block = memory.read(base + offset + i, BLOCK)
                        if check_block(block, want) is not None:
                            at = base + offset + i
                            found[monitor].append(at)
                            # The objects are made together and land together.
                            queue.sort(key=lambda r: abs(r[0] - at))
                            break
                    i = data.find(pattern, i + 1)
            if all(found.values()):
                return found
            offset += CHUNK
    return found


# ---- The real process ---------------------------------------------------------

class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
                ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]


if os.name == "nt":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.VirtualQueryEx.restype = ctypes.c_size_t
    _k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MBI),
                                    ctypes.c_size_t]
    _k32.ReadProcessMemory.restype = wintypes.BOOL
    _k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
else:                                                      # pragma: no cover
    _k32 = None

_BACKGROUND_BEGIN, _BACKGROUND_END = 0x00010000, 0x00020000


def background_mode(on: bool) -> None:
    """Ask Windows to schedule this thread, and its reads, behind everything else."""
    if _k32 is None:
        return
    _k32.SetThreadPriority(_k32.GetCurrentThread(),
                           _BACKGROUND_BEGIN if on else _BACKGROUND_END)


class ProcessMemory:
    """Read access to another process: query information and read, nothing else."""

    def __init__(self, pid: int):
        if _k32 is None:
            raise OSError("reading process memory needs Windows")
        self.handle = _k32.OpenProcess(0x0400 | 0x0010, False, pid)   # QUERY_INFORMATION | VM_READ
        if not self.handle:
            raise OSError(f"cannot open process {pid}: error {ctypes.get_last_error()}")
        self.read_bytes = 0

    def regions(self) -> Iterator[tuple[int, int]]:
        address, info = 0, _MBI()
        while _k32.VirtualQueryEx(self.handle, ctypes.c_void_p(address), ctypes.byref(info),
                                  ctypes.sizeof(info)):
            base, size = info.BaseAddress or 0, info.RegionSize
            if (info.State == _MEM_COMMIT and info.Protect & _PAGE_READWRITE
                    and not info.Protect & _PAGE_GUARD and info.Type == _MEM_PRIVATE
                    and size <= MAX_REGION):
                yield base, size
            address = base + size
            if address >= 0x7FFF_FFFF_FFFF:
                return

    def region_of(self, address: int) -> tuple[int, int] | None:
        """The region an address falls in, to remember the shape of its place."""
        info = _MBI()
        if not _k32.VirtualQueryEx(self.handle, ctypes.c_void_p(address), ctypes.byref(info),
                                   ctypes.sizeof(info)):
            return None
        return (info.BaseAddress or 0), info.RegionSize

    def read(self, address: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()
        _k32.ReadProcessMemory(self.handle, ctypes.c_void_p(address), buf, size,
                               ctypes.byref(got))
        self.read_bytes += got.value
        return buf.raw[: got.value]

    def close(self) -> None:
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None
