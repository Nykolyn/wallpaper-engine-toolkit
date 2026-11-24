---
date: 2026-09-17
status: accepted
source: "owner, in chat"
tags: [process]
---

# No personal data in source, tests or previews

**Decision.** No credential, hostname, account name, real id or personal path
goes into a committed file. Anything about to be published is audited first —
what is *staged*, not the working tree — and the audit says what it found.
Fixtures use clearly invented values (RFC 5737 addresses, RFC 2606 domains,
made-up authors and titles). Screenshots and snapshots are built from
fabricated fixtures with every route to the real disk stubbed
(`tools/ui_snapshot.py`). Live tests skip themselves unless an environment
variable names what to talk to.

**Why.** Preparing the repository for publication turned up a live Steam key in
a test fixture, a private database host, an account name and machine paths.
Nothing had been pushed, but one push would have published all of it. The
redesign's mock-ups also carry sample names that may come from a real library.

**Consequences.** `data/` is never committed. A new setting that needs a
private value gets an environment variable, not a literal. Building the real
main window in a grab reads real paths — stub them (see
[Development](../development.md#tests)).
