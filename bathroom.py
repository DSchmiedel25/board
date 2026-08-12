"""
bathroom.py — turn BathroomReport's analytics-data.json into the numbers the
Pixoo screens show.

The file is GA4 + Clarity web analytics, not app content stats, so the screens
report traffic: yesterday's users, a sparkline of recent days, and where the
sessions came from.

  ga4.daily      {"YYYY-MM-DD": {activeUsers, newUsers, sessions, pageViews}}
  ga4.sources    [{source, medium, sessions, users}, ...]
  errors         [] when the nightly pipeline ran clean
  generated      ISO timestamp of the last bake
"""

import datetime as dt

STALE_HOURS = 36        # nightly job, so anything past this missed a run


def _parse_iso(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def build(doc, now=None):
    now = now or dt.datetime.now().astimezone()
    ga = (doc or {}).get("ga4", {})
    daily = ga.get("daily", {}) or {}

    # GA4 has gaps, so work from the dates that exist rather than the calendar
    days = sorted(daily)
    recent = days[-7:]
    series = [daily[d].get("sessions", 0) for d in recent] or [0]

    latest = daily.get(days[-1], {}) if days else {}
    prev = daily.get(days[-2], {}) if len(days) > 1 else {}

    users = latest.get("activeUsers", 0)
    sessions = latest.get("sessions", 0)
    views = latest.get("pageViews", 0)
    delta = users - prev.get("activeUsers", users)

    # --- pipeline health drives the bar
    gen = _parse_iso(doc.get("generated"))
    errors = doc.get("errors") or []
    if errors:
        health = ("bad", f"{len(errors)} ERR")
    elif gen and (now - gen).total_seconds() > STALE_HOURS * 3600:
        health = ("stale", "STALE")
    else:
        health = ("ok", "CLEAN")

    # --- top real referrer, skipping GA4's placeholder buckets
    junk = ("(direct)", "(not set)", "(none)", "test", "qr")
    real = [s for s in ga.get("sources", [])
            if s.get("source", "").lower() not in junk]
    top = max(real, key=lambda s: s.get("sessions", 0), default=None)

    return {
        "health": health,
        "day": days[-1] if days else "",
        "users": users,
        "sessions": sessions,
        "views": views,
        "delta": delta,
        "series": series,
        "series_days": recent,
        "week_sessions": sum(series),
        "top_source": (top or {}).get("source", "DIRECT")[:14],
        "top_sessions": (top or {}).get("sessions", 0),
        "signups": ga.get("events", {}).get("sign_up", 0),
    }
