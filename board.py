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
    JELLYFIN_URL, JELLYFIN_KEY,
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
RAIL   = (58, 44, 37)
SODIUM = (242, 167, 59)
GREEN  = (63, 163, 77)
RED    = (196, 52, 43)
YELLOW = (229, 195, 74)

# BathroomReport's own palette, from its stylesheet. The project screens use
# it so they read as a different place from the track and weather screens.
BR_TEAL  = (46, 161, 170)     # #2ea1aa  PWA theme colour
BR_NAVY  = (11, 25, 42)       # #0b192a  charcoal
BR_CREAM = (245, 247, 250)    # #f5f7fa
BR_MUTED = (147, 165, 184)    # #93a5b8
BR_LINE  = (42, 64, 86)       # #2a4056  panel border
BR_UP    = (143, 214, 148)    # #8fd694
BR_DOWN  = (240, 138, 134)    # #f08a86

# Jellyfin's own two brand colours, so its screens read as a third place
JF_PURPLE = (170, 92, 195)    # #aa5cc3
JF_BLUE   = (0, 164, 220)     # #00a4dc
JF_INK    = (14, 14, 22)      # bar text on either of the above

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
    "new_users": 1, "errors": 0, "dead": 0, "bots": 1,
    "bot_share": 100, "engage": 62, "clarity_day": "2026-08-11",
    "week_sessions": 45, "top_source": "facebook.com", "top_sessions": 8,
    "signups": 1,
}

DEMO_JF = {
    "playing": True, "title": "THE PITT", "sub": "S1E4", "user": "AMANDA",
    "paused": False, "pct": 38, "art_id": None, "transcoding": False,
    "streams": 1, "watchers": 1,
    "movies": 412, "episodes": 3180, "series": 96,
}

# Populated by refresh_jf(). Kept module-level so the screens keep the same
# (dirt, bath, wx, cal) signature as the other four.
JF = None

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


def clip_text(s, scale=1, width=60):
    """fit_scale shrinks until it fits or runs out of scales. Titles are the
    one field long enough to overflow even at scale 1, so trim too — whole
    words first, because a title cut mid-word reads as a rendering fault."""
    s = (s or "").upper()
    while s and text_width(s, scale) > width and " " in s:
        s = s[:s.rfind(" ")]
    while s and text_width(s, scale) > width:
        s = s[:-1]
    return s.rstrip(" ,.-:")


FILLER = {"THE", "A", "AN", "AND", "OF", "TO", "IN", "ON", "FOR", "&"}


def fit_lines(s, max_scale=2, width=60, max_lines=2):
    """Wrap a title to at most two lines, at the largest scale that holds all
    of it. Truncating "EVERYTHING EVERYWHERE ALL AT ONCE" down to EVERYTHING
    loses the title; two smaller lines keep it. Returns (lines, scale)."""
    s = (s or "").upper()
    words = s.split()
    if not words:
        return [], 1

    def wrap_at(sc):
        lines, cur = [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if text_width(t, sc) <= width:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    for sc in range(max_scale, 0, -1):
        if any(text_width(w, sc) > width for w in words):
            continue                      # a single word won't fit; go smaller
        lines = wrap_at(sc)
        if len(lines) <= max_lines:
            return lines, sc

    # nothing fits whole — show as much as two small lines can carry, minus
    # any dangling article, because "BEYOND THE" reads as a broken string
    kept = [clip_text(l, 1, width) for l in wrap_at(1)[:max_lines]]
    while kept and kept[-1].split() and kept[-1].split()[-1] in FILLER:
        kept[-1] = kept[-1][:kept[-1].rfind(" ")] if " " in kept[-1] else ""
        if not kept[-1]:
            kept.pop()
    return kept, 1


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


# ---------------------------------------------------------------- project screens

BR_COL_W = 24        # usable width per column, keeps digits off the divider


def br_canvas():
    img = Image.new("RGB", (64, 64), BR_NAVY)
    return img, ImageDraw.Draw(img)


def br_bar(d, color, word):
    d.rectangle([0, 0, 63, BAR_H], fill=color)
    sc = min(BAR_SCALE, fit_scale(word, BAR_SCALE))
    draw_centered(d, word, (BAR_H - text_height(sc)) // 2 + 1, BR_NAVY, sc)


def br_pair(d, left, right):
    """Two labelled numbers either side of a rule. Both take the same scale so
    they read as a pair rather than one long number."""
    d.line([32, 20, 32, 55], fill=BR_LINE)

    def fits(v):
        for sc in range(4, 0, -1):
            if text_width(v, sc) <= BR_COL_W:
                return sc
        return 1

    vals = [str(left[1]), str(right[1])]
    nsc = min(fits(v) for v in vals)
    y = 28 + (20 - text_height(nsc)) // 2
    for (label, _), val, cx in zip((left, right), vals, (16, 48)):
        draw_text(d, label, cx - text_width(label, 1) // 2, 21, BR_MUTED, 1)
        draw_text(d, val, cx - text_width(val, nsc) // 2, y, BR_CREAM, nsc)


def br_footer(d, label):
    t = clock_str()
    draw_text(d, t, 2, LABEL_Y, BR_MUTED, 1)
    avail = 62 - (2 + text_width(t, 1) + 4)
    while label and text_width(label, 1) > avail:
        label = label[:label.rfind(" ")] if " " in label else label[:-1]
    if label:
        draw_text(d, label, 62 - text_width(label, 1), LABEL_Y, BR_MUTED, 1)


# ---------------------------------------------------------------- screens

def screen_flag(dirt, bath, wx, cal):
    """All three tracks at once. The bar carries tonight's headline; the rows
    say what each track is doing, so a dark Fonda is as visible as a green
    Albany. When nothing is running, the soonest track is lit — three equally
    dim rows make you read all of them to find the one that matters."""
    bar_color, bar_text, word = STATES.get(dirt["state"], STATES["standby"])
    img, d = canvas()

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

        d.rectangle([0, y, 2, y + ROW_H - 3], fill=risk_chip(r))
        draw_text(d, r["code"], 6, y + 1, DUST if (live or hot) else SLATE, 2)

        # two fixed columns so a 3-char code and a 3-char day never collide
        draw_text(d, r["when"], 34, y + 3,
                  SODIUM if (live or hot) else SLATE, 1)
        if r["prob"] is not None:
            p = f"{r['prob']}%"
            draw_text(d, p, 62 - text_width(p, 1), y + 3,
                      DUST if (live or hot) else SLATE, 1)

    draw_footer(d, dirt["label"])
    return img


def screen_traffic(dirt, bath, wx, cal):
    """Who showed up. The bar is direction, so the headline is the change
    rather than a number you have to compare against memory."""
    img, d = br_canvas()
    x = bath["delta"]
    if x > 0:
        br_bar(d, BR_UP, f"UP {x}")
    elif x < 0:
        br_bar(d, BR_DOWN, f"DOWN {abs(x)}")
    else:
        br_bar(d, BR_TEAL, "FLAT")

    br_pair(d, ("USERS", bath["users"]), ("NEW", bath["new_users"]))
    br_footer(d, f"{commas(bath['week_sessions'])} WK")
    return img


def screen_health(dirt, bath, wx, cal):
    """Quality rather than volume. Silent when things are fine, loud when
    they aren't — Clarity logged 11 script errors on a day nobody noticed."""
    img, d = br_canvas()

    kind, word = bath["health"]
    if kind != "ok":
        br_bar(d, BR_DOWN, word)                     # bake failed or went stale
    elif bath["errors"]:
        br_bar(d, BR_DOWN, f"{bath['errors']} ERROR" +
               ("S" if bath['errors'] != 1 else ""))
    else:
        br_bar(d, BR_TEAL, "NO ERRORS")

    # "62S" reads as "625" in this font, so the unit lives in the label
    br_pair(d, ("ACT SEC", bath["engage"]), ("BOTS", bath["bots"]))
    br_footer(d, f"{bath['dead']} DEAD")
    return img


def screen_weather(dirt, bath, wx, cal):
    img, d = canvas()
    c, tc, word = wx_bar(wx)

    # sprite and word travel together as one centred group
    d.rectangle([0, 0, 63, BAR_H], fill=c)
    tw = text_width(word, BAR_SCALE)
    x = (64 - (SPRITE_W + 3 + tw)) // 2
    draw_sprite(d, word, x, 3, tc)
    draw_text(d, word, x + SPRITE_W + 3, 4, tc, BAR_SCALE)
    stack(d, f"H {wx['high']}°  L {wx['low']}°", f"FEELS {wx['feels']}°",
          f"{wx['temp']}°", f"{wx['rain']}% {wx['wind']}MPH",
          big_color=DUST)
    return img


PLAY_ICON  = ["#..", "##.", "###", "##.", "#.."]
PAUSE_ICON = ["#.#", "#.#", "#.#", "#.#", "#.#"]

MARQUEE_FRAMES = 56       # device tops out around 60; leave headroom
MARQUEE_PXPS   = 18       # pixels per second — slow enough to read
MARQUEE_GAP    = 12       # blank run between the end and the wrap-around
RAIL_Y         = 49       # progress rail
TITLE_Y        = 52       # the row that scrolls
INFO_Y         = 58       # who's watching, and the year or episode


def draw_icon(d, rows, x, y, color):
    for ry, row in enumerate(rows):
        for rx, c in enumerate(row):
            if c == "#":
                d.point((x + rx, y + ry), fill=color)


def scrim(img, bands):
    """Darken bands of rows so type reads over artwork, leaving the rest of
    the poster alone. bands is [(y0, y1, alpha_at_y0, alpha_at_y1), ...]."""
    over = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    for y0, y1, a0, a1 in bands:
        span = max(1, y1 - y0)
        for y in range(y0, y1 + 1):
            a = int(a0 + (a1 - a0) * (y - y0) / span)
            od.line([0, y, 63, y], fill=(0, 0, 0, max(0, min(255, a))))
    return Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")


def text_mask(s):
    """Render a line of text to a 1-bit mask, width whatever it needs.

    A mask rather than a bitmap: pasting an RGB strip would drop an opaque
    black box over the poster. The marquee slices windows out of this instead
    of re-rendering the type on every frame.
    """
    w = max(1, text_width(s, 1))
    m = Image.new("L", (w, GH), 0)
    draw_text(ImageDraw.Draw(m), s, 0, 0, 255, 1)
    return m


def paste_text(img, mask, xy, color):
    img.paste(Image.new("RGB", mask.size, color), xy, mask)


def screen_jfnow(dirt, bath, wx, cal):
    """What's on. The poster gets the panel; the title runs small along the
    bottom so nothing covers the art.

    Returns a list of frames when the line is too long to sit still — the
    device loops them itself, so a marquee costs one burst of POSTs instead
    of a frame every 80ms for the whole dwell.
    """
    jf = JF or DEMO_JF

    if not jf["playing"]:
        # rotation() drops this screen when nothing is playing, so this only
        # shows if playback stopped between picking the screen and drawing it
        img, d = canvas()
        sc = min(fit_scale("NOTHING", 2), fit_scale("PLAYING", 2))
        draw_bar(d, LOAM, "IDLE", SLATE, rule=True)
        draw_centered(d, "NOTHING", 26, SLATE, sc)
        draw_centered(d, "PLAYING", 26 + text_height(sc) + 4, SLATE, sc)
        draw_footer(d, "")
        return img

    art = None
    if jf.get("art_id"):
        import jellyfin as _jf
        art = _jf.poster(JELLYFIN_URL, JELLYFIN_KEY, jf["art_id"])

    # ---- everything that doesn't move, drawn once
    base, d = canvas()
    if art is not None:
        base.paste(art.resize((64, 64)), (0, 0))
        base = scrim(base, [(44, 50, 0, 175), (51, 63, 175, 225)])
        d = ImageDraw.Draw(base)

    pct = jf["pct"]
    rail = SODIUM if jf["paused"] else JF_BLUE
    if pct is not None:
        d.rectangle([2, RAIL_Y, 61, RAIL_Y + 1], fill=RAIL)
        w = int(59 * pct / 100)
        if w:
            d.rectangle([2, RAIL_Y, 2 + w, RAIL_Y + 1], fill=rail)

    # bottom row holds the things that fit: glyph, name, and the year or
    # episode. Only the title is long enough to need moving.
    draw_icon(d, PAUSE_ICON if jf["paused"] else PLAY_ICON, 1, INFO_Y, rail)
    sub = clip_text(jf["sub"], 1, 24) if jf["sub"] else ""
    if sub:
        draw_text(d, sub, 62 - text_width(sub, 1), INFO_Y, SODIUM, 1)
    room = 62 - 6 - (text_width(sub, 1) + 4 if sub else 0)
    who = clip_text(jf["user"], 1, room)
    if who:
        draw_text(d, who, 6, INFO_Y, DUST, 1)

    # ---- the title, still if it fits and scrolling if it doesn't
    title = (jf["title"] or "").upper()
    mask = text_mask(title)
    window = 62

    if mask.width <= window:
        paste_text(base, mask, (1 + (window - mask.width) // 2, TITLE_Y), DUST)
        return base

    loop = mask.width + MARQUEE_GAP
    step = max(1, -(-loop // MARQUEE_FRAMES))      # ceil, so we stay under cap

    # a double-wide tape means each window is a straight crop, no wrap maths
    tape = Image.new("L", (loop * 2, GH), 0)
    tape.paste(mask, (0, 0))
    tape.paste(mask, (loop, 0))

    frames = []
    for off in range(0, loop, step):
        f = base.copy()
        paste_text(f, tape.crop((off, 0, off + window, GH)), (1, TITLE_Y), DUST)
        frames.append(f)

    return MarqueeFrames(frames, max(40, int(1000 * step / MARQUEE_PXPS)))


class MarqueeFrames(list):
    """A list of frames that also carries its playback speed, so push() can
    tell an animation from a still without changing every screen's signature."""

    def __init__(self, frames, speed_ms):
        super().__init__(frames)
        self.speed_ms = speed_ms


def screen_jflib(dirt, bath, wx, cal):
    """The library at a glance. Streams drive the bar, because the counts
    only move on a scan and the streams move all evening."""
    jf = JF or DEMO_JF
    img, d = canvas()

    n = jf["streams"]
    if n:
        draw_bar(d, JF_PURPLE, f"{n} STREAM" + ("S" if n > 1 else ""), JF_INK)
    else:
        draw_bar(d, LOAM, "JELLYFIN", SLATE, rule=True)

    stack(d, f"{commas(jf['movies'])} MOVIES",
          f"{commas(jf['series'])} SERIES",
          commas(jf["episodes"]), "EPISODES", big_color=JF_BLUE)
    return img


SCREENS = {
    "flag": screen_flag,
    "weather": screen_weather,
    "traffic": screen_traffic,
    "health": screen_health,
    "jfnow": screen_jfnow,
    "jflib": screen_jflib,
}


# ---------------------------------------------------------------- rotation

def is_race_night(now=None):
    now = now or dt.datetime.now()
    return now.weekday() in RACE_DAYS and RACE_WINDOW[0] <= now.hour < RACE_WINDOW[1]


def rotation(now=None):
    """(screen, seconds) pairs.

    Jellyfin only appears while something is playing. An idle media server
    has nothing to say that's worth a slot — the counts move once a week.
    """
    now = now or dt.datetime.now()
    watching = bool(JF and JF.get("playing"))

    if is_race_night(now):
        r = [("flag", 30), ("weather", 10)]
        return r + [("jfnow", 12)] if watching else r
    if watching:
        return [("jfnow", 24), ("flag", 12), ("weather", 12)]
    if MORNING[0] <= now.hour < MORNING[1]:
        return [("weather", 20), ("flag", 16)]
    return [("flag", 16), ("weather", 16)]


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


def refresh_jf():
    """Sessions change by the second and the server is on localhost, so this
    runs every frame rather than riding the five-minute fetch. Counts are
    cached inside the module, so this is one cheap call most of the time."""
    global JF
    try:
        import jellyfin as _jf
        JF = _jf.build(_jf.sessions(JELLYFIN_URL, JELLYFIN_KEY),
                       _jf.counts(JELLYFIN_URL, JELLYFIN_KEY))
    except Exception as e:
        print(f"jellyfin fetch failed ({e})", file=sys.stderr)


def fetch():
    """Adjust the two mappings below to match your real JSON.
    Nothing else in the file needs to change."""
    refresh_jf()
    ev_doc = _get(f"{DIRTCALL_BASE}/events.json", None, "dirtcall events")
    st_doc = _get(f"{DIRTCALL_BASE}/status.json", None, "dirtcall status")
    raw_b = None                    # BathroomReport screens are out of the rotation
    raw_w = _get(WEATHER_URL, None, "weather")

    if ev_doc and st_doc:
        import dirtcall
        dirt = dirtcall.build(ev_doc, st_doc)
        dirt["rows"] = dirtcall.track_rows(ev_doc, st_doc)
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

    return dirt, bath, wx, {}


def brightness_now():
    h = dt.datetime.now().hour
    return NIGHT_BRIGHTNESS if (h >= NIGHT_START or h < NIGHT_END) else DAY_BRIGHTNESS


# ---------------------------------------------------------------- main

def connect():
    if not PIXOO_IP:
        sys.exit("PIXOO_IP is not set in config.py. Find it in the Divoom app "
                 "under your device's settings, or run:\n"
                 "  curl -s -X POST https://app.divoom-gz.com/Device/ReturnSameLANDevice")
    from pixoo_client import Pixoo
    return Pixoo(PIXOO_IP)


def push(dev, img):
    dev.set_brightness(brightness_now())
    if isinstance(img, list):
        dev.push_frames(img, getattr(img, "speed_ms", 100))
    else:
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
        dirt, bath, wx, cal = DEMO_DIRT, DEMO_BATH, DEMO_WX, {}
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
        for name in ("weather", "traffic", "health"):
            SCREENS[name](dirt, bath, wx, cal).save(
                os.path.join(args.preview, f"{name}.png"))
            print(name)

        global JF
        for label, state in (
            ("jf-playing", DEMO_JF),
            ("jf-paused", {**DEMO_JF, "paused": True, "pct": 61}),
            ("jf-idle", {**DEMO_JF, "playing": False, "streams": 0}),
        ):
            JF = state
            out = SCREENS["jfnow"](dirt, bath, wx, cal)
            if isinstance(out, list):
                out[0].save(os.path.join(args.preview, f"{label}.gif"),
                            save_all=True, append_images=out[1:],
                            duration=getattr(out, "speed_ms", 100), loop=0)
                print(f"{label} ({len(out)} frames)")
            else:
                out.save(os.path.join(args.preview, f"{label}.png"))
                print(label)
        JF = DEMO_JF
        SCREENS["jflib"](dirt, bath, wx, cal).save(
            os.path.join(args.preview, "jflib.png"))
        print("jflib")
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
                else:
                    refresh_jf()                        # local, so poll it often
                push(dev, SCREENS[name](dirt, bath, wx, cal))
                time.sleep(dwell)

    ap.print_help()


if __name__ == "__main__":
    main()
