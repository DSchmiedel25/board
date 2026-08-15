#!/usr/bin/env python3
"""
board.py — DirtCheck + BathroomReport on a Divoom Pixoo-64.

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
    PIXOO_IP, LAT, LON, DIRTCHECK_BASE, DATA_DIR,
    JELLYFIN_URL, JELLYFIN_KEY, JELLYFIN_USER,
    DAY_BRIGHTNESS, NIGHT_BRIGHTNESS, NIGHT_START, NIGHT_END,
    MORNING, RACE_DAYS, RACE_WINDOW,
)

WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}"
    "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
    "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
    "&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=auto"
    "&forecast_days=4"
)
CALENDAR_URL = f"file://{DATA_DIR}/next.json"

# palette — same values as the wall dashboard
LOAM   = (28, 21, 18)
DUST   = (232, 220, 200)
SLATE  = (139, 122, 108)
RAIL   = (58, 44, 37)
SODIUM = (242, 167, 59)
GREEN  = (63, 163, 77)
RED    = (196, 52, 43)
YELLOW = (229, 195, 74)

# BathroomReport's own palette, from its stylesheet. The project screens use
# it so they read as a different place from the track and weather screens.

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
    "rows": [
        {"code": "AS",  "when": "NOW", "state": "racing", "prob": 5},
        {"code": "LV",  "when": "SAT", "state": "dark",   "prob": 2},
        {"code": "FON", "when": "SAT", "state": "dark",   "prob": 2},
    ],
}

DEMO_WX = {
    "temp": 68, "feels": 66, "high": 84, "low": 61,
    "code": 1, "wind": 7, "rain": 20,
    "days": [("SAT", 84, 61), ("SUN", 79, 58), ("MON", 71, 55)],
    "nascar": {"live": False, "venue": "RICHMOND", "rows": [
        {"label": "CUP", "when": "SAT 10:00", "net": "USA", "live": False},
        {"label": "XFN", "when": "SAT 7:30", "net": "CW", "live": False},
        {"label": "TRK", "when": "FRI 9:00", "net": "FS1", "live": False}]},
}

DEMO_CAL = {
    "title": "CREW HUDDLE",
    "where": "ERIE ST SITE",
    "time": "7:30",
    "minutes": 48,
    "more": 3,
}

DEMO_JF = {
    "playing": True, "title": "The Bear", "sub": "S3E5", "user": "Dave",
    "paused": False, "pct": 42, "art_id": None, "transcoding": False,
    "streams": 1, "watchers": 1,
    "movies": 812, "episodes": 6104, "series": 137,
}

DEMO_BATH = {
    "health": ("ok", "CLEAN"), "day": "2026-08-11",
    "users": 3, "sessions": 4, "views": 5, "delta": -1,
    "series": [9, 13, 12, 1, 2, 4, 4],
    "series_days": ["2026-08-05", "2026-08-06", "2026-08-07", "2026-08-08",
                    "2026-08-09", "2026-08-10", "2026-08-11"],
    "new_users": 1, "errors": 0, "dead": 0, "bots": 1,
    "bot_share": 100, "engage": 62, "clarity_day": "2026-08-11",
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


# ---------------------------------------------------------------- sprites

# 11x11, drawn on the same grid as the font so they sit level with the type
SPRITES = {
"CLEAR": """
.....#.....
.#...#...#.
..#.....#..
....###....
...#####...
#..#####..#
...#####...
....###....
..#.....#..
.#...#...#.
.....#.....""",
"CLOUDY": """
...........
...........
....###....
..##...##..
.#.......#.
#.........#
#.........#
.#########.
...........
...........
...........""",
"RAIN": """
...........
....###....
..##...##..
.#.......#.
#.........#
.#########.
...........
..#..#..#..
.#..#..#...
..#..#..#..
.#..#..#...""",
"SNOW": """
...........
....###....
..##...##..
.#.......#.
#.........#
.#########.
...........
..#.#.#.#..
...#.#.#...
..#.#.#.#..
...........""",
"NASCAR": """
...........
.##..##..##
.##..##..##
##..##..##.
##..##..##.
.##..##..##
.##..##..##
##..##..##.
##..##..##.
...........
...........""",
"STORMS": """
...........
....###....
..##...##..
.#.......#.
#.........#
.#########.
.....###...
....##.....
...#####...
.....##....
....#......""",
}
SPRITE_W = 11


def draw_sprite(d, key, x, y, color):
    rows = SPRITES[key].strip("\n").split("\n")
    for ry, row in enumerate(rows):
        for rx, c in enumerate(row):
            if c == "#":
                d.point((x + rx, y + ry), fill=color)


# ---------------------------------------------------------------- chrome

BAR_H = 16
LABEL_Y = 58

MARQUEE_STEP = 0.22      # seconds between frames while a title scrolls
MARQUEE_PX = 2           # pixels per frame
MARQUEE_MAX = 34         # seconds; cap so one long title can't own the board
MARQUEE_X0, MARQUEE_X1 = 1, 63
SCRIM_TOP = 57           # poster shows above this; text row below

# bar text is drawn at one fixed scale so it never jitters between screens
BAR_SCALE = 2


def draw_bar(d, color, text, text_color, rule=False):
    d.rectangle([0, 0, 63, BAR_H], fill=color)
    if rule:
        d.line([0, BAR_H, 63, BAR_H], fill=SLATE)
    sc = min(BAR_SCALE, fit_scale(text, BAR_SCALE, pad=1))
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
    """Four buckets. A wall board doesn't need drizzle-versus-showers; it
    needs to know whether water is falling."""
    if code in (0, 1):
        return "CLEAR"
    if code in (2, 3) or code in (45, 48):
        return "CLOUDY"
    if 71 <= code <= 77 or code in (85, 86):
        return "SNOW"
    if code >= 95:
        return "STORMS"
    if code >= 51:
        return "RAIN"
    return "CLEAR"


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


def draw_footer(img, label, phase=0, color=SLATE):
    """Bottom row: whatever context the screen wants, centred. No clock —
    there are three of those in this room already. Long labels scroll rather
    than getting trimmed, so nothing is silently lost."""
    if not label:
        return
    _marquee(img, label, LABEL_Y, color, 1, phase)


def canvas():
    img = Image.new("RGB", (64, 64), LOAM)
    return img, ImageDraw.Draw(img)


def draw_unavailable(img, d, what, status):
    """One look for every dead source. Grey, never green — the eye reads
    colour before words, and green here would be a lie."""
    d.rectangle([0, 0, 63, BAR_H], fill=RAIL)
    word = {"OFFLINE": "OFFLINE", "STALE": "STALE",
            "UNKNOWN": "NO KEY"}.get(status, "UNKNOWN")
    draw_centered(d, word, 4, DUST, BAR_SCALE)
    draw_centered(d, what, 28, SLATE, 1)
    draw_centered(d, "NO DATA" if status != "STALE" else "LAST KNOWN",
                  40, SLATE, 1)
    return img


def stale_note(status):
    """Footer text that admits what the screen is actually showing."""
    return "" if status == "OK" else status


# ---------------------------------------------------------------- screens

def screen_flag(dirt, bath, wx, cal, phase=0):
    """All three tracks at once. The bar carries tonight's headline; the rows
    say what each track is doing, so a dark Fonda is as visible as a green
    Albany. When nothing is running, the soonest track is lit — three equally
    dim rows make you read all of them to find the one that matters."""
    img, d = canvas()

    status = dirt.get("status", "OK")
    if status in ("OFFLINE", "UNKNOWN") or not dirt.get("rows"):
        return draw_unavailable(img, d, "DIRTCHECK", status)

    bar_color, bar_text, word = STATES.get(dirt["state"], STATES["standby"])

    # Stale data keeps its content but loses its colour. Green is a claim
    # about right now; an hour-old fetch has no business making it.
    if status == "STALE":
        bar_color, bar_text = RAIL, DUST

    standby = dirt["state"] == "standby"
    if standby:
        draw_bar(d, SODIUM, "DIRT CHK", LOAM)
    else:
        draw_bar(d, bar_color, word, bar_text)

    rows = dirt.get("rows") or []
    ROW_H, y0 = 12, BAR_H + 4

    def risk_chip(r):
        """The chip is rain risk, not race state — that way it carries
        information every day of the week rather than only on race nights.
        'Is it happening now' is answered by NOW in the day column and by the
        bar going green."""
        if r["state"] == "rained":
            return RED
        p = r["prob"]
        if p is None:
            return RAIL
        if p >= 60:
            return RED
        if p >= 30:
            return YELLOW
        return GREEN

    # on a standby screen the first row is the next race, so highlight it
    lit = 0 if standby and rows else -1

    for i, r in enumerate(rows[:3]):
        y = y0 + i * ROW_H
        live = r["state"] != "dark"
        hot = (i == lit)

        d.rectangle([0, y, 2, y + ROW_H - 3],
                    fill=RAIL if status == "STALE" else risk_chip(r))
        draw_text(d, r["code"], 6, y + 1, DUST if (live or hot) else SLATE, 2)

        # two fixed columns so a 3-char code and a 3-char day never collide
        draw_text(d, r["when"], 34, y + 3,
                  SODIUM if (live or hot) else SLATE, 1)
        if r["prob"] is not None:
            p = f"{r['prob']}%"
            draw_text(d, p, 62 - text_width(p, 1), y + 3,
                      DUST if (live or hot) else SLATE, 1)

    note = stale_note(status)
    draw_footer(img, f"{dirt['label']}  {note}".strip() if note
                else dirt["label"], phase, SODIUM if note else SLATE)
    return img


def screen_weather(dirt, bath, wx, cal, phase=0):
    """Now on top, the next three days underneath. The big number is what it
    is outside right now; everything below is planning."""
    img, d = canvas()

    status = wx.get("status", "OK")
    if status in ("OFFLINE", "UNKNOWN") or "temp" not in wx:
        return draw_unavailable(img, d, "WEATHER", status)

    c, tc, word = wx_bar(wx)

    # sprite and word travel together as one centred group
    d.rectangle([0, 0, 63, BAR_H], fill=c)
    tw = text_width(word, BAR_SCALE)
    x = (64 - (SPRITE_W + 3 + tw)) // 2
    draw_sprite(d, word, x, 3, tc)
    draw_text(d, word, x + SPRITE_W + 3, 4, tc, BAR_SCALE)

    draw_centered(d, f"{wx['temp']}\u00b0", 20, DUST, 4)

    d.line([2, 43, 61, 43], fill=RAIL)
    days = wx.get("days") or []
    for i, (name, hi, lo) in enumerate(days[:3]):
        cx = 11 + i * 21
        draw_text(d, name, cx - text_width(name, 1) // 2, 45, SLATE, 1)
        t = f"{hi}/{lo}"
        draw_text(d, t, cx - text_width(t, 1) // 2, 51, DUST, 1)

    note = stale_note(wx.get("status", "OK"))
    draw_footer(img, f"{wx['rain']}% RAIN  {wx['wind']} MPH"
                + (f"  {note}" if note else ""), phase,
                SODIUM if note else SLATE)
    return img


def _marquee(img, text, y, color, scale, phase,
             x0=MARQUEE_X0, x1=MARQUEE_X1):
    """Scroll long text through a window without spilling past its edges.

    Drawn onto a crop of the panel rather than the panel itself, so the poster
    behind it survives and the clipping is free.
    """
    win = x1 - x0
    w = text_width(text, scale)
    h = text_height(scale)

    if w <= win:
        draw_text(ImageDraw.Draw(img), text,
                  x0 + (win - w) // 2, y, color, scale)
        return

    strip = img.crop((x0, y, x1, y + h))
    sd = ImageDraw.Draw(strip)
    gap = 6 * scale
    span = w + gap
    off = phase % span
    draw_text(sd, text, -off, 0, color, scale)
    draw_text(sd, text, -off + span, 0, color, scale)   # wrap seam
    img.paste(strip, (x0, y))


def marquee_span(text, scale=1):
    """Pixels in one full loop, including the gap between repeats."""
    return text_width(text, scale) + 6 * scale


def scrolling_text(name, dirt, bath, wx):
    """What each screen would scroll, and at what scale and window width.
    Returns None when a screen has nothing that could overflow."""
    if name == "jellyfin":
        return jf_line(bath), 1, MARQUEE_X1 - MARQUEE_X0
    if name == "nascar":
        nc = wx.get("nascar") or {}
        return nc.get("venue", ""), BAR_SCALE, 62 - (2 + SPRITE_W + 3)
    if name == "weather":
        return f"{wx['rain']}% RAIN  {wx['wind']} MPH", 1, MARQUEE_X1 - MARQUEE_X0
    if name == "flag":
        return dirt.get("label", ""), 1, MARQUEE_X1 - MARQUEE_X0
    return None


def needs_marquee(name, dirt, bath, wx):
    got = scrolling_text(name, dirt, bath, wx)
    if not got:
        return False
    text, scale, window = got
    return text_width(text, scale) > window


def marquee_seconds(text, scale, floor):
    """Run for one whole cycle where it fits in a sane dwell, so you see the
    title rather than a third of it. Capped, or a very long name would hold
    the board hostage."""
    cycle = marquee_span(text, scale) / (MARQUEE_PX / MARQUEE_STEP)
    return min(max(cycle, floor), MARQUEE_MAX)


def jf_line(jf):
    """Title, episode and watcher on one line — the same row the clock uses
    on every other screen."""
    return "  ".join(x for x in (jf.get("title", ""),
                                 jf.get("sub", ""),
                                 jf.get("user", "")) if x).upper()


def screen_jellyfin(dirt, bath, wx, cal, phase=0):
    """Poster art gets everything above the footer row. The text sits exactly
    where the clock sits on the other screens, on a thin scrim — an outline
    instead reads as a black blob around each glyph at this size.
    """
    jf = bath              # the jellyfin bag rides in the same slot
    img, d = canvas()
    art = jf.get("art")

    if art is not None:
        img.paste(art, (0, 0))
        strip = Image.new("RGB", (64, 64 - SCRIM_TOP), LOAM)
        img.paste(Image.blend(img.crop((0, SCRIM_TOP, 64, 64)), strip, 0.82),
                  (0, SCRIM_TOP))
    else:
        draw_centered(d, "JELLYFIN", 26, SLATE, 2)

    _marquee(img, jf_line(jf), LABEL_Y, DUST, 1, phase)
    d = ImageDraw.Draw(img)

    # progress runs along the very bottom edge, full width
    pct = jf.get("pct")
    if pct is not None:
        d.line([0, 63, 63, 63], fill=RAIL)
        d.line([0, 63, int(63 * pct / 100), 63],
               fill=SLATE if jf.get("paused") else SODIUM)
    return img


def screen_nascar(dirt, bath, wx, cal, phase=0):
    """All three national series as rows: when the next race is and what
    channel. Same row grammar as the dirt tracks, so the eye already knows
    how to read it."""
    nc = wx.get("nascar") or {}
    rows = nc.get("rows") or []
    img, d = canvas()

    if not rows:
        draw_bar(d, LOAM, "NASCAR", SLATE, rule=True)
        draw_centered(d, "NO DATA", 32, SLATE, 1)
        return img

    # bar carries the weekend's track, or LIVE when anything is running
    if nc.get("live"):
        d.rectangle([0, 0, 63, BAR_H], fill=GREEN)
        word, ink = "LIVE", LOAM
    else:
        d.rectangle([0, 0, 63, BAR_H], fill=SODIUM)
        word, ink = nc.get("venue") or "NASCAR", LOAM
    # Sprite stays put; the track name scrolls in the space beside it rather
    # than shrinking to illegibility or getting clipped.
    draw_sprite(d, "NASCAR", 2, 3, ink)
    _marquee(img, word, 4, ink, BAR_SCALE, phase,
             x0=2 + SPRITE_W + 3, x1=62)
    d = ImageDraw.Draw(img)

    # one row per series: label left, network right, time filling the middle
    y = 22
    for r in rows[:3]:
        live = r.get("live")
        draw_text(d, r["label"], 1, y, DUST if live else SLATE, 1)

        net = r.get("net", "")
        if net:
            draw_text(d, net, 63 - text_width(net, 1), y, SODIUM, 1)

        when = r.get("when", "")
        lx = 1 + text_width(r["label"], 1) + 2
        rx = 63 - (text_width(net, 1) + 2 if net else 0)
        draw_text(d, when, lx + max(0, (rx - lx - text_width(when, 1)) // 2),
                  y, GREEN if live else DUST, 1)
        y += 12

    return img


SCREENS = {
    "flag": screen_flag,
    "weather": screen_weather,
    "jellyfin": screen_jellyfin,
    "nascar": screen_nascar,
}


# ---------------------------------------------------------------- rotation

def is_race_night(now=None):
    now = now or dt.datetime.now()
    return now.weekday() in RACE_DAYS and RACE_WINDOW[0] <= now.hour < RACE_WINDOW[1]


def poll_secs(now=None):
    """How often to re-fetch. A rainout called at 5:55 shouldn't wait five
    minutes to reach the wall, so race nights poll hard."""
    return 60 if is_race_night(now) else 300


def rotation(now=None):
    """(screen, seconds) pairs. Race nights hand most of the time to the
    tracks; mornings lead with the sky; otherwise it spreads evenly."""
    now = now or dt.datetime.now()
    if is_race_night(now):
        return [("flag", 30), ("weather", 10), ("nascar", 8), ("jellyfin", 16)]
    if MORNING[0] <= now.hour < MORNING[1]:
        return [("weather", 18), ("flag", 12), ("nascar", 10), ("jellyfin", 16)]
    return [("flag", 14), ("weather", 14), ("nascar", 12), ("jellyfin", 18)]


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


CACHE = {}                 # name -> (value, unix_seconds)
STALE_AFTER = 3600         # an hour old stops counting as current


def remember(name, value):
    CACHE[name] = (value, time.time())
    return value


def recall(name):
    """Last good value and its state. Anything older than STALE_AFTER is
    reported as STALE so the screen can say so rather than implying it's now."""
    got = CACHE.get(name)
    if not got:
        return None, "OFFLINE"
    value, when = got
    return value, ("STALE" if time.time() - when > STALE_AFTER else "OK")


def fetch():
    """Adjust the two mappings below to match your real JSON.
    Nothing else in the file needs to change."""
    ev_doc = _get(f"{DIRTCHECK_BASE}/events.json", None, "dirtcheck events")
    st_doc = _get(f"{DIRTCHECK_BASE}/status.json", None, "dirtcheck status")
    raw_w = _get(WEATHER_URL, None, "weather")
    raw_n = fetch_nascar()

    # NEVER fall back to demo data here. A demo Albany is green and racing,
    # so a failed fetch would put a confident "RACING" on the wall when the
    # truth is that we cannot reach DirtCheck. Last known good, clearly aged,
    # or an explicit offline state.
    if ev_doc and st_doc:
        import dirtcheck
        dirt = dirtcheck.build(ev_doc, st_doc)
        dirt["rows"] = dirtcheck.track_rows(ev_doc, st_doc)
        dirt["status"] = "OK"
        remember("dirt", dirt)
    else:
        cached, state = recall("dirt")
        dirt = dict(cached) if cached else {"state": "unknown", "rows": [],
                                           "label": ""}
        dirt["status"] = state
    bath = fetch_jellyfin()
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
            "days": [
                (_day_name(raw_w["daily"]["time"][i]),
                 round(day["temperature_2m_max"][i]),
                 round(day["temperature_2m_min"][i]))
                for i in range(1, min(4, len(day["temperature_2m_max"])))
            ],
        }
        wx["status"] = "OK"
        remember("wx", wx)
    else:
        cached, state = recall("wx")
        wx = dict(cached) if cached else {}
        wx["status"] = state

    if raw_n:
        import nascar as _nascar
        wx["nascar"] = _nascar.build(raw_n)

    return dirt, bath, wx, {}


def fetch_jellyfin():
    """Split out from fetch() because it's a call to a box on the same LAN —
    cheap enough to run every rotation step, so the screen appears within
    seconds of someone pressing play rather than at the next remote poll."""
    if not JELLYFIN_KEY:
        return {"playing": False, "status": "UNKNOWN"}
    import jellyfin
    sess = jellyfin.sessions(JELLYFIN_URL, JELLYFIN_KEY)
    cnts = jellyfin.counts(JELLYFIN_URL, JELLYFIN_KEY)
    jf = jellyfin.build(sess, cnts, user=JELLYFIN_USER)
    jf["art"] = (jellyfin.poster(JELLYFIN_URL, JELLYFIN_KEY, jf["art_id"])
                 if jf.get("art_id") else None)
    return jf


def fetch_nascar():
    """One call per series. A series that fails is simply left out rather
    than failing the whole screen."""
    import nascar
    docs = {}
    for _, slug, _f in nascar.SERIES:
        doc = _get(nascar.url(slug), None, f"nascar {slug}")
        if doc:
            docs[slug] = doc
    return docs or None


def _day_name(iso):
    y, m, d = (int(x) for x in iso.split("-"))
    return dt.date(y, m, d).strftime("%a").upper()


def brightness_now():
    h = dt.datetime.now().hour
    return NIGHT_BRIGHTNESS if (h >= NIGHT_START or h < NIGHT_END) else DAY_BRIGHTNESS


# ---------------------------------------------------------------- main

def connect():
    if not PIXOO_IP:
        sys.exit(
            "PIXOO_IP is not set.\n"
            "\n"
            "It belongs in local_config.py, which git ignores — so a pull can\n"
            "never overwrite it. On this machine:\n"
            "\n"
            "  cd ~/board\n"
            "  cp local_config.example.py local_config.py\n"
            "  nano local_config.py\n"
            "\n"
            "Find the IP in the Divoom app under your device's settings, or:\n"
            "  curl -s -X POST https://app.divoom-gz.com/Device/ReturnSameLANDevice")
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
        dirt, bath, wx, cal = DEMO_DIRT, DEMO_JF, DEMO_WX, {}
        dirt["status"] = wx["status"] = "DEMO"
        dark = {**dirt, "state": "standby", "countdown": "1D 17H",
                "label": "TO GREEN",
                "rows": [{"code": "AS",  "when": "FRI", "state": "dark", "prob": 5},
                         {"code": "LV",  "when": "SAT", "state": "dark", "prob": 2},
                         {"code": "FON", "when": "SAT", "state": "dark", "prob": 2}]}
        sat = {**dirt, "state": "racing", "label": "HOT LAPS",
               "rows": [{"code": "AS",  "when": "FRI",    "state": "dark",   "prob": 18},
                        {"code": "LV",  "when": "NOW", "state": "racing", "prob": 2},
                        {"code": "FON", "when": "NOW", "state": "watch",  "prob": 45}]}
        rain = {**dirt, "state": "rained", "countdown": "4:12", "label": "CALLED",
                "rows": [{"code": "AS",  "when": "NOW", "state": "rained", "prob": 85},
                         {"code": "LV",  "when": "SAT",    "state": "dark",   "prob": 2},
                         {"code": "FON", "when": "SAT",    "state": "dark",   "prob": 2}]}
        for name, dd in (("flag-friday", dirt), ("flag-dark", dark),
                         ("flag-saturday", sat), ("flag-rainout", rain)):
            SCREENS["flag"](dd, bath, wx, cal).save(
                os.path.join(args.preview, f"{name}.png"))
            print(name)
        for name in ("weather", "nascar", "jellyfin"):
            SCREENS[name](dirt, bath, wx, cal).save(
                os.path.join(args.preview, f"{name}.png"))
            print(name)
        return

    dirt, bath, wx, cal = fetch()

    if args.screen:
        push(connect(), SCREENS[args.screen](dirt, bath, wx, cal))
        return

    if args.once:
        name = next((n for n, _ in rotation()
                     if n != "jellyfin" or bath.get("playing")), "flag")
        push(connect(), SCREENS[name](dirt, bath, wx, cal))
        return

    if args.loop:
        dev = connect()
        dirt, bath, wx, cal = fetch()
        last_fetch = time.time()
        last_state = dirt.get("state")

        while True:
            for name, dwell in rotation():
                # Jellyfin is a LAN call, so refresh it every step rather than
                # on the remote poll. Playback shows up within a few seconds.
                bath = fetch_jellyfin()

                # nothing playing means no Jellyfin screen at all
                if name == "jellyfin" and not bath.get("playing"):
                    continue

                # A long title has to move to be read, which means pushing
                # frames instead of one still. Only when it doesn't fit.
                if needs_marquee(name, dirt, bath, wx):
                    text, scale, _w = scrolling_text(name, dirt, bath, wx)
                    dev.set_brightness(brightness_now())
                    steps = int(marquee_seconds(text, scale, dwell)
                                / MARQUEE_STEP)
                    for i in range(steps):
                        dev.push_image(SCREENS[name](dirt, bath, wx, cal,
                                                     phase=i * MARQUEE_PX))
                        time.sleep(MARQUEE_STEP)
                    continue

                push(dev, SCREENS[name](dirt, bath, wx, cal))

                # Sleep in slices rather than one long block, so a state
                # change can cut in instead of waiting out the dwell.
                waited = 0
                while waited < dwell:
                    nap = min(5, dwell - waited)
                    time.sleep(nap)
                    waited += nap

                    if time.time() - last_fetch < poll_secs():
                        continue

                    dirt, _, wx, cal = fetch()
                    last_fetch = time.time()

                    state = dirt.get("state")
                    if state == last_state:
                        continue

                    # something changed — show it now, whatever screen is up
                    last_state = state
                    push(dev, SCREENS["flag"](dirt, bath, wx, cal))
                    time.sleep(20)
                    waited = dwell

    ap.print_help()


if __name__ == "__main__":
    main()
