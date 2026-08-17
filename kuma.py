"""
kuma.py — service health from a local Uptime Kuma status page.

Uptime Kuma's dashboard needs a login and talks over Socket.IO, neither of
which suits a display loop. A *published status page* however exposes plain
public JSON with no key, which is exactly what we want:

    /api/status-page/{slug}/summary      names + up/down, one call
    /api/status-page/heartbeat/{slug}    latest heartbeat + 24h uptime

The summary endpoint is preferred because it is a single request. Uptime Kuma
gained it fairly late, so if it 404s this falls back to the older pair. Both
are documented as public for published pages.

Set up in Uptime Kuma: Status Pages -> New, slug "board", add your monitors,
tick Published. Nothing else.
"""

BASE = "http://localhost:3001"
SLUG = "board"


def urls(base=BASE, slug=SLUG):
    return {
        "summary": "%s/api/status-page/%s/summary" % (base, slug),
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
    """Flatten publicGroupList, which both the summary and config share."""
    out = []
    for group in (doc or {}).get("publicGroupList") or []:
        for m in group.get("monitorList") or []:
            if m.get("id") is not None:
                out.append(m)
    return out


def build(summary=None, config=None, beat=None):
    """Normalise whichever endpoints answered into one shape.

    Returns None when nothing usable came back, so the caller can show an
    explicit offline state rather than an empty list that reads as "all fine".
    """
    mons = _monitors(summary) or _monitors(config)
    if not mons:
        return None

    uptime = ((beat or {}).get("uptimeList") or {})
    beats = ((beat or {}).get("heartbeatList") or {})

    rows, down = [], []
    for m in mons:
        mid = m.get("id")
        status = m.get("status")            # summary gives "up"/"down"

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
