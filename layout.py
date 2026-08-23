#!/usr/bin/env python3
"""
layout.py — the wall board's slots, and the editor that arranges them.

This replaces a free-positioning grid editor. That version let any module sit
anywhere, which meant two could occupy the same cells, which meant one of them
silently vanished. Everything built afterwards — priority numbers, push-aside,
collision detection, warning banners, a shipped tie-break order — existed only
to manage a hazard the model itself created. On a phone the cells were 11px
tall and the resize grip was bigger than the card it resized.

The model here is a slot: a rectangle on the grid holding an ordered list of
modules. Slots cannot overlap — the server rejects it — so only one thing is
ever visible in a given place and the whole class of bugs disappears.

Two modes:

  single / takeover   the first module in the list with something to show
                      wins and keeps the slot until it has nothing. List
                      order is the priority; there is no priority field.
                      Now-playing over Gallery is this.

  rotate              cycle through the modules that have something to show,
                      dwell seconds each. Services and Pi-hole is this.

A module with nothing to show is skipped in both modes, so an off-season card
never rotates in blank.
"""

import json
import os
import re
import time

try:
    from config import DATA_DIR
except ImportError:
    DATA_DIR = "/var/www/html/data"

COLS, ROWS = 12, 18
LAYOUT = os.path.join(DATA_DIR, "layout.json")

# key, label, description, min width, min height (in cells).
#
# The minimums are floors on usefulness, not on rendering: the cards now
# scale their type and drop detail as they shrink, so a small one still looks
# right — it just stops answering the question it exists for. Racing needs
# height for six rows before they collapse into an unreadable stack; the
# radar below 3x3 is a blurry crop with all three pins on top of each other.
# key, label, description, min w, min h, max w, max h, min aspect, max aspect.
#
# Aspect is the rendered box in PIXELS, wide over tall — not cells. A grid cell
# is 138x39px, so it is 3.56:1 on its own and cell counts say almost nothing
# about shape: 3x1 is 11.7:1, 3x2 is 4.7:1, and 3x8 is square. Judging shape by
# cell count is how a card ends up in a box it cannot lay out in.
#
# The maximum is a different kind of limit from the minimum. A card below its
# minimum stops answering the question it exists for. A card outside its aspect
# range still renders, but renders badly — a row-based layout in a tall slot is
# a short strip floating in an empty box.
MODULES = [
    ("flag",     "Flag strip",  "Racing tonight, rained out, or standby", 4, 2, 12,  8,  3.0, 40.0),
    ("racing",   "Racing",      "3 dirt tracks and 3 NASCAR series",      4, 5, 12, 18,  0.55, 4.5),
    # Min height is 5, not 3: below 5 rows (~260px) the three-day forecast
    # hides itself, and below that a weather card that's just a temperature
    # and a condition word doesn't answer the question it's there for.
    ("weather",  "Weather",     "Now, three-day forecast and conditions", 3, 5, 12, 18,  0.40, 6.0),
    ("radar",    "Radar",       "Rain moving in, with the three tracks",  3, 3, 12, 18,  0.50, 6.0),
    ("wire",     "Wire",        "Headlines from your RSS feeds",          4, 1, 12,  6,  5.0, 50.0),
    # media's ceiling on height is the honest one: the card is flex-direction
    # row, so a tall slot gets a wide strip floating in space. Raise this when
    # the portrait layout exists, not before.
    # Height ceiling raised from 8 to 18 and the aspect range widened from a
    # wide-strip-only 3.0-35.0 to 0.5-35.0. The strip layout (row of compact
    # cells) still renders for a wide slot or multiple simultaneous viewers;
    # a slot tall enough to be portrait-ish now gets a poster-forward hero
    # layout instead — which needs the room this used to refuse to allow.
    # 0.5 as the floor, not gallery's 0.667, so media can go slightly taller
    # than a bare poster to fit the title/progress block underneath it.
    ("media",    "Now playing", "Jellyfin — only while something streams",4, 2, 12, 18,  0.5, 35.0),
    ("services", "Services",    "Uptime Kuma health",                     3, 1, 12,  8,  2.0, 50.0),
    ("pihole",   "Pi-hole",     "Share of DNS blocked today, and traffic",4, 1, 12,  8,  3.0, 50.0),
    # Gallery is deliberately the loosest: framing is stored per image as a
    # focal point and zoom, so it genuinely adapts to any shape. Aspect is
    # bounded to 2:3-3:2 rather than left wide open — a slot shaped nothing
    # like a photo or a phone video just means more of every image is
    # letterboxed padding instead of picture.
    ("gallery",  "Gallery",     "Photos, GIFs and clips you've uploaded", 3, 2, 12, 18,  0.6667, 1.5),
    # 2x2 is a real floor, not a courtesy: below that the hour and minute stop
    # fitting on one line and the thing reads as two numbers, not a time.
    ("clock",    "Clock",       "Time and date, ticking locally",         2, 2, 12, 10,  0.60, 12.0),
    ("system",   "Pi health",   "Temperature, load, memory and disks",    4, 2, 12,  8,  1.5, 25.0),
    ("net",      "Network",     "Link, latency, loss and throughput",     4, 2, 12,  8,  1.5, 25.0),
]
MINS = {m[0]: (m[3], m[4]) for m in MODULES}
MAXES = {m[0]: (m[5], m[6]) for m in MODULES}
ASPECT = {m[0]: (m[7], m[8]) for m in MODULES}
KEYS = [m[0] for m in MODULES]
LABELS = {m[0]: m[1] for m in MODULES}

MODES = ["single", "takeover", "rotate"]
DWELL_MIN, DWELL_MAX = 5, 300

# Per-module settings, declared so the editor renders controls for anything
# without knowing what it is, and so clean_opts can clamp from one table.
#   (key, label, kind, spec, default)   kind: range | chips | multi
OPTS = {
    "gallery": [
        ("dwell", "Seconds per item", "range", (3, 120), 8),
        ("order", "Order", "chips",
         [("shuffle", "Shuffle"), ("sequence", "In order")], "shuffle"),
        ("show", "Include", "multi",
         [("still", "Photos"), ("video", "Video & GIFs")], ["still", "video"]),
        ("fit", "Framing", "chips",
         [("contain", "Whole image"), ("cover", "Fill slot")], "cover"),
    ],
    "clock": [
        ("fmt", "Format", "chips",
         [("12", "12-hour"), ("24", "24-hour")], "12"),
        ("secs", "Seconds", "chips",
         [("off", "Hide"), ("on", "Show")], "off"),
        ("date", "Date line", "chips",
         [("long", "Tuesday, August 18"), ("short", "Tue Aug 18"),
          ("off", "Hide")], "long"),
    ],
    "wire": [
        ("dwell", "Seconds per set", "range", (6, 120), 22),
        ("rows", "Headlines shown", "range", (1, 5), 3),
        ("mode", "Motion", "chips",
         [("fade", "Cross-fade"), ("marquee", "Marquee")], "fade"),
    ],
    "radar": [
        ("tracks", "Track pins", "chips",
         [("show", "Show"), ("hide", "Hide")], "show"),
    ],
}

# Applies to every module, on top of whatever's in OPTS above — this is the
# knob for "the text stayed tiny even after I made the card huge" or the
# opposite. --u (see index.html) already scales type to a card's actual
# rendered size automatically; this multiplies on top of that per-module,
# for the cases where the automatic curve still isn't what a specific card
# at a specific size needs. 100 is the untouched default; the clamp() floors
# and ceilings each module declares in index.html still apply underneath
# it, so this shifts within that module's designed range rather than
# escaping it entirely.
UNIVERSAL_OPTS = [
    ("scale", "Text size", "range", (70, 140), 100),
    # Recolors the sodium-toned accents inside a module — headers, progress
    # fills, badges — not the whole card. Excludes exactly two things on
    # purpose: #cornerclock (the fallback widget when the clock module
    # isn't in any slot at all — outside the module system entirely, has
    # no opts to read) and the weather alert band, whose colour is tied to
    # NWS severity rather than decoration; overriding it would make a
    # Tornado Warning as visually calm as a Frost Advisory.
    ("accent", "Accent color", "color", None, "#e8b93f"),
    # Row-to-row spacing — only meaningfully touches the six modules that
    # actually have rows of repeated content (racing, wire, weather's
    # forecast, system/net's stat lines, pihole, services, and media's
    # multi-viewer strip). Applies everywhere for the same reason scale
    # does: a module without rows just won't visibly respond to it, which
    # is simpler and more predictable than a UI where some modules
    # mysteriously lack a control the rest have.
    ("rowgap", "Row spacing", "range", (50, 200), 100),
]

# Floor for a slot holding nothing yet.
SLOT_MIN = (3, 1)
SLOT_MAX = (COLS, ROWS)

# The stage is a fixed 1920x1080 surface that gets scaled to whatever screen
# it lands on, so shape can be reasoned about in absolute pixels. The gap is
# configurable, but the constraint maths uses the nominal 20 — the aspect
# ranges are far wider than the difference a few pixels of gutter makes.
STAGE_W, STAGE_H, NOMINAL_GAP = 1920, 1080, 20


def box_px(w, h, gap=NOMINAL_GAP):
    """Rendered size of a w x h slot, in stage pixels."""
    tw = (STAGE_W - 2 * gap - (COLS - 1) * gap) / COLS
    th = (STAGE_H - 2 * gap - (ROWS - 1) * gap) / ROWS
    return w * tw + (w - 1) * gap, h * th + (h - 1) * gap


def aspect(w, h, gap=NOMINAL_GAP):
    bw, bh = box_px(w, h, gap)
    return (bw / bh) if bh > 0 else 0.0


def slot_max(modules):
    """A slot may be no larger than its most restrictive occupant allows.

    Min takes the max across occupants and max takes the min, so a shared slot
    is the intersection of what everything in it can live with.
    """
    w, h = SLOT_MAX
    for k in modules or []:
        mw, mh = MAXES.get(k, SLOT_MAX)
        w, h = min(w, mw), min(h, mh)
    return w, h


def slot_aspect(modules):
    """Intersection of the occupants' shape ranges.

    Can come back empty — Wire wants at least 5:1 and Racing at most 4.5:1, so
    there is no box both are happy in. Callers check lo <= hi and refuse the
    pairing rather than picking a shape that suits neither.
    """
    lo, hi = 0.0, 1e9
    for k in modules or []:
        a, b = ASPECT.get(k, (0.0, 1e9))
        lo, hi = max(lo, a), min(hi, b)
    return lo, hi


def fit_shape(w, h, modules):
    """Clamp a slot to its occupants' size and shape limits.

    Shrink-only on the aspect correction. Growing a slot to fix its shape can
    push it into a neighbour, and sanitize drops overlapping slots outright —
    so a well-meant correction would cost the user their modules. A slightly
    wrong shape is a much smaller price.
    """
    mw, mh = slot_min(modules)
    xw, xh = slot_max(modules)
    xw, xh = max(mw, xw), max(mh, xh)          # min always wins a conflict
    w = clamp(w, mw, min(xw, COLS))
    h = clamp(h, mh, min(xh, ROWS))

    lo, hi = slot_aspect(modules)
    if lo > hi:
        return w, h                            # incompatible; leave it alone
    guard = 0
    while aspect(w, h) > hi and w > mw and guard < 64:
        w -= 1
        guard += 1
    while aspect(w, h) < lo and h > mh and guard < 64:
        h -= 1
        guard += 1
    return w, h


def slot_min(modules):
    """A slot must fit its largest occupant on each axis.

    Taking the max rather than the first module's minimum matters for shared
    slots: Now-playing over Services is 4x2 and 3x1, and sizing that slot to
    the Services minimum would leave the poster with nowhere to go the moment
    something started playing.
    """
    w, h = SLOT_MIN
    for k in modules or []:
        mw, mh = MINS.get(k, SLOT_MIN)
        w, h = max(w, mw), max(h, mh)
    return w, h

DEFAULT = {
    "slots": [
        {"id": "banner", "col": 1, "row": 1, "w": 12, "h": 3,
         "mode": "single", "dwell": 20, "modules": ["flag"]},
        # Racing in season, gallery the rest of the year. This is the winter
        # fallback: from November to February both racing sources are dark at
        # once, and without something underneath, half the board goes blank.
        {"id": "main", "col": 1, "row": 4, "w": 7, "h": 10,
         "mode": "takeover", "dwell": 20, "modules": ["racing", "gallery"]},
        {"id": "sky", "col": 8, "row": 4, "w": 5, "h": 6,
         "mode": "single", "dwell": 20, "modules": ["weather"]},
        {"id": "map", "col": 8, "row": 10, "w": 5, "h": 4,
         "mode": "single", "dwell": 20, "modules": ["radar"]},
        {"id": "news", "col": 1, "row": 14, "w": 12, "h": 2,
         "mode": "single", "dwell": 20, "modules": ["wire"]},
        {"id": "strip", "col": 1, "row": 16, "w": 12, "h": 3,
         "mode": "takeover", "dwell": 18,
         "modules": ["media", "services", "pihole"]},
    ],
    "off": [],
    "opts": {},
    "gap": 20,
    "ts": 0,
}


def clamp(v, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return lo


def boxes_overlap(a, b):
    return (a["col"] < b["col"] + b["w"] and b["col"] < a["col"] + a["w"] and
            a["row"] < b["row"] + b["h"] and b["row"] < a["row"] + a["h"])


def clean_opts(key, got):
    out = {}
    for name, _label, kind, spec, default in UNIVERSAL_OPTS + OPTS.get(key, []):
        v = got.get(name, default)
        if kind == "range":
            out[name] = clamp(v, spec[0], spec[1])
        elif kind == "chips":
            allowed = [a for a, _b in spec]
            out[name] = v if v in allowed else default
        elif kind == "multi":
            allowed = [a for a, _b in spec]
            picked = [x for x in (v if isinstance(v, list) else []) if x in allowed]
            # Empty means a module with nothing to show, which reads as broken.
            out[name] = picked or list(default)
        elif kind == "color":
            # #rrggbb only. A posted value could be anything — this isn't a
            # trust boundary in the security sense (only this box's own
            # editor ever posts here), but a malformed value would land
            # straight in a CSS custom property and silently do nothing
            # instead of showing an error, which is a worse failure mode
            # than just falling back to the default.
            out[name] = v if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v) else default
    return out


def sanitize(doc):
    """Force a posted layout into something the renderer cannot choke on.

    Clamps rather than rejects wherever it can: a slightly-wrong board beats
    a blank wall that needs SSH to fix. The one thing it will not do is let
    slots overlap — that is the hazard this whole model exists to remove, so
    an overlapping slot is dropped and its modules fall to the off tray.
    """
    slots, used, seen_mod, ids = [], [], set(), set()

    for i, s in enumerate(doc.get("slots") or []):
        if not isinstance(s, dict):
            continue

        mods = []
        for k in (s.get("modules") or []):
            # A module can only live in one slot; a duplicate would be two
            # cards rendering the same DOM node in two places.
            if k in KEYS and k not in seen_mod:
                mods.append(k)

        # Size is clamped against the slot's own occupants, so a module never
        # ends up in a box smaller than it can say anything in. Computed
        # before the box because the answer depends on what is inside.
        mw, mh = slot_min(mods)
        w, h = fit_shape(s.get("w", mw), s.get("h", mh), mods)
        col = clamp(s.get("col", 1), 1, COLS - w + 1)
        row = clamp(s.get("row", 1), 1, ROWS - h + 1)
        box = {"col": col, "row": row, "w": w, "h": h}
        if any(boxes_overlap(box, u) for u in used):
            continue                      # its modules end up off, below

        # Only now are these modules really placed: an overlapping slot is
        # dropped above, and marking them earlier would strand them nowhere.
        seen_mod.update(mods)
        used.append(box)

        sid = str(s.get("id") or "slot%d" % (i + 1))[:24]
        while sid in ids:
            sid += "_"
        ids.add(sid)

        mode = s.get("mode") if s.get("mode") in MODES else "single"
        if len(mods) > 1 and mode == "single":
            mode = "takeover"             # single with a list is meaningless
        slots.append({
            "id": sid, "mode": mode,
            "dwell": clamp(s.get("dwell", 20), DWELL_MIN, DWELL_MAX),
            "modules": mods, **box,
        })

    # Empty slots are pointless but harmless; drop them so the board isn't
    # reserving cells for nothing.
    slots = [s for s in slots if s["modules"]]

    off = [k for k in KEYS if k not in seen_mod]
    # Every module, not just the ones with entries in OPTS — that used to
    # mean 9 of 12 modules had no saved opts at all, which was fine while
    # OPTS was the only thing that lived there, but UNIVERSAL_OPTS applies
    # to everything, so everything needs a slot to save it in now.
    opts = {k: clean_opts(k, (doc.get("opts") or {}).get(k) or {})
            for k in KEYS}
    return {"slots": slots, "off": off, "opts": opts,
            "gap": clamp(doc.get("gap", 20), 0, 60), "ts": int(time.time())}


def load():
    try:
        with open(LAYOUT) as f:
            doc = json.load(f)
        # A file from the old free-grid schema has "modules" and no "slots".
        # Rather than guess at a translation, fall back to defaults: the
        # arrangement is one screen of dragging to redo, and a half-migrated
        # board is far more confusing than a fresh one.
        if "slots" not in doc:
            return sanitize(DEFAULT)
        return sanitize(doc)
    except Exception:
        return sanitize(DEFAULT)


def save(doc):
    os.makedirs(DATA_DIR, exist_ok=True)
    clean = sanitize(doc)
    tmp = LAYOUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(clean, f, separators=(",", ":"))
    os.replace(tmp, LAYOUT)          # atomic: the board never reads a half file
    return clean


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Board layout</title>
<style>
:root{--bg:#100f0d;--panel:#1b1917;--sunk:#232120;--rail:#37342f;
      --dust:#f0ebe0;--slate:#8a8378;--sodium:#e8b93f;--green:#5fbf5a;--red:#d9534a}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--dust);font:16px/1.4 system-ui,-apple-system,sans-serif;
     padding:14px;padding-bottom:96px}
h1{font-size:20px;font-weight:800;letter-spacing:.04em;text-transform:uppercase}
.sub{color:var(--slate);font-size:13px;margin:4px 0 14px}

/* Preview only. Slots are positioned here, but modules are never dragged on
   it — that was the unusable part. Assignment happens in the list below,
   where the targets are full-width rows. */
#canvas{position:relative;width:100%;aspect-ratio:16/9;background:#000;
        border:1px solid var(--rail);border-radius:10px;overflow:hidden;
        touch-action:none}
#grid{position:absolute;inset:0;pointer-events:none;opacity:.45}
.slot{position:absolute;background:rgba(27,25,23,.92);border:1.5px solid var(--rail);
      border-radius:6px;overflow:hidden;touch-action:none;
      display:flex;align-items:center;justify-content:center;text-align:center}
.slot .nm{font-size:10.5px;font-weight:700;letter-spacing:.05em;
          text-transform:uppercase;padding:2px;pointer-events:none;
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.slot.sel{border-color:var(--sodium);background:#2a2723;z-index:5}
.slot.bad{border-color:var(--red);background:#2a1414}
/* Uncovered cells. Hatched rather than filled so they read as absence,
   not as another card. Only regions worth caring about get a size label. */
.gap{position:absolute;pointer-events:none;border-radius:4px;
     background:repeating-linear-gradient(45deg,#ffffff08 0 6px,transparent 6px 12px)}
.gap.big{background:repeating-linear-gradient(45deg,#d9534a26 0 6px,transparent 6px 12px);
         outline:1px dashed #d9534a66;outline-offset:-2px;
         display:flex;align-items:center;justify-content:center}
.gap.big span{font-size:10px;font-weight:700;letter-spacing:.08em;color:#d9534a;
              background:#1b1917cc;padding:1px 5px;border-radius:3px}
#gaps{margin:12px 0 0;padding:10px 12px;border-radius:9px;background:var(--sunk);
      border:1px solid var(--rail);font-size:13px;color:var(--slate)}
#gaps b{color:var(--dust)}
#gaps.on{background:#241a18;border-color:#d9534a66;color:#e0b6b1}
#gaps.on b{color:#fff}
/* Four corner grips. Dragging the top or left edge moves the slot's origin as
   well as its size, so the opposite corner stays put — grabbing the top-left
   and pulling up should extend the slot upward, not drag the whole thing.

   28px is the visual size; the hit area is padded out to 44px with a negative
   inset so it clears the recommended touch target on a phone without drawing
   a grip that swamps a small slot. */
.grip{position:absolute;width:28px;height:28px;opacity:0;touch-action:none}
.grip:before{content:"";position:absolute;inset:-8px;}
.grip.se{right:0;bottom:0;
  background:linear-gradient(135deg,transparent 46%,var(--sodium) 46%)}
.grip.sw{left:0;bottom:0;
  background:linear-gradient(45deg,var(--sodium) 46%,transparent 46%)}
.grip.ne{right:0;top:0;
  background:linear-gradient(45deg,transparent 54%,var(--sodium) 54%)}
.grip.nw{left:0;top:0;
  background:linear-gradient(135deg,var(--sodium) 54%,transparent 54%)}
.slot.sel .grip{opacity:1}
/* A slot only 1-2 rows tall is shorter than two stacked grips, and they end up
   overlapping in the middle where neither is reachable. Below that, keep the
   two on the bottom edge only. */
.slot.short .grip.ne, .slot.short .grip.nw{display:none}

.bar{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 6px;align-items:center}
button{background:var(--sunk);color:var(--dust);border:1px solid var(--rail);
       border-radius:8px;padding:11px 15px;font-size:15px;font-weight:600}
button.go{background:var(--sodium);color:#241c05;border-color:var(--sodium)}
button.warn{color:var(--red)}
button:disabled{opacity:.4}

h2{font-size:13px;letter-spacing:.16em;text-transform:uppercase;color:var(--sodium);
   margin:20px 0 8px}
.sl{border:1px solid var(--rail);border-radius:10px;margin-bottom:10px;
    background:var(--panel);overflow:hidden}
.sl.sel{border-color:var(--sodium)}
.slhead{display:flex;align-items:center;gap:10px;padding:11px 12px;background:var(--sunk)}
.slhead .t{flex:1;min-width:0;font-weight:700;font-size:15px}
.slhead .d{font-size:11.5px;color:var(--slate);font-weight:400}
.modes{display:flex;gap:0;border:1px solid var(--rail);border-radius:7px;overflow:hidden}
.modes span{padding:7px 10px;font-size:12px;background:var(--sunk)}
.modes span.on{background:var(--sodium);color:#241c05;font-weight:700}
.chips{display:flex;flex-wrap:wrap;gap:8px;padding:12px;min-height:58px}
/* Big targets: this is the whole point of the rebuild. */
.chip{display:flex;align-items:center;gap:8px;padding:11px 13px;border-radius:9px;
      background:var(--sunk);border:1px solid var(--rail);font-size:14.5px;
      font-weight:600;touch-action:none}
.chip.drag{opacity:.45}
.chip .ord{font-family:ui-monospace,monospace;font-size:11px;color:var(--sodium)}
/* Same removal moveMod() already does for a drag to Off, one tap away —
   this editor already asks for a resize drag, a slot-move drag, and a
   module drag; a fourth drag just to take something off the board is one
   too many gestures for what should be the simplest action here. */
.chip .rm{margin-left:auto;color:var(--slate);font-size:17px;line-height:1;
          padding:9px 11px;border-radius:5px;min-width:24px;min-height:24px;
          display:inline-flex;align-items:center;justify-content:center}
.chip .rm:hover,.chip .rm:active{background:var(--rail);color:var(--red)}
.chips.over{background:#241f14;outline:2px dashed var(--sodium);outline-offset:-6px}
.empty{color:var(--slate);font-size:13px;padding:4px 2px}
.dwell{padding:0 12px 12px;display:none}
.sl[data-mode="rotate"] .dwell{display:block}
.dwell .lb{display:flex;justify-content:space-between;font-size:13px;
           color:var(--slate);margin-bottom:6px}
.dwell .lb b{color:var(--dust)}
input[type=range]{width:100%;accent-color:var(--sodium);height:30px}
#off{border-style:dashed}
.opt{padding:12px;border-top:1px solid var(--rail)}
/* The actual index.html, actual stylesheet, actual renderX() functions —
   embedded rather than reimplemented, so a preview can't quietly drift
   from what the real board would show the way a hand-copied mockup could.
   16:9 always, regardless of the module's real slot shape: matching that
   exactly would mean tracking each module's current aspect and resizing
   the iframe to fit, for a benefit — pixel-perfect current-slot framing —
   this doesn't promise. What it does promise is size, color and spacing,
   the three things that prompted building this at all, and those read
   correctly at any reasonable box shape. */
.preview{aspect-ratio:16/9;background:#000;border-bottom:1px solid var(--rail);
         overflow:hidden}
.preview iframe{width:100%;height:100%;border:0;display:block;
                 pointer-events:none}
.opt .lb{display:flex;justify-content:space-between;font-size:13.5px;
         color:var(--slate);margin-bottom:8px}
.opt .lb b{color:var(--dust)}
.ochips{display:flex;gap:8px;flex-wrap:wrap}
.ochip{flex:1;min-width:92px;text-align:center;padding:10px 8px;border-radius:8px;
       border:1px solid var(--rail);background:var(--sunk);font-size:13.5px}
.ochip.on{background:var(--sodium);color:#241c05;border-color:var(--sodium);font-weight:700}
#status{position:fixed;left:0;right:0;bottom:0;background:var(--sunk);
        border-top:1px solid var(--rail);padding:12px 14px;font-size:14px;display:flex;gap:10px}
#status .msg{flex:1;min-width:0;color:var(--slate)}
#status .msg.ok{color:var(--green)} #status .msg.bad{color:var(--red)}
</style></head><body>

<h1>Board layout</h1>
<div class="sub">Drag a slot to move it. Tap to select, then drag its corner to resize.</div>

<div id="canvas"><canvas id="grid"></canvas></div>

<div id="gaps"></div>

<div class="bar">
  <button id="undo" disabled>Undo</button>
  <button id="fill" disabled>Fill gaps</button>
  <button id="addslot">Add slot</button>
  <button id="delslot" disabled>Delete slot</button>
  <button id="reset" class="warn">Defaults</button>
  <button id="save" class="go" style="margin-left:auto">Save</button>
</div>

<h2>Slots</h2>
<div id="slots"></div>

<h2>Not shown</h2>
<div class="sl" id="offwrap"><div class="chips" id="off" data-slot="__off__"></div></div>

<h2 id="optshead" style="display:none">Module settings</h2>
<div id="opts"></div>

<div id="status"><span class="msg" id="msg">Loaded</span></div>

<script>
const COLS = {{COLS}}, ROWS = {{ROWS}};
const MODULES = {{MODULES}};
const OPTS = {{OPTS}};
const UNIVERSAL_OPTS = {{UNIVERSAL_OPTS}};
const SLOT_MIN = {{SLOT_MIN}};
const SLOT_MAX = {{SLOT_MAX}};
const MINS = {{MINS}};
const MAXES = {{MAXES}};
const ASPECT = {{ASPECT}};
const STAGE = {{STAGE}};

/* These mirror layout.py exactly. The editor enforcing the same rules as the
   server is the whole point: anything it lets you build should survive Save
   untouched, so you never see a slot quietly change shape after you saved it.

   Shape is measured on the rendered box in pixels, not in cells. A grid cell
   is 138x39, so it is 3.56:1 on its own — 3x1 is 11.7:1 and 3x8 is square. */
function boxPx(w, h){
  const [SW, SH, G] = STAGE;
  const tw = (SW - 2*G - (COLS-1)*G) / COLS;
  const th = (SH - 2*G - (ROWS-1)*G) / ROWS;
  return [w*tw + (w-1)*G, h*th + (h-1)*G];
}
function ratio(w, h){ const [bw, bh] = boxPx(w, h); return bh > 0 ? bw/bh : 0; }

function slotMax(mods){
  let w = SLOT_MAX[0], h = SLOT_MAX[1];
  (mods || []).forEach(k => {
    const m = MAXES[k]; if(!m) return;
    w = Math.min(w, m[0]); h = Math.min(h, m[1]);
  });
  return [w, h];
}

/* Intersection of the occupants' shape ranges. Comes back empty for a pairing
   with no box that suits both — Wire wants 5:1 or wider, Racing 4.5:1 or
   narrower — and callers refuse the pairing rather than split the difference. */
function slotAspect(mods){
  let lo = 0, hi = 1e9;
  (mods || []).forEach(k => {
    const a = ASPECT[k]; if(!a) return;
    lo = Math.max(lo, a[0]); hi = Math.min(hi, a[1]);
  });
  return [lo, hi];
}

/* Size and shape clamp. Shrink-only on the aspect pass, same as the server:
   growing a slot to fix its shape can push it into a neighbour, and that
   costs the user a whole slot to fix a cosmetic problem. */
function fitShape(w, h, mods){
  const [mw, mh] = slotMin(mods);
  let [xw, xh] = slotMax(mods);
  xw = Math.max(mw, xw); xh = Math.max(mh, xh);
  w = Math.max(mw, Math.min(Math.min(xw, COLS), w));
  h = Math.max(mh, Math.min(Math.min(xh, ROWS), h));
  const [lo, hi] = slotAspect(mods);
  if(lo > hi) return [w, h];
  let g = 0;
  while(ratio(w, h) > hi && w > mw && g++ < 64) w--;
  while(ratio(w, h) < lo && h > mh && g++ < 64) h--;
  return [w, h];
}

/* Why a given size was refused, in words. "Too wide" is meaningless without
   saying what it is too wide for. */
/* Names whichever module(s) actually set the binding limit, so hitting a
   wall reads as "Clock caps this at 5x4" instead of an unexplained refusal.
   Width and height are judged separately — two modules can tie on one axis
   (both maxed out at the grid's own ceiling of 12 wide, say) while only one
   of them is the real reason the other axis is stuck. Merging the two axes
   into one owner-set was the bug: it let an unrelated tie mask the actual
   culprit. Only speaks up when an axis isn't unanimous — if every occupant
   agrees there's nothing surprising to explain. */
function limitNote(mods){
  if(!mods || mods.length < 2) return "";
  const [mw, mh] = slotMin(mods), [xw, xh] = slotMax(mods);
  const owners = (table, idx, val) =>
    mods.filter(k => table[k] && table[k][idx] === val).map(label);
  const bits = [];
  const collect = (verb, table, wv, hv) => {
    const names = new Set();
    owners(table, 0, wv).forEach(n => { if (owners(table,0,wv).length < mods.length) names.add(n); });
    owners(table, 1, hv).forEach(n => { if (owners(table,1,hv).length < mods.length) names.add(n); });
    if(names.size) bits.push(verb + " by " + [...names].join(", "));
  };
  collect("floor set", MINS, mw, mh);
  collect("capped", MAXES, xw, xh);
  return bits.length ? " \u2014 " + bits.join("; ") : "";
}

function shapeWhy(w, h, mods){
  const [mw, mh] = slotMin(mods), [xw, xh] = slotMax(mods);
  if(w < mw || h < mh) return "needs at least " + mw + "\u00d7" + mh + limitNote(mods);
  if(w > xw || h > xh) return "no bigger than " + xw + "\u00d7" + xh + limitNote(mods);
  const [lo, hi] = slotAspect(mods);
  if(lo > hi) return "these modules want shapes that don't overlap";
  const r = ratio(w, h);
  if(r > hi) return "too wide for its height";
  if(r < lo) return "too tall for its width";
  return "";
}

function slotMin(mods){
  let w = SLOT_MIN[0], h = SLOT_MIN[1];
  (mods || []).forEach(k => {
    const m = MINS[k]; if(!m) return;
    w = Math.max(w, m[0]); h = Math.max(h, m[1]);
  });
  return [w, h];
}
const DEFAULTS = {{DEFAULTS}};
let L = {{LAYOUT}};
let sel = null, dirty = false, optFor = null, history = [];

/* Undo. Fill, delete and drops all move things the user didn't touch
   directly, so there has to be a way back that isn't "reset everything". */
function snapshot(){
  history.push(JSON.stringify(L));
  if(history.length > 30) history.shift();
  const b = document.getElementById("undo");
  if(b) b.disabled = false;
}

const $ = s => document.querySelector(s);
const canvas = $("#canvas");
const label = k => (MODULES.find(m => m[0] === k) || [k, k])[1];
const slotOf = id => L.slots.find(s => s.id === id);
const mark = (t, c) => { const m = $("#msg"); m.textContent = t; m.className = "msg " + (c||""); };
const touch = t => { dirty = true; mark(t || "Unsaved changes"); };

function cell(){ return {w: canvas.clientWidth / COLS, h: canvas.clientHeight / ROWS}; }

function drawGrid(){
  const c = $("#grid"), r = window.devicePixelRatio || 1;
  c.width = canvas.clientWidth * r; c.height = canvas.clientHeight * r;
  c.style.width = canvas.clientWidth + "px"; c.style.height = canvas.clientHeight + "px";
  const x = c.getContext("2d"); x.scale(r, r);
  x.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  x.strokeStyle = "#37342f"; x.lineWidth = .5;
  const cs = cell();
  for(let i = 1; i < COLS; i++){ x.beginPath(); x.moveTo(i*cs.w,0); x.lineTo(i*cs.w,canvas.clientHeight); x.stroke(); }
  for(let j = 1; j < ROWS; j++){ x.beginPath(); x.moveTo(0,j*cs.h); x.lineTo(canvas.clientWidth,j*cs.h); x.stroke(); }
}

function overlaps(a, b){
  return a.col < b.col+b.w && b.col < a.col+a.w && a.row < b.row+b.h && b.row < a.row+a.h;
}
function collides(s){
  return L.slots.some(o => o !== s && overlaps(s, o));
}

function place(el, s){
  const cs = cell();
  el.style.left   = ((s.col-1)*cs.w + 1) + "px";
  el.style.top    = ((s.row-1)*cs.h + 1) + "px";
  el.style.width  = (s.w*cs.w - 2) + "px";
  el.style.height = (s.h*cs.h - 2) + "px";
}

function buildCanvas(){
  canvas.querySelectorAll(".slot").forEach(n => n.remove());
  L.slots.forEach(s => {
    const el = document.createElement("div");
    el.className = "slot" + (sel === s.id ? " sel" : "") + (collides(s) ? " bad" : "");
    el.dataset.id = s.id;
    const names = s.modules.map(label).join(" / ") || "empty";
    el.innerHTML = `<span class="nm">${names}</span>` +
      ["nw","ne","sw","se"].map(c => `<span class="grip ${c}" data-corner="${c}"></span>`).join("");
    if(s.h <= 2) el.classList.add("short");
    place(el, s);
    canvas.appendChild(el);
  });
}

function buildSlots(){
  $("#slots").innerHTML = L.slots.map(s => `
    <div class="sl ${sel===s.id?"sel":""}" data-mode="${s.mode}" data-id="${s.id}">
      <div class="slhead">
        <div class="t">${s.w}&times;${s.h} at ${s.col},${s.row}
          <div class="d">${s.modules.length} module${s.modules.length===1?"":"s"}${
            (() => { const [mw,mh] = slotMin(s.modules), [xw,xh] = slotMax(s.modules);
                     if(!s.modules.length) return "";
                     /* Both ends, always, once anything is in the slot: seeing
                        only the floor is what makes a ceiling feel like a bug
                        when you hit it. limitNote names the culprit when the
                        stack doesn't agree on the number. */
                     return ` &middot; ${mw}×${mh} to ${xw}×${xh}${limitNote(s.modules)}`; })()}${
            (() => { const w = shapeWhy(s.w, s.h, s.modules);
                     return w ? ` &middot; <b class="warn">${w}</b>` : ""; })()}</div></div>
        <div class="modes" data-id="${s.id}">
          <span data-mode="single"   class="${s.mode==="single"?"on":""}">One</span>
          <span data-mode="takeover" class="${s.mode==="takeover"?"on":""}">Takeover</span>
          <span data-mode="rotate"   class="${s.mode==="rotate"?"on":""}">Rotate</span>
        </div>
      </div>
      <div class="chips" data-slot="${s.id}">
        ${s.modules.length
          ? s.modules.map((k,i) => `<div class="chip" draggable="true" data-mod="${k}">
              ${s.mode!=="rotate" ? `<span class="ord">${i+1}</span>` : ""}${label(k)}<span class="rm" data-rm="${k}" title="Remove">&times;</span></div>`).join("")
          : `<div class="empty">Drag a module here</div>`}
      </div>
      <div class="dwell">
        <div class="lb"><span>Seconds each</span><b>${s.dwell}</b></div>
        <input type="range" min="{{DMIN}}" max="{{DMAX}}" value="${s.dwell}" data-dwell="${s.id}">
      </div>
    </div>`).join("");
  $("#off").innerHTML = L.off.length
    ? L.off.map(k => `<div class="chip" draggable="true" data-mod="${k}">${label(k)}</div>`).join("")
    : `<div class="empty">Everything is on the board</div>`;
}

// Created once per module, on first need, then moved (never recreated) into
// each rebuild's fresh placeholder div — appendChild relocates an existing
// node rather than destroying it, so the iframe's live browsing context
// (already past its previewReady handshake, already showing the fixture)
// survives every buildOpts() rebuild instead of reloading from scratch on
// every unrelated slider drag or slot resize.
const previewFrames = {};

// control.py serves this editor on its own port; the wall page the preview
// needs to embed is nginx's, on the default port — cross-origin from here,
// which is exactly what postMessage exists for.
function previewSrc(k){
  return location.protocol + "//" + location.hostname + "/?preview=" + encodeURIComponent(k);
}

function previewFrame(k){
  if(previewFrames[k]) return previewFrames[k];
  const f = document.createElement("iframe");
  f.src = previewSrc(k);
  f.loading = "lazy";
  f.title = label(k) + " preview";
  previewFrames[k] = f;
  return f;
}

function sendPreviewOpts(k){
  const f = previewFrames[k];
  if(!f || !f.contentWindow) return;
  const o = (L.opts && L.opts[k]) || {};
  f.contentWindow.postMessage({type:"previewOpts", opts:o}, "*");
}

window.addEventListener("message", e => {
  if(!e.data || e.data.type !== "previewReady") return;
  sendPreviewOpts(e.data.module);
});

function buildOpts(){
  // Every module placed in a slot gets a section now, not just the three
  // that happen to have entries in OPTS — UNIVERSAL_OPTS (currently just
  // Text size) applies to all twelve, so there's no longer a module with
  // truly nothing to show here.
  const shown = [...new Set(L.slots.flatMap(s => s.modules))];
  $("#optshead").style.display = shown.length ? "" : "none";
  $("#opts").innerHTML = shown.map(k => {
    const o = (L.opts && L.opts[k]) || {};
    const rows = UNIVERSAL_OPTS.concat(OPTS[k] || []).map(([name, lab, kind, spec, dflt]) => {
      const v = (name in o) ? o[name] : dflt;
      if(kind === "range")
        return `<div class="opt"><div class="lb"><span>${lab}</span><b>${v}</b></div>
          <input type="range" min="${spec[0]}" max="${spec[1]}" value="${v}"
                 data-mod="${k}" data-opt="${name}"></div>`;
      if(kind === "color")
        return `<div class="opt"><div class="lb"><span>${lab}</span></div>
          <input type="color" value="${v}" data-mod="${k}" data-opt="${name}" data-color="1"></div>`;
      const cur = kind === "multi" ? (Array.isArray(v)?v:[]) : [v];
      return `<div class="opt"><div class="lb"><span>${lab}</span></div>
        <div class="ochips">${spec.map(([val,l2]) =>
          `<div class="ochip ${cur.includes(val)?"on":""}" data-mod="${k}"
                data-${kind==="multi"?"multi":"opt"}="${name}" data-val="${val}">${l2}</div>`
        ).join("")}</div></div>`;
    }).join("");
    return `<div class="sl"><div class="slhead"><div class="t">${label(k)}</div></div>
      <div class="preview" data-mod="${k}"></div>${rows}</div>`;
  }).join("");
  // The iframes themselves never go through innerHTML — see previewFrame().
  shown.forEach(k => {
    const slot = document.querySelector(`.preview[data-mod="${k}"]`);
    if(slot) slot.appendChild(previewFrame(k));
  });
}

/* ------------------------------------------------------------------ gaps
   Slots are placed by hand and cannot overlap, so the leftovers are real
   holes on the wall. A thin strip along an edge reads as spacing; a big
   region in the middle reads as a card that failed to load, which is what
   makes this worth surfacing at all.

   Empty cells are grouped into rectangles by flood fill and then greedily
   boxed, so the report is "one 6x4 hole" rather than 24 loose cells. */
const GAP_MIN_CELLS = 6;          // below this it looks like breathing room
// Largest hole a single step will absorb, and there is a running budget on
// top. One card extending into a leftover strip is a fill; one card growing
// to cover a quarter of the wall is a rebuild.
const GAP_MAX_FILL = 30;

function coverGrid(){
  const g = Array.from({length: ROWS + 1}, () => new Array(COLS + 1).fill(false));
  L.slots.forEach(s => {
    for(let r = s.row; r < s.row + s.h; r++)
      for(let c = s.col; c < s.col + s.w; c++)
        if(r <= ROWS && c <= COLS) g[r][c] = true;
  });
  return g;
}

/* Greedy maximal rectangles: take the first free cell, extend right as far
   as it stays free, then down as far as the whole width stays free. Not a
   minimal cover, but it names the holes the way a person would. */
function gapRects(){
  const g = coverGrid(), out = [];
  for(let r = 1; r <= ROWS; r++)
    for(let c = 1; c <= COLS; c++){
      if(g[r][c]) continue;
      let w = 0;
      while(c + w <= COLS && !g[r][c + w]) w++;
      let h = 1;
      grow:
      while(r + h <= ROWS){
        for(let i = 0; i < w; i++) if(g[r + h][c + i]) break grow;
        h++;
      }
      for(let rr = r; rr < r + h; rr++)
        for(let cc = c; cc < c + w; cc++) g[rr][cc] = true;
      out.push({col: c, row: r, w, h, cells: w * h});
    }
  return out.sort((a, b) => b.cells - a.cells);
}

function paintGaps(){
  const rects = gapRects();
  const cs = cell();
  canvas.querySelectorAll(".gap").forEach(n => n.remove());
  rects.forEach(g => {
    const el = document.createElement("div");
    el.className = "gap" + (g.cells >= GAP_MIN_CELLS ? " big" : "");
    el.style.left   = ((g.col-1)*cs.w) + "px";
    el.style.top    = ((g.row-1)*cs.h) + "px";
    el.style.width  = (g.w*cs.w) + "px";
    el.style.height = (g.h*cs.h) + "px";
    if(g.cells >= GAP_MIN_CELLS) el.innerHTML = `<span>${g.w}×${g.h}</span>`;
    canvas.insertBefore(el, canvas.firstChild.nextSibling);
  });

  const empty = rects.reduce((n, g) => n + g.cells, 0);
  const total = COLS * ROWS;
  const big = rects.filter(g => g.cells >= GAP_MIN_CELLS);
  const el = $("#gaps");
  el.className = big.length ? "on" : "";
  $("#fill").disabled = !big.length;
  el.innerHTML = big.length
    ? `<b>${Math.round((1 - empty/total) * 100)}% covered</b> &middot; ` +
      big.length + " empty " + (big.length === 1 ? "area" : "areas") +
      " (" + big.map(g => g.w + "×" + g.h).join(", ") + ")"
    : `<b>${Math.round((1 - empty/total) * 100)}% covered</b>` +
      (empty ? " &middot; only thin edges left over" : " &middot; no gaps");
}

/* ------------------------------------------------------------------ fill
   For each hole, find a neighbour that can grow into it without colliding.
   Only whole-edge matches are taken: a slot is stretched into a gap when the
   gap spans its full width or height on the touching side. Partial growth
   would need splitting a slot in two, which is a bigger idea than a button.
*/
function fillGaps(){
  snapshot();                       // fill can move several slots at once
  let filled = 0;
  /* Budget, not a ratio. A ratio cap was tried both ways and neither works:
     measured against the current size it compounds across passes (16 cells
     becomes 32, 64, 128 — the runaway it was meant to stop), and measured
     against the starting size it refuses the ordinary case of a 6-cell slot
     growing back into the 20-cell space it came from.

     An absolute limit gets both right. A leftover strip is small; the void
     left by two tiny slots on an empty board is not. */
  let budget = Math.round(COLS * ROWS * 0.25);
  for(let pass = 0; pass < 6; pass++){
    const rects = gapRects().filter(g => g.cells >= 1);
    if(!rects.length) break;
    let any = false;
    for(const g of rects){
      for(const s of L.slots){
        let want = null;
        // gap directly below, same columns
        if(s.col === g.col && s.w === g.w && s.row + s.h === g.row)
          want = {col:s.col, row:s.row, w:s.w, h:s.h + g.h};
        // directly above
        else if(s.col === g.col && s.w === g.w && g.row + g.h === s.row)
          want = {col:s.col, row:g.row, w:s.w, h:s.h + g.h};
        // to the right, same rows
        else if(s.row === g.row && s.h === g.h && s.col + s.w === g.col)
          want = {col:s.col, row:s.row, w:s.w + g.w, h:s.h};
        // to the left
        else if(s.row === g.row && s.h === g.h && g.col + g.w === s.col)
          want = {col:g.col, row:s.row, w:s.w + g.w, h:s.h};
        if(!want) continue;
        /* A card may extend into a leftover strip; it may not swallow the
           board. Without this, two small slots on an empty grid grow until
           they meet — which is a rebuild, not a fill. Doubling is the most a
           single step can add. */
        if(g.cells > GAP_MAX_FILL || g.cells > budget) continue;
        if(want.col < 1 || want.row < 1 ||
           want.col + want.w - 1 > COLS || want.row + want.h - 1 > ROWS) continue;
        /* Respect the occupants' ceiling and shape range. Without this, Fill
           would grow a card past its limits and the server would shrink it
           back on save — which looks like Fill silently not working. */
        {
          const f = fitShape(want.w, want.h, s.modules);
          if(f[0] !== want.w || f[1] !== want.h) continue;
        }
        if(L.slots.some(o => o !== s && overlaps(want, o))) continue;
        Object.assign(s, want);
        filled += g.cells; budget -= g.cells; any = true;
        break;
      }
      if(any) break;
    }
    if(!any) break;
  }
  if(filled){ touch("Filled " + filled + " cells"); build(); }
  else { history.pop(); $("#undo").disabled = !history.length;
         mark("Nothing next to those gaps can grow into them", "bad"); }
}

function build(){
  buildCanvas(); buildSlots(); buildOpts(); paintGaps();
  /* Was only ever set from the canvas pointerdown handler, so after Add slot,
     Undo or Defaults the button's state described the previous selection. */
  $("#delslot").disabled = !slotOf(sel);
}

/* ------------------------------------------------------------- slot drag
   Pointer events so one path covers finger and mouse. Only slots move on the
   canvas; modules never do. */
let drag = null;
canvas.addEventListener("pointerdown", e => {
  const el = e.target.closest(".slot");
  if(!el){ sel = null; build(); $("#delslot").disabled = true; return; }
  const s = slotOf(el.dataset.id);
  const corner = e.target.dataset ? e.target.dataset.corner : null;
  const resizing = !!corner;
  if(sel !== s.id){ sel = s.id; build(); }
  $("#delslot").disabled = false;
  const node = canvas.querySelector(`.slot[data-id="${s.id}"]`);
  node.setPointerCapture(e.pointerId);
  drag = {s, resizing, corner, x0: e.clientX, y0: e.clientY,
          base: {...s}, node, moved: false};
  e.preventDefault();
});
canvas.addEventListener("pointermove", e => {
  if(!drag) return;
  const cs = cell();
  const dx = Math.round((e.clientX - drag.x0)/cs.w), dy = Math.round((e.clientY - drag.y0)/cs.h);
  if(dx || dy) drag.moved = true;
  const s = drag.s, b = drag.base;
  if(drag.resizing){
    /* Work in edges, not width and height. Whichever corner is held, the
       opposite one is the anchor and must not move — so the drag sets the two
       edges you are holding and leaves the other two where they were. Deriving
       w/h from those edges is what makes a top-left drag extend the slot
       upward instead of sliding the whole thing. */
    const c = drag.corner || "se";
    const west = c[1] === "w", north = c[0] === "n";
    const right = b.col + b.w - 1, bottom = b.row + b.h - 1;

    const left = west ? Math.max(1, Math.min(right, b.col + dx)) : b.col;
    const top  = north ? Math.max(1, Math.min(bottom, b.row + dy)) : b.row;
    const rgt  = west ? right  : Math.max(left, Math.min(COLS, right + dx));
    const bot  = north ? bottom : Math.max(top,  Math.min(ROWS, bottom + dy));

    const wantW = rgt - left + 1, wantH = bot - top + 1;
    const [fw, fh] = fitShape(wantW, wantH, s.modules);
    s.w = fw; s.h = fh;
    /* Re-anchor after the clamp. Without this, a size the limits refused would
       leave the slot pinned to the edge you were dragging, so the whole card
       would creep across the board as you fought the minimum. */
    s.col = west ? right - fw + 1 : left;
    s.row = north ? bottom - fh + 1 : top;
    s.col = Math.max(1, Math.min(COLS - fw + 1, s.col));
    s.row = Math.max(1, Math.min(ROWS - fh + 1, s.row));

    // Say why the handle stopped following the finger, or it reads as a bug.
    drag.pinned = (fw !== wantW || fh !== wantH)
                ? shapeWhy(wantW, wantH, s.modules) : "";
  }else{
    s.col = Math.max(1, Math.min(COLS - s.w + 1, b.col + dx));
    s.row = Math.max(1, Math.min(ROWS - s.h + 1, b.row + dy));
  }
  place(drag.node, s);
  drag.node.classList.toggle("bad", collides(s));
});
function endDrag(){
  if(!drag) return;
  /* Overlap is refused outright rather than resolved. The server would drop
     the slot anyway, and losing one on save is exactly the silent
     disappearance this rebuild exists to prevent. */
  if(drag.moved && collides(drag.s)){
    Object.assign(drag.s, drag.base);
    mark("Slots can't overlap — moved back", "bad");
  }else if(drag.moved){
    if(drag.pinned) mark(drag.pinned, "warn");
    touch();
  }
  drag = null; build();
}
canvas.addEventListener("pointerup", endDrag);
canvas.addEventListener("pointercancel", endDrag);

/* ------------------------------------------------------- module drag/drop
   HTML5 drag for mouse, plus a pointer fallback for touch, which does not
   fire dragstart on any mobile browser worth supporting. */
let carry = null;

/* Grow a slot to fit a module being dropped into it, if there is room.
   Returns false when there isn't — better to refuse the drop than to accept
   it and have the server silently clamp the slot back, or leave a module in
   a box too small to say anything in. */
function growToFit(s, mods){
  const [mw, mh] = slotMin(mods);
  if(s.w >= mw && s.h >= mh) return true;
  const want = {col: s.col, row: s.row, w: Math.max(s.w, mw), h: Math.max(s.h, mh)};
  // Try growing right/down first, then pull the origin back toward 1,1.
  if(want.col + want.w - 1 > COLS) want.col = Math.max(1, COLS - want.w + 1);
  if(want.row + want.h - 1 > ROWS) want.row = Math.max(1, ROWS - want.h + 1);
  if(want.col + want.w - 1 > COLS || want.row + want.h - 1 > ROWS) return false;
  if(L.slots.some(o => o !== s && overlaps(want, o))) return false;
  Object.assign(s, want);
  return true;
}

function moveMod(key, toSlot){
  snapshot();
  const from = L.slots.find(s => s.modules.includes(key));
  if(toSlot !== "__off__"){
    const s = slotOf(toSlot);
    if(!s) return;
    if(s !== from && !growToFit(s, s.modules.concat([key]))){
      history.pop(); $("#undo").disabled = !history.length;
      const why = shapeWhy(s.w, s.h, s.modules.concat([key]));
      mark(label(key) + " doesn't fit that slot — " +
           (why || "no room to grow it"), "bad");
      return;
    }
  }
  L.slots.forEach(s => { const i = s.modules.indexOf(key); if(i >= 0) s.modules.splice(i,1); });
  L.off = L.off.filter(k => k !== key);
  if(toSlot === "__off__") L.off.push(key);
  else {
    const s = slotOf(toSlot);
    s.modules.push(key);
    if(s.modules.length > 1 && s.mode === "single") s.mode = "takeover";
  }
  // Removing the largest occupant leaves the slot oversized, which is fine —
  // shrinking it automatically would move things the user didn't ask to move.
  touch(); build();
}

document.addEventListener("dragstart", e => {
  const c = e.target.closest(".chip");
  if(!c) return;
  carry = c.dataset.mod; c.classList.add("drag");
  e.dataTransfer.effectAllowed = "move";
  try{ e.dataTransfer.setData("text/plain", carry); }catch(_){}
});
document.addEventListener("dragend", () => {
  document.querySelectorAll(".chip.drag").forEach(c => c.classList.remove("drag"));
  document.querySelectorAll(".chips.over").forEach(c => c.classList.remove("over"));
  carry = null;
});
document.addEventListener("dragover", e => {
  const z = e.target.closest(".chips");
  if(!z || !carry) return;
  e.preventDefault();
  document.querySelectorAll(".chips.over").forEach(c => c.classList.remove("over"));
  z.classList.add("over");
});
document.addEventListener("drop", e => {
  const z = e.target.closest(".chips");
  if(!z || !carry) return;
  e.preventDefault();
  moveMod(carry, z.dataset.slot); carry = null;
});

/* Touch path: track the finger and drop on whatever zone is under it. */
let tdrag = null;
document.addEventListener("pointerdown", e => {
  if(e.pointerType === "mouse") return;
  // A tap that starts on the × has to be excluded here, not just handled
  // correctly by the click handler below — otherwise a tiny bit of touch
  // jitter (real touchscreens rarely register a tap at exactly 0 movement)
  // clears the 8px threshold this same drag path uses, and the whole chip
  // starts a drag-and-drop-back-into-its-own-slot alongside the actual
  // removal. Both fire, both call moveMod(), both push an undo snapshot —
  // one tap ends up two undo-steps deep, for a button whose entire point
  // was to be a single unambiguous action.
  if(e.target.closest(".rm")) return;
  const c = e.target.closest(".chip");
  if(!c) return;
  tdrag = {key: c.dataset.mod, node: c, x: e.clientX, y: e.clientY, moved: false};
  c.setPointerCapture(e.pointerId);
});
document.addEventListener("pointermove", e => {
  if(!tdrag) return;
  if(Math.abs(e.clientX-tdrag.x) + Math.abs(e.clientY-tdrag.y) > 8){
    tdrag.moved = true; tdrag.node.classList.add("drag");
  }
  if(!tdrag.moved) return;
  e.preventDefault();
  const z = document.elementFromPoint(e.clientX, e.clientY);
  const zone = z && z.closest(".chips");
  document.querySelectorAll(".chips.over").forEach(c => c.classList.remove("over"));
  if(zone) zone.classList.add("over");
});
document.addEventListener("pointerup", e => {
  if(!tdrag) return;
  const z = document.elementFromPoint(e.clientX, e.clientY);
  const zone = z && z.closest(".chips");
  tdrag.node.classList.remove("drag");
  document.querySelectorAll(".chips.over").forEach(c => c.classList.remove("over"));
  if(tdrag.moved && zone) moveMod(tdrag.key, zone.dataset.slot);
  tdrag = null;
});

/* ---------------------------------------------------------------- controls */
$("#slots").addEventListener("click", e => {
  const rm = e.target.closest(".rm");
  if(rm){ moveMod(rm.dataset.rm, "__off__"); return; }
  const m = e.target.closest(".modes span");
  if(m){
    const s = slotOf(e.target.closest(".modes").dataset.id);
    s.mode = m.dataset.mode;
    if(s.mode === "single" && s.modules.length > 1)
      mark("One shows only the first module — the rest stay hidden");
    touch(); build(); return;
  }
  const row = e.target.closest(".sl");
  if(row && row.dataset.id){ sel = row.dataset.id; $("#delslot").disabled = false; build(); }
});
$("#slots").addEventListener("input", e => {
  const id = e.target.dataset.dwell;
  if(!id) return;
  slotOf(id).dwell = +e.target.value;
  e.target.closest(".dwell").querySelector("b").textContent = e.target.value;
  touch();
});

$("#opts").addEventListener("input", e => {
  const k = e.target.dataset.mod, name = e.target.dataset.opt;
  if(!k || !name) return;
  if(e.target.type === "range"){
    L.opts[k] = L.opts[k] || {};
    L.opts[k][name] = +e.target.value;
    e.target.closest(".opt").querySelector("b").textContent = e.target.value;
    touch();
  }else if(e.target.type === "color"){
    // No <b> readout to update here — the swatch itself is the readout.
    L.opts[k] = L.opts[k] || {};
    L.opts[k][name] = e.target.value;
    touch();
  }else{
    return;   // not one of the two kinds this handler knows how to save
  }
  // Live, on every drag tick — not just once buildOpts() next happens to
  // run for some unrelated reason. This only covers the universal knobs
  // (scale/rowgap/accent); a module's own chip options (gallery's Fill
  // slot, clock's 24-hour) aren't threaded into the fixture, so the
  // preview won't react to those specifically — a real gap, not
  // pretended away.
  sendPreviewOpts(k);
});
$("#opts").addEventListener("click", e => {
  const c = e.target.closest(".ochip");
  if(!c) return;
  const k = c.dataset.mod;
  L.opts[k] = L.opts[k] || {};
  if(c.dataset.multi){
    const n = c.dataset.multi;
    const cur = Array.isArray(L.opts[k][n]) ? L.opts[k][n].slice() : [];
    const i = cur.indexOf(c.dataset.val);
    if(i < 0) cur.push(c.dataset.val); else cur.splice(i,1);
    if(!cur.length){ mark("Keep at least one", "bad"); return; }
    L.opts[k][n] = cur;
  }else L.opts[k][c.dataset.opt] = c.dataset.val;
  touch(); buildOpts();
});

$("#undo").onclick = () => {
  if(!history.length) return;
  L = JSON.parse(history.pop());
  $("#undo").disabled = !history.length;
  sel = null; touch("Undone"); build();
};

$("#fill").onclick = fillGaps;

/* Add slot.

   The old version looked for one free 4x3 box and, finding none, did nothing
   at all — no slot, no message. On the default layout that is always: the six
   default slots cover all 216 cells, so the button appeared dead on a fresh
   board. Three things were wrong and all three are fixed here: it now tries
   progressively smaller boxes, it takes room from a neighbour when the board
   is genuinely full, and it says so when it truly cannot. */
const ADD_SIZES = [[4,3],[4,2],[3,2],[SLOT_MIN[0],SLOT_MIN[1]]];

function newSlot(box){
  const id = "slot" + Date.now().toString(36).slice(-4) +
             Math.floor(Math.random()*36).toString(36);
  L.slots.push({id, mode:"single", dwell:20, modules:[], ...box});
  return id;
}

function freeBox(){
  for(const [w, h] of ADD_SIZES)
    for(let r = 1; r + h - 1 <= ROWS; r++)
      for(let c = 1; c + w - 1 <= COLS; c++){
        const cand = {col:c, row:r, w, h};
        if(!L.slots.some(s => overlaps(cand, s))) return cand;
      }
  return null;
}

/* Take the room from the largest slot that can spare it — largest because a
   big card loses proportionally least, and only if the donor still satisfies
   its own size and shape limits afterwards. */
function carveBox(){
  let best = null;
  for(const s of L.slots){
    const [mw, mh] = slotMin(s.modules);
    for(const [w, h] of ADD_SIZES){
      if(s.h - h >= mh){
        const f = fitShape(s.w, s.h - h, s.modules);
        if(f[0] === s.w && f[1] === s.h - h && (!best || s.w*s.h > best.cells))
          best = {cells:s.w*s.h, s, nw:s.w, nh:s.h - h,
                  box:{col:s.col, row:s.row + s.h - h, w:Math.min(w, s.w), h}};
      }
      if(s.w - w >= mw){
        const f = fitShape(s.w - w, s.h, s.modules);
        if(f[0] === s.w - w && f[1] === s.h && (!best || s.w*s.h > best.cells))
          best = {cells:s.w*s.h, s, nw:s.w - w, nh:s.h,
                  box:{col:s.col + s.w - w, row:s.row, w, h:Math.min(h, s.h)}};
      }
    }
  }
  return best;
}

$("#addslot").onclick = () => {
  snapshot();                        // adding was not undoable before
  const free = freeBox();
  if(free){
    sel = newSlot(free);
    touch("Slot added — drag a module into it"); build(); return;
  }
  const cut = carveBox();
  if(cut){
    cut.s.w = cut.nw; cut.s.h = cut.nh;
    sel = newSlot(cut.box);
    touch("Slot added — room taken from " +
          (cut.s.modules.map(label).join(" / ") || cut.s.id)); build(); return;
  }
  history.pop(); $("#undo").disabled = !history.length;
  mark("No room for another slot — shrink or delete one first", "bad");
};
$("#delslot").onclick = () => {
  const s = slotOf(sel);
  if(!s) return;
  snapshot();
  s.modules.forEach(k => L.off.push(k));
  L.slots = L.slots.filter(x => x !== s);
  sel = null; $("#delslot").disabled = true;
  touch("Slot deleted — its modules moved to Not shown"); build();
};
$("#reset").onclick = () => {
  if(!confirm("Reset the layout to defaults?")) return;
  snapshot();
  L = JSON.parse(JSON.stringify(DEFAULTS));
  sel = null; touch("Defaults restored — not saved yet"); build();
};
$("#save").onclick = async () => {
  $("#save").disabled = true; mark("Saving…");
  try{
    const r = await fetch("/layout/save", {method:"POST",
      headers:{"Content-Type":"application/json"}, body: JSON.stringify(L)});
    if(!r.ok) throw new Error(r.status);
    const sentIds = L.slots.map(s => s.id);
    L = await r.json();               // server clamps; take back what it kept
    dirty = false; build();
    /* The server drops slots holding no modules, so an added-but-unfilled slot
       vanishes on save. That is the right behaviour — an empty slot reserves
       cells and shows nothing — but it has to be said out loud, or it reads as
       the save having eaten your work. */
    const kept = new Set(L.slots.map(s => s.id));
    const gone = sentIds.filter(id => !kept.has(id)).length;
    mark(gone
      ? "Saved — " + gone + " empty slot" + (gone===1?"":"s") +
        " dropped; a slot needs a module to survive"
      : "Saved — the wall updates within 10 seconds", gone ? "warn" : "ok");
  }catch(err){ mark("Save failed (" + err.message + ")", "bad"); }
  $("#save").disabled = false;
};

addEventListener("beforeunload", e => { if(dirty){ e.preventDefault(); e.returnValue=""; } });
addEventListener("resize", () => { drawGrid(); buildCanvas(); paintGaps(); });
drawGrid(); build();
</script></body></html>
"""


def page():
    return (PAGE
            .replace("{{COLS}}", str(COLS))
            .replace("{{ROWS}}", str(ROWS))
            .replace("{{MODULES}}", json.dumps([[m[0], m[1], m[2]] for m in MODULES]))
            .replace("{{OPTS}}", json.dumps(OPTS))
            .replace("{{UNIVERSAL_OPTS}}", json.dumps(UNIVERSAL_OPTS))
            .replace("{{SLOT_MIN}}", json.dumps(list(SLOT_MIN)))
            .replace("{{MINS}}", json.dumps(MINS))
            .replace("{{MAXES}}", json.dumps(MAXES))
            .replace("{{ASPECT}}", json.dumps(ASPECT))
            .replace("{{SLOT_MAX}}", json.dumps(list(SLOT_MAX)))
            .replace("{{STAGE}}", json.dumps([STAGE_W, STAGE_H, NOMINAL_GAP]))
            .replace("{{DMIN}}", str(DWELL_MIN))
            .replace("{{DMAX}}", str(DWELL_MAX))
            .replace("{{DEFAULTS}}", json.dumps(sanitize(DEFAULT)))
            .replace("{{LAYOUT}}", json.dumps(load())))


def gallery_shape():
    """Aspect ratio of whatever slot the gallery currently sits in.

    The framing editor has to show the real frame, not a guess — adjusting a
    photo against a 16:9 preview and then seeing it in a 2:3 slot is worse
    than no editor. Falls back to the board's own 16:9 if the gallery is in
    the off tray.
    """
    GAP, PAD = 20, 20
    colw = (1920 - 2*PAD - (COLS-1)*GAP) / COLS
    rowh = (1080 - 2*PAD - (ROWS-1)*GAP) / ROWS
    for s in load()["slots"]:
        if "gallery" in s["modules"]:
            w = s["w"]*colw + (s["w"]-1)*GAP
            h = s["h"]*rowh + (s["h"]-1)*GAP
            return round(w / h, 4)
    return round(16/9, 4)
