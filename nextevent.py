#!/usr/bin/env python3
"""
nextevent.py — turn a published .ics into the small JSON board.py reads.

Recurring events are the reason this isn't a regex: a weekly crew huddle
appears once in the file as an RRULE, not as 52 entries. recurring-ical-events
expands them properly.

Run it from cron. Set ICS_URL in config.py.
"""

import datetime as dt
import json
import os

import icalendar
import recurring_ical_events
import requests

from config import ICS_URL, CALENDAR_SKIP, DATA_DIR

OUT = f"{DATA_DIR}/next.json"


def main():
    cal = icalendar.Calendar.from_ical(requests.get(ICS_URL, timeout=20).content)

    now = dt.datetime.now().astimezone()
    end_of_day = now.replace(hour=23, minute=59, second=59)

    events = recurring_ical_events.of(cal).between(now, end_of_day)

    upcoming = []
    for e in events:
        start = e["DTSTART"].dt
        if isinstance(start, dt.date) and not isinstance(start, dt.datetime):
            continue                                   # all-day: no useful time
        if start.tzinfo is None:
            start = start.astimezone()
        if start < now:
            continue
        title = str(e.get("SUMMARY", ""))
        if any(w in title.lower() for w in CALENDAR_SKIP):
            continue
        upcoming.append((start, title, str(e.get("LOCATION", ""))))

    upcoming.sort(key=lambda x: x[0])

    if not upcoming:
        out = {"title": "NOTHING LEFT", "where": "REST OF TODAY",
               "time": "", "minutes": None, "more": 0}
    else:
        start, title, where = upcoming[0]
        out = {
            "title": title[:16],
            "where": (where or "").split(",")[0][:16],
            "time": start.strftime("%-I:%M"),
            "minutes": int((start - now).total_seconds() // 60),
            "more": len(upcoming) - 1,
        }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f)
    print(out)


if __name__ == "__main__":
    main()
