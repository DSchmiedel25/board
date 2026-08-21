#!/usr/bin/env python3
"""
wallmedia.py — the wall board's half of the upload.

control.py already turns every upload into a finished 64x64 PNG for the
Pixoo. That is the right shape for a device that reads frames inside a
rotation loop, and completely useless at 1920x1080 — a 64px image stretched
across a twelve-cell slot is mush. So the upload forks: the Pixoo keeps its
thumbnail, and this module keeps a wall-resolution copy of the same file.

Three input kinds, three treatments:

  stills   fitted to 1920 wide, JPEG. Nothing is cropped; the gallery puts a
           blurred copy of the same image behind it, so an upright phone photo
           in a 5:1 strip doesn't sit in black bars.

  GIFs     transcoded to H.264. A browser decodes every frame of a GIF on the
           CPU with no hardware path, and a big one is a permanent drain on a
           Pi that is already running two canvas loops.

  video    re-encoded to 720p and stripped of audio. Capped at 720p because
           1080p decode alongside the dust and sky canvases is where a Pi 4
           starts dropping frames. The audio track is removed rather than
           muted at playback: smaller files, and silence that doesn't depend
           on an attribute surviving a future edit.

Everything lands in DATA_DIR/wall/ with an index.json the page reads.
"""

import json
import os
import re
import subprocess
import time

try:
    from config import DATA_DIR
except ImportError:
    DATA_DIR = "/var/www/html/data"

WALL_DIR = os.path.join(DATA_DIR, "wall")
INDEX = os.path.join(WALL_DIR, "index.json")

MAX_ITEMS = 60
STILL_W = 1920
STILL_Q = 82
VIDEO_H = 720
VIDEO_MAX_SEC = 90
FFMPEG_TIMEOUT = 300           # a long clip on a Pi 4 is genuinely slow

# Tried first, falls back to software automatically if it fails or produces
# a suspect file — see the note on save_video(). Flip to False to skip it
# and always use software encoding, if it turns out unreliable on your Pi.
VIDEO_HW_ENCODE = True
# h264_v4l2m2m doesn't support -crf (reported "private to the encoder" and
# ignored) — bitrate is the only quality knob it actually obeys. 2.5Mbps is
# a reasonable middle ground for 720p phone clips; raise it if the hardware
# path looks noticeably worse than the software one at the same resolution.
VIDEO_HW_BITRATE = "2500k"

GIF_MAGIC = (b"GIF87a", b"GIF89a")


def have_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def kind_of(path, name=""):
    """Sniff the bytes rather than trusting the extension — phones hand over
    .jpg files that are HEIC and .mov files that are MP4.

    Takes a path and reads only the handful of bytes needed for the magic
    number, not the whole file — this used to take a full blob just to look
    at its first 12 bytes, which mattered once uploads started arriving as
    on-disk temp files rather than in-memory bytes (see save_video below)."""
    with open(path, "rb") as f:
        head = f.read(12)
    if head[:6] in GIF_MAGIC:
        return "gif"
    if head[4:8] == b"ftyp":                    # MP4 / MOV / M4V family
        return "video"
    if head[:4] == b"\x1a\x45\xdf\xa3":         # Matroska / WebM
        return "video"
    if head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n":
        return "still"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "still"
    if name.lower().endswith((".heic", ".heif", ".webp", ".bmp", ".tif", ".tiff")):
        return "still"
    return "still"                              # let PIL decide and fail loudly


def stem_of(name):
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(name)[0])[:40]
    return s or "item"


def free_path(stem, ext):
    """Whole-second timestamps collide when a phone uploads several files at
    once; walk until the name is free rather than silently overwriting."""
    os.makedirs(WALL_DIR, exist_ok=True)
    base = "%d_%s" % (time.time(), stem)
    p = os.path.join(WALL_DIR, base + ext)
    n = 2
    while os.path.exists(p):
        p = os.path.join(WALL_DIR, "%s_%d%s" % (base, n, ext))
        n += 1
    return p


def _run(args):
    r = subprocess.run(args, capture_output=True, timeout=FFMPEG_TIMEOUT)
    if r.returncode != 0:
        tail = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(tail[-1] if tail else "ffmpeg failed")


def save_still(path, stem):
    from PIL import Image, ImageOps
    src = Image.open(path)
    src = ImageOps.exif_transpose(src).convert("RGB")   # honour phone rotation
    if src.width > STILL_W:
        src = src.resize((STILL_W, round(src.height * STILL_W / src.width)),
                         Image.LANCZOS)
    out = free_path(stem, ".jpg")
    src.save(out, "JPEG", quality=STILL_Q, optimize=True, progressive=True)
    return {"file": os.path.basename(out), "type": "still",
            "w": src.width, "h": src.height}


def save_video(path, stem, src_ext=".bin"):
    """Transcode an already-on-disk upload to a wall-safe mp4.

    Used to take a blob and write it straight back out to a fresh temp file
    just so ffmpeg — a subprocess, which reads from disk or stdin, never
    from a Python object — had a path to point at. The upload was already a
    file on disk before it ever got here (control.py streams it there); this
    now runs ffmpeg on that file directly instead of round-tripping it
    through memory and a second copy on disk first. src_ext no longer means
    anything since there's no intermediate file left to name — kept as a
    parameter so callers don't need to change.

    Tries the Pi's hardware H.264 encoder first, falls back to software.
    Software x264 on a Pi 4 that's also running Jellyfin is genuinely slow —
    FFMPEG_TIMEOUT below is set to 5 minutes because a long clip can
    actually take that. The hardware encoder (h264_v4l2m2m) is much
    faster, but real-world reports of it are mixed: it doesn't honour
    -crf (bitrate has to be set directly instead, which is a cruder quality
    knob), and some sources report corrupted output at specific
    resolutions. This was never tested against this box's actual hardware
    — there's no way to: the encoder needs a real /dev/video* V4L2 device
    that doesn't exist anywhere it could be tried ahead of time. So rather
    than trust a clean ffmpeg exit code, the output gets sanity-checked —
    near-zero duration means ffprobe couldn't find a real video stream in
    it — and a bad result quietly falls through to the software path
    instead of shipping something broken. Set VIDEO_HW_ENCODE = False to
    skip the attempt entirely if it turns out unreliable on your Pi.
    """
    if not have_ffmpeg():
        raise RuntimeError("ffmpeg not installed")
    out = free_path(stem, ".mp4")

    if VIDEO_HW_ENCODE:
        try:
            _run([
                "ffmpeg", "-y", "-i", path,
                "-t", str(VIDEO_MAX_SEC),
                "-vf", "scale=-2:'min(%d,ih)',format=yuv420p" % VIDEO_H,
                "-an",
                "-c:v", "h264_v4l2m2m", "-b:v", VIDEO_HW_BITRATE,
                "-movflags", "+faststart",
                out,
            ])
            dur = probe_duration(out)
            if dur > 0.5:
                return {"file": os.path.basename(out), "type": "video", "dur": dur}
        except Exception:
            pass
        try:
            os.remove(out)          # clear a partial/corrupt attempt before the retry
        except OSError:
            pass

    _run([
        "ffmpeg", "-y", "-i", path,
        "-t", str(VIDEO_MAX_SEC),
        # -2 keeps width even, which H.264 requires; odd widths fail here
        # rather than at playback, which is far harder to diagnose.
        "-vf", "scale=-2:'min(%d,ih)'" % VIDEO_H,
        "-an",                                  # no audio track at all
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-pix_fmt", "yuv420p",                  # some players need this
        "-movflags", "+faststart",              # play before fully read
        out,
    ])
    return {"file": os.path.basename(out), "type": "video",
            "dur": probe_duration(out)}


def probe_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, timeout=30)
        return round(float((r.stdout or b"0").decode().strip()), 1)
    except Exception:
        return 0.0


# Per-item framing. Stored as intent — where to look and how close — rather
# than a cropped file. The gallery slot's shape is a setting the user can
# change, so a crop baked in at upload is wrong the moment 3:2 becomes 2:3.
# These three numbers re-render correctly at any ratio, and the original file
# is never touched.
FRAME_DEFAULT = {"fit": "auto", "zoom": 1.0, "x": 50, "y": 50}


def clean_frame(got):
    out = dict(FRAME_DEFAULT)
    if not isinstance(got, dict):
        return out
    fit = got.get("fit")
    if fit in ("auto", "contain", "cover"):
        out["fit"] = fit
    try:
        out["zoom"] = round(max(1.0, min(4.0, float(got.get("zoom", 1.0)))), 3)
    except (TypeError, ValueError):
        pass
    for axis in ("x", "y"):
        try:
            out[axis] = round(max(0.0, min(100.0, float(got.get(axis, 50)))), 1)
        except (TypeError, ValueError):
            pass
    return out


def adjust(fname, frame):
    """Set one item's framing. Returns the updated entry, or None."""
    fname = os.path.basename(fname)
    lst = items()
    hit = None
    for it in lst:
        if it.get("file") == fname:
            it["frame"] = clean_frame(frame)
            hit = it
    if hit:
        write_index(lst)
    return hit


def add(name, path):
    """Process one already-on-disk upload into the wall gallery. Returns its
    index entry.

    Takes a path rather than a bytes blob — control.py streams every upload
    to a temp file in fixed-size chunks specifically so the whole thing is
    never resident in memory at once, and passing a blob through here used
    to undo that: it read the file straight back into RAM just to hand it
    to functions (PIL, ffmpeg) that work directly from a path anyway.
    """
    stem = stem_of(name)
    kind = kind_of(path, name)
    if kind == "still":
        item = save_still(path, stem)
    elif kind == "gif":
        item = save_video(path, stem, ".gif")
        item["from"] = "gif"
    else:
        item = save_video(path, stem, ".mp4")
    item["frame"] = dict(FRAME_DEFAULT)
    item["ts"] = int(time.time())
    item["name"] = name[:60]
    write_index(items() + [item])
    return item


def items():
    try:
        with open(INDEX) as f:
            got = json.load(f).get("items", [])
    except Exception:
        got = []
    # Drop entries whose file has been deleted from disk by hand.
    out = []
    for i in got:
        if not os.path.exists(os.path.join(WALL_DIR, i.get("file", ""))):
            continue
        # Items uploaded before framing existed have no frame key.
        i["frame"] = clean_frame(i.get("frame"))
        out.append(i)
    return out


def write_index(lst):
    os.makedirs(WALL_DIR, exist_ok=True)
    lst = sorted(lst, key=lambda i: i.get("ts", 0))
    # Oldest out first once over the cap, and take their files with them.
    while len(lst) > MAX_ITEMS:
        gone = lst.pop(0)
        try:
            os.remove(os.path.join(WALL_DIR, gone["file"]))
        except OSError:
            pass
    tmp = INDEX + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"ts": int(time.time()), "items": lst}, f,
                  separators=(",", ":"))
    os.replace(tmp, INDEX)       # atomic: the page never reads a half file
    return lst


def remove(fname):
    fname = os.path.basename(fname)
    keep = [i for i in items() if i.get("file") != fname]
    try:
        os.remove(os.path.join(WALL_DIR, fname))
    except OSError:
        pass
    write_index(keep)
    return keep
