"""The Steam client, checked without touching Steam.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_steam_api.py
    .venv\\Scripts\\python.exe tests\\test_steam_api.py --live

Everything above the ``--live`` line works on captured response shapes and a
temporary cache file, so it says nothing about the network. The live section is
opt-in and needs a Steam Web API key in the secret store; it is what confirms
the two facts the client's design rests on and that no document can promise:
that ``GetUserFiles`` really is ordered by ``time_updated`` descending, and that
it really does return the mature items the community listing hides.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.engines.steam_api as sa      # noqa: E402
from app import secrets                 # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_steam_test_"))

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


# ---- Profiles --------------------------------------------------------------

PROFILE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<profile><steamID64>76561198000000015</steamID64>
<steamID><![CDATA[johnny]]></steamID>
<privacyState>public</privacyState>
<avatarMedium><![CDATA[https://x/av.jpg]]></avatarMedium>
<customURL><![CDATA[o0p]]></customURL></profile>"""

ERROR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><error><![CDATA[The specified profile could not be found.]]></error></response>"""

found = sa.parse_profile_xml(PROFILE_XML)
check("a profile xml gives the numeric id and the vanity name together",
      found.id64 == "76561198000000015" and found.vanity == "o0p")
check("and the display name, which is what a new author is filed under",
      found.name == "johnny" and not found.private)
check("both keys travel together, because the database holds either form",
      found.keys == ["76561198000000015", "o0p"])

gone = sa.parse_profile_xml(ERROR_XML)
check("a vanity name nobody answers to is an answer, not a crash",
      gone.exists is False and gone.id64 is None)

summary = sa.parse_summary({
    "steamid": "76561198000000013", "personaname": "Vanity",
    "profileurl": "https://steamcommunity.com/id/vanityone/",
    "communityvisibilitystate": 3, "avatarmedium": "https://x/av.jpg"})
check("a summary's profile url is where the vanity name hides",
      summary.vanity == "vanityone" and summary.id64 == "76561198000000013")

numeric_only = sa.parse_summary({
    "steamid": "76561198000000012", "personaname": "nobody",
    "profileurl": "https://steamcommunity.com/profiles/76561198000000012/",
    "communityvisibilitystate": 1})
check("an author without a custom url has no vanity name to match on",
      numeric_only.vanity is None and numeric_only.private)


# ---- What a wallpaper is, and what it costs ---------------------------------

scene = sa.ItemDetails.from_steam({
    "publishedfileid": "9", "title": "t", "time_created": 1, "file_size": "6266036",
    "tags": [{"tag": "Scene"}, {"tag": "Anime"}, {"tag": "2560 x 1440"}]})
check("a wallpaper's type is read out of its tags",
      scene.kind == "Scene")
check("and its size comes as a number even when Steam sends a string",
      scene.file_size == 6266036)
check("the size is written the way a card has room for",
      scene.size_text == "6.0 MB")
check("a video large enough to matter says so in gigabytes",
      sa.ItemDetails(id="1", ok=True, file_size=2 * 1024 ** 3).size_text == "2.0 GB")
check("an item with no tags has no type rather than a wrong one",
      sa.ItemDetails.from_steam({"publishedfileid": "8", "tags": [{"tag": "Anime"}]}).kind == "")
check("and one with no size shows nothing rather than 0 B",
      sa.ItemDetails(id="1", ok=True).size_text == "")
check("both survive the cache",
      sa.ItemDetails.from_json(scene.to_json()).kind == "Scene"
      and sa.ItemDetails.from_json(scene.to_json()).file_size == 6266036)

old_cache = sa._Cache(TMP / "old.sqlite")
old_cache.put("item", {"9": {"id": "9", "ok": True, "title": "cached before sizes"}})
client_on_old = sa.SteamClient(api_key=None, cache_path=TMP / "old.sqlite")
check("an item cached before types and sizes existed is not served again",
      client_on_old._cache.get(sa.ITEM_CACHE, "9", sa.DETAILS_TTL) is None)
client_on_old.close()
old_cache.close()


# ---- Item details ----------------------------------------------------------

item = sa.ItemDetails.from_steam({
    "publishedfileid": "3796616409", "result": 1, "creator": "76561198000000013",
    "title": "The Cliff", "time_created": 1788677429, "time_updated": 1788724021,
    "preview_url": "https://x/p.jpg", "subscriptions": 15})
check("an item carries its author, its date and a preview to show",
      item.ok and item.creator == "76561198000000013" and item.preview)

missing = sa.ItemDetails.from_steam({"publishedfileid": "1", "result": 9})
check("an item Steam will not describe stays in the list, marked",
      missing.id == "1" and not missing.ok)

from_user_files = sa.ItemDetails.from_steam({
    "publishedfileid": "2", "title": "x", "time_created": 5, "time_updated": 6})
check("GetUserFiles omits `result` for the items it does return",
      from_user_files.ok and from_user_files.created == 5)


# ---- What a review asks of an author's list --------------------------------

def made(item_id: str, days_ago: float, updated_days_ago: float | None = None):
    now = time.time()
    return sa.ItemDetails(
        id=item_id, ok=True, creator="A", title=item_id,
        created=int(now - days_ago * 86400),
        updated=int(now - (days_ago if updated_days_ago is None else updated_days_ago) * 86400))


author = sa.AuthorItems(id64="A", total=4, items=[
    made("d", 1), made("c", 3), made("b", 10), made("a", 40)])
visited = datetime.now() - timedelta(days=5)

check("what is new since the visit is counted exactly",
      [i.id for i in author.since(visited)] == ["d", "c"])
check("a review starts at the oldest unseen wallpaper, not the newest",
      author.first_after(visited).id == "c")
check("an author with nothing new says so",
      author.first_after(datetime.now()) is None)
check("with no visit on record, everything is unseen",
      len(author.since(None)) == 4)
check("the listing page is worked out from the item's place in the list",
      author.page_of("d", per_page=2) == 1 and author.page_of("b", per_page=2) == 2)


# ---- Paging stops as soon as it cannot matter ------------------------------

class FakeSteam(sa.SteamClient):
    """A client whose GetUserFiles is a page of dates, not a request."""

    def __init__(self, pages, delay=0.0, **kwargs):
        super().__init__(api_key="x", cache_path=None, **kwargs)
        self.pages = pages
        self.asked = 0
        self.delay = delay
        self.in_flight = 0
        self.most_at_once = 0
        self._seen = threading.Lock()

    def _api(self, url, params):
        with self._seen:
            self.asked += 1
            self.in_flight += 1
            self.most_at_once = max(self.most_at_once, self.in_flight)
        try:
            if self.delay:
                time.sleep(self.delay)
            page = self.pages[params["page"] - 1]
            return {"total": sum(len(p) for p in self.pages),
                    "publishedfiledetails": page}
        finally:
            with self._seen:
                self.in_flight -= 1


def page(count: int, days_ago: float) -> list[dict]:
    stamp = int(time.time() - days_ago * 86400)
    return [{"publishedfileid": f"{days_ago}-{n}", "time_created": stamp,
             "time_updated": stamp} for n in range(count)]


full = sa.USER_FILES_PER_PAGE
client = FakeSteam([page(full, 1), page(full, 30), page(full, 90), page(5, 200)])
whole = client.author_items("A")
check("with no cutoff the whole workshop is read", len(whole.items) == full * 3 + 5)
check("and it is not marked partial", not whole.partial and not whole.truncated)

client = FakeSteam([page(full, 1), page(full, 30), page(full, 90), page(5, 200)])
recent = client.author_items("A", since=datetime.now() - timedelta(days=7))
check("a cutoff stops at the first page that is entirely older",
      client.asked == 2 and recent.partial)
check("everything newer than the cutoff is still there",
      len(recent.since(datetime.now() - timedelta(days=7))) == full)
check("and the author's real total survives the shortcut", recent.total == full * 3 + 5)

client = FakeSteam([page(full, 1)] * (sa.MAX_AUTHOR_PAGES + 2))
capped = client.author_items("A", max_pages=3)
check("hitting the page cap is the one thing that admits to missing items",
      capped.truncated and client.asked == 3)

# A cutoff makes the paging sequential on purpose: each page is what decides
# whether the next one is worth asking for. A full fetch has no such decision,
# every page will be read, and the first response already says how many there
# are — so the rest go out together. On the real thing that is 17.1 s against
# 4.3 s for an author with 1 271 wallpapers.
pages = [page(full, 1), page(full, 30), page(full, 90), page(5, 200)]

client = FakeSteam(pages, delay=0.1, workers=8)
started = time.perf_counter()
whole = client.author_items("A")
overlapped = time.perf_counter() - started
check("a full fetch asks for the pages after the first together",
      client.most_at_once > 1)
check("so four pages of 100 ms are one wait, not four", overlapped < 0.35)
check("and every page still arrives", len(whole.items) == full * 3 + 5)
check("in the order the gallery wants, newest first",
      [i.created for i in whole.items] == sorted(
          (i.created for i in whole.items), reverse=True))

client = FakeSteam(pages, delay=0.1, workers=8)
client.author_items("A", since=datetime.now() - timedelta(days=7))
check("a cutoff run still asks one page at a time, or it could not stop",
      client.most_at_once == 1 and client.asked == 2)


# ---- The community listing, and why it is the fallback ---------------------

LISTING = """<html><body>
<div id="leftContents"><div class="workshopBrowseItems">
  <a href="https://steamcommunity.com/sharedfiles/filedetails/?id=111"><img></a>
  <a href="https://steamcommunity.com/sharedfiles/filedetails/?id=111">Title</a>
  <a href="https://steamcommunity.com/sharedfiles/filedetails/?id=222">Other</a>
</div></div>
<div class="workshopBrowsePagingInfo">Showing 1-2 of 1,204 entries</div>
<a href="https://steamcommunity.com/sharedfiles/filedetails/?id=999">recommended</a>
</body></html>"""

listing = sa.parse_workshop_page(LISTING, page=1)
check("each item is counted once however many links point at it",
      listing.ids == ["111", "222"])
check("ids outside the browse container are not items on this page",
      "999" not in listing.ids)
check("the listing states its own total, comma and all", listing.total == 1204)
check("so the number of pages is known before reading them",
      listing.pages == 41 and listing.has_more)

check("a listing with no paging line still knows what it saw",
      sa.parse_workshop_page(
          '<div class="workshopBrowseItems">'
          '<a href="/sharedfiles/filedetails/?id=5"></a></div>', page=1).total == 1)


# ---- Urls ------------------------------------------------------------------

check("an account number is seventeen digits on a fixed base",
      sa.is_steam_id64("76561198000000011")
      and not sa.is_steam_id64("7656119809872572")      # one short
      and not sa.is_steam_id64("12345678901234567"))    # wrong base

# Both of these are real authors in the collection, filed under a vanity url
# that happens to be all digits. Read as account numbers they resolve to
# nobody, and every lookup for them fails quietly.
check("a vanity name made only of digits is not an account number",
      not sa.is_steam_id64("328786869") and not sa.is_steam_id64("97777779"))
check("so it is looked up as a name",
      "/id/328786869/" in sa.workshop_url("328786869"))

check("an account number goes down /profiles/ and a name down /id/",
      "/profiles/76561198000000011/" in sa.workshop_url("76561198000000011")
      and "/id/vanityone/" in sa.workshop_url("vanityone"))
check("the workshop url is filtered to Wallpaper Engine",
      f"appid={sa.APP_ID}" in sa.workshop_url("76561198000000011"))
check("an item can be opened in the Steam client, where Subscribe is one click",
      sa.ItemDetails(id="7", ok=True).steam_url == "steam://url/CommunityFilePage/7")


# ---- Keeping the key out of the logs ---------------------------------------

check("a url is only ever logged with the key taken out",
      sa._safe("https://api.steampowered.com/x/?steamid=1&key=SECRET&p=2")
      == "https://api.steampowered.com/x/?steamid=1&key=<hidden>&p=2")

no_key = sa.SteamClient(api_key=None, cache_path=None)
try:
    no_key._api(sa.USER_FILES_URL, {"steamid": "1"})
    refused = False
except sa.SteamAuthError:
    refused = True
check("a Web API call without a key is refused here, not by Steam", refused)
check("and the client knows it is working blind", not no_key.has_key)

blind = sa.SteamClient(api_key=None, cache_path=None)
blind.workshop_page = lambda *_a, **_kw: sa.WorkshopPage(ids=[], total=0, page=1)
check("so it falls back to the listing and says the answer is incomplete",
      blind.author_items("1").complete is False)


# ---- The cache -------------------------------------------------------------

cache = sa._Cache(TMP / "cache.sqlite")
cache.put("item", {"1": {"id": "1", "ok": True}})
check("a cached answer comes back", cache.get("item", "1", 60)["ok"] is True)
check("an expired one does not", cache.get("item", "1", -1) is None)
check("and neither does one nobody stored", cache.get("item", "nope", 60) is None)
cache.forget("item", "1")
check("forgetting one entry removes it", cache.get("item", "1", 60) is None)
cache.close()


# ---- Throttling ------------------------------------------------------------

# Four waits at 0.05 s is three gaps, so the ideal total is exactly 0.150 s —
# and asserting ">= 0.150" against a target of 0.150 is a knife edge. Windows
# sleeps to a coarse timer and can come back a fraction early, which fails a
# run that throttled perfectly well. The claim worth making is that the
# requests were spaced at all: unthrottled, four of them take about nothing.
throttle = sa._Throttle(0.05)
start = time.monotonic()
for _ in range(4):
    throttle.wait()
spent = time.monotonic() - start
check("requests to one host are spaced apart", spent >= 0.12)

throttle = sa._Throttle(0.0)
throttle.penalise(0.2)
start = time.monotonic()
throttle.wait()
check("a 429 holds back every thread, not only the one that was refused",
      time.monotonic() - start >= 0.15)


# ---- The secret store ------------------------------------------------------

vault = TMP / "secrets.json"
# A made-up value of the right shape. Never put a live key here: this file
# is in the repository and a key in it is a published key.
secrets.put("probe", "0123456789ABCDEF0123456789ABCDEF", path=vault)
check("a secret survives the round trip",
      secrets.get("probe", path=vault) == "0123456789ABCDEF0123456789ABCDEF")
check("it is not written down in the clear",
      "0123456789ABCDEF0123456789ABCDEF" not in vault.read_text(encoding="utf-8"))
check("and it is shown as a stub, never in full",
      secrets.masked("probe", path=vault) == "0123…CDEF")
secrets.put("probe", "", path=vault)
check("clearing a secret removes it", not secrets.has("probe", path=vault))


# ---- Live, opt-in ----------------------------------------------------------

if "--live" in sys.argv:
    print("\n-- live (needs the stored Steam Web API key) --")
    key = secrets.get(secrets.STEAM_API_KEY)
    if not key:
        check("a key is stored", False)
    else:
        live = sa.SteamClient(api_key=key, cache_path=None)
        check("Steam accepts the key", live.check_key())

        # The author whose workshop is invisible to a signed-out reader: the
        # community listing shows nothing at all, the API shows all of it.
        hidden = live.author_items("76561198000000012")
        check("an author the listing hides entirely is read in full",
              hidden.complete and len(hidden.items) > 100)
        check("every item has the date and preview a gallery needs",
              all(i.created and i.preview for i in hidden.items))

        stamps = [i.updated for i in
                  live._author_items_api("76561198000000011", None, 3, None).items]
        check("GetUserFiles is ordered by time_updated, which the early stop rests on",
              all(a >= b for a, b in zip(stamps, stamps[1:])))

        profiles = live.profiles(["76561198000000013", "76561198000000011", "vanityone"])
        check("authors come back named", all(p.name for p in profiles.values()))
        check("a vanity name resolves to the same person as its number",
              profiles["vanityone"].id64 == "76561198000000013")
        live.close()

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
