#!/usr/bin/env python3
"""
nextevent-mac.py — read the next event straight out of Calendar.app.

Use this instead of nextevent.py when the board runs on a Mac. No published
.ics, no public URL, and it sees every calendar the Mac has — including
subscribed and work accounts that can't be shared.

  pip install pyobjc-framework-EventKit

FIRST RUN MUST BE IN TERMINAL. macOS will show a Calendar access prompt, and
a launchd background agent can't display it. Run it by hand once, approve,
then launchd works from then on. If you ever deny it by mistake:
System Settings -> Privacy & Security -> Calendars.
"""

import datetime as dt
import json
import os
import sys
import threading

try:
    from EventKit import EKEventStore, EKEntityTypeEvent
    from Foundation import NSDate
except ImportError:
    sys.exit("pip install pyobjc-framework-EventKit")

from config import CALENDAR_SKIP, DATA_DIR

OUT = f"{DATA_DIR}/next.json"


def request_access(store):
    """macOS 14 renamed the access API. Try the new name, fall back."""
    done, granted = threading.Event(), {"ok": False}

    def handler(ok, err):
        granted["ok"] = bool(ok)
        done.set()

    if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
        store.requestFullAccessToEventsWithCompletion_(handler)
    else:
        store.requestAccessToEntityType_completion_(EKEntityTypeEvent, handler)

    done.wait(30)
    return granted["ok"]


def main():
    store = EKEventStore.alloc().init()
    if not request_access(store):
        sys.exit("Calendar access denied. Run this in Terminal once and approve, "
                 "or check System Settings -> Privacy & Security -> Calendars.")

    now = dt.datetime.now().astimezone()
    end_of_day = now.replace(hour=23, minute=59, second=59)

    pred = store.predicateForEventsWithStartDate_endDate_calendars_(
        NSDate.dateWithTimeIntervalSince1970_(now.timestamp()),
        NSDate.dateWithTimeIntervalSince1970_(end_of_day.timestamp()),
        None,
    )

    upcoming = []
    for e in store.eventsMatchingPredicate_(pred) or []:
        if e.isAllDay():
            continue                                   # no useful time to show
        start = dt.datetime.fromtimestamp(
            e.startDate().timeIntervalSince1970()).astimezone()
        if start < now:
            continue
        title = str(e.title() or "")
        if any(w in title.lower() for w in CALENDAR_SKIP):
            continue
        upcoming.append((start, title, str(e.location() or "")))

    upcoming.sort(key=lambda x: x[0])

    # --- the rest of the week, for the wall dashboard's lower list
    week_end = now.replace(hour=23, minute=59) + dt.timedelta(days=6)
    later = []
    later_events = store.eventsMatchingPredicate_(
        store.predicateForEventsWithStartDate_endDate_calendars_(
            NSDate.dateWithTimeIntervalSince1970_(end_of_day.timestamp()),
            NSDate.dateWithTimeIntervalSince1970_(week_end.timestamp()),
            None,
        )) or []

    for e in later_events:
        start = e.startDate().timeIntervalSince1970()
        start = dt.datetime.fromtimestamp(start).astimezone()
        title = str(e.title() or "")
        if any(w in title.lower() for w in CALENDAR_SKIP):
            continue
        later.append({
            "day": start.strftime("%a").upper(),
            "time": "ALL" if e.isAllDay() else start.strftime("%-I:%M"),
            "title": title,
            "where": str(e.location() or "").split(",")[0],
            "sort": start.timestamp(),
        })
    later.sort(key=lambda x: x["sort"])
    for x in later:
        x.pop("sort")

    today_list = [{
        "time": s.strftime("%-I:%M"),
        "title": t,
        "where": w.split(",")[0],
    } for s, t, w in upcoming]

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

    # flat fields above are what the Pixoo reads; the lists are for the wall
    out["generated"] = now.isoformat()
    out["today"] = today_list
    out["later"] = later[:5]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f)
    print(f"next: {out['title']} | today: {len(today_list)} | later: {len(out['later'])}")


if __name__ == "__main__":
    main()
