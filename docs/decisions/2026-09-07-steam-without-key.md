---
date: 2026-09-07
status: accepted
source: "owner, in chat; #10"
tags: [steam, review]
---

# Steam is read without a Web API key

**Decision.** Authors and workshop items are resolved through Steam's keyless
endpoints: `ISteamRemoteStorage/GetPublishedFileDetails` (200 ids per request),
a profile's `?xml=1` page (maps steamID64 ↔ vanity name in one request) and the
profile's workshop page (newest first, 30 per page). The Steam key stays
**optional**, but going without it is warned about: a banner (its Hide is
remembered), "list incomplete" per author, and a Cancel-default question before
a visit date is written from a keyless list. Subscribing goes through Wallpaper
Engine's own `steam_api64.dll` (`ISteamUGC::SubscribeItem`), with "open Steam's
page" kept as the fallback.

**Why.** A new user should have nothing to set up. Local files cannot say when a
wallpaper was added — folder times and `appworkshop_431960.acf` are bulk-stamped
by Steam validation.

**Consequences.** Using the DLL declares app id 431960, which Valve does not
sanction for apps that do not own it; it can break on a Steam update, so the
fallback must stay. See [Review](../review.md) and [Gallery](../gallery.md#subscribing).
