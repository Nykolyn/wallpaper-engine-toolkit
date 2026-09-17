# Authors database

**The MongoDB collection behind the [Review](review.md) tab.**

Only the Review tab uses it. Every other tab in this app works without it.

## What it is for

Review's whole premise is that the interesting unit is the *author*, not the
wallpaper — and that means remembering, per author, **when you last looked at
their work**. That date is what turns "here are 1 271 wallpapers" into "here are
the nine published since you were last here".

The collection is that: one document per author, carrying at minimum an
identifier, a name, and a visit date.

## Connecting

The connection string is set in the Review tab's credentials dialog and stored
DPAPI-encrypted in `data/secrets.json`. Nothing is read from the source tree and
nothing is logged.

Three ways to supply it:

1. **Paste it** into the dialog. This is the normal path.
2. **Take it from a `.env`** — the dialog's *Take the connection string from a
   .env file…* button, for when a web service already talks to the same database
   and retyping its credentials is how they get mistyped. The file is parsed
   for `MONGODB_URI`, or for `DB_HOST` / `DB_PASS` / `DB_NAME` / `DB_CLUSTER`.
3. **Environment variables**, for a scripted or headless setup:

   | Variable | Meaning |
   |---|---|
   | `WET_DB_CLUSTER` | the cluster host, when the `.env` has no `DB_CLUSTER` line |
   | `WET_SERVER_ENV` | pre-fills the `.env` picker with a path |

> **`DB_HOST` in such a file is the *username*, not a host.** That is a
> misnaming inherited from the service these files come from, and it is kept in
> one place here rather than rediscovered.

**The cluster is never baked into the source.** It is deployment-specific, so it
comes from `DB_CLUSTER` or `WET_DB_CLUSTER` and the app says so plainly when
neither is set.

Behind a VPN, a `mongodb+srv://` string needs
[special handling](review.md#reaching-mongodb-through-a-vpn) — which the app
does for you.

## The identifier problem

Steam offers two forms of profile URL and, over five years, both were pasted in:
an **account number** for 18 535 records and a **vanity name** for the other
20 512.

A vanity name is not an identity. It can be changed, and the old one is then
*released* for somebody else to claim — which had already happened in this
collection. Two records keyed on the same string were two different people; one
person appeared twice under two keys.

`app/engines/migration.py` re-keys the collection to account numbers:

- **19 573** records rewritten
- **150** duplicates merged away
- **822** that nothing could identify left alone

Two independent sources were used and **neither trusted alone** — the author of
a wallpaper the record points at, and the vanity name itself. They agreed on
**98.9%** of the records where both answered; the rest were left for a human.

## Nothing is written without saying what it would write

Every change is **planned first** and shown as a line of English —
`create New Person (76561199999999999)` — before anything is sent. You approve
the plan, not the intention.

- A full **dump of the collection** is taken before a migration.
- **Per-change backups** of the previous state go to `data/authors_backup/`
  otherwise.
- A restore can either add back what was removed, or make the collection match
  the dump exactly.

## A suggested index

The author lookup asks MongoDB for a case-insensitive collation, which costs the
index: a query cannot use an index whose collation differs, so it scans every
document — 46 ms on the server against 2 ms through the index.

Splitting it into an indexed query and a collated one was measured **slower**,
not faster: the round trip is 220 ms and the scan it saves is 46 ms, so two
queries (369 ms) lose to one (269 ms). The split is therefore taken only when
every key is digits, which have no case and can all go through the index.

The real fix is a second index carrying that collation:

```
db.steamusers.createIndex(
    { steamId: 1 },
    { name: "steamId_ci", collation: { locale: "en", strength: 2 } })
```

That is a change to *your* database rather than to this app, and a collection
like this is usually the only copy of years of work — so it is written down here
rather than done quietly.

## Testing against a real database

The test suite runs entirely against fakes by default. Two opt-in flags reach a
real one:

```
.venv\Scripts\python.exe tests\test_authors_db.py --live
.venv\Scripts\python.exe tests\test_authors_db.py --live-write
```

`--live` reads. `--live-write` round-trips a single throwaway record and removes
it again.
