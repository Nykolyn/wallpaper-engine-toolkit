# Migrate physical copies to multipliers

Migration first inspects the configured reserve, myprojects, duplicates folder
and saved Copier destination. It groups `_copyN` folders (including chained
suffixes) only when every filename, empty directory and file byte matches a
readable original. Original plus three copies becomes **Multiplier 4**;
numbering gaps do not inflate the count. Edited copies, links/junctions, missing
originals and occupied survivor names stay in place and appear in the report.

Open **Rotator → Migrate copies** to inspect the plan and its preserved-copy
report. Confirm **Migrate copies** after reviewing it. The app waits for pending
multiplier saves, blocks library jobs and quota edits, then closes Wallpaper
Engine and restarts it after the folders and playlists are consistent.
The original stays as the survivor; when a copy is currently active, the
survivor is placed in myprojects. A protected member keeps the survivor
protected. Config and binary playlist-deck references are remapped to the
survivor and duplicates are removed within each playlist.

Before changing folders, migration writes a durable manifest and exact backups
of supplied suite/rotator settings, history, multiplier state, Wallpaper Engine
config and playlist state. Verified source folders are renamed into adjacent
quarantine directories outside all scanned wallpaper roots. The new quota
cycle is published only after the survivor and playlist changes succeed.
Current active entities count as used once.

Finalization removes redundant quarantine copies, retaining **one verified
payload per group** for rollback. This recovery payload consumes one wallpaper's
disk space, regardless of its former copy count. Do not manually remove it or
the manifest while rollback is needed.

Open **Migrate copies** again to recover an interrupted migration or choose
**Restore last migration**. Recovery can resume without counting copies twice,
or restore the exact old folder names and preference bytes. Restoration refuses
to overwrite later wallpaper or preference edits. If a failed migration cannot
restore consistent folders, Wallpaper Engine stays closed until recovery finishes.

Rotations, retries and multiplier edits refuse unfinished migrations. Inspecting
a plan never migrates the library. Tests use explicit temporary Wallpaper Engine
paths and reject live-path discovery.
