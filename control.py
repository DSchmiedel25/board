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

import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    from config import DATA_DIR
except ImportError:
    DATA_DIR = "/var/www/html/data"

PORT = 8081
STATE = os.path.join(DATA_DIR, "screens.json")

PHOTO_DIR = os.path.join(DATA_DIR, "photos")
MAX_UPLOAD = 12 * 1024 * 1024          # generous for a phone photo
MAX_PHOTOS = 40

SCREENS = [
    ("flag",     "DirtCheck",   "Albany-Saratoga, Lebanon Valley, Fonda"),
    ("weather",  "Weather",     "Now and the three-day forecast"),
    ("nascar",   "NASCAR",      "Next race for Cup, Xfinity and Trucks"),
    ("podium",   "Race field",  "Starting grid or finishing order"),
    ("photo",    "Photos",      "Your uploads, one per turn"),
    ("jellyfin", "Now playing", "Only while something is streaming"),
]
KEYS = [k for k, _l, _d in SCREENS]

DEFAULTS = {"screens": {k: True for k in KEYS}, "pin": None,
            "brightness": "auto", "command": None, "command_id": 0}


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


# ---------------------------------------------------------------- photos

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


PAGE = """<!doctype html><html><head><meta charset="utf-8">
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
<div class="sub">{{STATUS}}</div>

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

<button type="submit" name="do" value="save">Save</button>
<div class="pair">
  <button class="ghost" type="submit" name="do" value="refresh">Refresh data</button>
  <button class="ghost" type="submit" name="do" value="restart">Restart board</button>
</div>

</form>

<div class="col c-pho">
<h2>Photos</h2>
<div class="card">
  <form method="post" enctype="multipart/form-data" action="/upload">
    <input type="file" name="pic" accept="image/*" multiple id="pick">
    <label for="pick" class="filebtn">Choose photos</label>
    <button type="submit">Upload</button>
  </form>
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
</script>
</body></html>"""

ROW = """<div class="card"><div class="row">
  <div class="txt"><div class="name">{{LABEL}}</div><div class="desc">{{DESC}}</div></div>
  <label class="sw"><input type="checkbox" name="{{KEY}}" {{ON}} {{DIS}}>
    <span class="trk"></span><span class="knob"></span></label>
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
                    save_photo(name, blob)
                    done += 1
                except Exception:
                    failed += 1          # a bad file shouldn't lose the rest
            self._page("Added %d photo%s%s" % (done, "" if done == 1 else "s",
                       ", %d failed" % failed if failed else ""))
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
        form = {}
        for part in raw.split("&"):
            if not part:
                continue
            k, _, v = part.partition("=")
            form[k] = v.replace("+", " ")

        cfg = load()
        action = form.get("do", "save")

        if action == "save":
            cfg["screens"] = {k: (k in form) for k in KEYS}
            if not any(cfg["screens"].values()):
                cfg["screens"]["flag"] = True   # a dark board reads as broken
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
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
