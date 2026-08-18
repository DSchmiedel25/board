"""
kuma.py — service health from a local Uptime Kuma status page.

Uptime Kuma's dashboard needs a login and talks over Socket.IO, neither of
which suits a display loop. A *published status page* however exposes plain
public JSON with no key. There are exactly two such routes:

    /api/status-page/{slug}              config + publicGroupList (the monitors)
    /api/status-page/heartbeat/{slug}    heartbeatList + uptimeList

An earlier version of this file also tried /api/status-page/{slug}/summary.
That route does not exist in Uptime Kuma — it 404'd on every poll and pinned
the control panel's "services" light red even when everything was working.

Set up in Uptime Kuma: Status Pages -> New, slug "board", add your monitors
to a group, tick Published, Save. A status page with no monitors on it reads
here as empty, which is why the board can reach Kuma and still show nothing.
"""

try:
    from config import KUMA_URL as BASE, KUMA_SLUG as SLUG
except ImportError:                    # standalone use, e.g. tests
    BASE, SLUG = "http://localhost:3001", "board"

if not BASE:
    BASE = "http://localhost:3001"
if not SLUG:
    SLUG = "board"


def urls(base=None, slug=None):
    base = (base or BASE).rstrip("/")
    slug = slug or SLUG
    return {
        "config": "%s/api/status-page/%s" % (base, slug),
        "beat": "%s/api/status-page/heartbeat/%s" % (base, slug),
    }


def _short(name):
    """Trim the noise so a name fits a 64px panel."""
    n = str(name or "").strip()
    for junk in (" Server", " Service", " (local)", " Pi"):
        if n.endswith(junk):
            n = n[: -len(junk)]
    return n.upper()


def _monitors(doc):
    """Flatten publicGroupList into a flat monitor list."""
    out = []
    for group in (doc or {}).get("publicGroupList") or []:
        for m in group.get("monitorList") or []:
            if m.get("id") is not None:
                out.append(m)
    return out


def build(config=None, beat=None, summary=None):
    """Normalise whichever endpoints answered into one shape.

    Never returns None. An unusable result comes back as {"offline": True}
    with a "reason" the screen can print, because "can't reach Kuma" and
    "reached Kuma, status page is empty" need different fixes and looking at
    a blank panel shouldn't leave you guessing which one you have.

    summary= is accepted and ignored so an older board.py still imports.
    """
    doc = config or summary
    if doc is None:
        return {"offline": True, "reason": "NO ANSWER"}

    mons = _monitors(doc)
    if not mons:
        return {"offline": True, "reason": "NO MONITORS"}

    uptime = ((beat or {}).get("uptimeList") or {})
    beats = ((beat or {}).get("heartbeatList") or {})

    rows, down = [], []
    for m in mons:
        mid = m.get("id")
        status = m.get("status")            # some builds inline it

        if status is None:                  # heartbeat gives 1 up / 0 down
            hist = beats.get(str(mid)) or []
            status = "up" if (hist and hist[-1].get("status") == 1) else (
                "down" if hist else "unknown")

        up = status == "up"
        pct = uptime.get("%s_24" % mid)
        row = {"name": _short(m.get("name")), "up": up,
               "unknown": status == "unknown",
               "uptime": round(pct * 100, 2) if isinstance(pct, (int, float)) else None}
        rows.append(row)
        if not up and status != "unknown":
            down.append(row["name"])

    rows.sort(key=lambda r: (r["up"], r["name"]))   # trouble first
    return {
        "rows": rows,
        "total": len(rows),
        "up": sum(1 for r in rows if r["up"]),
        "down": down,
        "ok": not down,
    }
