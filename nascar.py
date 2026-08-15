"""
nascar.py — next race for Cup, Xfinity and Truck, with where to watch.

One request per series against ESPN's public scoreboard. Each response
carries the whole season calendar (leagues[0].calendar) plus the most recent
event (events[0]).

On broadcast: calendar entries carry no network field — only the current or
most recent event does. So the network shown is the live value during a race
weekend, and the NETWORKS fallback the rest of the time. Edit those when the
TV deal changes.

The endpoints are undocumented. Everything fails soft: a series that errors
or changes shape drops off the screen instead of taking the board down.
"""

import datetime as dt

BASE = "https://site.api.espn.com/apis/site/v2/sports/racing"

# label, ESPN slug, fallback network
SERIES = [
    ("CUP", "nascar-premier", "USA"),
    ("XFN", "nascar-secondary", "CW"),
    ("TRK", "nascar-truck", "FS1"),
]


# Track names that won't fit a 64px bar even at the smallest type. The
# right-hand side is what a race fan would actually call the place.
ABBREV = {
    "WORLD WIDE TECHNOLOGY RACEWAY": "GATEWAY",
    "CIRCUIT OF THE AMERICAS": "COTA",
    "INDIANAPOLIS MOTOR SPEEDWAY": "INDY",
    "INDIANAPOLIS": "INDY",
    "NORTH WILKESBORO": "WILKESBORO",
    "NEW HAMPSHIRE MOTOR SPEEDWAY": "NEW HAMPSHIRE",
    "HOMESTEAD-MIAMI": "MIAMI",
    "LAS VEGAS MOTOR SPEEDWAY": "LAS VEGAS",
    "CHARLOTTE MOTOR SPEEDWAY ROVAL": "ROVAL",
}


def short(name):
    n = str(name or "").strip().upper()
    return ABBREV.get(n, n)


# ESPN's calendar publishes the END of each race slot, not the green flag.
# Measured against races where both numbers are known:
#
#   Iowa          calendar 22:30Z   actual 19:30Z
#   Daytona 500   calendar 21:30Z   actual 18:30Z
#   Coca-Cola 600 calendar 01:00Z   actual 22:00Z
#   Clash         calendar 02:00Z   actual 23:00Z
#   Duel #1       calendar 03:00Z   actual 00:00Z
#
# Exactly three hours every time. events[0] carries the true start but only
# for the most recent race, so future races have to come off the calendar
# with the offset applied. If start times ever drift by three hours, this
# constant is the first place to look.
SLOT_HOURS = 3


def url(slug):
    return f"{BASE}/{slug}/scoreboard"


def _iso(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _network(doc):
    """Present only on the current/most recent event, so treat it as a bonus."""
    ev = (doc.get("events") or [{}])[0]
    comp = (ev.get("competitions") or [{}])[0]
    for b in comp.get("broadcasts") or []:
        names = b.get("names") or []
        if names:
            return str(names[0]).upper().replace(" NET", "")
    raw = str(comp.get("broadcast") or "")
    return raw.split("/")[0].upper().replace(" NET", "") if raw else ""


def _is_live(doc):
    ev = (doc.get("events") or [{}])[0]
    return ((ev.get("status") or {}).get("type") or {}).get("state") == "in"


def one(doc, label, fallback, now):
    """A single series row: when its next race is, and where it's on."""
    cal = ((doc or {}).get("leagues") or [{}])[0].get("calendar") or []
    nxt = None
    for c in cal:
        t = _iso(c.get("startDate"))
        if not t:
            continue
        t -= dt.timedelta(hours=SLOT_HOURS)      # slot end -> green flag
        if t > now:
            nxt = (c, t)
            break

    net = _network(doc) or fallback

    if _is_live(doc):
        return {"label": label, "when": "RACING", "net": net,
                "live": True, "at": None, "venue": ""}

    if not nxt:
        return {"label": label, "when": "--", "net": "",
                "live": False, "at": None, "venue": ""}

    local = nxt[1].astimezone()
    hour = local.hour % 12 or 12
    venue = str(nxt[0].get("label") or "")
    if " at " in venue:
        venue = venue.split(" at ")[-1]

    return {
        "label": label,
        "when": f"{local.strftime('%a').upper()} {hour}:{local.minute:02d}",
        "net": net,
        "live": False,
        "at": local,
        "venue": venue.strip().upper(),
    }


def podium(doc):
    """Top three from the most recent finished Cup race."""
    ev = (doc.get("events") or [{}])[0]
    comp = (ev.get("competitions") or [{}])[0]
    state = ((ev.get("status") or {}).get("type") or {}).get("state", "")
    if state != "post":
        return None

    finishers = sorted(
        [c for c in comp.get("competitors") or [] if c.get("order")],
        key=lambda c: c["order"])[:3]
    if len(finishers) < 3:
        return None

    where = str(ev.get("name") or "")
    if " at " in where:
        where = where.split(" at ")[-1]

    return {
        "venue": short(where),
        "top": [(str(c["order"]),
                 str((c.get("athlete") or {}).get("shortName")
                     or (c.get("athlete") or {}).get("fullName") or "")
                 .split(". ")[-1].upper())
                for c in finishers],
    }


def build(docs, now=None):
    """docs: {slug: parsed json}. Missing or broken slugs are skipped."""
    now = now or dt.datetime.now().astimezone()
    rows = []
    for label, slug, fallback in SERIES:
        doc = (docs or {}).get(slug)
        if doc:
            try:
                rows.append(one(doc, label, fallback, now))
            except Exception:
                pass

    # header venue comes from whichever series races soonest
    dated = [r for r in rows if r.get("at")]
    venue = min(dated, key=lambda r: r["at"])["venue"] if dated else ""

    # "at" is a datetime used only for that comparison. It must not survive
    # into the return value — the caller serialises this to JSON for the wall
    # page, and a datetime there takes the whole board down.
    for r in rows:
        r.pop("at", None)

    cup = (docs or {}).get("nascar-premier")
    return {"rows": rows, "venue": short(venue),
            "live": any(r.get("live") for r in rows),
            "podium": podium(cup) if cup else None}
