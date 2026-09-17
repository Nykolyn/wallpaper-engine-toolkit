"""Resolving `mongodb+srv://` through Windows' own resolver instead of dnspython.

A `+srv` connection string is not a host — it is an instruction to look up an
SRV record for the seed list and a TXT record for the default options. pymongo
does that with **dnspython**, which reads the configured nameservers and talks
to them itself over UDP.

That is where a machine behind a VPN falls over. A VPN installs nameservers of
its own, and they commonly answer the Windows DNS Client but not a direct query
from a process: every dnspython lookup runs its full 20-second lifetime and
dies, while `Resolve-DnsName` and `socket.getaddrinfo` answer the same names in
a tenth of a second. The database is reachable the whole time — only the name
lookup in front of it is not.

So the lookup is done here, through `DnsQuery_W` in `dnsapi.dll` (the library
behind the `windns.h` header), which is the resolver Windows itself uses. The
seed list it returns is folded into an ordinary `mongodb://` URI, and pymongo
then resolves those host names with
`getaddrinfo` — the OS path again, and the one every other part of this app
already depends on.

Everything else about the URI is preserved: the credentials verbatim (they are
already percent-encoded and must not be touched), the database, and any option
written by hand, which wins over the same option coming from TXT.
"""
from __future__ import annotations

import ctypes
import socket
from ctypes import wintypes

DNS_TYPE_SRV = 33
DNS_TYPE_TEXT = 16
_DNS_QUERY_STANDARD = 0
_DNS_FREE_RECORD_LIST = 1

# The SRV name a MongoDB seed list lives under.
_SRV_PREFIX = "_mongodb._tcp."
# Options a TXT record is allowed to set, per the connection-string spec.
_TXT_ALLOWED = {"authsource", "replicaset", "loadbalanced"}


class SrvError(RuntimeError):
    """The seed list could not be looked up."""


# ---- The Windows resolver --------------------------------------------------

class _Header(ctypes.Structure):
    """The part every DNS_RECORD starts with, whatever type it carries."""
    _fields_ = [
        ("pNext", ctypes.c_void_p),
        ("pName", wintypes.LPWSTR),
        ("wType", wintypes.WORD),
        ("wDataLength", wintypes.WORD),
        ("Flags", wintypes.DWORD),
        ("dwTtl", wintypes.DWORD),
        ("dwReserved", wintypes.DWORD),
    ]


class _SrvRecord(ctypes.Structure):
    _fields_ = _Header._fields_ + [
        ("pNameTarget", wintypes.LPWSTR),
        ("wPriority", wintypes.WORD),
        ("wWeight", wintypes.WORD),
        ("wPort", wintypes.WORD),
        ("Pad", wintypes.WORD),
    ]


class _TxtRecord(ctypes.Structure):
    # The real struct ends in `PWSTR pStringArray[1]` — an inline array, not a
    # pointer, so the strings start at this offset and run for dwStringCount.
    _fields_ = _Header._fields_ + [
        ("dwStringCount", wintypes.DWORD),
        ("pStringArray", wintypes.LPWSTR * 1),
    ]


def _windns():
    # windns.h is the header; the library it declares is dnsapi.dll.
    dll = ctypes.WinDLL("dnsapi.dll")
    dll.DnsQuery_W.argtypes = [wintypes.LPCWSTR, wintypes.WORD, wintypes.DWORD,
                               ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                               ctypes.c_void_p]
    dll.DnsQuery_W.restype = ctypes.c_long
    dll.DnsRecordListFree.argtypes = [ctypes.c_void_p, ctypes.c_int]
    dll.DnsRecordListFree.restype = None
    return dll


def _query(name: str, record_type: int) -> list:
    """Every record of `record_type` for `name`, as Windows itself resolves it."""
    try:
        dll = _windns()
    except (OSError, AttributeError) as err:       # not Windows, or no dnsapi
        raise SrvError(f"the Windows resolver is not available: {err}") from err

    results = ctypes.c_void_p()
    status = dll.DnsQuery_W(name, record_type, _DNS_QUERY_STANDARD, None,
                            ctypes.byref(results), None)
    if status != 0:
        raise SrvError(f"looking up {name} failed (Windows DNS status {status})")
    if not results:
        raise SrvError(f"no answer for {name}")

    out: list = []
    try:
        node = results.value
        while node:
            header = _Header.from_address(node)
            if header.wType == record_type == DNS_TYPE_SRV:
                srv = _SrvRecord.from_address(node)
                out.append((srv.pNameTarget, int(srv.wPort),
                            int(srv.wPriority), int(srv.wWeight)))
            elif header.wType == record_type == DNS_TYPE_TEXT:
                txt = _TxtRecord.from_address(node)
                count = int(txt.dwStringCount)
                strings = (wintypes.LPWSTR * count).from_address(
                    ctypes.addressof(txt) + _TxtRecord.pStringArray.offset)
                out.append("".join(s or "" for s in strings))
            node = header.pNext
    finally:
        dll.DnsRecordListFree(results, _DNS_FREE_RECORD_LIST)
    return out


def resolve_srv(host: str) -> list[tuple[str, int]]:
    """The `(host, port)` seed list for a MongoDB SRV host name."""
    records = _query(_SRV_PREFIX + host, DNS_TYPE_SRV)
    parent = ".".join(host.split(".")[1:])
    seeds: list[tuple[str, int]] = []
    for target, port, _priority, _weight in sorted(records, key=lambda r: (r[2], r[0])):
        target = (target or "").rstrip(".")
        if not target:
            continue
        # The spec insists on this: a seed has to sit in the same parent domain
        # as the name asked for, so a forged answer cannot point somewhere else.
        if not target.lower().endswith("." + parent.lower()):
            raise SrvError(f"{target} is outside {parent}, refusing to use it")
        seeds.append((target, port))
    if not seeds:
        raise SrvError(f"no seed list under {_SRV_PREFIX}{host}")
    return seeds


def resolve_txt_options(host: str) -> dict[str, str]:
    """The default options a MongoDB SRV host publishes in TXT, if any."""
    try:
        records = _query(host, DNS_TYPE_TEXT)
    except SrvError:
        return {}                     # TXT is optional; SRV alone is enough
    options: dict[str, str] = {}
    for text in records:
        for pair in str(text).split("&"):
            key, _, value = pair.partition("=")
            key = key.strip().lower()
            if key in _TXT_ALLOWED and value:
                options[key] = value.strip()
    return options


# ---- The URI ---------------------------------------------------------------

def is_srv(uri: str) -> bool:
    return uri.strip().lower().startswith("mongodb+srv://")


def split_uri(uri: str) -> tuple[str, str, str, str]:
    """(credentials, host, database, query) — credentials kept exactly as given."""
    tail = uri.split("://", 1)[1]
    authority, _, rest = tail.partition("/")
    credentials, _, host = authority.rpartition("@")
    database, _, query = rest.partition("?")
    return credentials, host, database, query


def expand(uri: str, seeds=None, txt=None) -> str:
    """Rewrite a `mongodb+srv://` URI as a plain one with the seed list in it.

    `seeds` and `txt` are for the tests; left out, they are looked up through
    Windows. Options already written into the URI win over the TXT record, and
    TLS is turned on explicitly because `+srv` implies it and `mongodb://` does
    not.
    """
    if not is_srv(uri):
        return uri
    credentials, host, database, query = split_uri(uri)
    if host.count(".") < 2:
        raise SrvError(f"{host} is not a valid SRV host name")
    if ":" in host:
        raise SrvError("an SRV host name cannot carry a port")

    seeds = resolve_srv(host) if seeds is None else seeds
    txt = resolve_txt_options(host) if txt is None else txt

    written = {p.split("=", 1)[0].strip().lower()
               for p in query.split("&") if p.strip()}
    options = [p for p in query.split("&") if p.strip()]
    for key, value in txt.items():
        if key not in written:
            options.append(f"{key}={value}")
    if not {"tls", "ssl"} & written:
        options.append("tls=true")

    authority = ",".join(f"{h}:{p}" for h, p in seeds)
    if credentials:
        authority = f"{credentials}@{authority}"
    out = f"mongodb://{authority}/{database}"
    return out + ("?" + "&".join(options) if options else "")


def usable() -> bool:
    """Whether this platform can be asked for an SRV record at all."""
    if not hasattr(ctypes, "WinDLL"):
        return False
    try:
        _windns()
        return True
    except (OSError, AttributeError):
        return False


def host_reachable(host: str, port: int, timeout: float = 5.0) -> bool:
    """Whether a seed answers at all — used only to explain a failure."""
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False
