#!/usr/bin/env python3
"""
control.py — web panel for the Pixoo board.

Runs as its own service on port 8081, deliberately separate from the display
loop. nginx only serves static files so something has to accept the POST, but
putting that inside board.py would let a bug in a web handler take the board
down — which has already happened once, from a JSON error. If this process
dies the board carries on with whatever was last saved.

Writes screens.json; board.py reads it at the top of every rotation, so a
change lands within one screen. Reads frame.png and state.json, both written
by board.py, for the live mirror and the source health list.
"""

import base64
import binascii
import tempfile
import json
import os
import re
import time
from urllib.parse import parse_qsl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from config import DATA_DIR, HOMEKIT_TOKEN
except ImportError:
    DATA_DIR = "/var/www/html/data"
    HOMEKIT_TOKEN = ""

# The wall board's grid lives in its own module: it has nothing to do with
# the Pixoo rotation this file otherwise manages, and keeping the editor's
# markup out of here stops one page's bug from taking the other down.
import layout as _layout
import wallmedia as _wall
import wallpage as _wallpage

PORT = 8081
STATE = os.path.join(DATA_DIR, "screens.json")

HOMEKIT_FILE = os.path.join(DATA_DIR, "homekit.json")
# A reading older than this reads as stale rather than wrong — a HomePod
# whose Shortcut stopped firing should say so, not freeze on the last number
# it happened to send. Twice the intended post interval gives one missed run
# some slack before the card admits anything.
HOMEKIT_STALE_S = 60 * 60

PHOTO_DIR = os.path.join(DATA_DIR, "photos")
# Upload scratch. Deliberately beside the data rather than in /tmp, which on
# a Pi is often tmpfs — writing a 150MB video there is writing it to RAM,
# which is the exact thing the streaming path exists to avoid.
UPLOAD_TMP = os.path.join(DATA_DIR, "tmp")
# Raised from 12MB now that clips are accepted. A 90-second phone video at
# 1080p lands around 150MB, so this is a real ceiling rather than a formality;
# anything larger should be trimmed before it comes over the wire.
MAX_UPLOAD = 200 * 1024 * 1024
MAX_PHOTOS = 40

SCREENS = [
    ("flag",     "DirtCheck",   "Albany-Saratoga, Lebanon Valley, Fonda"),
    ("weather",  "Weather",     "Now and the three-day forecast"),
    ("nascar",   "NASCAR",      "Next race for Cup, Xfinity and Trucks"),
    ("podium",   "Race field",  "Starting grid or finishing order"),
    ("photo",    "Photos",      "Your uploads, one per turn"),
    ("services", "Services",    "Uptime Kuma \u2014 red when something's down"),
    ("jellyfin", "Now playing", "Only while something is streaming"),
]
KEYS = [k for k, _l, _d in SCREENS]

DWELL_MIN, DWELL_MAX = 4, 90
# Matches board.py's time-of-day plan for a normal afternoon; the slider only
# writes a value once you move it, so untouched screens keep following the
# race-night / morning schedules.
DWELL_HINT = {"flag": 14, "weather": 14, "nascar": 12, "podium": 10,
              "photo": 12, "services": 10, "jellyfin": 18}

DEFAULTS = {"screens": {k: True for k in KEYS}, "pin": None,
            "brightness": "auto", "dwell": {}, "command": None,
            "command_id": 0}


def load():
    try:
        with open(STATE) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    out = dict(DEFAULTS)
    scr = saved.get("screens")
    out["screens"] = {k: bool((scr or {}).get(k, True)) for k in KEYS}
    out["pin"] = saved.get("pin") if saved.get("pin") in KEYS else None
    b = saved.get("brightness", "auto")
    out["brightness"] = b if (b == "auto" or isinstance(b, (int, float))) else "auto"
    dw = saved.get("dwell")
    out["dwell"] = {k: int(v) for k, v in (dw or {}).items()
                    if k in KEYS and str(v).isdigit()}
    out["command_id"] = saved.get("command_id") or 0
    return out


def save(cfg):
    """Returns an error string, or "" on success.

    A failed write used to be invisible: the page re-rendered from the
    unchanged file, so a switch appeared to flip itself back. Now it says so.
    """
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f)
        os.replace(tmp, STATE)      # atomic: board.py never reads half a file
        return ""
    except OSError as e:
        return "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------- homekit

def homekit_readings():
    """Everything a Shortcut has ever posted, keyed by sensor id.

    Read fresh on every request rather than cached in memory — this process
    can go a long time between deploys, and a stale in-memory copy would mean
    a restart is the only way a corrected reading ever shows up.
    """
    try:
        with open(HOMEKIT_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_homekit_reading(sensor_id, label, temp_f, humidity):
    """Merges one sensor's reading into the file rather than overwriting it.

    Each HomePod's Shortcut posts on its own schedule, so a naive overwrite
    would mean the last one to post is the only one that ever shows —
    exactly what happened to the pihole card before it learned to keep
    last-known-good per key instead of per file.
    """
    data = homekit_readings()
    data[sensor_id] = {"label": label, "temp_f": temp_f,
                        "humidity": humidity, "ts": time.time()}
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = HOMEKIT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, HOMEKIT_FILE)


# ---------------------------------------------------------------- photos

def stream_to_disk(rfile, n, dest_dir):
    """Read an upload straight to disk in fixed chunks.

    The old path was `body = self.rfile.read(n)` followed by
    `body.split(boundary)`, which put two full copies of the upload in RAM —
    peak around 2.5x the file. A 150MB phone video therefore cost most of
    400MB on a Pi that is also running Jellyfin, and the ceiling on uploads
    was really "how much before something gets OOM-killed".

    Reading in 256KB chunks makes memory constant regardless of file size, so
    the only real limit left is how long the transcode takes.
    """
    os.makedirs(dest_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(dir=dest_dir, prefix="up_", suffix=".part")
    got = 0
    try:
        with os.fdopen(fd, "wb") as f:
            while got < n:
                chunk = rfile.read(min(262144, n - got))
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path, got


def split_multipart_file(path, boundary):
    """Walk a multipart body on disk, yielding (filename, part_path).

    Same contract as parse_multipart but never holds a whole part in memory:
    each one is copied out to its own temp file as the scan passes it.
    """
    sep = b"--" + boundary.encode()
    out = []
    with open(path, "rb") as f:
        buf, part, name, writing = b"", None, None, False
        while True:
            chunk = f.read(262144)
            if not chunk:
                break
            buf += chunk
            while True:
                i = buf.find(sep)
                if i < 0:
                    # Keep back enough to catch a boundary split across reads.
                    keep = len(sep) + 4
                    if writing and len(buf) > keep:
                        part.write(buf[:-keep])
                        buf = buf[-keep:]
                    break
                if writing:
                    data = buf[:i]
                    if data.endswith(b"\r\n"):
                        data = data[:-2]
                    part.write(data)
                    part.close()
                    out.append((name, part.name))
                    writing = False
                buf = buf[i + len(sep):]
                j = buf.find(b"\r\n\r\n")
                if j < 0:
                    break
                head = buf[:j].decode("utf-8", "replace")
                buf = buf[j + 4:]
                if "filename=" not in head:
                    continue
                name = os.path.basename(head.split('filename="', 1)[-1].split('"', 1)[0])
                if not name:
                    continue
                part = tempfile.NamedTemporaryFile(
                    dir=os.path.dirname(path), prefix="part_", delete=False)
                writing = True
        if writing:
            data = buf
            k = data.find(sep)
            if k >= 0:
                data = data[:k]
            if data.endswith(b"\r\n"):
                data = data[:-2]
            part.write(data)
            part.close()
            out.append((name, part.name))
    return out


def parse_multipart(body, boundary):
    """Pull uploaded files out of a multipart body.

    Hand-rolled because Python 3.13 removed the cgi module, and pulling in a
    dependency for one form would be silly on a box that has to keep working
    unattended.
    """
    out = []
    sep = b"--" + boundary.encode()
    for chunk in body.split(sep):
        head, marker, data = chunk.partition(b"\r\n\r\n")
        if not marker:
            continue
        text = head.decode("utf-8", "replace")
        if "filename=" not in text:
            continue
        name = text.split('filename="', 1)[-1].split('"', 1)[0]
        if not name:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]           # the CRLF that precedes the next part
        if data:
            out.append((os.path.basename(name), data))
    return out


def photo_files():
    try:
        return sorted(f for f in os.listdir(PHOTO_DIR) if f.endswith(".png"))
    except OSError:
        return []


def save_photo(name, blob):
    """Resize on upload, not on display.

    The board reads these inside its rotation loop, so they land as finished
    64x64 PNGs. A whole photo is fitted rather than centre-cropped — cropping
    a group shot to a square usually removes the people — and the gaps are
    filled with a blurred copy so the panel isn't letterboxed in black.
    """
    from PIL import Image, ImageOps, ImageFilter, ImageEnhance
    import io

    src = Image.open(io.BytesIO(blob))
    src = ImageOps.exif_transpose(src).convert("RGB")   # honour phone rotation

    back = ImageOps.fit(src, (64, 64), Image.LANCZOS).filter(
        ImageFilter.GaussianBlur(5))
    back = ImageEnhance.Brightness(back).enhance(0.55)
    front = src.copy()
    front.thumbnail((64, 64), Image.LANCZOS)
    back.paste(front, ((64 - front.width) // 2, (64 - front.height) // 2))

    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(name)[0])[:40] or "photo"
    os.makedirs(PHOTO_DIR, exist_ok=True)

    # Whole-second timestamps collide when several files arrive together, and
    # the second upload silently replaced the first. Walk until the name is
    # free instead.
    base = "%d_%s" % (time.time(), stem)
    path = os.path.join(PHOTO_DIR, base + ".png")
    n = 2
    while os.path.exists(path):
        path = os.path.join(PHOTO_DIR, "%s_%d.png" % (base, n))
        n += 1

    back.save(path, format="PNG")
    return path


def save_cropped(name, blob):
    """Store a 64x64 PNG the browser already composed.

    The editor does the cropping, so this only has to check the size is what
    it claims and pick a free filename — no resizing, no guessing at intent.
    """
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(blob)).convert("RGB")
    if img.size != (64, 64):
        img = img.resize((64, 64), Image.LANCZOS)

    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(name)[0])[:40] or "photo"
    os.makedirs(PHOTO_DIR, exist_ok=True)
    base = "%d_%s" % (time.time(), stem)
    path = os.path.join(PHOTO_DIR, base + ".png")
    k = 2
    while os.path.exists(path):
        path = os.path.join(PHOTO_DIR, "%s_%d.png" % (base, k))
        k += 1
    img.save(path, format="PNG")
    return path


def ago(ts):
    if not ts:
        return "never"
    s = int(time.time() - ts)
    if s < 90:
        return "%ds ago" % s
    if s < 5400:
        return "%dm ago" % round(s / 60)
    return "%dh ago" % round(s / 3600)


def gallery():
    shots = photo_files()
    if not shots:
        return '<div class="empty">No photos yet.</div>'
    return "".join(
        '<div class="shot"><img src="photo/%s" alt="">'
        '<form method="post" action="/delete" style="display:inline">'
        '<input type="hidden" name="f" value="%s">'
        '<button type="submit" title="delete">&times;</button></form></div>'
        % (f, f) for f in shots)


def health_rows():
    try:
        with open(os.path.join(DATA_DIR, "state.json")) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return '<div class="hrow"><span class="dot bad"></span>' \
               '<span class="hname">No snapshot — board not running</span></div>'
    rows = []
    for name, h in sorted((doc.get("health") or {}).items()):
        ok = h.get("ok")
        detail = ago(h.get("last_ok")) if ok else (h.get("error") or "failed")
        rows.append(
            '<div class="hrow"><span class="dot %s"></span>'
            '<span class="hname">%s</span><span class="hval">%s</span></div>'
            % ("ok" if ok else "bad", name, detail))
    return "".join(rows) or '<div class="hrow"><span class="hname">no data yet</span></div>'


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Board control</title>
<style>
:root{--loam:#100f0d;--panel:#1b1917;--sunk:#232120;--rail:#37342f;
      --dust:#f0ebe0;--slate:#8a8378;--sodium:#e8b93f;--green:#5fbf5a;
      --red:#d9534a}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--loam);color:var(--dust);padding:20px 18px 60px;
     font-family:"Barlow Condensed","Arial Narrow",Helvetica,Arial,sans-serif;
     font-size:18px;max-width:560px;margin:0 auto}

/* One column on a phone. On anything wider the three groups sit side by
   side rather than stretching a 560px ribbon down the middle of a monitor. */
.wrap{display:grid;gap:0 26px}
.col{min-width:0}
/* Blocks are placed rather than flowed: the controls column is much taller
   than the rest, so letting them stack in source order leaves a column of
   dead space beside it. */
@media (min-width:900px){
  body{max-width:1500px;padding:28px 30px 60px;font-size:17px}
  h1{font-size:34px}
  .wrap{align-items:start;
        grid-template-columns:minmax(280px,360px) minmax(320px,1fr);
        grid-template-areas:"mir ctl" "pho ctl" "src ctl"}
  .c-mir{grid-area:mir} .c-ctl{grid-area:ctl}
  .c-pho{grid-area:pho} .c-src{grid-area:src}
  .c-mir h2:first-child,.c-ctl h2:first-child{margin-top:0}
  .mirwrap{position:sticky;top:20px}   /* board stays in view while scrolling */
  #mirror{width:290px;height:290px}
}
@media (min-width:1280px){
  .wrap{grid-template-columns:minmax(300px,380px) minmax(320px,1fr) minmax(300px,26rem);
        grid-template-areas:"mir ctl pho" "src ctl pho"}
  .c-pho h2:first-child{margin-top:0}
  .gal{grid-template-columns:repeat(5,1fr)}
}
@media (hover:hover){
  .card{transition:background .15s}
  .row:hover{cursor:default}
  button:hover{filter:brightness(1.08)}
  .chip span:hover{background:var(--rail);color:var(--dust)}
  .chip input:checked + span:hover{background:var(--sodium);color:#12100c}
  .filebtn:hover{background:var(--rail)}
  .shot button{opacity:0;transition:opacity .15s}
  .shot:hover button{opacity:1}
}
/* Touch has no hover, so the delete buttons must always be visible there */
@media (hover:none){.shot button{opacity:1}}
h1{font-size:30px;letter-spacing:.06em;text-transform:uppercase}
h2{font-size:15px;letter-spacing:.2em;text-transform:uppercase;color:var(--slate);
   margin:26px 0 10px;font-family:ui-monospace,Menlo,monospace;font-weight:400}
.sub{color:var(--slate);font-size:14px;letter-spacing:.14em;
     text-transform:uppercase;font-family:ui-monospace,Menlo,monospace}
.card{background:var(--panel);border-radius:14px;padding:14px 16px;margin-bottom:10px}
.row{display:flex;align-items:center;gap:14px}
.txt{flex:1;min-width:0}
.name{font-size:23px;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
.desc{color:var(--slate);font-size:13px;margin-top:2px}

/* Live mirror of the panel on the wall. */
#mirror{display:block;width:230px;height:230px;margin:0 auto;border-radius:12px;
        image-rendering:pixelated;background:var(--sunk)}
.mirwrap{text-align:center;padding:16px}

/* A real checkbox underneath, so it works without JS and keeps keyboard
   and screen-reader behaviour. */
.sw{position:relative;flex:none;width:62px;height:34px}
.sw input{position:absolute;inset:0;opacity:0;margin:0;cursor:pointer;z-index:2}
.trk{position:absolute;inset:0;background:var(--rail);border-radius:17px;
     transition:background .18s}
.knob{position:absolute;top:4px;left:4px;width:26px;height:26px;background:var(--dust);
      border-radius:50%;transition:transform .18s}
.sw input:checked ~ .trk{background:var(--green)}
.sw input:checked ~ .knob{transform:translateX(28px)}
.sw input:disabled ~ .trk{opacity:.4}

.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{position:relative}
.chip input{position:absolute;inset:0;opacity:0;margin:0;cursor:pointer}
.chip span{display:block;padding:10px 14px;border-radius:10px;background:var(--sunk);
           color:var(--slate);font-size:16px;letter-spacing:.08em;
           text-transform:uppercase;font-weight:700}
.chip input:checked + span{background:var(--sodium);color:#12100c}

.hrow{display:flex;align-items:center;gap:10px;padding:7px 0;
      border-bottom:1px solid var(--rail);font-size:15px}
.hrow:last-child{border-bottom:0}
.dot{width:9px;height:9px;border-radius:50%;flex:none;background:var(--slate)}
.dot.ok{background:var(--green)} .dot.bad{background:var(--red)}
.hname{flex:1;font-family:ui-monospace,Menlo,monospace;color:var(--dust)}
.hval{color:var(--slate);font-family:ui-monospace,Menlo,monospace;font-size:13px}

button{width:100%;padding:15px;font-size:19px;font-weight:700;letter-spacing:.1em;
       text-transform:uppercase;color:#12100c;background:var(--sodium);border:0;
       border-radius:13px;cursor:pointer;font-family:inherit;margin-top:14px}
button.ghost{background:var(--sunk);color:var(--dust)}
.pair{display:flex;gap:10px}
.pair button{margin-top:0}
.note{color:var(--slate);font-size:13px;margin-top:18px;line-height:1.55}
.flag{color:var(--sodium);font-size:12px;letter-spacing:.16em}

/* Dwell slider, one per screen */
.dwell{display:flex;align-items:center;gap:12px;margin-top:10px;
       padding-top:10px;border-top:1px solid var(--rail)}
.dwell input{flex:1;accent-color:var(--sodium);height:22px}
.dval{font-family:ui-monospace,Menlo,monospace;font-size:13px;color:var(--slate);
      min-width:3.4em;text-align:right;letter-spacing:.06em}
.card:has(input[type=checkbox]:not(:checked)) .dwell{opacity:.35}

/* Photo editor */
.edhead{display:flex;gap:10px;align-items:baseline;margin:14px 0 8px;
        font-family:ui-monospace,Menlo,monospace;font-size:12px;
        letter-spacing:.14em;color:var(--slate);text-transform:uppercase}
#edname{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.edstage{position:relative;width:300px;max-width:100%;margin:0 auto;
         aspect-ratio:1;background:#000;border-radius:12px;overflow:hidden;
         touch-action:none}      /* drag must not scroll the page */
#edcanvas{width:100%;height:100%;display:block;cursor:grab}
.edframe{position:absolute;inset:0;pointer-events:none;border-radius:12px;
  box-shadow:inset 0 0 0 2px rgba(255,255,255,.5)}
.edframe::before,.edframe::after{content:"";position:absolute;inset:0;
  border:1px dashed rgba(255,255,255,.22)}
.edframe::before{border-width:0 1px;left:33.33%;right:33.33%}
.edframe::after{border-width:1px 0;top:33.33%;bottom:33.33%}
.edmodes{display:flex;gap:8px;margin:12px 0 8px}
.mode{flex:1;margin:0;padding:11px;background:var(--sunk);color:var(--slate);
      font-size:15px}
.mode.on{background:var(--sodium);color:#12100c}
#edzoom{width:100%;accent-color:var(--sodium)}
.edprev{display:flex;align-items:center;gap:12px;margin:12px 0}
#edout{width:64px;height:64px;image-rendering:pixelated;border-radius:8px;
       background:var(--sunk)}

/* Photos */
#pick{position:absolute;opacity:0;width:0;height:0}
.filebtn{display:block;text-align:center;padding:15px;border-radius:13px;
  background:var(--sunk);color:var(--dust);font-size:19px;font-weight:700;
  letter-spacing:.1em;text-transform:uppercase;cursor:pointer}
.gal{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:14px}
.shot{position:relative;aspect-ratio:1}
.shot img{width:100%;height:100%;border-radius:9px;image-rendering:pixelated;
          display:block}
.shot button{position:absolute;top:-6px;right:-6px;width:26px;height:26px;
  margin:0;padding:0;border-radius:50%;background:var(--red);color:#fff;
  font-size:16px;line-height:1;letter-spacing:0}
.empty{color:var(--slate);font-size:14px;padding:10px 0}
</style></head><body>
<h1>Board control</h1>
<div class="sub">{{STATUS}} <span id="saveflag" class="flag"></span></div>
<!-- This page controls the Pixoo rotation. The wall board's grid is a
     different thing entirely, so it gets its own page rather than a section
     wedged into this one. -->
<div class="sub"><a href="/layout" style="color:var(--sodium)">Wall layout &rarr;</a>
  &nbsp;&middot;&nbsp;
  <a href="/gallery" style="color:var(--sodium)">Gallery &amp; framing &rarr;</a></div>

<div class="wrap">
<div class="col c-mir">
<div class="card mirwrap">
  <img id="mirror" src="frame.png" alt="live board">
  <div class="sub" style="margin-top:10px">Live &middot; refreshes every 2s</div>
</div>

</div>

<form method="post" class="col c-ctl">
<h2>Screens</h2>
{{ROWS}}

<h2>Hold one screen</h2>
<div class="card"><div class="chips">{{PINS}}</div></div>

<h2>Brightness</h2>
<div class="card"><div class="chips">{{BRIGHT}}</div></div>

<button class="savebtn" type="submit" name="do" value="save">Save</button>
<div class="pair">
  <button class="ghost" type="submit" name="do" value="refresh">Refresh data</button>
  <button class="ghost" type="submit" name="do" value="restart">Restart board</button>
</div>

</form>

<div class="col c-pho">
<h2>Photos &amp; clips</h2>
<div class="card">
  <div class="sub" style="margin-bottom:8px">Adds to both the Pixoo and the
    wall. For wall-only items and per-photo framing, use
    <a href="/gallery" style="color:var(--sodium)">Gallery</a>.</div>
  <form method="post" enctype="multipart/form-data" action="/upload">
    <!-- image/* alone hides video in the phone picker, so clips could be
         processed but never selected. HEIC needs naming explicitly on iOS. -->
    <input type="file" name="pic" multiple id="pick"
           accept="image/*,video/*,.gif,.heic,.heif,.mov,.mp4,.m4v,.webm">
    <label for="pick" class="filebtn">Choose photos</label>
    <button type="submit" class="plainup">Upload</button>
  </form>
  <div class="sub" id="upstat"></div>

  <div id="editor" hidden>
    <div class="edhead"><span id="edcount"></span><span id="edname"></span></div>
    <div class="edstage">
      <canvas id="edcanvas" width="300" height="300"></canvas>
      <div class="edframe"></div>
    </div>
    <div class="edmodes">
      <button type="button" class="mode on" data-mode="fill">Crop to fill</button>
      <button type="button" class="mode" data-mode="fit">Whole photo</button>
    </div>
    <input type="range" id="edzoom" min="100" max="400" value="100">
    <div class="edprev">
      <canvas id="edout" width="64" height="64"></canvas>
      <span class="sub">Board preview</span>
    </div>
    <div class="pair">
      <button type="button" class="ghost" id="edskip">Skip</button>
      <button type="button" id="edadd">Add to board</button>
    </div>
  </div>
  <div class="gal">{{GALLERY}}</div>
</div>
</div>

<div class="col c-src">
<h2>Sources</h2>
<div class="card">{{HEALTH}}</div>

<p class="note">Changes land within one screen. Holding a screen ignores the
rotation and the on/off switches until you set it back to Off. Turning every
screen off leaves DirtCheck on — a blank board looks broken rather than off.</p>
</div>
</div>

<script>
/* Cache-bust the mirror; the file is rewritten in place every couple of
   seconds and the browser would otherwise sit on the first copy. */
setInterval(() => {
  document.getElementById("mirror").src = "frame.png?t=" + Date.now();
}, 2000);

/* Flipping a switch and then having to find a Save button is a trap — it
   looks like the toggle reverted when really nothing was ever submitted.
   Every control posts itself. The Save button stays for the no-JS case. */
/* Photo editor. Crops in the browser and posts a finished 64x64 PNG, so the
   Pi never handles a 12-megapixel JPEG and you see the exact pixels that will
   land on the board before committing. */
(function editor(){
  const pick = document.getElementById("pick");
  const box = document.getElementById("editor");
  if (!pick || !box) return;

  const stage = document.getElementById("edcanvas");
  const cx = stage.getContext("2d");
  const out = document.getElementById("edout");
  const ox = out.getContext("2d");
  const zoom = document.getElementById("edzoom");

  let queue = [], img = null, mode = "fill", current = null;
  let scale = 1, base = 1, tx = 0, ty = 0;

  function reset(){
    const S = stage.width;
    base = (mode === "fill")
      ? Math.max(S / img.width, S / img.height)     // cover the square
      : Math.min(S / img.width, S / img.height);    // whole photo inside it
    scale = base;
    zoom.value = 100;
    tx = (S - img.width * scale) / 2;
    ty = (S - img.height * scale) / 2;
    draw();
  }

  function clamp(){
    if (mode !== "fill") return;                   // fit mode may sit centred
    const S = stage.width, w = img.width * scale, h = img.height * scale;
    tx = Math.min(0, Math.max(tx, S - w));
    ty = Math.min(0, Math.max(ty, S - h));
  }

  function paint(ctx, size){
    const k = size / stage.width;
    ctx.clearRect(0, 0, size, size);
    if (mode === "fit"){
      // blurred copy behind, so the gaps carry the photo's colour rather
      // than black bars — same treatment the posters get
      const cover = Math.max(size / img.width, size / img.height);
      ctx.save();
      ctx.filter = "blur(" + Math.max(2, size / 14) + "px) brightness(.55)";
      ctx.drawImage(img, (size - img.width * cover) / 2,
                    (size - img.height * cover) / 2,
                    img.width * cover, img.height * cover);
      ctx.restore();
    }
    ctx.drawImage(img, tx * k, ty * k, img.width * scale * k,
                  img.height * scale * k);
  }

  function draw(){ clamp(); paint(cx, stage.width); paint(ox, 64); }

  function load(file){
    document.getElementById("edname").textContent = file.name;
    document.getElementById("edcount").textContent =
      queue.length ? (queue.length + 1) + " left" : "";
    const fr = new FileReader();
    fr.onload = () => {
      img = new Image();
      img.onload = () => { box.hidden = false; reset(); };
      img.src = fr.result;
    };
    fr.readAsDataURL(file);
  }

  function next(){
    if (!queue.length){ box.hidden = true; img = null; current = null; return; }
    current = queue.shift();
    load(current);
  }

  pick.addEventListener("change", async () => {
    const all = Array.from(pick.files || []);
    /* The crop editor draws into a canvas, which can only take an image.
       Clips and GIFs have no 64x64 crop to choose anyway — the Pixoo isn't
       their destination — so they bypass the editor and post straight to
       /upload, where ffmpeg handles them. */
    const isClip = f => /^video\//.test(f.type) || /\.(gif|mp4|mov|m4v|webm)$/i.test(f.name);
    const clips = all.filter(isClip);
    queue = all.filter(f => !isClip(f));

    if (clips.length){
      const st = document.getElementById("upstat");
      st.textContent = "Uploading " + clips.length + " clip" +
                       (clips.length === 1 ? "" : "s") + " — transcoding, this can take a minute…";
      const fd = new FormData();
      clips.forEach(f => fd.append("pic", f));
      try{
        await fetch("/upload", {method: "POST", body: fd});
        st.textContent = "Clips added";
      }catch(e){
        st.textContent = "Clip upload failed";
      }
      if (!queue.length){ location.reload(); return; }
    }
    if (!queue.length) return;
    document.querySelector(".plainup").style.display = "none";
    next();
  });

  zoom.addEventListener("input", () => {
    const S = stage.width, cxp = S / 2, cyp = S / 2;
    const old = scale;
    scale = base * (zoom.value / 100);
    // zoom about the middle of the frame, not the top-left corner
    tx = cxp - (cxp - tx) * (scale / old);
    ty = cyp - (cyp - ty) * (scale / old);
    draw();
  });

  document.querySelectorAll(".mode").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".mode").forEach(b => b.classList.remove("on"));
      btn.classList.add("on");
      mode = btn.dataset.mode;
      if (img) reset();
    });
  });

  let dragging = false, lx = 0, ly = 0;
  stage.addEventListener("pointerdown", e => {
    dragging = true; lx = e.clientX; ly = e.clientY;
    stage.setPointerCapture(e.pointerId);
  });
  stage.addEventListener("pointermove", e => {
    if (!dragging || !img) return;
    const k = stage.width / stage.getBoundingClientRect().width;
    tx += (e.clientX - lx) * k; ty += (e.clientY - ly) * k;
    lx = e.clientX; ly = e.clientY;
    draw();
  });
  stage.addEventListener("pointerup", () => { dragging = false; });

  document.getElementById("edskip").addEventListener("click", next);

  document.getElementById("edadd").addEventListener("click", async () => {
    const body = new URLSearchParams();
    body.set("img", out.toDataURL("image/png"));
    body.set("name", document.getElementById("edname").textContent || "photo");
    await fetch("/crop", {method: "POST", body,
      headers: {"Content-Type": "application/x-www-form-urlencoded"}});
    /* The crop path only ever produced the 64x64 Pixoo tile, so photos added
       this way never reached the wall gallery. Post the untouched original
       too — the wall wants the full-resolution frame, not the crop. */
    if (current){
      const fd = new FormData();
      fd.append("pic", current);
      fetch("/wall/add", {method: "POST", body: fd}).catch(() => {});
    }
    if (queue.length) next(); else location.reload();
  });
})();

(function autosave(){
  const form = document.querySelector("form.c-ctl");
  if (!form) return;
  const flag = document.getElementById("saveflag");

  let timer = null;
  function send(){
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const body = new URLSearchParams(new FormData(form));
      body.set("do", "save");
      flag.textContent = "saving";
      try {
        const r = await fetch("/", {method: "POST", body,
          headers: {"Content-Type": "application/x-www-form-urlencoded"}});
        flag.textContent = r.ok ? "saved" : "save failed";
      } catch (e) {
        flag.textContent = "save failed";
      }
      setTimeout(() => { flag.textContent = ""; }, 1600);
    }, 220);          // coalesce rapid taps into one write
  }

  form.addEventListener("change", e => {
    if (e.target.matches("input[type=checkbox],input[type=radio],input[type=range]"))
      send();
  });

  /* The number tracks the thumb while dragging; the save waits for release,
     so one drag is one write rather than fifty. */
  form.addEventListener("input", e => {
    if (!e.target.matches("input[type=range][data-for]")) return;
    const lab = document.getElementById("dv_" + e.target.dataset.for);
    if (lab) lab.textContent = e.target.value + "s";
  });

  /* Pinning disables the on/off switches server-side; mirror that here so
     the page doesn't need a round trip to look right. */
  form.addEventListener("change", e => {
    if (e.target.name !== "pin") return;
    const pinned = !!e.target.value;
    form.querySelectorAll("input[type=checkbox]").forEach(c => {
      c.disabled = pinned;
    });
  });

  document.querySelector(".savebtn").style.display = "none";
})();
</script>
</body></html>"""

ROW = """<div class="card"><div class="row">
  <div class="txt"><div class="name">{{LABEL}}</div><div class="desc">{{DESC}}</div></div>
  <label class="sw"><input type="checkbox" name="{{KEY}}" {{ON}} {{DIS}}>
    <span class="trk"></span><span class="knob"></span></label>
</div>
<div class="dwell">
  <input type="range" name="d_{{KEY}}" min="{{DMIN}}" max="{{DMAX}}"
         value="{{DVAL}}" data-for="{{KEY}}">
  <span class="dval" id="dv_{{KEY}}">{{DVAL}}s</span>
</div></div>"""

CHIP = """<label class="chip"><input type="radio" name="{{GROUP}}" value="{{VAL}}" {{ON}}>
  <span>{{LABEL}}</span></label>"""


def chips(group, options, current):
    out = []
    for val, label in options:
        out.append(CHIP.replace("{{GROUP}}", group).replace("{{VAL}}", str(val))
                   .replace("{{LABEL}}", label)
                   .replace("{{ON}}", "checked" if str(val) == str(current) else ""))
    return "".join(out)


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype="text/html; charset=utf-8", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _page(self, status):
        cfg = load()
        pinned = cfg["pin"]
        rows = "".join(
            ROW.replace("{{KEY}}", k).replace("{{LABEL}}", l).replace("{{DESC}}", d)
               .replace("{{ON}}", "checked" if cfg["screens"][k] else "")
               .replace("{{DIS}}", "disabled" if pinned else "")
               .replace("{{DMIN}}", str(DWELL_MIN)).replace("{{DMAX}}", str(DWELL_MAX))
               .replace("{{DVAL}}", str(cfg["dwell"].get(k, DWELL_HINT.get(k, 12))))
            for k, l, d in SCREENS)

        body = (PAGE.replace("{{ROWS}}", rows)
                    .replace("{{STATUS}}", status)
                    .replace("{{PINS}}", chips("pin", [("", "Off")] +
                             [(k, l) for k, l, _d in SCREENS], pinned or ""))
                    .replace("{{BRIGHT}}", chips("brightness",
                             [("auto", "Auto"), (10, "10%"), (30, "30%"),
                              (60, "60%"), (100, "100%")], cfg["brightness"]))
                    .replace("{{HEALTH}}", health_rows())
                    .replace("{{GALLERY}}", gallery()))
        self._send(body.encode())

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            cfg = load()
            on = sum(cfg["screens"].values())
            note = ("holding %s" % cfg["pin"]) if cfg["pin"] else \
                   ("%d of %d screens on" % (on, len(SCREENS)))
            self._page(note)
            return
        if path.startswith("/photo/"):
            name = os.path.basename(path[len("/photo/"):])
            try:
                with open(os.path.join(PHOTO_DIR, name), "rb") as f:
                    self._send(f.read(), "image/png")
            except OSError:
                self.send_error(404)
            return
        if path.startswith("/wall/"):
            name = os.path.basename(path[len("/wall/"):])
            p = os.path.join(_wall.WALL_DIR, name)
            ctype = ("video/mp4" if name.endswith(".mp4") else
                     "application/json" if name.endswith(".json") else "image/jpeg")
            try:
                with open(p, "rb") as f:
                    self._send(f.read(), ctype)
            except OSError:
                self.send_error(404)
            return
        if path == "/gallery":
            self._send(_wallpage.page().encode())
            return
        if path == "/layout":
            self._send(_layout.page().encode())
            return
        if path == "/homekit/reading":
            # Same URL as the POST, GET instead — lets you confirm a
            # Shortcut actually landed without digging into the Pi's
            # filesystem over SSH.
            self._send(json.dumps(homekit_readings()).encode(), "application/json")
            return
        if path == "/frame.png":
            try:
                with open(os.path.join(DATA_DIR, "frame.png"), "rb") as f:
                    self._send(f.read(), "image/png")
            except OSError:
                self.send_error(404)
            return
        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)

        if path == "/wall/add":
            # Wall-resolution copy only. The Pixoo tile for this same photo
            # arrives separately from the crop editor, already finished.
            ctype = self.headers.get("Content-Type", "")
            if n > MAX_UPLOAD or "boundary=" not in ctype:
                self._send(b'{"error":"too large"}', "application/json", 400)
                return
            boundary = ctype.split("boundary=", 1)[1].strip().strip('"')
            done, tmp, parts = 0, None, []
            try:
                tmp, _got = stream_to_disk(self.rfile, n, UPLOAD_TMP)
                parts = split_multipart_file(tmp, boundary)
                for name, ppath in parts:
                    try:
                        with open(ppath, "rb") as f:
                            _wall.add(name, f.read())
                        done += 1
                    except Exception:
                        pass
            finally:
                # Temp files are the one thing that will quietly fill a disk,
                # so they go regardless of how this exits.
                for _n, p in parts:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                if tmp:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            self._send(json.dumps({"added": done}).encode(), "application/json")
            return

        if path == "/wall/adjust":
            try:
                d = json.loads(self.rfile.read(n).decode() or "{}")
                it = _wall.adjust(d.get("file", ""), d.get("frame") or {})
                self._send(json.dumps({"item": it}).encode(), "application/json")
            except Exception as e:
                self._send(json.dumps({"error": type(e).__name__}).encode(),
                           "application/json", 400)
            return

        if path == "/wall/delete":
            raw = self.rfile.read(n).decode()
            fname = os.path.basename(raw.split("f=", 1)[-1].split("&")[0])
            _wall.remove(fname)
            self._page("Removed from the wall gallery")
            return

        if path == "/homekit/reading":
            # Fed by an iOS Shortcut, not a browser, so this answers plain
            # JSON with real status codes rather than the HTML control panel
            # every other route in here returns.
            try:
                d = json.loads(self.rfile.read(n).decode() or "{}")
            except ValueError:
                self._send(b'{"error":"bad json"}', "application/json", 400)
                return
            if not HOMEKIT_TOKEN or d.get("token") != HOMEKIT_TOKEN:
                self._send(b'{"error":"forbidden"}', "application/json", 403)
                return
            sensor_id = re.sub(r"[^a-z0-9_-]", "", str(d.get("id", "")).lower())
            if not sensor_id:
                self._send(b'{"error":"missing id"}', "application/json", 400)
                return
            try:
                temp_f = float(d["temp_f"])
            except (KeyError, TypeError, ValueError):
                self._send(b'{"error":"missing temp_f"}', "application/json", 400)
                return
            humidity = d.get("humidity")
            try:
                humidity = float(humidity) if humidity is not None else None
            except (TypeError, ValueError):
                humidity = None
            label = str(d.get("label") or sensor_id).strip()[:40]
            save_homekit_reading(sensor_id, label, temp_f, humidity)
            self._send(b'{"ok":true}', "application/json")
            return

        if path == "/layout/save":
            # Answers JSON, not a redirect to the panel: the editor is a live
            # canvas and reloading it mid-edit would throw away the selection.
            try:
                doc = json.loads(self.rfile.read(n).decode() or "{}")
                saved = _layout.save(doc)
                self._send(json.dumps(saved).encode(), "application/json")
            except Exception as e:
                self._send(json.dumps({"error": type(e).__name__}).encode(),
                           "application/json", 400)
            return

        if path == "/upload":
            ctype = self.headers.get("Content-Type", "")
            if n > MAX_UPLOAD or "boundary=" not in ctype:
                self._page("Upload too large or malformed")
                return
            body = self.rfile.read(n)
            boundary = ctype.split("boundary=", 1)[1].strip().strip('"')
            done, failed = 0, 0
            for name, blob in parse_multipart(body, boundary):
                if len(photo_files()) >= MAX_PHOTOS:
                    break
                try:
                    # Stills feed both devices. Clips are wall-only: there is
                    # nothing sensible to do with 90 seconds of video on a
                    # 64x64 panel that shows one frame per rotation.
                    kind = _wall.kind_of(blob, name)
                    if kind == "still":
                        save_photo(name, blob)
                    _wall.add(name, blob)
                    done += 1
                except Exception:
                    failed += 1          # a bad file shouldn't lose the rest
            self._page("Added %d photo%s%s" % (done, "" if done == 1 else "s",
                       ", %d failed" % failed if failed else ""))
            return

        if path == "/crop":
            raw = self.rfile.read(n).decode()
            form = dict(parse_qsl(raw, keep_blank_values=True))
            try:
                blob = base64.b64decode(form.get("img", "").split(",")[-1])
                save_cropped(form.get("name", "photo"), blob)
                self._page("Photo added")
            except (binascii.Error, ValueError, OSError) as e:
                self._page("Could not save photo (%s)" % type(e).__name__)
            return

        if path == "/delete":
            raw = self.rfile.read(n).decode()
            name = os.path.basename(raw.split("f=", 1)[-1].split("&")[0])
            try:
                os.remove(os.path.join(PHOTO_DIR, name))
                self._page("Deleted")
            except OSError:
                self._page("Could not delete")
            return

        raw = self.rfile.read(n).decode()
        # parse_qsl rather than splitting by hand: the crop editor posts
        # base64, which is full of + and = and would come out corrupted.
        form = dict(parse_qsl(raw, keep_blank_values=True))

        cfg = load()
        action = form.get("do", "save")

        # Logged so `journalctl -u control` shows exactly which fields the
        # browser submitted. A checkbox that never arrives and one that
        # arrives unchecked look identical in the saved file.
        print("POST %s fields=%s" % (action, sorted(form)), flush=True)

        if action == "save":
            cfg["screens"] = {k: (k in form) for k in KEYS}
            if not any(cfg["screens"].values()):
                cfg["screens"]["flag"] = True   # a dark board reads as broken
            cfg["dwell"] = {}
            for k in KEYS:
                v = form.get("d_" + k, "")
                if v.isdigit():
                    cfg["dwell"][k] = max(DWELL_MIN, min(DWELL_MAX, int(v)))
            cfg["pin"] = form.get("pin") or None
            b = form.get("brightness", "auto")
            cfg["brightness"] = b if b == "auto" else int(b)
            msg = "Saved"
        else:
            cfg["command"] = action
            cfg["command_id"] = int(time.time())
            msg = "Refreshing" if action == "refresh" else "Restarting board"

        err = save(cfg)
        self._page(("Could not save &mdash; " + err) if err else msg)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("control panel on http://0.0.0.0:%d  (state: %s)" % (PORT, STATE))
    # Threaded because uploads are slow. A single-threaded server spent the
    # whole of an ffmpeg transcode refusing every other request, so the panel
    # looked crashed for minutes at a time from a phone.
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
