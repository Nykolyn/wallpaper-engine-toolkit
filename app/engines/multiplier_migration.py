"""Verified Copier-folder migration, with a durable recovery manifest.

Planning only reads. Applying is an explicit caller action, while rotations and
Wallpaper Engine are stopped. One verified payload per group remains available
for rollback; the other identical physical copies are removed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import playlist_refresh as playlists
from .rotator.core import PROTECTED_PREFIX, is_protected
from .wallpaper_timer import STATE_FILE, read_state_file, write_state_file

COPY_SUFFIX = re.compile(r"(?:_copy\d+)+$", re.IGNORECASE)


class MigrationCancelled(Exception):
    pass


def _check_cancel(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise MigrationCancelled("Migration cancelled; original folders are recoverable.")


def _linked(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _plain_path(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for parent in (path, *path.parents):
        if os.path.lexists(parent) and _linked(parent):
            raise ValueError(f"Linked path is not safe to migrate: {parent}")
    return path


def _key(path: str | Path) -> str:
    return str(path).replace("\\", "/").rstrip("/").casefold()


def _base(name: str) -> tuple[str, bool]:
    plain = name[len(PROTECTED_PREFIX):].lstrip() if is_protected(name) else name
    return COPY_SUFFIX.sub("", plain), bool(COPY_SUFFIX.search(plain))


def _fingerprint(folder: Path, cancelled: Callable[[], bool] = lambda: False) -> str:
    """Hash every relative filename, directory and byte; never follow a link."""
    _plain_path(folder)
    digest = hashlib.sha256()
    stack = [folder]
    while stack:
        parent = stack.pop()
        for path in sorted(parent.iterdir(), key=lambda p: p.name.casefold()):
            _check_cancel(cancelled)
            if _linked(path):
                raise ValueError(f"Link or junction inside wallpaper: {path}")
            rel = path.relative_to(folder).as_posix().encode("utf-8")
            digest.update(len(rel).to_bytes(8, "little"))
            digest.update(rel)
            if path.is_dir():
                digest.update(b"D")
                stack.append(path)
            elif path.is_file():
                digest.update(b"F")
                digest.update(path.stat().st_size.to_bytes(8, "little"))
                with path.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        _check_cancel(cancelled)
                        digest.update(chunk)
            else:
                raise ValueError(f"Unsupported wallpaper file: {path}")
    return digest.hexdigest()


def _playable(folder: Path) -> str:
    item = playlists.playlist_item(folder)
    if item is None:
        raise ValueError(f"No readable wallpaper project in {folder}")
    target = _plain_path(Path(item))
    if not target.is_relative_to(folder):
        raise ValueError(f"Wallpaper project points outside its folder: {folder}")
    return target.relative_to(folder).as_posix()


@dataclass
class MigrationGroup:
    original: str
    survivor: str
    members: list[str]
    fingerprint: str
    item: str

    @property
    def multiplier(self) -> int:
        return len(self.members)


@dataclass
class MigrationPlan:
    roots: list[str]
    current_root: str | None = None
    groups: list[MigrationGroup] = field(default_factory=list)
    preserved: list[str] = field(default_factory=list)

    @property
    def copy_count(self) -> int:
        return sum(group.multiplier - 1 for group in self.groups)


def plan_migration(roots, current_root=None, *, cancelled=lambda: False) -> MigrationPlan:
    """Inspect supplied reserve/myprojects/duplicates/old Copier destination roots."""
    resolved = []
    for value in roots:
        if not value or not str(value).strip():
            continue
        path = _plain_path(Path(value))
        if path.exists() and not path.is_dir():
            raise ValueError(f"Migration root is not a directory: {path}")
        if path not in resolved:
            resolved.append(path)
    current = _plain_path(Path(current_root)) if current_root else None
    if current is not None and current not in resolved:
        resolved.append(current)
    for root in resolved:
        if any(root != other and root.is_relative_to(other) for other in resolved):
            raise ValueError("Migration roots must not contain one another.")
    plan = MigrationPlan([str(p) for p in resolved], str(current) if current else None)
    families = {}
    for root in resolved:
        if not root.exists():
            continue
        for folder in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
            _check_cancel(cancelled)
            if not folder.is_dir() and not folder.is_symlink():
                continue
            base, copied = _base(folder.name)
            families.setdefault(base.casefold(), []).append((folder, base, copied))
    for family in families.values():
        if not any(copied for _, _, copied in family):
            continue
        originals = [(p, name) for p, name, copied in family if not copied]
        if not originals:
            plan.preserved.extend(f"Missing original: {p}" for p, _, _ in family)
            continue
        fingerprints = {}
        for folder, _, _ in family:
            try:
                fingerprints[folder] = (_fingerprint(folder, cancelled), _playable(folder))
            except (OSError, ValueError) as error:
                plan.preserved.append(f"Preserved {folder}: {error}")
        original_hashes = {fingerprints[p][0] for p, _ in originals if p in fingerprints}
        if len(original_hashes) != 1:
            plan.preserved.extend(f"Ambiguous or unreadable original: {p}" for p, _, _ in family)
            continue
        expected = next(iter(original_hashes))
        matching = [p for p, _, _ in family if p in fingerprints and fingerprints[p][0] == expected]
        plan.preserved.extend(f"Edited copy: {p}" for p, _, copied in family
                              if copied and p in fingerprints and p not in matching)
        if len(matching) < 2:
            continue
        original = min((p for p, _ in originals if p in matching),
                       key=lambda p: (p.parent != current, not is_protected(p.name), str(p)))
        base = _base(original.name)[0]
        protected = any(is_protected(p.name) for p in matching)
        name = f"{PROTECTED_PREFIX} {base}" if protected else base
        destination_root = current if current and any(p.parent == current for p in matching) else original.parent
        survivor = destination_root / name
        if survivor.exists() and survivor not in matching:
            plan.preserved.extend(f"Survivor name is occupied: {p}" for p in matching)
            continue
        plan.groups.append(MigrationGroup(str(original), str(survivor), [str(p) for p in matching],
                                          expected, fingerprints[original][1]))
    return plan


def _replace(path: Path, data: bytes) -> None:
    _plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".multiplier-new")
    _plain_path(pending)
    with pending.open("wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.replace(pending, path)


def _save(manifest: Path, data: dict) -> None:
    _replace(manifest, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def _mapping(groups) -> dict[str, str]:
    return {_key(member): group["survivor"] for group in groups for member in group["members"]}


def _remap(value: str, mapping: dict[str, str]) -> str:
    parts = value.replace("\\", "/").split("/")
    for count in range(len(parts), 0, -1):
        survivor = mapping.get(_key("/".join(parts[:count])))
        if survivor is not None:
            relative = Path(*parts[count:])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Wallpaper reference escapes its folder: {value}")
            return str(Path(survivor) / relative).replace("\\", "/")
    return value


def remap_config(config: dict, mapping: dict[str, str]) -> dict:
    """Rewrite copied-folder references, keeping one entry in every playlist."""
    def walk(value):
        if isinstance(value, str):
            return _remap(value, mapping)
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            result = {key: walk(item) for key, item in value.items()}
            if isinstance(result.get("items"), list):
                seen = set()
                result["items"] = [item for item in result["items"]
                                   if not isinstance(item, str) or _unique(item, seen)]
            return result
        return value
    return walk(config)


def _unique(value: str, seen: set[str]) -> bool:
    key = _key(value)
    if key in seen:
        return False
    seen.add(key)
    return True


def remap_state(state: bytes, mapping: dict[str, str]) -> bytes:
    sections = read_state_file(state)
    for section in sections:
        for monitor in section.monitors:
            monitor.current = _remap(monitor.current, mapping)
            seen = {_key(monitor.current)}
            waiting, marks = [], []
            for index, item in enumerate(monitor.waiting):
                item = _remap(item, mapping)
                if _unique(item, seen):
                    waiting.append(item)
                    marks.append(monitor.marks[index] if index < len(monitor.marks) else 0)
            monitor.waiting, monitor.marks = waiting, marks
    return write_state_file(sections)


def apply_migration(plan: MigrationPlan, store, *, runs=(), current_names=(), config_path=None,
                    settings_paths=(), backup_root=None, cancelled=lambda: False) -> Path:
    """Back up, apply and remove verified copies. Never call while another worker runs."""
    _check_cancel(cancelled)
    store.reload()
    if getattr(store, "problem", ""):
        raise ValueError(store.problem)
    if not plan.groups:
        raise ValueError("There are no verified copy groups to migrate.")
    # Replan at confirmation: a folder edited since the preview must never be removed.
    fresh = plan_migration(plan.roots, plan.current_root, cancelled=cancelled)
    if asdict(fresh) != asdict(plan):
        raise ValueError("Wallpaper folders changed after preview; inspect the migration again.")
    root = _plain_path(Path(backup_root) if backup_root else store.path.parent / "multiplier-migration")
    if pending_migrations(root):
        raise ValueError("Recover the unfinished multiplier migration before starting another.")
    transaction = root / uuid.uuid4().hex
    if any(transaction.is_relative_to(Path(value)) for value in plan.roots):
        raise ValueError("Migration backups must be outside the wallpaper roots.")
    transaction.mkdir(parents=True)
    groups = [asdict(group) for group in plan.groups]
    data = {"version": 1, "id": transaction.name, "status": "preparing", "plan": asdict(plan),
            "groups": groups, "sources": [], "files": [], "store_path": str(store.path)}
    manifest = transaction / "manifest.json"
    _save(manifest, data)
    try:
        paths = list(dict.fromkeys([Path(p) for p in settings_paths] + [store.path]))
        config = Path(config_path) if config_path else None
        if config:
            paths += [config, config.parent / STATE_FILE]
        for index, path in enumerate(dict.fromkeys(paths)):
            _check_cancel(cancelled)
            path = _plain_path(path)
            before = path.read_bytes() if path.exists() else None
            entry = {"path": str(path), "backup": f"before-{index}", "existed": before is not None}
            if before is not None:
                _replace(transaction / entry["backup"], before)
            data["files"].append(entry)
        mapping = _mapping(groups)
        for entry in data["files"]:
            path = Path(entry["path"])
            if not entry["existed"]:
                continue
            before = (transaction / entry["backup"]).read_bytes()
            if path == config:
                parsed = json.loads(before.decode("utf-8-sig"))
                if not isinstance(parsed, dict):
                    raise ValueError("Wallpaper Engine config must contain an object.")
                after = playlists.dump_config(remap_config(parsed, mapping))
            elif config and path == config.parent / STATE_FILE:
                after = remap_state(before, mapping)
            else:
                continue
            entry["after"] = entry["backup"] + "-new"
            _replace(transaction / entry["after"], after)
        current = list(current_names)
        if plan.current_root and Path(plan.current_root).exists():
            current = [p.name for p in Path(plan.current_root).iterdir() if p.is_dir()]
        for group in groups:
            if plan.current_root and any(Path(p).parent == Path(plan.current_root) for p in group["members"]):
                current = [n for n in current if not any(_key(n) == _key(Path(p).name) for p in group["members"])]
                current.append(Path(group["survivor"]).name)
        staged_path = transaction / "multipliers-new.json"
        store_entry = next(entry for entry in data["files"] if Path(entry["path"]) == store.path)
        if store_entry["existed"]:
            _replace(staged_path, (transaction / store_entry["backup"]).read_bytes())
        staged = type(store)(staged_path)
        staged.initialize(runs, current, {Path(g["survivor"]).name: len(g["members"]) for g in groups})
        store_entry["after"] = "multipliers-new.json"
        for index, group in enumerate(groups):
            survivor = Path(group["survivor"])
            group["created"] = not survivor.exists()
            group["staging"] = str(survivor.parent.parent / f".{survivor.parent.name}-multiplier-{data['id']}" / f"new-{index}")
            candidates = []
            for member in group["members"]:
                source = Path(member)
                if source == survivor:
                    continue
                quarantine = source.parent.parent / f".{source.parent.name}-multiplier-{data['id']}" / f"old-{len(data['sources'])}"
                entry = {"source": str(source), "quarantine": str(quarantine), "group": index}
                candidates.append(entry)
                data["sources"].append(entry)
            group["payload"] = candidates[0]["quarantine"]
        data["status"] = "prepared"
        _save(manifest, data)
        return resume_migration(manifest, store, cancelled=cancelled)
    except MigrationCancelled:
        rollback_migration(manifest, store)
        raise


def _load(manifest_path) -> tuple[Path, dict]:
    manifest = _plain_path(Path(manifest_path))
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("id") != manifest.parent.name:
        raise ValueError("Invalid multiplier migration manifest.")
    roots = [_plain_path(Path(value)) for value in data["plan"]["roots"]]
    for group in data["groups"]:
        for value in [group["original"], group["survivor"], *group["members"]]:
            if _plain_path(Path(value)).parent not in roots:
                raise ValueError("Migration wallpaper is outside the approved roots.")
        if group.get("staging"):
            survivor = Path(group["survivor"])
            expected = survivor.parent.parent / f".{survivor.parent.name}-multiplier-{data['id']}"
            if _plain_path(Path(group["staging"])).parent != expected:
                raise ValueError("Invalid migration staging folder.")
    for entry in data["sources"]:
        source = _plain_path(Path(entry["source"]))
        quarantine = _plain_path(Path(entry["quarantine"]))
        expected = source.parent.parent / f".{source.parent.name}-multiplier-{data['id']}"
        if source.parent not in roots or quarantine.parent != expected:
            raise ValueError("Invalid migration quarantine folder.")
        if not 0 <= entry["group"] < len(data["groups"]):
            raise ValueError("Invalid migration source group.")
        if entry["source"] not in data["groups"][entry["group"]]["members"]:
            raise ValueError("Migration source is not a verified group member.")
    for index, group in enumerate(data["groups"]):
        if group.get("payload") and not any(entry["group"] == index and entry["quarantine"] == group["payload"]
                                            for entry in data["sources"]):
            raise ValueError("Invalid migration recovery payload.")
    for entry in data["files"]:
        for key in ("backup", "after"):
            if key in entry and Path(entry[key]).name != entry[key]:
                raise ValueError("Invalid migration backup filename.")
    return manifest, data


def pending_migrations(backup_root) -> list[Path]:
    """Unfinished transactions must be recovered before another family count is made."""
    root = _plain_path(Path(backup_root))
    if not root.exists():
        return []
    return [path for path in sorted(root.glob("*/manifest.json"))
            if _load(path)[1]["status"] not in ("complete", "rolled-back")]


def resume_migration(manifest_path, store, *, cancelled=lambda: False) -> Path:
    manifest, data = _load(manifest_path)
    if _key(store.path) != _key(data["store_path"]):
        raise ValueError("Migration belongs to another multiplier store.")
    if data["status"] == "complete":
        store.reload()
        return manifest
    if data["status"] in ("committed", "finalizing"):
        try:
            finalize_migration(manifest, cancelled=cancelled)
        except MigrationCancelled:
            rollback_migration(manifest, store)
            raise
        store.reload()
        return manifest
    if data["status"] not in ("prepared", "applying"):
        raise ValueError(f"Migration cannot resume from {data['status']}.")
    try:
        _check_cancel(cancelled)
        data["status"] = "applying"
        _save(manifest, data)
        for group in data["groups"]:
            _check_cancel(cancelled)
            survivor = Path(group["survivor"])
            if not survivor.exists():
                original = Path(group["original"])
                if not original.exists():
                    original = next(Path(e["quarantine"]) for e in data["sources"]
                                    if e["source"] == group["original"])
                if _fingerprint(original, cancelled) != group["fingerprint"]:
                    raise ValueError(f"Original changed: {original}")
                staged = Path(group["staging"])
                if staged.exists():
                    _remove(staged, staged.parent)
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(original, staged)
                if _fingerprint(staged, cancelled) != group["fingerprint"]:
                    raise OSError(f"Survivor copy verification failed: {staged}")
                survivor.parent.mkdir(parents=True, exist_ok=True)
                staged.rename(survivor)
            if _fingerprint(survivor, cancelled) != group["fingerprint"]:
                raise ValueError(f"Survivor changed: {survivor}")
        for entry in data["sources"]:
            _check_cancel(cancelled)
            source, quarantine = Path(entry["source"]), Path(entry["quarantine"])
            expected = data["groups"][entry["group"]]["fingerprint"]
            if quarantine.exists():
                if source.exists() or _fingerprint(quarantine, cancelled) != expected:
                    raise ValueError(f"Quarantine conflict: {source}")
                continue
            if _fingerprint(source, cancelled) != expected:
                raise ValueError(f"Source changed: {source}")
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            source.rename(quarantine)
            _save(manifest, data)
        # State is installed before config, and the multiplier cycle last.
        entries = sorted(data["files"], key=lambda e: (e["path"] == data["store_path"],
                                                       Path(e["path"]).name == "config.json"))
        for entry in entries:
            if "after" not in entry:
                continue
            _check_cancel(cancelled)
            path = Path(entry["path"])
            after = (manifest.parent / entry["after"]).read_bytes()
            before = (manifest.parent / entry["backup"]).read_bytes() if entry["existed"] else None
            current = path.read_bytes() if path.exists() else None
            if current not in (before, after):
                raise ValueError(f"Preferences changed during migration: {path}")
            _replace(path, after)
        data["status"] = "committed"
        _save(manifest, data)
        store.reload()
        finalize_migration(manifest, cancelled=cancelled)
        return manifest
    except MigrationCancelled:
        rollback_migration(manifest, store)
        raise


def _remove(path: Path, parent: Path) -> None:
    path, parent = _plain_path(path), _plain_path(parent)
    if path.parent != parent or path == parent or not path.is_dir():
        raise ValueError(f"Unsafe migration removal: {path}")
    shutil.rmtree(path)


def finalize_migration(manifest_path, *, cancelled=lambda: False) -> Path:
    """Remove extra quarantine payloads; keep exactly one verified backup per group."""
    manifest, data = _load(manifest_path)
    if data["status"] == "complete":
        return manifest
    if data["status"] not in ("committed", "finalizing"):
        raise ValueError("Only a committed migration can remove redundant copies.")
    for group in data["groups"]:
        _check_cancel(cancelled)
        for path in (group["survivor"], group["payload"]):
            if _fingerprint(Path(path), cancelled) != group["fingerprint"]:
                raise ValueError(f"Recovery payload changed: {path}")
    data["status"] = "finalizing"
    _save(manifest, data)
    for entry in data["sources"]:
        _check_cancel(cancelled)
        quarantine = Path(entry["quarantine"])
        if entry["quarantine"] == data["groups"][entry["group"]]["payload"]:
            continue
        if quarantine.exists():
            if _fingerprint(quarantine, cancelled) != data["groups"][entry["group"]]["fingerprint"]:
                raise ValueError(f"Quarantined copy changed: {quarantine}")
            _remove(quarantine, quarantine.parent)
        _save(manifest, data)
    data["status"] = "complete"
    _save(manifest, data)
    return manifest


def rollback_migration(manifest_path, store=None) -> Path:
    """Restore original folders and exact preference bytes; refuse changed survivors."""
    manifest, data = _load(manifest_path)
    if data["status"] == "rolled-back":
        return manifest
    for group in data["groups"]:
        survivor = Path(group["survivor"])
        if survivor.exists() and _fingerprint(survivor) != group["fingerprint"]:
            raise ValueError(f"Survivor changed; rollback would discard edits: {survivor}")
    for entry in data["files"]:
        if "after" not in entry:
            continue
        path = Path(entry["path"])
        before = (manifest.parent / entry["backup"]).read_bytes() if entry["existed"] else None
        after = (manifest.parent / entry["after"]).read_bytes()
        current = path.read_bytes() if path.exists() else None
        if current not in (before, after):
            raise ValueError(f"Preferences changed; rollback would discard edits: {path}")
    data["status"] = "rolling-back"
    _save(manifest, data)
    for index, entry in reversed(list(enumerate(data["sources"]))):
        source, quarantine = Path(entry["source"]), Path(entry["quarantine"])
        group = data["groups"][entry["group"]]
        if source.exists():
            if _fingerprint(source) != group["fingerprint"]:
                raise ValueError(f"Original path is occupied by changed content: {source}")
            continue
        source.parent.mkdir(parents=True, exist_ok=True)
        if quarantine.exists():
            if _fingerprint(quarantine) != group["fingerprint"]:
                raise ValueError(f"Quarantine changed: {quarantine}")
            quarantine.rename(source)
        else:
            payload = Path(group["payload"])
            if not payload.exists():
                payload = Path(group["survivor"])
            if _fingerprint(payload) != group["fingerprint"]:
                raise ValueError(f"Recovery payload changed: {payload}")
            staged = quarantine.parent / f"restore-{index}"
            if staged.exists():
                _remove(staged, staged.parent)
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(payload, staged)
            if _fingerprint(staged) != group["fingerprint"]:
                raise OSError(f"Rollback copy failed verification: {staged}")
            staged.rename(source)
        _save(manifest, data)
    for group in data["groups"]:
        survivor = Path(group["survivor"])
        if group.get("created") and survivor.exists():
            _remove(survivor, survivor.parent)
        staged = Path(group["staging"]) if group.get("staging") else None
        if staged and staged.exists():
            _remove(staged, staged.parent)
    for entry in data["files"]:
        if "after" not in entry:
            continue
        path = _plain_path(Path(entry["path"]))
        if entry["existed"]:
            _replace(path, (manifest.parent / entry["backup"]).read_bytes())
        elif path.exists():
            path.unlink()
    data["status"] = "rolled-back"
    _save(manifest, data)
    if store is not None:
        store.reload()
    return manifest
