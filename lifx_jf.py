#!/usr/bin/env python3
"""
lifx_jf.py — drive a LIFX bulb from whatever Jellyfin is playing.

Pulls the poster for the current item, finds its most vibrant colour, and
fades the bulb to it. When playback stops, the bulb goes back to exactly the
colour it was before we touched it.

Two rules keep this from being annoying:

  * If the bulb is off when playback starts, it stays off. A media server
    should not turn on a lamp.
  * Whatever the bulb was set to is captured on takeover and restored on
    release, so changing it by hand between films isn't overwritten.

  python3 lifx_jf.py --probe     list bulbs and their MACs
  python3 lifx_jf.py --once      apply once and exit
  python3 lifx_jf.py --loop      follow playback (systemd runs this)
"""

import argparse
import colorsys
import json
import os
import sys
import time

from lifxlan import LifxLAN, Light

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import JELLYFIN_URL, JELLYFIN_KEY, LIFX_IP     # noqa: E402
import jellyfin                                            # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".lifx.json")

POLL          = 4          # seconds between session checks
FADE_MS       = 1800       # transition into a new colour
RESTORE_MS    = 1200
MIN_SAT       = 0.35       # push washed-out posters up to at least this
MAX_BRIGHT    = 0.55       # a lamp at full tilt beside a TV is unpleasant
PAUSE_BRIGHT  = 0.25       # dip while paused
KELVIN        = 3500


# ---------------------------------------------------------------- device

def find_bulb(ip=LIFX_IP, tries=6):
    """Discovery is UDP broadcast and drops devices at random, so the MAC is
    cached on first success and used directly from then on. A service that
    dies because one broadcast went missing is not much of a service."""
    try:
        with open(CACHE) as f:
            mac = json.load(f)["mac"]
        return Light(mac, ip)
    except Exception:
        pass

    lan = LifxLAN()
    for _ in range(tries):
        for d in lan.get_lights():
            if d.get_ip_addr() == ip:
                try:
                    with open(CACHE, "w") as f:
                        json.dump({"mac": d.get_mac_addr(), "ip": ip}, f)
                except Exception:
                    pass
                return d
        time.sleep(2)
    return None


# ---------------------------------------------------------------- colour

def vibrant(img, boost=True):
    """Pick a colour worth looking at, not the average of the poster.

    Averaging a film poster reliably produces brown. Quantising and then
    scoring buckets by saturation against how much of the frame they cover
    finds the colour a person would name if you asked them about the poster.
    """
    small = img.convert("RGB").resize((48, 48))
    pal = small.quantize(colors=8, method=2).convert("RGB")
    counts = {}
    for px in pal.getdata():
        counts[px] = counts.get(px, 0) + 1
    total = sum(counts.values())

    best, best_score = None, -1.0
    for (r, g, b), n in counts.items():
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        share = n / total
        if v < 0.12:
            continue                       # near-black carries no hue
        # weight saturation heavily, but not so heavily that a stray three
        # pixels of neon beats the colour the poster is actually made of
        score = (s ** 1.5) * (0.35 + share) * (0.4 + v)
        if score > best_score:
            best, best_score = (h, s, v), score

    if best is None:
        return 0.08, 0.0, 0.5              # nothing usable — warm white
    h, s, v = best
    if boost:
        s = max(s, MIN_SAT)
        v = min(max(v, 0.35), MAX_BRIGHT)
    return h, s, v


def hsbk(h, s, v, kelvin=KELVIN):
    return [int(h * 65535), int(s * 65535), int(v * 65535), kelvin]


# ---------------------------------------------------------------- state

class Follower:
    def __init__(self, bulb):
        self.bulb = bulb
        self.saved = None          # colour + power before we took over
        self.current_art = None

    def _capture(self):
        if self.saved is None:
            self.saved = (self.bulb.get_color(), self.bulb.get_power())

    def release(self):
        if self.saved is None:
            return
        color, power = self.saved
        try:
            self.bulb.set_color(color, duration=RESTORE_MS)
            self.bulb.set_power(power)
        except Exception as e:
            print(f"restore failed: {e}", file=sys.stderr)
        self.saved = None
        self.current_art = None
        print("released")

    def apply(self, jf):
        if not jf["playing"]:
            self.release()
            return

        # never turn a dark room on by ourselves
        if self.saved is None and not self.bulb.get_power():
            return

        art_id = jf.get("art_id")
        if art_id and art_id != self.current_art:
            img = jellyfin.poster(JELLYFIN_URL, JELLYFIN_KEY, art_id, size=128)
            if img is None:
                return
            self._capture()
            h, s, v = vibrant(img)
            self.hsv = (h, s, v)
            self.current_art = art_id
            self.bulb.set_color(hsbk(h, s, v), duration=FADE_MS)
            print(f"{jf['title']}: h={h:.2f} s={s:.2f} v={v:.2f}")

        # dip while paused, come back when it resumes
        if self.saved is not None and hasattr(self, "hsv"):
            h, s, v = self.hsv
            want = PAUSE_BRIGHT if jf["paused"] else v
            self.bulb.set_color(hsbk(h, s, want), duration=700)


# ---------------------------------------------------------------- main

def read_jf():
    return jellyfin.build(jellyfin.sessions(JELLYFIN_URL, JELLYFIN_KEY),
                          jellyfin.counts(JELLYFIN_URL, JELLYFIN_KEY))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="list bulbs and MACs")
    ap.add_argument("--once", action="store_true", help="apply once and exit")
    ap.add_argument("--loop", action="store_true", help="follow playback")
    args = ap.parse_args()

    if args.probe:
        for d in LifxLAN().get_lights():
            print(f"{d.get_label():24} {d.get_ip_addr():16} {d.get_mac_addr()}")
        return

    bulb = find_bulb()
    if bulb is None:
        sys.exit(f"no LIFX bulb at {LIFX_IP} — check the IP in config.py")

    f = Follower(bulb)

    if args.once:
        f.apply(read_jf())
        return

    if args.loop:
        try:
            while True:
                try:
                    f.apply(read_jf())
                except Exception as e:
                    print(f"tick failed: {e}", file=sys.stderr)
                time.sleep(POLL)
        except KeyboardInterrupt:
            f.release()
        return

    ap.print_help()


if __name__ == "__main__":
    main()
