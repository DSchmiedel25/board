"""
jellyfin.py — turn the Jellyfin API into the fields the Pixoo screens need.

Two endpoints, both on the local server, so polling them is nearly free:

  /Sessions        who is connected, and what (if anything) is playing
  /Items/Counts    library totals

Artwork comes from /Items/{id}/Images/Primary with fillWidth/fillHeight, so
the server does the crop. Scaling a 2:3 poster into a 64x64 panel ourselves
either letterboxes it into a stripe or squashes the faces.

Counts barely move; sessions move by the second. They're cached separately
for that reason — see COUNTS_TTL.
"""

import io
import time

import requests

TIMEOUT = 6
COUNTS_TTL = 300          # seconds; a library scan is the only thing that moves it

_counts_cache = ({}, 0.0)
_art_cache = (None, None)  # (item_id, PIL image)


# ---------------------------------------------------------------- transport

def _get(base, key, path, raw=False):
    r = requests.get(base.rstrip("/") + path,
                     headers={"X-Emby-Token": key},
                     timeout=TIMEOUT)
    r.raise_for_status()
    return r.content if raw else r.json()


def sessions(base, key):
    """Every failure here is a blank screen, not a crash — the board is
    allowed to be wrong for five seconds, never allowed to stop."""
    try:
        d = _get(base, key, "/Sessions")
        return d if isinstance(d, list) else []
    except Exception:
        return []


def counts(base, key, now=None):
    global _counts_cache
    now = now or time.time()
    cached, at = _counts_cache
    if cached and now - at < COUNTS_TTL:
        return cached
    try:
        d = _get(base, key, "/Items/Counts") or {}
        _counts_cache = (d, now)
        return d
    except Exception:
        return cached          # last good numbers beat zeros


def poster(base, key, item_id, size=64, clear=None):
    """The whole poster, fitted, on a blurred fill of itself.

    Asking Jellyfin for a square crop of a 2:3 poster throws away the top and
    bottom and leaves you with whatever happened to be in the middle band —
    usually an unrecognisable close-up. So the full poster comes down at
    natural aspect, gets scaled to fit, and the leftover width is filled with
    a blurred, darkened copy rather than dead panel.

    `clear` is the height not covered by the text strip. The poster is fitted
    into that band so its own title block isn't hidden under ours, while the
    blurred fill still runs the full panel.

    Cached on item id: the same poster stays up for a whole dwell, and
    Jellyfin re-encodes the image on every request.
    """
    clear = size if clear is None else clear
    global _art_cache
    if not item_id:
        return None
    cid, cimg = _art_cache
    if cid == (item_id, size, clear):
        return cimg

    try:
        from PIL import Image, ImageFilter, ImageEnhance, ImageOps
        # bigger than needed, then downscaled here — Jellyfin's own tiny
        # resize is soft, and LANCZOS on a larger source is much sharper
        raw = _get(base, key,
                   "/Items/%s/Images/Primary?maxWidth=300&quality=92"
                   % item_id, raw=True)
        src = Image.open(io.BytesIO(raw)).convert("RGB")

        back = ImageOps.fit(src, (size, size), Image.LANCZOS)
        back = back.filter(ImageFilter.GaussianBlur(size // 14 or 1))
        back = ImageEnhance.Brightness(back).enhance(0.45)
        back = ImageEnhance.Color(back).enhance(1.35)

        front = src.copy()
        front.thumbnail((size, clear), Image.LANCZOS)
        back.paste(front, ((size - front.width) // 2,
                           (clear - front.height) // 2))
        img = back
    except Exception:
        img = None

    _art_cache = ((item_id, size, clear), img)
    return img


_hi_cache = (None, None)


def poster_full(base, key, item_id, width=600):
    """The poster at native aspect and real resolution, for the wall display.

    Deliberately separate from poster(): that one returns a 64px square
    composed for the LED panel, and upscaling it for a 1080p screen produces
    exactly the blocky mess you would expect.
    """
    global _hi_cache
    if not item_id:
        return None
    cid, cimg = _hi_cache
    if cid == (item_id, width):
        return cimg
    try:
        from PIL import Image
        raw = _get(base, key,
                   "/Items/%s/Images/Primary?maxWidth=%d&quality=92"
                   % (item_id, width), raw=True)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        img = None
    _hi_cache = ((item_id, width), img)
    return img


# ---------------------------------------------------------------- mapping

def _pct(sess, item):
    total = item.get("RunTimeTicks") or 0
    pos = (sess.get("PlayState") or {}).get("PositionTicks") or 0
    if not total:
        return None
    return max(0, min(100, round(pos / total * 100)))


def build(sess_list, count_doc, user=None, device=None):
    """One dict, whether or not anything is playing. Screens read it directly.

    user/device narrow which stream counts. The Pixoo wants any of them — it's
    a house-wide board. The lamp wants one room, or it repaints itself when
    somebody else starts a film in another building.
    """
    playing = [s for s in (sess_list or []) if s.get("NowPlayingItem")]
    if user:
        playing = [s for s in playing
                   if (s.get("UserName") or "").lower() == user.lower()]
    if device:
        d = device.lower()
        playing = [s for s in playing
                   if d in (s.get("DeviceName") or "").lower()
                   or d in (s.get("Client") or "").lower()]
    doc = count_doc or {}

    out = {
        "streams": len(playing),
        "watchers": len({s.get("UserName") for s in playing}),
        "movies": doc.get("MovieCount", 0),
        "episodes": doc.get("EpisodeCount", 0),
        "series": doc.get("SeriesCount", 0),
        "playing": False,
        "title": "", "sub": "", "user": "",
        "paused": False, "pct": None, "art_id": None,
        "transcoding": False,
    }
    if not playing:
        return out

    # With several streams up, show the one furthest from finishing. A show
    # with four minutes left is the least useful thing to put on a wall.
    playing.sort(key=lambda s: _pct(s, s["NowPlayingItem"]) or 0)
    s = playing[0]
    item = s["NowPlayingItem"]

    if item.get("Type") == "Episode":
        title = item.get("SeriesName") or item.get("Name", "")
        se, ep = item.get("ParentIndexNumber"), item.get("IndexNumber")
        sub = "S%dE%d" % (se, ep) if se is not None and ep is not None \
            else (item.get("Name") or "")
        art = item.get("SeriesId") or item.get("Id")   # series art is recognisable
    else:
        title = item.get("Name", "")
        yr = item.get("ProductionYear")
        sub = str(yr) if yr else ""
        art = item.get("Id")

    method = str((s.get("PlayState") or {}).get("PlayMethod") or "")

    out.update({
        "playing": True,
        "title": title,
        "sub": sub,
        "user": s.get("UserName", ""),
        "paused": bool((s.get("PlayState") or {}).get("IsPaused")),
        "pct": _pct(s, item),
        "art_id": art,
        "transcoding": "transcode" in method.lower(),
    })
    return out
