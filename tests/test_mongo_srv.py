"""Resolving a `mongodb+srv://` seed list through Windows instead of dnspython.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_mongo_srv.py
    .venv\\Scripts\\python.exe tests\\test_mongo_srv.py --live

The default run asks no nameserver anything: the lookup is replaced with fixed
answers, so what is checked here is the rewriting — which is where a mistake
would be silent and expensive, because a wrong option means connecting to the
wrong thing rather than failing to connect. ``--live`` additionally resolves a
real cluster named by ``WET_TEST_CLUSTER``, and is the only part that needs
a network.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engines import mongo_srv as ms     # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


# example.net is reserved for documentation (RFC 2606). A fabricated Atlas
# host with a password on it is indistinguishable from a real one to a
# secret scanner, and a repository full of false alarms is a repository
# whose alarms stop being read. Nothing here depends on the domain: the
# module never mentions mongodb.net.
SEEDS = [("a.cluster.example.net", 27017), ("b.cluster.example.net", 27017)]
TXT = {"authsource": "admin", "replicaset": "shard-0"}
URI = "mongodb+srv://bob:p%40ss%2F@cluster.example.net/wallpapers"


# ---- Telling the two kinds of connection string apart -----------------------

check("an srv string is recognised", ms.is_srv(URI))
check("whatever the case", ms.is_srv("MongoDB+SRV://h.x.net/"))
check("a plain one is not", not ms.is_srv("mongodb://host:27017/db"))
check("and a plain one is handed back untouched",
      ms.expand("mongodb://host:27017/db") == "mongodb://host:27017/db")


# ---- Taking the string apart ------------------------------------------------

creds, host, database, query = ms.split_uri(URI + "?retryWrites=true")
check("the credentials are kept exactly as written, never re-encoded",
      creds == "bob:p%40ss%2F")
check("the host, database and options are separated",
      (host, database, query) == ("cluster.example.net", "wallpapers", "retryWrites=true"))
check("a string with no credentials is fine",
      ms.split_uri("mongodb+srv://cluster.example.net/")[0] == "")
check("and one with no database or options",
      ms.split_uri("mongodb+srv://cluster.example.net")[1:] == ("cluster.example.net", "", ""))


# ---- Rewriting it -----------------------------------------------------------

out = ms.expand(URI, seeds=SEEDS, txt=TXT)
check("the seed list becomes the host part",
      out.startswith("mongodb://bob:p%40ss%2F@a.cluster.example.net:27017,b.cluster.example.net:27017/"))
check("the database survives", "/wallpapers?" in out)
check("TXT options are carried over",
      "authsource=admin" in out and "replicaset=shard-0" in out)
check("and TLS is stated, because +srv implies it and mongodb:// does not",
      "tls=true" in out)

# What the user wrote must win: TXT is a *default*, not an override.
out = ms.expand(URI + "?replicaSet=mine", seeds=SEEDS, txt=TXT)
check("an option written by hand beats the same option from TXT",
      "replicaSet=mine" in out and "replicaset=shard-0" not in out)
check("while the others still come through", "authsource=admin" in out)

out = ms.expand(URI + "?tls=false", seeds=SEEDS, txt=TXT)
check("and TLS is not forced on over an explicit choice",
      "tls=false" in out and "tls=true" not in out)
check("nor when it was spelled the old way",
      "tls=true" not in ms.expand(URI + "?ssl=false", seeds=SEEDS, txt=TXT))

out = ms.expand("mongodb+srv://cluster.example.net/", seeds=SEEDS, txt={})
check("no credentials means no stray @ in the result", "@" not in out)


# ---- Refusing what cannot be right -----------------------------------------

for bad, why in (("mongodb+srv://x.net/", "a host with too few labels"),
                 ("mongodb+srv://cluster.example.net:27017/", "a host carrying a port")):
    try:
        ms.expand(bad, seeds=SEEDS, txt=TXT)
        refused = False
    except ms.SrvError:
        refused = True
    check(f"{why} is refused", refused)

# A seed outside the parent domain would be a redirect to somewhere else, which
# is the one thing the SRV rule in the connection-string spec exists to stop.
real_query = ms._query
try:
    ms._query = lambda name, kind: [("evil.example.com", 27017, 0, 0)]
    try:
        ms.resolve_srv("cluster.example.net")
        refused = False
    except ms.SrvError:
        refused = True
    check("a seed outside the parent domain is refused", refused)

    ms._query = lambda name, kind: [("b.cluster.example.net.", 27017, 10, 0),
                                    ("a.cluster.example.net.", 27017, 1, 0)]
    seeds = ms.resolve_srv("cluster.example.net")
    check("the trailing dot is trimmed and priority decides the order",
          seeds == [("a.cluster.example.net", 27017), ("b.cluster.example.net", 27017)])

    ms._query = lambda name, kind: []
    try:
        ms.resolve_srv("cluster.example.net")
        complained = False
    except ms.SrvError:
        complained = True
    check("an empty answer is an error, not an empty seed list", complained)

    # TXT is optional: a cluster without one still has to connect.
    def no_txt(name, kind):
        raise ms.SrvError("no answer")

    ms._query = no_txt
    check("a missing TXT record is not fatal", ms.resolve_txt_options("x.y.net") == {})

    ms._query = lambda name, kind: ["authSource=admin&javascriptEnabled=true"]
    check("and TXT can only set the options it is allowed to",
          ms.resolve_txt_options("x.y.net") == {"authsource": "admin"})
finally:
    ms._query = real_query


# ---- Saying what went wrong -------------------------------------------------

from app.engines.authors_db import explain          # noqa: E402

# Addresses from TEST-NET-1 (RFC 5737), which exists to be written down.
timeout = ("The resolution lifetime expired after 20.001 seconds: "
           "Server Do53:192.0.2.1@53 answered The DNS operation timed out.; "
           "Server Do53:192.0.2.53@53 answered The DNS operation timed out.; "
           "Server Do53:192.0.2.1@53 answered The DNS operation timed out.")
said = explain(RuntimeError(timeout))
check("a DNS timeout is explained rather than dumped",
      "name lookup timed out" in said and len(said) < len(timeout))
check("and it names each server once, not once per attempt",
      said.count("192.0.2.1") == 1 and "192.0.2.53" in said)
check("a refused password says so",
      "password was refused" in explain(RuntimeError("Authentication failed.")))
check("anything else is passed through, trimmed",
      explain(RuntimeError("something else entirely")) == "something else entirely")


# ---- Live, opt-in -----------------------------------------------------------

if "--live" in sys.argv:
    # Which cluster to ask is yours, not this file's. Give it one:
    #
    #     set WET_TEST_CLUSTER=mycluster.ab12c.mongodb.net
    #     .venv\\Scripts\\python.exe tests\\test_mongo_srv.py --live
    #
    cluster = os.environ.get("WET_TEST_CLUSTER", "").strip()
    if not cluster:
        print("\n-- live: skipped, set WET_TEST_CLUSTER to a cluster host --")
    else:
        domain = cluster.split(".", 1)[1] if "." in cluster else cluster
        print("\n-- live (asks the real resolver about %s) --" % cluster)
        check("this platform can be asked for an SRV record", ms.usable())
        seeds = ms.resolve_srv(cluster)
        check("the cluster's seed list comes back", len(seeds) >= 1)
        check("every seed is in the cluster's own domain",
              all(h.endswith(domain) and p == 27017 for h, p in seeds))
        options = ms.resolve_txt_options(cluster)
        check("and the TXT record names a replica set",
              bool(options.get("replicaset")))
        check("a seed actually answers on its port",
              ms.host_reachable(seeds[0][0], seeds[0][1]))

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
