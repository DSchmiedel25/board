#!/usr/bin/env python3
"""
wallpage.py — the gallery: what's in it, and how each item is framed.

Separate from control.py's photo section, which crops to a 64x64 square for
the Pixoo. That editor bakes its result into a file because the Pixoo panel
is always 64x64 and always will be. The wall is not: the gallery slot's shape
is a setting, so framing here is stored as intent — a focal point and a zoom —
and applied at render time. Change the slot from 3:2 to 2:3 and every photo
re-frames itself around the point you chose instead of being wrong.
"""

import json

import wallmedia as _wall

try:
    import layout as _layout
except ImportError:
    _layout = None


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Gallery</title>
<style>
:root{--bg:#100f0d;--panel:#1b1917;--sunk:#232120;--rail:#37342f;
      --dust:#f0ebe0;--slate:#8a8378;--sodium:#e8b93f;--green:#5fbf5a;--red:#d9534a}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--dust);font:16px/1.4 system-ui,-apple-system,sans-serif;
     padding:14px;padding-bottom:96px}
h1{font-size:20px;font-weight:800;letter-spacing:.04em;text-transform:uppercase}
.sub{color:var(--slate);font-size:13px;margin:4px 0 14px}
a{color:var(--sodium)}

/* The frame is the gallery slot's real shape, taken from layout.json.
   Adjusting against a 16:9 preview and then seeing the result in a 2:3 slot
   would be worse than offering no editor at all. */
#frame{position:relative;width:100%;aspect-ratio:{{RATIO}};background:#000;
       border:1px solid var(--rail);border-radius:10px;overflow:hidden;
       touch-action:none}
#frame .back{position:absolute;inset:0;background-size:cover;background-position:center;
             filter:blur(50px) saturate(1.8) brightness(.5);transform:scale(1.35)}
/* .shotel: shared position/sizing for whichever of the two preview elements
   is currently showing — exactly one of #shotImg/#shotVideo at a time,
   picked by item type in applyFrame(). */
.shotel{position:absolute;inset:0;width:100%;height:100%;z-index:1}
#frame .hint{position:absolute;left:0;right:0;bottom:0;z-index:3;
             background:linear-gradient(transparent,#000b);color:#cfc8bb;
             font-size:12px;padding:16px 10px 7px;text-align:center}
/* Thirds, only while dragging — a grid left up permanently reads as part of
   the picture. */
#frame .thirds{position:absolute;inset:0;z-index:2;opacity:0;transition:opacity .2s;
  background:
    linear-gradient(90deg,transparent 33.3%,#fff6 33.3% 33.5%,transparent 33.5%,
                    transparent 66.6%,#fff6 66.6% 66.8%,transparent 66.8%),
    linear-gradient(180deg,transparent 33.3%,#fff6 33.3% 33.5%,transparent 33.5%,
                    transparent 66.6%,#fff6 66.6% 66.8%,transparent 66.8%)}
#frame.moving .thirds{opacity:1}

.row{display:flex;align-items:center;gap:12px;margin:14px 0 6px}
.row .lb{flex:1;font-size:14px;color:var(--slate)}
.row .lb b{color:var(--dust)}
input[type=range]{width:100%;accent-color:var(--sodium);height:32px}
.chips{display:flex;gap:8px}
.chip{flex:1;text-align:center;padding:11px 8px;border-radius:8px;font-size:14px;
      border:1px solid var(--rail);background:var(--sunk)}
.chip.on{background:var(--sodium);color:#241c05;border-color:var(--sodium);font-weight:700}
button{background:var(--sunk);color:var(--dust);border:1px solid var(--rail);
       border-radius:8px;padding:11px 15px;font-size:15px;font-weight:600}
button.go{background:var(--sodium);color:#241c05;border-color:var(--sodium)}
button.warn{color:var(--red)}
button:disabled{opacity:.4}
.bar{display:flex;gap:8px;margin:14px 0}

h2{font-size:13px;letter-spacing:.16em;text-transform:uppercase;color:var(--sodium);
   margin:22px 0 10px}
.strip{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:9px}
.th{position:relative;aspect-ratio:1;border-radius:9px;overflow:hidden;
    border:2px solid transparent;background:#000}
.th.on{border-color:var(--sodium)}
.th img,.th video{width:100%;height:100%;object-fit:cover;display:block}
.th .tag{position:absolute;left:4px;bottom:4px;font-size:10px;font-weight:800;
         letter-spacing:.1em;background:#000a;padding:2px 5px;border-radius:4px;
         color:var(--sodium)}
.none{color:var(--slate);font-size:14px}
/* The picker itself is hidden and driven by its label: a bare file input
   renders differently on every platform and can't be made finger-sized. */
.up{margin-bottom:14px}
.up input[type=file]{position:absolute;width:1px;height:1px;opacity:0}
.filebtn{display:block;text-align:center;padding:15px;border-radius:10px;
         border:1px dashed var(--rail);background:var(--sunk);
         font-size:15px;font-weight:600;color:var(--sodium)}
.up.busy .filebtn{opacity:.5}
#status{position:fixed;left:0;right:0;bottom:0;background:var(--sunk);
        border-top:1px solid var(--rail);padding:12px 14px;font-size:14px;display:flex}
#status .msg{flex:1;color:var(--slate)}
#status .msg.ok{color:var(--green)} #status .msg.bad{color:var(--red)}
</style></head><body>

<h1>Gallery</h1>
<div class="sub">Drag the picture to choose what stays in frame.
  <a href="/">Back to control</a> &middot; <a href="/layout">Layout</a></div>

<div class="up">
  <input type="file" id="pick" multiple
         accept="image/*,video/*,.gif,.heic,.heif,.mov,.mp4,.m4v,.webm">
  <label for="pick" class="filebtn">Add photos, GIFs or clips</label>
  <div class="sub" id="upstat"></div>
</div>

<div id="frame">
  <div class="back" id="back"></div>
  <img class="shotel" id="shotImg" alt="">
  <video class="shotel" id="shotVideo" muted playsinline loop></video>
  <div class="thirds"></div>
  <div class="hint" id="hint">Drag to reposition</div>
</div>

<div class="row"><span class="lb">Zoom <b id="zv">1.0&times;</b></span></div>
<input type="range" id="zoom" min="100" max="400" value="100">

<div class="row"><span class="lb">Framing</span></div>
<div class="chips" id="fit">
  <div class="chip" data-fit="auto">Slot default</div>
  <div class="chip" data-fit="contain">Whole image</div>
  <div class="chip" data-fit="cover">Fill</div>
</div>

<div class="bar">
  <button id="centre">Re-centre</button>
  <button id="del" class="warn">Delete</button>
  <button id="save" class="go" style="margin-left:auto">Save</button>
</div>

<h2>Items</h2>
<div class="strip" id="strip"></div>

<div id="status"><span class="msg" id="msg">&nbsp;</span></div>

<script>
let ITEMS = {{ITEMS}};
let at = 0, F = null, dirty = false;
const $ = s => document.querySelector(s);
const mark = (t,c) => { const m=$("#msg"); m.textContent=t; m.className="msg "+(c||""); };
const cur = () => ITEMS[at];

function paintStrip(){
  const box = $("#strip");
  if(!ITEMS.length){ box.innerHTML = `<div class="none">Nothing uploaded yet.</div>`; return; }
  box.innerHTML = ITEMS.map((it,i) => `
    <div class="th ${i===at?"on":""}" data-i="${i}">
      ${it.type==="video"
        ? `<video src="wall/${it.file}" muted playsinline preload="metadata"></video>`
        : `<img src="wall/${it.file}" alt="" loading="lazy">`}
      ${it.type==="video"?`<span class="tag">${it.from==="gif"?"GIF":"CLIP"}</span>`:""}
    </div>`).join("");
}

/* Framing is applied the same way here and on the board: object-fit decides
   letterbox versus crop, object-position is the focal point, and scale is the
   zoom. Keeping the two identical is the only reason the preview is
   trustworthy. */
/* Bug fix: this never set the preview element's src at all — framing
   styles (object-fit, position, zoom) were applied to whatever the <img>
   last happened to be showing, which on first load was nothing, so the
   frame you were "editing" was blank or stale. It also assumed the preview
   was always an <img>; a video/GIF item had no element that could show it.
   Now there are two preview elements and applyFrame picks the one that
   matches the current item's type, sets its src, and hides the other. */
function applyFrame(){
  const it = cur();
  const img = $("#shotImg"), vid = $("#shotVideo");
  if(!it){
    // Audit item #8 in miniature: deleting down to zero items doesn't
    // crash (pick(-1) short-circuits harmlessly through cur() returning
    // undefined) but without this, the last-deleted photo or clip just
    // sits there looking selected. Hide both and stop.
    img.style.display = "none"; vid.style.display = "none"; vid.pause();
    $("#back").style.backgroundImage = "none";
    return;
  }
  const isVideo = it.type === "video";
  const el = isVideo ? vid : img, other = isVideo ? img : vid;

  other.style.display = "none";
  if(!isVideo){ vid.pause(); vid.removeAttribute("src"); vid.load(); }
  el.style.display = "";

  const fit = F.fit === "auto" ? "contain" : F.fit;
  el.style.objectFit = fit;
  el.style.objectPosition = F.x + "% " + F.y + "%";
  el.style.transform = "scale(" + F.zoom + ")";
  el.style.transformOrigin = F.x + "% " + F.y + "%";

  const src = "wall/" + it.file;
  // Compare before assigning: setting .src on a <video> that's already
  // showing that file restarts playback from frame zero on every drag/
  // zoom tick, which is what a naive unconditional assignment would do.
  if(el.getAttribute("src") !== src){
    el.src = src;
    if(isVideo) vid.play().catch(() => {});   // autoplay can be blocked before a user gesture; fine to stay paused
  }

  // Matches the live board: the blurred backdrop sits behind a letterboxed
  // still only. Video has no equivalent there, and a video file can't be
  // used as a CSS background-image regardless.
  $("#back").style.backgroundImage = isVideo ? "none" : `url("${src}")`;

  $("#zv").textContent = F.zoom.toFixed(1) + "\u00d7";
  $("#zoom").value = Math.round(F.zoom * 100);
  document.querySelectorAll("#fit .chip").forEach(c =>
    c.classList.toggle("on", c.dataset.fit === F.fit));
  $("#hint").textContent = fit === "contain"
    ? "Whole image — zoom in to crop" : "Drag to reposition";
}

function pick(i){
  at = i; F = Object.assign({fit:"auto",zoom:1,x:50,y:50}, cur() && cur().frame);
  dirty = false; paintStrip(); applyFrame(); mark(" ");
}

/* Uploads post straight here rather than through control.py's crop editor:
   that editor exists to choose a 64x64 Pixoo tile, and framing for the wall
   is chosen on this page instead — non-destructively, after the fact. */
$("#pick").addEventListener("change", async () => {
  const files = Array.from($("#pick").files || []);
  if(!files.length) return;
  const box = document.querySelector(".up");
  box.classList.add("busy");
  const clips = files.filter(f => (f.type || "").startsWith("video/") ||
                                  /\.(gif|mp4|mov|m4v|webm)$/i.test(f.name));
  mark(clips.length
    ? "Uploading — transcoding " + clips.length + " clip" +
      (clips.length===1?"":"s") + ", this can take a minute…"
    : "Uploading " + files.length + " file" + (files.length===1?"":"s") + "…");
  try{
    const fd = new FormData();
    files.forEach(f => fd.append("pic", f));
    const r = await fetch("/wall/add", {method:"POST", body: fd});
    if(!r.ok) throw new Error(r.status);
    const back = await r.json();
    // Re-read the index rather than guessing what the server made of them:
    // a HEIC becomes a JPEG and a GIF becomes an MP4 on the way in.
    const idx = await (await fetch("wall/index.json?t="+Date.now(),
                                   {cache:"no-store"})).json();
    ITEMS = idx.items || [];
    pick(Math.max(0, ITEMS.length - (back.added || 1)));
    mark("Added " + (back.added || 0) + " item" +
         ((back.added===1)?"":"s") + " — frame it below", "ok");
  }catch(err){ mark("Upload failed (" + err.message + ")", "bad"); }
  box.classList.remove("busy");
  $("#pick").value = "";
});

$("#strip").addEventListener("click", e => {
  const t = e.target.closest(".th"); if(t) pick(+t.dataset.i);
});

/* Drag moves the focal point. Inverted on purpose: dragging the picture left
   should reveal what is off to the right, which means increasing x. */
let d = null;
const frame = $("#frame");
frame.addEventListener("pointerdown", e => {
  if(!cur()) return;
  d = {x:e.clientX, y:e.clientY, fx:F.x, fy:F.y};
  frame.classList.add("moving"); frame.setPointerCapture(e.pointerId);
});
frame.addEventListener("pointermove", e => {
  if(!d) return;
  const w = frame.clientWidth, h = frame.clientHeight;
  F.x = Math.max(0, Math.min(100, d.fx - (e.clientX - d.x) / w * 100));
  F.y = Math.max(0, Math.min(100, d.fy - (e.clientY - d.y) / h * 100));
  dirty = true; applyFrame();
});
function stop(){ if(!d) return; d = null; frame.classList.remove("moving");
                 if(dirty) mark("Unsaved"); }
frame.addEventListener("pointerup", stop);
frame.addEventListener("pointercancel", stop);

$("#zoom").addEventListener("input", e => {
  F.zoom = +e.target.value / 100; dirty = true; applyFrame(); mark("Unsaved");
});
$("#fit").addEventListener("click", e => {
  const c = e.target.closest(".chip"); if(!c) return;
  F.fit = c.dataset.fit; dirty = true; applyFrame(); mark("Unsaved");
});
$("#centre").onclick = () => { F = {fit:F.fit, zoom:1, x:50, y:50};
                               dirty = true; applyFrame(); mark("Unsaved"); };

$("#save").onclick = async () => {
  if(!cur()) return;
  $("#save").disabled = true; mark("Saving…");
  try{
    const r = await fetch("/wall/adjust", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({file: cur().file, frame: F})});
    if(!r.ok) throw new Error(r.status);
    const back = await r.json();
    if(back.item) ITEMS[at] = back.item;
    F = Object.assign({}, ITEMS[at].frame);
    dirty = false; applyFrame(); mark("Saved", "ok");
  }catch(err){ mark("Save failed (" + err.message + ")", "bad"); }
  $("#save").disabled = false;
};

$("#del").onclick = async () => {
  if(!cur() || !confirm("Delete this permanently?")) return;
  const body = new URLSearchParams(); body.set("f", cur().file);
  await fetch("/wall/delete", {method:"POST", body,
    headers:{"Content-Type":"application/x-www-form-urlencoded"}});
  ITEMS.splice(at, 1);
  pick(Math.min(at, ITEMS.length - 1));
  mark("Deleted", "ok");
};

addEventListener("beforeunload", e => { if(dirty){ e.preventDefault(); e.returnValue=""; } });
if(ITEMS.length) pick(0); else paintStrip();
</script></body></html>
"""


def page():
    ratio = _layout.gallery_shape() if _layout else 16 / 9
    return (PAGE
            .replace("{{RATIO}}", "%.4f" % ratio)
            .replace("{{ITEMS}}", json.dumps(_wall.items())))
