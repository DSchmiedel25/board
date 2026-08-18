#!/usr/bin/env python3
"""
layout.py — the wall board's grid, and the editor that moves it around.

The board used to be a fixed set of CSS grid-template-areas baked into
index.html. Named areas can't express an arbitrary rectangle, so anything
drag-and-drop needs a uniform cell matrix instead: 12 columns x 18 rows over
the 1920x1080 surface, which is 160 x 60 px per cell. Twelve columns because
the old 1.32fr / 1fr split lands almost exactly on 7/5, and eighteen rows
because the short strips — the wire at ~128px and the media bar at ~152px —
need finer vertical steps than a 12-row grid can give them.

control.py serves the editor and writes layout.json. index.html polls that
file and sets grid-column / grid-row on each card. Nothing about the grid
lives in the stylesheet any more.

Overlap is allowed on purpose. Two modules sharing cells is how the media
strip already hands its slot to services when nothing is playing — highest
priority that is currently eligible wins, the rest hide.
"""

import json
import os
import time

try:
    from config import DATA_DIR
except ImportError:
    DATA_DIR = "/var/www/html/data"

COLS, ROWS = 12, 18
LAYOUT = os.path.join(DATA_DIR, "layout.json")

# key, label, description, minimum width/height in cells. The minimums are
# not fussiness: below them the type inside the card wraps and pushes its own
# content out of the box, which looks like a bug rather than a small card.
MODULES = [
    ("flag",     "Flag strip",  "Racing tonight, rained out, or standby",  4, 2),
    ("tracks",   "Tracks",      "All three tracks, next date and rain %",  4, 3),
    ("weather",  "Weather",     "Now, forecast and the radar window",      3, 4),
    ("nascar",   "NASCAR",      "Next race per series, plus the field",    5, 3),
    ("wire",     "Wire",        "Headlines from your RSS feeds",           4, 1),
    ("media",    "Now playing", "Jellyfin — only while something streams", 4, 2),
    ("services", "Services",    "Uptime Kuma health",                      3, 1),
    ("gallery",  "Gallery",     "Photos, GIFs and clips you've uploaded",  3, 2),
]

# Per-module settings, declared rather than hand-coded, so the editor can
# render controls for any module without knowing what it is and sanitize()
# can clamp them from the same table. Adding a knob is one line here.
#
#   (key, label, kind, spec, default)
#   kind "range" -> spec is (min, max)
#   kind "chips" -> spec is [(value, label), ...]
#   kind "multi" -> spec is [(value, label), ...], value is a list
OPTS = {
    "gallery": [
        ("dwell", "Seconds per item", "range", (3, 120), 8),
        ("order", "Order", "chips",
         [("shuffle", "Shuffle"), ("sequence", "In order")], "shuffle"),
        ("show", "Include", "multi",
         [("still", "Photos"), ("video", "Video & GIFs")], ["still", "video"]),
        ("fit", "Framing", "chips",
         [("contain", "Whole image"), ("cover", "Fill slot")], "contain"),
    ],
    "wire": [
        ("dwell", "Seconds per set", "range", (6, 120), 22),
        ("rows", "Headlines shown", "range", (1, 5), 3),
        ("mode", "Motion", "chips",
         [("fade", "Cross-fade"), ("marquee", "Marquee")], "fade"),
    ],
    "tracks": [
        ("dwell", "Seconds per track page", "range", (6, 120), 20),
    ],
}
KEYS = [m[0] for m in MODULES]
MINS = {m[0]: (m[3], m[4]) for m in MODULES}
LABELS = {m[0]: m[1] for m in MODULES}

# The layout as it shipped, translated onto the 12x18 grid. Rows: flag 3,
# the tracks/nascar/weather block 10, wire 2, media 3.
DEFAULT = {
    "modules": {
        "flag":     {"col": 1, "row": 1,  "w": 12, "h": 3,  "on": True,  "priority": 0},
        "tracks":   {"col": 1, "row": 4,  "w": 7,  "h": 6,  "on": True,  "priority": 0},
        "nascar":   {"col": 1, "row": 10, "w": 7,  "h": 4,  "on": True,  "priority": 0},
        "weather":  {"col": 8, "row": 4,  "w": 5,  "h": 10, "on": True,  "priority": 0},
        "wire":     {"col": 1, "row": 14, "w": 12, "h": 2,  "on": True,  "priority": 0},
        "media":    {"col": 1, "row": 16, "w": 12, "h": 3,  "on": True,  "priority": 10},
        "services": {"col": 1, "row": 16, "w": 12, "h": 3,  "on": True,  "priority": 0},
        # Sits under the whole racing block. In season nothing sees it; from
        # November to February it is what's on the wall.
        "gallery":  {"col": 1, "row": 1,  "w": 12, "h": 13, "on": True,  "priority": 0},
    },
    "gap": 20,
    "ts": 0,
}


def clamp(v, lo, hi):
    return max(lo, min(hi, int(v)))


def sanitize(doc):
    """Force a posted layout into something the renderer can't choke on.

    The editor already constrains dragging, but this is a public POST on the
    LAN and a bad layout.json is a blank wall that needs SSH to fix. Anything
    unrecognised is dropped and anything out of range is clamped rather than
    rejected — a slightly-wrong board beats no board.
    """
    out = {"modules": {}, "gap": clamp(doc.get("gap", 20), 0, 60),
           "ts": int(time.time())}
    src = doc.get("modules") or {}
    for key in KEYS:
        d = src.get(key) or {}
        base = DEFAULT["modules"][key]
        mw, mh = MINS[key]
        w = clamp(d.get("w", base["w"]), mw, COLS)
        h = clamp(d.get("h", base["h"]), mh, ROWS)
        col = clamp(d.get("col", base["col"]), 1, COLS - w + 1)
        row = clamp(d.get("row", base["row"]), 1, ROWS - h + 1)
        out["modules"][key] = {
            "col": col, "row": row, "w": w, "h": h,
            "on": bool(d.get("on", base["on"])),
            "priority": clamp(d.get("priority", base.get("priority", 0)), 0, 99),
        }
        if key in OPTS:
            out["modules"][key]["opts"] = clean_opts(key, d.get("opts") or {})
    return out


def clean_opts(key, got):
    """Clamp settings against the declared spec. Same reasoning as the grid
    itself: an out-of-range dwell is a module that never advances, and fixing
    that needs SSH. Clamp, don't reject."""
    out = {}
    for name, _label, kind, spec, default in OPTS[key]:
        v = got.get(name, default)
        if kind == "range":
            lo, hi = spec
            try:
                out[name] = clamp(v, lo, hi)
            except (TypeError, ValueError):
                out[name] = default
        elif kind == "chips":
            allowed = [a for a, _b in spec]
            out[name] = v if v in allowed else default
        elif kind == "multi":
            allowed = [a for a, _b in spec]
            picked = [x for x in (v if isinstance(v, list) else []) if x in allowed]
            # An empty set means a module with nothing to show, which reads as
            # broken. Fall back to everything rather than to nothing.
            out[name] = picked or list(default)
    return out


def load():
    try:
        with open(LAYOUT) as f:
            return sanitize(json.load(f))
    except Exception:
        return sanitize(DEFAULT)


def save(doc):
    os.makedirs(DATA_DIR, exist_ok=True)
    clean = sanitize(doc)
    tmp = LAYOUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(clean, f, separators=(",", ":"))
    os.replace(tmp, LAYOUT)      # atomic: the board never reads a half file
    return clean


# --------------------------------------------------------------- editor page

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Board layout</title>
<style>
:root{--bg:#100f0d;--panel:#1b1917;--sunk:#232120;--rail:#37342f;
      --dust:#f0ebe0;--slate:#8a8378;--sodium:#e8b93f;--green:#5fbf5a;--red:#d9534a}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--dust);font:16px/1.4 system-ui,-apple-system,sans-serif;
     padding:14px;padding-bottom:120px}
h1{font-size:20px;font-weight:800;letter-spacing:.04em;text-transform:uppercase}
.sub{color:var(--slate);font-size:13px;margin:4px 0 14px}

/* The canvas is the board at 16:9, whatever the phone is. Absolute
   positioning inside rather than CSS grid: dragging needs pixel maths, and
   reading positions back out of a grid is far messier than owning them. */
#canvas{position:relative;width:100%;aspect-ratio:16/9;background:#000;
        border:1px solid var(--rail);border-radius:10px;overflow:hidden;
        touch-action:none}
#grid{position:absolute;inset:0;pointer-events:none;opacity:.5}
.mod{position:absolute;background:var(--panel);border:1.5px solid var(--rail);
     border-radius:6px;overflow:hidden;touch-action:none;cursor:move;
     display:flex;align-items:center;justify-content:center;text-align:center}
.mod .nm{font-size:11px;font-weight:700;letter-spacing:.05em;
         text-transform:uppercase;padding:2px;pointer-events:none;
         white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.mod.off{opacity:.28;border-style:dashed}
.mod.sel{border-color:var(--sodium);background:#2a2723;z-index:5}
.mod.drag{opacity:.85;z-index:9}
/* Resize grip, bottom-right. 26px because a 10px handle is unusable with a
   thumb, and this whole thing is meant to be driven from the couch. */
.grip{position:absolute;right:0;bottom:0;width:26px;height:26px;cursor:nwse-resize;
      background:linear-gradient(135deg,transparent 46%,var(--sodium) 46%);
      opacity:0;touch-action:none}
.mod.sel .grip{opacity:1}
.ghost{position:absolute;border:2px dashed var(--sodium);border-radius:6px;
       pointer-events:none;opacity:0;z-index:8}

.bar{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 6px;align-items:center}
button{background:var(--sunk);color:var(--dust);border:1px solid var(--rail);
       border-radius:8px;padding:11px 15px;font-size:15px;font-weight:600}
button.go{background:var(--sodium);color:#241c05;border-color:var(--sodium)}
button.warn{color:var(--red)}
button:disabled{opacity:.4}
.list{margin-top:16px;border-top:1px solid var(--rail)}
.row{display:flex;align-items:center;gap:12px;padding:12px 2px;
     border-bottom:1px solid var(--rail)}
.row .txt{flex:1;min-width:0}
.row .nm2{font-weight:700}
.row .ds{font-size:12px;color:var(--slate)}
.row.sel .nm2{color:var(--sodium)}
.sw{position:relative;width:50px;height:29px;flex:none}
.sw input{position:absolute;opacity:0;width:100%;height:100%;margin:0;z-index:2}
.sw i{position:absolute;inset:0;background:var(--rail);border-radius:15px;transition:.15s}
.sw i:after{content:"";position:absolute;width:23px;height:23px;left:3px;top:3px;
            background:var(--dust);border-radius:50%;transition:.15s}
.sw input:checked + i{background:var(--green)}
.sw input:checked + i:after{transform:translateX(21px)}
/* Settings for whichever module is selected. Appears only when that module
   has any, so most selections show nothing here rather than an empty box. */
#opts{margin-top:18px}
#opts h2{font-size:14px;letter-spacing:.14em;text-transform:uppercase;
         color:var(--sodium);margin-bottom:10px}
.opt{padding:12px 2px;border-bottom:1px solid var(--rail)}
.opt .lb{display:flex;justify-content:space-between;font-size:14px;
         color:var(--slate);margin-bottom:9px}
.opt .lb b{color:var(--dust);font-variant-numeric:tabular-nums}
.opt input[type=range]{width:100%;accent-color:var(--sodium);height:30px}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{flex:1;min-width:96px;text-align:center;padding:11px 8px;border-radius:8px;
      border:1px solid var(--rail);background:var(--sunk);font-size:14px}
.chip.on{background:var(--sodium);color:#241c05;border-color:var(--sodium);
         font-weight:700}
#status{position:fixed;left:0;right:0;bottom:0;background:var(--sunk);
        border-top:1px solid var(--rail);padding:12px 14px;font-size:14px;
        display:flex;gap:10px;align-items:center}
#status .msg{flex:1;min-width:0;color:var(--slate)}
#status .msg.ok{color:var(--green)} #status .msg.bad{color:var(--red)}
</style></head><body>

<h1>Board layout</h1>
<div class="sub">Drag to move. Tap to select, then drag the corner to resize.</div>

<div id="canvas"><canvas id="grid"></canvas><div class="ghost" id="ghost"></div></div>

<div class="bar">
  <button id="undo" disabled>Undo</button>
  <button id="reset" class="warn">Defaults</button>
  <button id="save" class="go" style="margin-left:auto">Save</button>
</div>

<div class="list" id="list"></div>
<div id="opts"></div>

<div id="status"><span class="msg" id="msg">Loaded</span></div>

<script>
const COLS = {{COLS}}, ROWS = {{ROWS}};
const MODULES = {{MODULES}};
const MINS = {{MINS}};
let L = {{LAYOUT}};
let sel = null, dirty = false, history = [];

const $ = s => document.querySelector(s);
const canvas = $("#canvas");

function cell(){ return {w: canvas.clientWidth / COLS, h: canvas.clientHeight / ROWS}; }

function drawGrid(){
  const c = $("#grid"), r = window.devicePixelRatio || 1;
  c.width = canvas.clientWidth * r; c.height = canvas.clientHeight * r;
  c.style.width = canvas.clientWidth + "px"; c.style.height = canvas.clientHeight + "px";
  const x = c.getContext("2d"); x.scale(r, r);
  x.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  x.strokeStyle = "#37342f"; x.lineWidth = .5;
  const cs = cell();
  for(let i = 1; i < COLS; i++){
    x.beginPath(); x.moveTo(i*cs.w, 0); x.lineTo(i*cs.w, canvas.clientHeight); x.stroke();
  }
  for(let j = 1; j < ROWS; j++){
    x.beginPath(); x.moveTo(0, j*cs.h); x.lineTo(canvas.clientWidth, j*cs.h); x.stroke();
  }
}

function place(el, m){
  const cs = cell();
  el.style.left   = ((m.col-1) * cs.w + 1) + "px";
  el.style.top    = ((m.row-1) * cs.h + 1) + "px";
  el.style.width  = (m.w * cs.w - 2) + "px";
  el.style.height = (m.h * cs.h - 2) + "px";
}

function build(){
  canvas.querySelectorAll(".mod").forEach(n => n.remove());
  MODULES.forEach(([key, label]) => {
    const m = L.modules[key];
    const el = document.createElement("div");
    el.className = "mod" + (m.on ? "" : " off") + (sel === key ? " sel" : "");
    el.dataset.key = key;
    el.innerHTML = `<span class="nm">${label}</span><span class="grip"></span>`;
    place(el, m);
    canvas.appendChild(el);
  });
  buildList();
  buildOpts();
}

function buildList(){
  $("#list").innerHTML = MODULES.map(([key, label, desc]) => {
    const m = L.modules[key];
    return `<div class="row ${sel===key?"sel":""}" data-key="${key}">
      <div class="txt"><div class="nm2">${label}</div>
        <div class="ds">${desc} &middot; ${m.w}&times;${m.h} at ${m.col},${m.row}</div></div>
      <label class="sw"><input type="checkbox" data-on="${key}" ${m.on?"checked":""}><i></i></label>
    </div>`;
  }).join("");
}

/* Controls are generated from the OPTS table rather than written per module,
   so a new knob is one line of Python and appears here automatically. */
const OPTS = {{OPTS}};

function buildOpts(){
  const box = $("#opts");
  if(!sel || !OPTS[sel]){ box.innerHTML = ""; return; }
  const o = L.modules[sel].opts || {};
  const rows = OPTS[sel].map(([name, label, kind, spec, dflt]) => {
    const v = (name in o) ? o[name] : dflt;
    if(kind === "range"){
      const [lo, hi] = spec;
      return `<div class="opt"><div class="lb"><span>${label}</span><b id="v_${name}">${v}</b></div>
        <input type="range" min="${lo}" max="${hi}" value="${v}" data-opt="${name}"></div>`;
    }
    if(kind === "chips"){
      return `<div class="opt"><div class="lb"><span>${label}</span></div>
        <div class="chips">${spec.map(([val,lab]) =>
          `<div class="chip ${v===val?"on":""}" data-opt="${name}" data-val="${val}">${lab}</div>`
        ).join("")}</div></div>`;
    }
    const cur = Array.isArray(v) ? v : [];
    return `<div class="opt"><div class="lb"><span>${label}</span></div>
      <div class="chips">${spec.map(([val,lab]) =>
        `<div class="chip ${cur.includes(val)?"on":""}" data-multi="${name}" data-val="${val}">${lab}</div>`
      ).join("")}</div></div>`;
  }).join("");
  box.innerHTML = `<h2>${MODULES.find(m=>m[0]===sel)[1]} settings</h2>${rows}`;
}

$("#opts").addEventListener("input", e => {
  const name = e.target.dataset.opt;
  if(!name || e.target.type !== "range") return;
  L.modules[sel].opts = L.modules[sel].opts || {};
  L.modules[sel].opts[name] = +e.target.value;
  const out = document.getElementById("v_" + name);
  if(out) out.textContent = e.target.value;
  dirty = true; mark("Unsaved changes");
});

$("#opts").addEventListener("click", e => {
  const chip = e.target.closest(".chip");
  if(!chip) return;
  snapshot();
  const o = L.modules[sel].opts = L.modules[sel].opts || {};
  if(chip.dataset.multi){
    const name = chip.dataset.multi, val = chip.dataset.val;
    const cur = Array.isArray(o[name]) ? o[name].slice() : [];
    const i = cur.indexOf(val);
    if(i < 0) cur.push(val); else cur.splice(i, 1);
    /* Turning everything off leaves a module with nothing to show, which
       looks broken. The server refills it anyway; refuse here so the UI
       doesn't lie about what was saved. */
    if(!cur.length){ mark("Keep at least one", "bad"); history.pop(); return; }
    o[name] = cur;
  }else{
    o[chip.dataset.opt] = chip.dataset.val;
  }
  dirty = true; buildOpts(); mark("Unsaved changes");
});

function snapshot(){
  history.push(JSON.stringify(L));
  if(history.length > 30) history.shift();
  $("#undo").disabled = false;
}

function mark(txt, cls){
  const m = $("#msg"); m.textContent = txt; m.className = "msg " + (cls || "");
}

/* ------------------------------------------------------------ interaction
   Pointer events rather than mouse or touch handlers: one code path covers
   a finger on the wall Pi's own screen, a finger on your phone, and a mouse
   on the MacBook. setPointerCapture keeps the drag alive when the finger
   slides outside the box it started in. */
let drag = null;

canvas.addEventListener("pointerdown", e => {
  const el = e.target.closest(".mod");
  if(!el) { sel = null; build(); return; }
  const key = el.dataset.key, m = L.modules[key];
  const resizing = e.target.classList.contains("grip");
  if(sel !== key){ sel = key; build(); }
  const node = canvas.querySelector(`.mod[data-key="${key}"]`);
  node.classList.add("drag");
  node.setPointerCapture(e.pointerId);
  drag = {key, resizing, x0: e.clientX, y0: e.clientY, m: {...m}, moved: false, node};
  snapshot();
  e.preventDefault();
});

canvas.addEventListener("pointermove", e => {
  if(!drag) return;
  const cs = cell();
  const dx = Math.round((e.clientX - drag.x0) / cs.w);
  const dy = Math.round((e.clientY - drag.y0) / cs.h);
  if(dx || dy) drag.moved = true;
  const m = L.modules[drag.key], b = drag.m;
  const [mw, mh] = MINS[drag.key];
  if(drag.resizing){
    m.w = Math.max(mw, Math.min(COLS - b.col + 1, b.w + dx));
    m.h = Math.max(mh, Math.min(ROWS - b.row + 1, b.h + dy));
  }else{
    m.col = Math.max(1, Math.min(COLS - m.w + 1, b.col + dx));
    m.row = Math.max(1, Math.min(ROWS - m.h + 1, b.row + dy));
  }
  place(drag.node, m);
  buildList();
});

function endDrag(){
  if(!drag) return;
  drag.node.classList.remove("drag");
  if(drag.moved){ dirty = true; mark("Unsaved changes"); }
  else history.pop();          // a tap to select is not an undo step
  $("#undo").disabled = !history.length;
  drag = null;
}
canvas.addEventListener("pointerup", endDrag);
canvas.addEventListener("pointercancel", endDrag);

$("#list").addEventListener("change", e => {
  const key = e.target.dataset.on;
  if(!key) return;
  snapshot();
  L.modules[key].on = e.target.checked;
  dirty = true; build(); mark("Unsaved changes");
});

$("#list").addEventListener("click", e => {
  const row = e.target.closest(".row");
  if(!row || e.target.closest(".sw")) return;
  sel = row.dataset.key; build();
});

$("#undo").onclick = () => {
  if(!history.length) return;
  L = JSON.parse(history.pop());
  $("#undo").disabled = !history.length;
  dirty = true; build(); mark("Undone");
};

$("#reset").onclick = () => {
  if(!confirm("Reset the layout to defaults?")) return;
  snapshot();
  L = JSON.parse(JSON.stringify({{DEFAULTS}}));
  dirty = true; build(); mark("Defaults restored — not saved yet");
};

$("#save").onclick = async () => {
  $("#save").disabled = true;
  mark("Saving…");
  try{
    const r = await fetch("/layout/save", {method:"POST",
      headers:{"Content-Type":"application/json"}, body: JSON.stringify(L)});
    if(!r.ok) throw new Error(r.status);
    L = await r.json();                 // server clamps; take back what it kept
    dirty = false; build();
    mark("Saved — the wall updates within 10 seconds", "ok");
  }catch(err){
    mark("Save failed (" + err.message + ")", "bad");
  }
  $("#save").disabled = false;
};

addEventListener("beforeunload", e => { if(dirty){ e.preventDefault(); e.returnValue = ""; } });
addEventListener("resize", () => { drawGrid(); build(); });
drawGrid(); build();
</script></body></html>
"""


def page():
    return (PAGE
            .replace("{{COLS}}", str(COLS))
            .replace("{{ROWS}}", str(ROWS))
            .replace("{{MODULES}}", json.dumps([[m[0], m[1], m[2]] for m in MODULES]))
            .replace("{{MINS}}", json.dumps(MINS))
            .replace("{{OPTS}}", json.dumps(OPTS))
            .replace("{{DEFAULTS}}", json.dumps(DEFAULT))
            .replace("{{LAYOUT}}", json.dumps(load())))
