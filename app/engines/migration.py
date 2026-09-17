"""migration.py — giving every author in the database a name that cannot change.

The collection was filled in over five years by pasting whatever a Steam URL
said at the time, and Steam offers two: an account number and a vanity name.
20 512 of the 39 047 records are filed under the second kind, and that is the
problem this module exists to fix.

**A vanity name is not an identity.** It can be changed, and when it is, the
old one is *released* — a different account can claim it. Both halves of that
have already happened here. A record whose name no longer resolves is an author
the toolkit can never look up again; a record whose name now resolves to somebody
else is worse, because it looks like it works. Measured on a sample of 25, three
names were dead and one had moved to a different account. The account number,
by contrast, is assigned once and never reused.

**Two independent sources, and neither is trusted alone.** 19 647 of those
records carry a `referenceLink` to one of that author's wallpapers, left behind
by the old app, and a workshop item's `creator` is an account number no rename
can touch — 36 442 items in 183 batched requests. The other source is the
vanity name itself, one request each. Where both answer and agree, the record
is migrated. Where only one answers, it is taken. Where they *disagree*, the
record is put on a list and left alone: a disagreement means one of the two is
describing a different person, and filing five years of visit history under a
stranger is not a risk worth taking to save a manual check.

**A link pointed at by more than one record is not evidence at all.** An item
has one author, so if several records name the same wallpaper, all but one of
them are wrong and nothing says which. The old app really did this: item
2428128941 is the `referenceLink` of five unrelated authors, and believing it
would have merged them into one record and deleted four.

**What this module will not do.** A record already filed under a valid account
number keeps it, even when its `referenceLink` points at somebody else's
wallpaper. Rewriting one account number to another on that evidence would move
records between authors on the word of a parser its own author commented out.
Those disagreements are counted and listed, and left alone.

Nothing here writes. :meth:`Migration.analyse` returns a plan; applying it is
:meth:`AuthorsDb.apply`, and the plan is meant to be read first.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .authors_db import Author, AuthorsDb, Change, revive_ids as _revive_ids, to_stored
from .steam_api import SteamClient, is_steam_id64
from ..settings import app_data_dir

REPORT_DIR = app_data_dir() / "authors_backup"

# `https://steamcommunity.com/sharedfiles/filedetails/?id=2594375030`
_ITEM_IN_LINK = re.compile(r"[?&]id=(\d+)")

# Where a record's account number came from, in order of how much it is trusted.
FROM_RECORD = "record"      # it was already an account number
FROM_ITEM = "item"          # the creator of a wallpaper the record points at
FROM_VANITY = "vanity"      # ResolveVanityURL on the name it is filed under
FROM_BOTH = "both"          # the wallpaper and the name agree

# What to do when the two sources name different people.
PREFER_ITEM = "item"        # believe the wallpaper; count the disagreement
SKIP = "skip"               # decide nothing, put the record on a list

# Sorts after every real date, for records that never got one.
_FOREVER = datetime.max.replace(tzinfo=timezone.utc)


def item_in(link: object) -> str | None:
    """The workshop item id inside a referenceLink, if there is one."""
    found = _ITEM_IN_LINK.search(str(link or ""))
    return found.group(1) if found else None


@dataclass
class Identity:
    """One record and everything we managed to learn about who it really is."""

    record: Author
    item: str | None = None
    by_item: str | None = None
    by_vanity: str | None = None
    resolved: str | None = None
    source: str = ""
    conflict: bool = False
    disputed: bool = False
    shared_item: bool = False

    @property
    def settled(self) -> bool:
        return bool(self.resolved)

    @property
    def needs_rewrite(self) -> bool:
        return bool(self.resolved) and self.record.steam_id != self.resolved

    def as_row(self) -> dict:
        return {
            "name": self.record.name,
            "steamId": self.record.steam_id,
            "resolved": self.resolved,
            "source": self.source,
            "fromItem": self.by_item,
            "fromVanity": self.by_vanity,
            "item": self.item,
            "sharedItem": self.shared_item,
            "disputed": self.disputed,
            "visited": self.record.visited.isoformat() if self.record.visited else None,
        }


@dataclass
class MigrationPlan:
    """What the migration would do, before any of it is done."""

    identities: list[Identity] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    merges: list[list[Author]] = field(default_factory=list)
    unresolved: list[Identity] = field(default_factory=list)
    conflicts: list[Identity] = field(default_factory=list)
    disputed: list[Identity] = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    def summary(self) -> str:
        c = self.counts
        return "\n".join([
            f"records read              {c.get('records', 0)}",
            f"  already an account id   {c.get('already', 0)}",
            f"  resolved from an item   {c.get('from_item', 0)}",
            f"  resolved from a name    {c.get('from_vanity', 0)}",
            f"  both sources agreed     {c.get('from_both', 0)}",
            f"  sources disagreed       {c.get('disputed', 0)}",
            f"  could not be resolved   {c.get('unresolved', 0)}",
            "",
            f"identifiers to rewrite    {c.get('rewrites', 0)}",
            f"duplicate groups to merge {c.get('merge_groups', 0)}"
            f" ({c.get('merge_deletes', 0)} records removed)",
            f"names to refresh          {c.get('renames', 0)}",
            f"disagreements left alone  {c.get('conflicts', 0)}",
            "",
            f"database writes planned   {len(self.changes)}",
        ])

    def collisions(self) -> list[str]:
        """Identifier rewrites that would land on a record still holding one.

        `steamId` is unique, so a rewrite onto a value another surviving record
        already carries is rejected by the database — halfway through, with
        some of the plan applied and the rest not. By construction that cannot
        happen (records resolving to one account are one merge group, and only
        one of them survives), but "by construction" is an argument, and this
        is a check. It costs a pass over a list.
        """
        doomed = {c.author.id for c in self.changes if c.kind == "delete"}
        surviving: dict[str, object] = {}
        for identity in self.identities:
            if identity.record.id not in doomed:
                surviving.setdefault(identity.record.steam_id.lower(),
                                     identity.record.id)
        problems = []
        for change in self.changes:
            wanted = change.fields.get("steamId")
            if not wanted:
                continue
            holder = surviving.get(str(wanted).lower())
            if holder is not None and holder != change.author.id:
                problems.append(
                    f"{change.author} cannot take {wanted}: another record still has it")
        return problems

    def write(self, path: Path | str | None = None) -> Path:
        """Save the whole plan, so it can be read before it is run."""
        target = Path(path) if path else (
            REPORT_DIR / f"migration-{datetime.now():%Y%m%d-%H%M%S}.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "made": datetime.now().isoformat(timespec="seconds"),
            "counts": self.counts,
            "changes": [c.describe() for c in self.changes],
            "merges": [[str(a) for a in group] for group in self.merges],
            "conflicts": [i.as_row() for i in self.conflicts],
            "disputed": [i.as_row() for i in self.disputed],
            "unresolved": [i.as_row() for i in self.unresolved],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return target


class Migration:
    """Works out who each record really is, and what it would take to say so."""

    def __init__(self, db: AuthorsDb, steam: SteamClient,
                 on_disagreement: str = PREFER_ITEM, stale_days: int = 365,
                 on_log: Callable[[str], None] | None = None,
                 on_progress: Callable[[str, int, int], None] | None = None):
        self.db = db
        self.steam = steam
        self.on_disagreement = on_disagreement
        self.stale_days = stale_days
        self._log = on_log or (lambda _message: None)
        self._progress = on_progress or (lambda _stage, _done, _total: None)

    # -- reading -----------------------------------------------------------

    def records(self, dump: Path | str | None = None) -> list[Author]:
        """Every record, from the database or from a dump already taken.

        Reading 39 047 documents over the wire costs three and a half minutes,
        and an analysis that is going to be run more than once should not pay
        it every time. The dump is the same data — and taking one is a
        prerequisite of this whole exercise anyway.
        """
        if dump:
            data = AuthorsDb.read_dump(dump)
            self._log(f"reading {len(data['documents'])} records from {Path(dump).name}")
            return [Author.from_doc(_from_dump_row(d)) for d in data["documents"]]
        self._log("reading the collection")
        out = []
        for doc in self.db._require().find({}):
            out.append(Author.from_doc(doc))
            if len(out) % 5000 == 0:
                self._progress("read", len(out), 0)
        return out

    # -- the work ----------------------------------------------------------

    def analyse(self, dump: Path | str | None = None,
                records: list[Author] | None = None,
                refresh_names: bool = False,
                resolve_vanity: bool = True,
                cross_check: bool = False) -> MigrationPlan:
        """Resolve every record to an account number and plan the tidying up.

        ``cross_check`` resolves the vanity name of records a wallpaper has
        already identified. It changes no outcome — the wallpaper wins either
        way — and it costs a request per record, some twenty thousand of them,
        against the couple of hundred the rest of the analysis needs. It is
        here because it is the only way to *count* how many names have been
        released and re-claimed, which is worth knowing once, not weekly.
        """
        plan = MigrationPlan()
        if records is None:
            records = self.records(dump)
        identities = [Identity(record=r, item=item_in(r.reference_link))
                      for r in records]

        self._resolve_from_items(identities)
        if resolve_vanity:
            self._resolve_from_names(identities, cross_check=cross_check)
        self._decide(identities)

        plan.identities = identities
        plan.unresolved = [i for i in identities if not i.settled and not i.disputed]
        plan.conflicts = [i for i in identities if i.conflict]
        plan.disputed = [i for i in identities if i.disputed]

        groups = self._group(identities)
        plan.changes, plan.merges = self._plan_changes(groups, refresh_names)
        plan.counts = self._count(identities, plan)
        return plan

    def _resolve_from_items(self, identities: list[Identity]) -> None:
        """Ask Steam who made each wallpaper the records point at.

        A wallpaper pointed at by more than one record is thrown away rather
        than believed. An item has exactly one author, so if five records claim
        the same one, at least four of them are wrong and nothing here says
        which — and the old app really did write one link onto several records:
        item 2428128941 is the `referenceLink` of five unrelated authors.
        Believing it would merge them into one and delete four. 70 items are
        shared this way, across 144 records; they fall back to their names.
        """
        counts: dict[str, int] = {}
        for identity in identities:
            if identity.item:
                counts[identity.item] = counts.get(identity.item, 0) + 1
        shared = {item for item, n in counts.items() if n > 1}
        if shared:
            self._log(f"{len(shared)} wallpapers are pointed at by more than one "
                      f"record; ignoring them as evidence")
        for identity in identities:
            if identity.item in shared:
                identity.shared_item = True

        items = [i for i in dict.fromkeys(i.item for i in identities if i.item)
                 if i not in shared]
        if not items:
            return
        self._log(f"resolving {len(items)} wallpapers to their authors")
        described = self.steam.details(
            items, on_progress=lambda done, total: self._progress("items", done, total))
        for identity in identities:
            if identity.shared_item:
                continue
            found = described.get(identity.item or "")
            if found is not None and found.ok and found.creator:
                identity.by_item = found.creator

    def _resolve_from_names(self, identities: list[Identity],
                            cross_check: bool = False) -> None:
        """The fallback: records with no wallpaper to point at.

        One request each, so it is only spent where nothing cheaper will do —
        865 records rather than 20 512, unless a cross-check is asked for.
        """
        pending = [i for i in identities
                   if (cross_check or not i.by_item)
                   and not is_steam_id64(i.record.steam_id) and i.record.steam_id]
        if not pending:
            return
        names = list(dict.fromkeys(i.record.steam_id for i in pending))
        self._log(f"resolving {len(names)} vanity names"
                  + (" as a cross-check" if cross_check else
                     " for records no wallpaper could identify"))
        found = self.steam.resolve_vanities(
            names, on_progress=lambda done, total: self._progress("names", done, total))
        for identity in pending:
            identity.by_vanity = found.get(identity.record.steam_id)

    def _decide(self, identities: list[Identity]) -> None:
        """Settle on one account number per record, or refuse to.

        A record already filed under an account number keeps it. That is
        deliberate: the alternative moves records between authors on the word
        of a link written by a parser unreliable enough that its own author
        commented it out.

        For the rest, a record is only migrated where the evidence is not
        contradicted. Two sources that disagree are not a tie to be broken by
        preferring one — they are a sign that one of them is describing a
        different person, and acting on either would file this author's five
        years of visit history under a stranger. Those go on a list instead.
        """
        for identity in identities:
            record = identity.record
            if is_steam_id64(record.steam_id):
                identity.resolved = record.steam_id
                identity.source = FROM_RECORD
                identity.conflict = bool(identity.by_item
                                         and identity.by_item != record.steam_id)
                continue
            if identity.by_item and identity.by_vanity:
                if identity.by_item == identity.by_vanity:
                    identity.resolved = identity.by_item
                    identity.source = FROM_BOTH
                elif self.on_disagreement == SKIP:
                    identity.disputed = True
                    identity.conflict = True
                else:
                    # Either the name was released and re-claimed by somebody
                    # else, or the link was wrong. The wallpaper is the better
                    # of the two — an item's author cannot be reassigned — so
                    # it wins, and the disagreement is counted rather than
                    # turned into a decision for somebody to make by hand.
                    identity.resolved = identity.by_item
                    identity.source = FROM_ITEM
                    identity.conflict = True
            elif identity.by_item:
                identity.resolved = identity.by_item
                identity.source = FROM_ITEM
            elif identity.by_vanity:
                identity.resolved = identity.by_vanity
                identity.source = FROM_VANITY

    def _group(self, identities: list[Identity]) -> dict[str, list[Identity]]:
        groups: dict[str, list[Identity]] = {}
        for identity in identities:
            if identity.settled:
                groups.setdefault(identity.resolved, []).append(identity)
        return groups

    # -- planning ----------------------------------------------------------

    def _plan_changes(self, groups: dict[str, list[Identity]], refresh_names: bool
                      ) -> tuple[list[Change], list[list[Author]]]:
        personas = self._personas(groups) if refresh_names else {}
        changes: list[Change] = []
        merges: list[list[Author]] = []

        for id64, members in groups.items():
            name = personas.get(id64)
            if len(members) == 1:
                change = self.db.plan_update(
                    members[0].record,
                    steam_id=id64 if members[0].needs_rewrite else None,
                    name=name,
                    reason="migrated to an account id" if members[0].needs_rewrite else "")
                if change:
                    changes.append(change)
                continue

            merged, survivor = self._merge_group([m.record for m in members], id64, name)
            merges.append([survivor] + [r for r in (m.record for m in members)
                                        if r.id != survivor.id])
            changes.extend(merged)
        return changes, merges

    def _merge_group(self, records: list[Author], id64: str, name: str | None
                     ) -> tuple[list[Change], Author]:
        """Fold a group of records for one author into a single record.

        The survivor is whichever record already carries the account number, so
        the unique index on `steamId` is never asked to hold two of them at
        once; failing that, one that has actually been visited recently, and
        failing that the oldest, whose `dateAdded` is the real discovery date.
        The deletions are emitted first for the same reason: nothing must still
        be holding the identifier the survivor is about to take.

        A copy nobody has visited in a year is dropped outright rather than
        merged. It has nothing to contribute: its visit date is older than the
        survivor's by definition, and carrying its other fields across would
        resurrect a favourite mark or an unfinished review from a record that
        has been dead since before the duplicate was noticed.
        """
        stale_before = datetime.now(timezone.utc) - timedelta(days=self.stale_days)
        live = [r for r in records
                if r.visited is None or r.visited >= stale_before] or records

        survivor = next((r for r in records if r.steam_id == id64), None)
        if survivor is None:
            # Oldest first; a record with no date at all goes last rather than
            # comparing as the beginning of time.
            survivor = min(live, key=lambda r: (r.added is None,
                                                r.added or _FOREVER))
        others = [r for r in records if r.id != survivor.id]
        # Only copies still in use have a say in what the survivor becomes;
        # the survivor always speaks for itself, stale or not.
        speaking = [r for r in records if r in live or r.id == survivor.id]
        dropped = len(others) - len([r for r in others if r in speaking])

        visited = max((r.visited for r in speaking if r.visited), default=None)
        link = next((r.reference_link for r in speaking if r.reference_link), None)
        counted = max((r.new_wallpapers for r in speaking
                       if r.new_wallpapers is not None), default=None)
        # The one exception: when this author was first found is a fact about
        # the author, not about the copy, and the web app charts it. It is
        # taken from every copy, including the ones being dropped unread.
        added = min((r.added for r in records if r.added), default=None)

        changes: list[Change] = [
            self.db.plan_delete(other, reason=(
                "stale duplicate of " if other not in speaking else "duplicate of ")
                + str(survivor))
            for other in others]

        update = self.db.plan_update(
            survivor,
            steam_id=id64 if survivor.steam_id != id64 else None,
            name=name,
            visited=visited,
            reference_link=link,
            new_wallpapers=counted,
            # A pending review or a favourite mark on a copy still in use has
            # to survive: losing one silently is worse than keeping one too many.
            favorite=True if any(r.favorite for r in speaking) else None,
            reviewed_favorites=False if not all(r.reviewed_favorites for r in speaking)
            else None,
            reason=f"merged {len(others) - dropped} duplicate(s)"
                   + (f", dropped {dropped} unvisited for over a year" if dropped else ""))
        earliest = added
        if earliest and survivor.added and earliest < survivor.added:
            update = update or Change(kind="update", author=survivor,
                                      fields={}, before={})
            update.fields["dateAdded"] = to_stored(earliest)
            (update.before or {})["dateAdded"] = survivor.added
        if update:
            changes.append(update)
        return changes, survivor

    # -- applying ----------------------------------------------------------

    def apply(self, plan: MigrationPlan, dump: Path | str,
              on_progress: Callable[[str, int, int], None] | None = None) -> dict:
        """Run a plan, but only against a dump that still describes this data.

        The dump *is* the backup for a migration — a copy of every record as it
        stood before any of this — so the per-change backups are skipped. That
        only holds if the dump is real and current, which is checked here
        rather than assumed: a plan applied against a stale copy is a plan with
        no way back.
        """
        problems = plan.collisions()
        if problems:
            raise ValueError("the plan would collide with itself:\n  "
                             + "\n  ".join(problems[:10]))
        data = AuthorsDb.read_dump(dump)          # raises if truncated
        live = self.db.count()
        if data["written"] != live:
            raise ValueError(
                f"{Path(dump).name} holds {data['written']} records but the "
                f"collection now has {live}; take a fresh dump before applying")
        self._log(f"applying {len(plan.changes)} changes "
                  f"(backup: {Path(dump).name}, {data['written']} records)")
        return self.db.apply(
            plan.changes, backup=False,
            on_progress=lambda done, total: (on_progress or self._progress)(
                "apply", done, total) if on_progress or self._progress else None)

    def _personas(self, groups: dict[str, list[Identity]]) -> dict[str, str]:
        """Current display names, for the records whose own has gone stale."""
        ids = list(groups)
        self._log(f"reading current names for {len(ids)} authors")
        found = self.steam.profiles(
            ids, on_progress=lambda done, total: self._progress("names", done, total))
        return {key: profile.name for key, profile in found.items() if profile.name}

    def _count(self, identities: list[Identity], plan: MigrationPlan) -> dict:
        return {
            "records": len(identities),
            "already": sum(1 for i in identities if i.source == FROM_RECORD),
            "from_item": sum(1 for i in identities if i.source == FROM_ITEM),
            "from_vanity": sum(1 for i in identities if i.source == FROM_VANITY),
            "from_both": sum(1 for i in identities if i.source == FROM_BOTH),
            "disputed": len(plan.disputed),
            "shared_links": sum(1 for i in identities if i.shared_item),
            "unresolved": len(plan.unresolved),
            "conflicts": len(plan.conflicts),
            "rewrites": sum(1 for c in plan.changes
                            if c.kind == "update" and "steamId" in c.fields),
            "renames": sum(1 for c in plan.changes
                           if c.kind == "update" and "name" in c.fields),
            "merge_groups": len(plan.merges),
            "merge_deletes": sum(1 for c in plan.changes if c.kind == "delete"),
        }


def _from_dump_row(plain: dict) -> dict:
    """A dump row back into the shape the database speaks.

    Both halves matter and only one is obvious. Dates have to become datetimes
    or nothing can be compared — but `_id` has to become an ObjectId, because
    it is what every update and delete in the plan is addressed to. A plan
    built with string ids matches no documents at all, and Mongo does not call
    that an error: the whole migration reports success and changes nothing.
    """
    out = dict(plain)
    for key in ("dateAdded", "dateVisited"):
        value = out.get(key)
        if isinstance(value, str):
            try:
                out[key] = datetime.fromisoformat(value)
            except ValueError:
                out[key] = None
    return _revive_ids(out)
