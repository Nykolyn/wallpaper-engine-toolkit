---
date: 2026-10-23
status: accepted
source: "#89, #93"
tags: [release, process]
---

# Releases are made by hand

**Decision.** A pull request writes its change under `## [Unreleased]` in
CHANGELOG.md and leaves `__version__` alone. Merging runs nothing. A release is
a manual run of the **Release** workflow on `main`: it prepares the next patch
version and dated notes, runs the tests, builds and checks the installer, tags
and publishes. A minor or major release has its version and notes prepared in
a pull request first. A release is made only when the owner asks for one.

**Why.** The owner batches several features into one release and wants to save
CI. This replaced the earlier rule (2026-09-18) of a version bump and dated
section in every pull request.

**Consequences.** `tools/release.py check --base origin/main` runs on every
pull request and fails one that writes nothing under `[Unreleased]`. Details in
[CONTRIBUTING.md](../../CONTRIBUTING.md#versions).
