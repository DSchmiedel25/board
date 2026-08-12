"""
dirtcall.py — turn DirtCall's two data files into the five fields the flag
screen needs.

  events.json   season schedule + track metadata
  status.json   per-event flag, rain probability, trend

Events are keyed "YYYY-MM-DD|TRACK", e.g. "2026-08-15|FON".
"""

import datetime as dt

# words that mean the night is off, checked against status.flag and .official
OFF_WORDS = ("rain", "postpone", "cancel", "off", "washed")
ON_WORDS = ("green", "go", "racing", "on", "run")

RACE_TYPES = ("race", "practice")


def _hhmm(s):
    h, m = s.split(":")
    return int(h), int(m)


def _at(date_str, time_str):
    y, mo, d = (int(x) for x in date_str.split("-"))
    h, mi = _hhmm(time_str)
    return dt.datetime(y, mo, d, h, mi).astimezone()


def _countdown(target, now):
    """Short string sized for a 64px panel. Anything over 10 hours drops the
    minutes — "16:00" reads as a clock time, "16H" doesn't."""
    secs = int((target - now).total_seconds())
    if secs < 0:
        return "NOW"
    days, rem = divmod(secs, 86400)
    hrs, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"{days}D {hrs}H"
    if hrs >= 10:
        return f"{hrs}H"
    if hrs:
        return f"{hrs}:{mins:02d}"
    return f"{mins}M"


def _title(raw, limit=16):
    """Event titles are long and full of separators. Take the headline part
    and cut on a word boundary — mid-word truncation looks broken."""
    t = str(raw or "")
    for sep in ("\u2014", " - ", " + ", "/", ":"):
        t = t.split(sep)[0]
    t = t.strip().upper()
    if t.startswith("THE "):
        t = t[4:]
    if len(t) <= limit:
        return t
    cut = t[:limit + 1]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,-+")


def _state_from_status(st):
    """status.json's flag/official are free text and often null. Read them
    loosely rather than assuming a fixed vocabulary."""
    for field in ("flag", "official"):
        val = (st or {}).get(field)
        if not val:
            continue
        low = str(val).lower()
        if any(w in low for w in OFF_WORDS):
            return "rained"
        if any(w in low for w in ON_WORDS):
            return "racing"
        return "watch"          # something was said, but not something we know
    return None


def build(events_doc, status_doc, now=None):
    now = now or dt.datetime.now().astimezone()
    today = now.strftime("%Y-%m-%d")

    tracks = events_doc.get("tracks", {})
    statuses = (status_doc or {}).get("events", {})

    upcoming = sorted(
        (e for e in events_doc.get("events", [])
         if e.get("type") in RACE_TYPES and e.get("date", "") >= today),
        key=lambda e: (e["date"], e.get("times", {}).get("race", "23:59")),
    )

    if not upcoming:
        return {"state": "standby", "track": "SEASON OVER", "town": "",
                "countdown": "", "label": "SEE YOU IN SPRING"}

    # --- anything today? take the one that hasn't finished yet
    todays = [e for e in upcoming if e["date"] == today]
    for ev in todays:
        code = ev["track"]
        tk = tracks.get(code, {})
        times = ev.get("times") or tk.get("defaults", {})
        st = statuses.get(f"{today}|{code}", {})

        state = _state_from_status(st)
        prob = st.get("prob")

        if state is None:
            # nothing official yet — let the rain probability speak
            state = "watch" if (prob is not None and prob >= 40) else "racing"

        # count down to the next gate that hasn't passed
        target, label = None, ""
        for key, text in (("gates", "TO GATES"),
                          ("hotlaps", "HOT LAPS"),
                          ("race", "TO GREEN")):
            if key in times:
                when = _at(today, times[key])
                if when > now:
                    target, label = when, text
                    break

        if state == "rained":
            why = st.get("why") or "CALLED OFF"
            return {"state": "rained", "track": tk.get("short", code),
                    "town": _title(why),
                    "countdown": times.get("race", ""), "label": "SCHEDULED"}

        if target is None:
            continue                        # night's over, fall through to next

        return {
            "state": state,
            "track": tk.get("short", code),
            "town": _title(ev.get("title")),
            "countdown": _countdown(target, now),
            "label": f"{prob}% RAIN" if state == "watch" and prob is not None else label,
        }

    # --- nothing left today: count down to the next one
    ev = upcoming[0] if upcoming[0]["date"] != today else (
        upcoming[1] if len(upcoming) > 1 else upcoming[0])
    code = ev["track"]
    tk = tracks.get(code, {})
    times = ev.get("times") or tk.get("defaults", {})
    target = _at(ev["date"], times.get("gates") or times.get("race") or "18:00")
    st = statuses.get(f"{ev['date']}|{code}", {})
    prob = st.get("prob")

    return {
        "state": "standby",
        "track": tk.get("short", code),
        "town": _title(ev.get("title")),
        "countdown": _countdown(target, now),
        "label": f"{prob}% RAIN" if prob is not None else "TO GATES",
    }

TRACK_ORDER = ["AS", "LV", "FON"]
TRACK_CODE = {"AS": "AS", "LV": "LV", "FON": "FON"}


def track_rows(events_doc, status_doc, now=None):
    """One row per track: what's next there, and how it looks.

    Tracks with nothing tonight still get a row — knowing Fonda is dark
    until Saturday is as useful as knowing Albany is green.
    """
    now = now or dt.datetime.now().astimezone()
    today = now.strftime("%Y-%m-%d")
    tracks = events_doc.get("tracks", {})
    statuses = (status_doc or {}).get("events", {})
    races = sorted((e for e in events_doc.get("events", [])
                    if e.get("type") in RACE_TYPES and e.get("date", "") >= today),
                   key=lambda e: e["date"])

    rows = []
    codes = [c for c in TRACK_ORDER if c in tracks] + \
            [c for c in tracks if c not in TRACK_ORDER]

    for code in codes:
        nxt = next((e for e in races if e["track"] == code), None)
        if not nxt:
            rows.append({"code": TRACK_CODE.get(code, code), "when": "\u2014",
                         "state": "dark", "prob": None})
            continue

        st = statuses.get(f"{nxt['date']}|{code}", {})
        prob = st.get("prob")
        y, mo, dd = (int(x) for x in nxt["date"].split("-"))
        when = dt.date(y, mo, dd)

        if nxt["date"] == today:
            state = _state_from_status(st) or (
                "watch" if (prob is not None and prob >= 40) else "racing")
            label = "NOW"
        else:
            state = "dark"
            delta = (when - now.date()).days
            label = when.strftime("%a").upper() if delta <= 6 else when.strftime("%-m/%-d")

        rows.append({"code": TRACK_CODE.get(code, code), "when": label,
                     "state": state, "prob": prob})
    return rows
