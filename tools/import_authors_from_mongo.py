"""Carry the authors over from the old MongoDB collection into `authors.sqlite`.

A one-time move, for whoever used the Review tab when it read MongoDB. It takes
a fresh dump of the collection (or reads one you already have), converts it,
writes it, and then checks the result against the dump row by row before
saying it is done. The Mongo collection itself is only ever read.

    .venv\\Scripts\\python.exe tools\\import_authors_from_mongo.py
    .venv\\Scripts\\python.exe tools\\import_authors_from_mongo.py --target dist\\WallpaperEngineToolkit\\data
    .venv\\Scripts\\python.exe tools\\import_authors_from_mongo.py --dump data\\authors_backup\\dump-….json

``--target`` is the `data` folder the database goes into (the one beside the
exe, for a deployed copy). The connection string is read from the
DPAPI-protected `secrets.json` in that folder, or from ``--secrets``. An
existing database with authors in it is only replaced with ``--replace``, and
even then it is snapshotted first.

This tool, `authors_db.py`, `mongo_srv.py` and `migration.py` go away together
in a later release, once the local database has been in use for a while.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import secrets  # noqa: E402
from app.engines import authors_store as st  # noqa: E402
from app.settings import app_data_dir  # noqa: E402


def say(text: str = "") -> None:
    print(text, flush=True)


def take_dump(uri: str, folder: Path) -> Path:
    from app.engines.authors_db import AuthorsDb

    target = folder / f"dump-{datetime.now():%Y%m%d-%H%M%S}.json"
    started = time.monotonic()
    with AuthorsDb(uri) as mongo:
        say(f"connected to {mongo.database} ({mongo.dialled}); "
            f"{mongo.count()} documents")

        def progress(done: int, total: int) -> None:
            print(f"\r  dumping {done}/{total}", end="", flush=True)

        mongo.dump(target, on_progress=progress)
    say(f"\n  written to {target} in {time.monotonic() - started:.0f} s")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target", type=Path, default=app_data_dir(),
                        help="the data folder to write authors.sqlite into")
    parser.add_argument("--secrets", type=Path, default=None,
                        help="secrets.json holding the connection string "
                             "(default: the one in --target)")
    parser.add_argument("--dump", type=Path, default=None,
                        help="use this dump instead of taking a fresh one")
    parser.add_argument("--replace", action="store_true",
                        help="replace a database that already has authors in it")
    args = parser.parse_args(argv)

    target: Path = args.target.resolve()
    database = target / st.DB_NAME
    backups = target / st.BACKUP_DIRNAME
    say(f"target: {database}")

    # Refuse before the slow part, not after it.
    store = st.AuthorsStore(database).open()
    existing = store.count()
    if existing and not args.replace:
        say(f"\n{database.name} already holds {existing} authors. Nothing was "
            "changed. Run again with --replace to overwrite it; what is there "
            "is snapshotted first.")
        return 2

    # 1. The dump: fresh unless one is named.
    if args.dump:
        dump = args.dump.resolve()
        say(f"reading the dump {dump}")
    else:
        where = (args.secrets or target / "secrets.json").resolve()
        uri = secrets.get(secrets.AUTHORS_DB_URI, where)
        if not uri:
            say(f"\nNo connection string in {where}. Pass --secrets, or --dump "
                "with a dump you already have.")
            return 2
        dump = take_dump(uri, backups)
    from app.engines.authors_db import AuthorsDb
    data = AuthorsDb.read_dump(dump)          # raises if truncated
    documents = data["documents"]
    say(f"  {len(documents)} documents, taken {data.get('taken')}")

    # 2. Converted.
    rows, report = st.from_mongo_documents(documents)
    say(f"\nconverted: {report['rows']} authors")
    say(f"  {report['by_account_number']} filed by account number, "
        f"{report['by_vanity_name']} by vanity name")
    say(f"  {report['never_visited']} never visited")
    if report["skipped_without_key"]:
        say(f"  {report['skipped_without_key']} documents had no key and were left out")
    for group in report["folded"]:
        say(f"  folded, differing only in case: {' + '.join(group)}")

    # 3. Written, in one transaction, snapshotted before and after.
    result = store.replace_all(rows, reason=f"imported from {dump.name}")
    if result["previous"]:
        say(f"\nthe {existing} authors that were there are saved in "
            f"{Path(result['previous']).name}")
    say(f"\nwritten: {result['written']} authors; snapshot {Path(result['backup']).name}")

    # 4. Checked against the dump, row by row — the store read fresh from disk.
    store.close()
    with st.AuthorsStore(database) as check:
        stored = {r["key"]: r for r in check.rows()}
    wanted = {r["key"]: r for r in rows}
    missing = sorted(set(wanted) - set(stored))
    extra = sorted(set(stored) - set(wanted))
    different = sorted(k for k in set(wanted) & set(stored) if wanted[k] != stored[k])
    accounted = len(rows) + sum(len(g) - 1 for g in report["folded"]) \
        + report["skipped_without_key"]
    say("\nverification:")
    say(f"  documents in the dump        {len(documents)}")
    say(f"  = authors written            {len(rows)}")
    say(f"  + folded case duplicates     {sum(len(g) - 1 for g in report['folded'])}")
    say(f"  + documents with no key      {report['skipped_without_key']}")
    ok = (accounted == len(documents) and not missing and not extra
          and not different and len(stored) == len(rows))
    if missing or extra or different:
        say(f"  MISMATCH: {len(missing)} missing, {len(extra)} unexpected, "
            f"{len(different)} different — first: {(missing + extra + different)[:5]}")
    say(f"  every row reads back exactly as converted: {'yes' if ok else 'NO'}")
    say(f"\n{database} — {database.stat().st_size / 1e6:.1f} MB")
    say("The Mongo collection was only read; it is as it was.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
