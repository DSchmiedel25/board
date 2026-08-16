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

# RSS feeds for the wall dashboard's wire panel. Add or remove freely.
NEWS_FEEDS = [
    "https://feeds.npr.org/1001/rss.xml",
    "https://feeds.washingtonpost.com/rss/national",
]

# Calendar entries containing any of these are dropped — races already have
# their own screen on the Pixoo.
CALENDAR_SKIP = ("albany-saratoga", "fonda", "lebanon valley")

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

# Pi-hole. Leave PIHOLE_HOST empty to drop the screen from the rotation.
# On v6 with no password set, no credential is needed — the API is open.
PIHOLE_HOST = ""
PIHOLE_PASSWORD = ""
PIHOLE_TOKEN = ""                     # v5 only

try:
    from local_config import *          # noqa: F401,F403  (overrides above)
except ImportError:
    pass
