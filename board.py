#!/usr/bin/env python3
"""
board.py — DirtCall + BathroomReport on a Divoom Pixoo-64.

Four screens, drawn at 64x64 with a built-in pixel font (no font file,
no anti-aliasing):

  flag      track status, or the countdown to the next green flag
  sites     BathroomReport location count and today's scans
  trend     7-day scan sparkline
  queue     FlushPanel moderation backlog

Screens rotate. On Friday and Saturday evenings the flag screen takes
most of the dwell time; the rest of the week it's mostly BathroomReport.

  pip3 install pixoo pillow
  python3 board.py --preview out/    # render PNGs, no device needed
  python3 board.py --once            # push one frame (good for cron)
  python3 board.py --loop            # rotate forever (good for systemd)
"""

import argparse
import datetime as dt
import json
import os
import sys
import time

import requests
from PIL import Image, ImageDraw

from config import (
    PIXOO_IP, LAT, LON, DIRTCALL_BASE, BATHROOM_URL, DATA_DIR,
    DAY_BRIGHTNESS, NIGHT_BRIGHTNESS, NIGHT_START, NIGHT_END,
    MORNING, RACE_DAYS, RACE_WINDOW,
)

WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}"
    "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
    "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
    "&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=auto"
)
CALENDAR_URL = f"file://{DATA_DIR}/next.json"

# palette — same values as the wall dashboard
LOAM   = (28, 21, 18)
DUST   = (232, 220, 200)
SLATE  = (139, 122, 108)
SODIUM = (242, 167, 59)
GREEN  = (63, 163, 77)
RED    = (196, 52, 43)
YELLOW = (229, 195, 74)

# flag states: bar color, bar text color, word
STATES = {
    "racing":  (GREEN,  LOAM,  "RACING"),
    "rained":  (RED,    DUST,  "RAINOUT"),
    "watch":   (YELLOW, LOAM,  "WATCH"),
    "standby": (LOAM,   SLATE, "STANDBY"),
}

DEMO_DIRT = {
    "state": "racing",
    "track": "ALBANY-SARATOGA",
    "town": "MALTA NY",
    "countdown": "2:48",
    "label": "HOT LAPS",
}

DEMO_WX = {
    "temp": 68, "feels": 66, "high": 84, "low": 61,
    "code": 1, "wind": 7, "rain": 20,
}

DEMO_CAL = {
    "title": "CREW HUDDLE",
    "where": "ERIE ST SITE",
    "time": "7:30",
    "minutes": 48,
    "more": 3,
}

DEMO_BATH = {
    "health": ("ok", "CLEAN"), "day": "2026-08-11",
    "users": 3, "sessions": 4, "views": 5, "delta": -1,
    "series": [9, 13, 12, 1, 2, 4, 4],
    "series_days": ["2026-08-05", "2026-08-06", "2026-08-07", "2026-08-08",
                    "2026-08-09", "2026-08-10", "2026-08-11"],
    "week_sessions": 45, "top_source": "facebook.com", "top_sessions": 8,
    "signups": 1,
}

# ---------------------------------------------------------------- 3x5 font

_GLYPHS = """
A ### #.# ### #.# #.#
B ##. #.# ##. #.# ##.
C ### #.. #.. #.. ###
D ##. #.# #.# #.# ##.
E ### #.. ##. #.. ###
F ### #.. ##. #.. #..
G ### #.. #.# #.# ###
H #.# #.# ### #.# #.#
I ### .#. .#. .#. ###
J ..# ..# ..# #.# ###
K #.# #.# ##. #.# #.#
L #.. #.. #.. #.. ###
M #..# #### #### #..# #..#
N #..# ##.# #.## #..# #..#
O ### #.# #.# #.# ###
P ### #.# ### #.. #..
Q ### #.# #.# ### ..#
R ### #.# ##. #.# #.#
S ### #.. ### ..# ###
T ### .#. .#. .#. .#.
U #.# #.# #.# #.# ###
V #.# #.# #.# #.# .#.
W #..# #..# #### #### #..#
X #.# #.# .#. #.# #.#
Y #.# #.# .#. .#. .#.
Z ### ..# .#. #.. ###
0 ### #.# #.# #.# ###
1 .#. ##. .#. .#. ###
2 ### ..# ### #.. ###
3 ### ..# ### ..# ###
4 #.# #.# ### ..# ..#
5 ### #.. ### ..# ###
6 ### #.. ### #.# ###
7 ### ..# ..# ..# ..#
8 ### #.# ### #.# ###
9 ### #.# ### ..# ###
- ... ... ### ... ...
. ... ... ... ... .#.
, ... ... ... .#. #..
: ... .#. ... .#. ...
/ ..# ..# .#. #.. #..
! .#. .#. .#. ... .#.
% #.# ..# .#. #.. #.#
+ ... .#. ### .#. ...
° ##. #.# ##. ... ...
"""

FONT = {}
for _line in _GLYPHS.strip().splitlines():
    _ch, *_rows = _line.split(" ")
    FONT[_ch] = _rows
FONT[" "] = ["..."] * 5

GH = 5                 # every glyph is 5 rows; width varies (M/N/W are 4)
GAP = 1


def glyph(ch):
    return FONT.get(ch, FONT[" "])


def text_width(s, scale):
    if not s:
        return 0
    return (sum(len(glyph(c)[0]) + GAP for c in s.upper()) - GAP) * scale


def text_height(scale):
    return GH * scale


def draw_text(d, s, x, y, color, scale=1):
    cx = x
    for ch in s.upper():
        rows = glyph(ch)
        for ry, row in enumerate(rows):
            for rx, cell in enumerate(row):
                if cell == "#":
                    px, py = cx + rx * scale, y + ry * scale
                    d.rectangle([px, py, px + scale - 1, py + scale - 1], fill=color)
        cx += (len(rows[0]) + GAP) * scale


def draw_centered(d, s, y, color, scale=1, width=64):
    draw_text(d, s, (width - text_width(s, scale)) // 2, y, color, scale)


def fit_scale(s, max_scale, width=64, pad=2):
    for sc in range(max_scale, 0, -1):
        if text_width(s, sc) <= width - pad * 2:
            return sc
    return 1


def commas(n):
    return f"{n:,}"


# ---------------------------------------------------------------- chrome

BAR_H = 16
LABEL_Y = 58

# bar text is drawn at one fixed scale so it never jitters between screens
BAR_SCALE = 2


def draw_bar(d, color, text, text_color, rule=False):
    d.rectangle([0, 0, 63, BAR_H], fill=color)
    if rule:
        d.line([0, BAR_H, 63, BAR_H], fill=SLATE)
    sc = min(BAR_SCALE, fit_scale(text, BAR_SCALE))
    draw_centered(d, text, (BAR_H - text_height(sc)) // 2 + 1, text_color, sc)


def queue_bar(bath):
    """The bar always answers 'is there something happening'. On project
    screens that's whether the nightly analytics bake is healthy."""
    kind, word = bath["health"]
    if kind == "bad":
        return RED, DUST, word
    if kind == "stale":
        return YELLOW, LOAM, word
    return GREEN, LOAM, word


# WMO weather codes, collapsed to words short enough for the bar
def wx_word(code):
    if code == 0:
        return "CLEAR"
    if code in (1, 2):
        return "PARTLY"
    if code == 3:
        return "CLOUDY"
    if code in (45, 48):
        return "FOG"
    if 51 <= code <= 57:
        return "DRIZZLE"
    if 61 <= code <= 67:
        return "RAIN"
    if 71 <= code <= 77 or code in (85, 86):
        return "SNOW"
    if 80 <= code <= 82:
        return "SHOWERS"
    if code >= 95:
        return "STORMS"
    return "WEATHER"


def wx_bar(wx):
    """Same question as every other bar: is something happening. Here that's
    whether the sky is about to interfere with anything."""
    word = wx_word(wx["code"])
    wet = wx["code"] >= 51
    if wet:
        return RED, DUST, word
    if wx["rain"] >= 50:
        return YELLOW, LOAM, word
    return GREEN, LOAM, word


def cal_bar(cal):
    if cal["minutes"] is None:
        return LOAM, SLATE, "CLEAR"
    if cal["minutes"] <= 60:
        return SODIUM, LOAM, f"IN {cal['minutes']}M"
    return LOAM, SLATE, "NEXT UP"


def clock_str(now=None):
    now = now or dt.datetime.now()
    h = now.hour % 12 or 12
    return f"{h}:{now.minute:02d}"


def draw_footer(d, label):
    """Bottom row: clock hard left, context hard right. The clock is the same
    on every screen, so it reads as chrome rather than data. If the label
    won't fit alongside it, the label gets trimmed — the time always wins."""
    t = clock_str()
    draw_text(d, t, 2, LABEL_Y, SLATE, 1)

    avail = 62 - (2 + text_width(t, 1) + 4)
    if text_width(label, 1) > avail and " " in label:
        while label and text_width(label, 1) > avail:   # shed whole words first
            label = label[:label.rfind(" ")] if " " in label else ""
    while label and text_width(label, 1) > avail:
        label = label[:-1].rstrip(" ,.")
    if label:
        draw_text(d, label, 62 - text_width(label, 1), LABEL_Y, SLATE, 1)


def stack(d, title, sub, big, label, big_color=DUST, title_scale_max=2):
    """The shared layout: title, subtitle, one big number, one footer.
    Positions are computed so nothing ever collides."""
    y = BAR_H + 5
    tsc = fit_scale(title, title_scale_max)
    draw_centered(d, title, y, DUST, tsc)
    y += text_height(tsc) + 3
    draw_centered(d, sub, y, SLATE, 1)
    y += text_height(1)

    sc = fit_scale(big, 4)
    top, bottom = y + 2, LABEL_Y - 2
    draw_centered(d, big, top + (bottom - top - text_height(sc)) // 2, big_color, sc)

    draw_footer(d, label)


def canvas():
    img = Image.new("RGB", (64, 64), LOAM)
    return img, ImageDraw.Draw(img)


# ---------------------------------------------------------------- screens

def screen_flag(dirt, bath, wx, cal):
    bar_color, bar_text, word = STATES.get(dirt["state"], STATES["standby"])
    img, d = canvas()
    draw_bar(d, bar_color, word, bar_text, rule=(dirt["state"] == "standby"))
    accent = SODIUM if dirt["state"] in ("standby", "watch") else DUST
    stack(d, dirt["track"], dirt["town"], dirt["countdown"], dirt["label"],
          big_color=accent)
    return img


def screen_sites(dirt, bath, wx, cal):
    img, d = canvas()
    c, tc, t = queue_bar(bath)
    draw_bar(d, c, t, tc)
    arrow = "+" if bath["delta"] > 0 else "-" if bath["delta"] < 0 else ""
    sub = f"{bath['sessions']} SESS {bath['views']} PV"
    stack(d, "BATHROOMREPORT", sub, commas(bath["users"]),
          f"{arrow}{abs(bath['delta'])} YDAY" if bath["delta"] else "USERS",
          big_color=DUST)
    return img


def screen_trend(dirt, bath, wx, cal):
    img, d = canvas()
    c, tc, t = queue_bar(bath)
    draw_bar(d, c, t, tc)

    draw_centered(d, "SESSIONS", BAR_H + 5, DUST, 1)

    vals = bath["series"][-7:]
    n = len(vals)
    bw, gap = 7, 1
    total_w = n * bw + (n - 1) * gap
    x0 = (64 - total_w) // 2
    top, bottom = 28, 49
    lo, hi = min(vals) * 0.85, max(vals)
    span = max(hi - lo, 1)

    letters = "MTWTFSS"
    order = []
    for ds in bath["series_days"][-n:]:
        try:
            y, mo, dd = (int(x) for x in ds.split("-"))
            order.append(dt.date(y, mo, dd).weekday())
        except Exception:
            order.append(0)

    for i, v in enumerate(vals):
        h = max(2, int((v - lo) / span * (bottom - top)))
        x = x0 + i * (bw + gap)
        color = DUST if i == n - 1 else SODIUM
        d.rectangle([x, bottom - h, x + bw - 1, bottom], fill=color)
        draw_text(d, letters[order[i]], x + 2, 51, SLATE, 1)

    draw_footer(d, f"{commas(sum(vals))} WK")
    return img


def screen_source(dirt, bath, wx, cal):
    img, d = canvas()
    c, tc, t = queue_bar(bath)
    draw_bar(d, c, t, tc)
    stack(d, "TOP REFERRER", bath["top_source"].upper(),
          commas(bath["top_sessions"]), "SESSIONS",
          big_color=SODIUM)
    return img


def screen_weather(dirt, bath, wx, cal):
    img, d = canvas()
    c, tc, t = wx_bar(wx)
    draw_bar(d, c, t, tc, rule=(c == LOAM))
    stack(d, f"H {wx['high']}°  L {wx['low']}°", f"FEELS {wx['feels']}°",
          f"{wx['temp']}°", f"{wx['rain']}% {wx['wind']}MPH",
          big_color=DUST)
    return img


def screen_calendar(dirt, bath, wx, cal):
    img, d = canvas()
    c, tc, t = cal_bar(cal)
    draw_bar(d, c, t, tc, rule=(c == LOAM))
    more = f"+{cal['more']} TODAY" if cal["more"] else "NOTHING AFTER"
    stack(d, cal["title"], cal["where"], cal["time"], more,
          big_color=SODIUM if (cal["minutes"] or 999) <= 60 else DUST)
    return img


SCREENS = {
    "flag": screen_flag,
    "weather": screen_weather,
    "calendar": screen_calendar,
    "sites": screen_sites,
    "trend": screen_trend,
    "source": screen_source,
}


# ---------------------------------------------------------------- rotation

def is_race_night(now=None):
    now = now or dt.datetime.now()
    return now.weekday() in RACE_DAYS and RACE_WINDOW[0] <= now.hour < RACE_WINDOW[1]


def rotation(now=None):
    """(screen, seconds) pairs. Three moods: mornings lead with what you
    need before you leave, race nights hand most of the time to the flag,
    and the rest of the day belongs to the projects."""
    now = now or dt.datetime.now()
    if is_race_night(now):
        return [("flag", 30), ("weather", 8), ("calendar", 6),
                ("sites", 5), ("trend", 5), ("source", 5)]
    if MORNING[0] <= now.hour < MORNING[1]:
        return [("calendar", 16), ("weather", 14), ("flag", 6),
                ("sites", 8), ("source", 8), ("trend", 6)]
    return [("calendar", 10), ("weather", 10), ("sites", 10),
            ("trend", 10), ("source", 8), ("flag", 8)]


# ---------------------------------------------------------------- data

def _get(url, fallback, name):
    """Uses requests rather than urllib. The python.org macOS build ships
    without a wired-up CA bundle, so urllib fails every HTTPS call with
    CERTIFICATE_VERIFY_FAILED until you run Install Certificates.command.
    requests carries its own bundle and just works."""
    try:
        if url.startswith("file://"):
            with open(url[7:], "r") as f:
                return json.load(f)
        r = requests.get(url, timeout=10, headers={"User-Agent": "board/1.0"})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"{name} fetch failed ({e}); using demo data", file=sys.stderr)
        return fallback


def fetch():
    """Adjust the two mappings below to match your real JSON.
    Nothing else in the file needs to change."""
    ev_doc = _get(f"{DIRTCALL_BASE}/events.json", None, "dirtcall events")
    st_doc = _get(f"{DIRTCALL_BASE}/status.json", None, "dirtcall status")
    raw_b = _get(BATHROOM_URL, None, "bathroom")
    raw_w = _get(WEATHER_URL, None, "weather")
    raw_c = _get(CALENDAR_URL, DEMO_CAL, "calendar")

    if ev_doc and st_doc:
        import dirtcall
        dirt = dirtcall.build(ev_doc, st_doc)
    else:
        dirt = DEMO_DIRT
    if raw_b and "ga4" in raw_b:
        import bathroom
        bath = bathroom.build(raw_b)
    else:
        bath = DEMO_BATH
    if raw_w and "current" in raw_w:
        cur, day = raw_w["current"], raw_w["daily"]
        wx = {
            "temp": round(cur["temperature_2m"]),
            "feels": round(cur["apparent_temperature"]),
            "high": round(day["temperature_2m_max"][0]),
            "low": round(day["temperature_2m_min"][0]),
            "code": cur["weather_code"],
            "wind": round(cur["wind_speed_10m"]),
            "rain": day["precipitation_probability_max"][0] or 0,
        }
    else:
        wx = DEMO_WX

    cal = {
        "title": raw_c.get("title", ""),
        "where": raw_c.get("where", ""),
        "time": raw_c.get("time", ""),
        "minutes": raw_c.get("minutes"),
        "more": raw_c.get("more", 0),
    }
    return dirt, bath, wx, cal


def brightness_now():
    h = dt.datetime.now().hour
    return NIGHT_BRIGHTNESS if (h >= NIGHT_START or h < NIGHT_END) else DAY_BRIGHTNESS


# ---------------------------------------------------------------- main

def connect():
    from pixoo_client import Pixoo
    return Pixoo(PIXOO_IP)


def push(dev, img):
    dev.set_brightness(brightness_now())
    dev.push_image(img)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", metavar="DIR", help="render PNGs, no device")
    ap.add_argument("--once", action="store_true", help="push one frame and exit")
    ap.add_argument("--loop", action="store_true", help="rotate screens forever")
    ap.add_argument("--screen", choices=list(SCREENS), help="force one screen")
    args = ap.parse_args()

    if args.preview:
        os.makedirs(args.preview, exist_ok=True)
        dirt, bath, wx, cal = DEMO_DIRT, DEMO_BATH, DEMO_WX, DEMO_CAL
        variants = {
            "flag-racing": (dirt, bath),
            "flag-rainout": ({**dirt, "state": "rained", "track": "FONDA",
                              "town": "FONDA NY", "countdown": "4:12",
                              "label": "CALLED PM"}, bath),
            "flag-standby": ({**dirt, "state": "standby", "town": "NEXT UP FRI",
                              "countdown": "2D 10H", "label": "TO GREEN FLAG"}, bath),
        }
        for name, (dd, bb) in variants.items():
            SCREENS["flag"](dd, bb, wx, cal).save(os.path.join(args.preview, f"{name}.png"))
            print(name)
        for name in ("weather", "calendar", "sites", "trend", "source"):
            SCREENS[name](dirt, bath, wx, cal).save(
                os.path.join(args.preview, f"{name}.png"))
            print(name)
        return

    dirt, bath, wx, cal = fetch()

    if args.screen:
        push(connect(), SCREENS[args.screen](dirt, bath, wx, cal))
        return

    if args.once:
        name = rotation()[0][0]
        push(connect(), SCREENS[name](dirt, bath, wx, cal))
        return

    if args.loop:
        dev = connect()
        last_fetch = 0
        while True:
            for name, dwell in rotation():
                if time.time() - last_fetch > 300:      # refresh data every 5 min
                    dirt, bath, wx, cal = fetch()
                    last_fetch = time.time()
                push(dev, SCREENS[name](dirt, bath, wx, cal))
                time.sleep(dwell)

    ap.print_help()


if __name__ == "__main__":
    main()
