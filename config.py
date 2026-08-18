"""
config.py — shared, public settings. TRACKED BY GIT.

Nothing machine-specific lives here. Your Pixoo IP, Jellyfin key and calendar
URL belong in local_config.py, which .gitignore keeps out of the repo.

That split exists for a practical reason: this file gets replaced wholesale
whenever you upload a new version, and anything you'd hand-edited here gets
wiped with it. local_config.py is never uploaded, so it survives every pull.

    # on the Pi, once:
    cp local_config.example.py local_config.py
    nano local_config.py
"""

# ---------------------------------------------------------------- location

LAT, LON = 42.8142, -73.9396          # Schenectady

# ---------------------------------------------------------------- sources

# DirtCheck publishes events.json (schedule) and status.json (flags, rain).
# Note: renaming a repo breaks its GitHub Pages URL — git redirects, Pages
# does not. If you rename again, this line has to change.
DIRTCHECK_BASE = "https://dschmiedel25.github.io/dirtcheck/data"

# RSS/Atom feeds for the wall dashboard's wire panel.
#
# (label, url). The label is what the card prints, so keep it short — it sits
# in a 15px mono eyebrow and anything past about six characters crowds the
# headline beside it. Named rather than positional on purpose: fetch.py used
# to write news1.xml, news2.xml by index, so deleting a feed from the middle
# renumbered every file after it and left an orphan on disk that still looked
# live. Labels are stable under edits.
NEWS_FEEDS = [
    ("NR",    "https://www.nationalreview.com/feed/"),
    ("EXAM",  "https://www.washingtonexaminer.com/feed/"),
    ("WIRE",  "https://www.dailywire.com/feeds/rss.xml"),
    ("DC",    "https://dailycaller.com/feed/"),
    ("BBART", "https://www.breitbart.com/feed/"),
    ("TH",    "https://townhall.com/rss"),
]

# National Weather Service active alerts for this point. No key, no cost.
# Silent most of the time, and the most useful thing on the board when it
# isn't. Set to False to drop the band entirely.
NWS_ALERTS = True

# The NWS asks for a contact address in the User-Agent and returns 403
# without a plausible one. Yours is better than a generic string if you ever
# need them to tell you why you're being throttled.
NWS_CONTACT = "board@localhost"

# Headlines kept in news.json. The card shows far fewer; the surplus is there
# so the rotation has somewhere to go and a single dead feed doesn't empty it.
NEWS_KEEP = 24

# Drop anything older than this. A feed that stops updating shouldn't leave
# three-day-old headlines sitting on the wall looking current.
NEWS_MAX_AGE_HOURS = 36

# Calendar entries containing any of these are dropped — races already have
# their own screen on the Pixoo.
CALENDAR_SKIP = ("albany-saratoga", "fonda", "lebanon valley")

# Uptime Kuma. Override in local_config.py if it isn't on this box.
KUMA_URL = "http://localhost:3001"
KUMA_SLUG = "board"

# ---------------------------------------------------------------- behavior

DAY_BRIGHTNESS = 75                   # 0-100
NIGHT_BRIGHTNESS = 12
NIGHT_START, NIGHT_END = 22, 6        # 24h clock

MORNING = (5, 10)                     # weather leads the rotation
RACE_DAYS = (4, 5)                    # Mon=0, so Fri and Sat
RACE_WINDOW = (15, 23)                # flag screen dominates in here

# Leave as None to show any stream in the house; set a username for only yours.
JELLYFIN_USER = None

# ---------------------------------------------------------------- paths

import sys as _sys, os as _os

if _sys.platform == "darwin":
    DATA_DIR = _os.path.expanduser("~/Library/Application Support/board/data")
else:
    DATA_DIR = "/var/www/html/data"

# ---------------------------------------------------------------- local

# Machine-specific values. Defaults are deliberately empty or harmless so a
# missing local_config.py produces a clear message rather than a confusing
# network error.
PIXOO_IP = ""
JELLYFIN_URL = "http://localhost:8096"
JELLYFIN_KEY = ""
ICS_URL = ""

# LIFX bulb driven by Jellyfin now-playing art (lifx_jf.py). Declared here
# with harmless defaults because a fresh clone otherwise fails on import:
# these lived only in local_config.py, so the service crashed on any machine
# that hadn't been set up by hand.
LIFX_IP = ""                          # blank disables the bulb entirely
LIFX_USER = None                      # only mirror this Jellyfin user
LIFX_DEVICE = None                    # bulb label, if more than one answers
LIFX_AUTO_POWER = True                # turn the bulb on when playback starts

# Pi-hole. Leave PIHOLE_HOST empty to drop the screen from the rotation.
# On v6 with no password set, no credential is needed — the API is open.
PIHOLE_HOST = ""
PIHOLE_PASSWORD = ""
PIHOLE_TOKEN = ""                     # v5 only

try:
    from local_config import *          # noqa: F401,F403  (overrides above)
except ImportError:
    pass
