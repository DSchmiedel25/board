"""
config.py — the only file you should need to edit.

Everything else in this repo reads from here. Change a value on GitHub,
then `git pull` on the Pi. No SSH editing.
"""

# ---------------------------------------------------------------- device

# Divoom app -> your device -> settings. Give it a DHCP reservation in your
# router so it doesn't move.
PIXOO_IP = "192.168.1.169"

# ---------------------------------------------------------------- location

LAT, LON = 42.8142, -73.9396          # Schenectady

# ---------------------------------------------------------------- sources

# DirtCall publishes events.json (schedule) and status.json (flags, rain).
DIRTCALL_BASE = "https://dschmiedel25.github.io/dirtcall/data"
BATHROOM_URL = "https://bathroomreport.app/analytics-data.json"

# Jellyfin. The board runs on the same Pi as the server, so localhost is
# right and the traffic never leaves the box. Create the key in
# Dashboard -> API Keys and name it something you'll recognise later.
JELLYFIN_URL = "http://localhost:8096"
JELLYFIN_KEY = "151ce4d992f74ad2a79ed41cd6e20774"

# LIFX bulb near the TV. lifx_jf.py caches its MAC on first discovery, so this
# only has to be right once — give the bulb a DHCP reservation anyway.
LIFX_IP = "192.168.1.218"

# Apple Calendar -> right-click the calendar -> Share Calendar -> Public
# Calendar -> copy link, then change webcal:// to https://
ICS_URL = "https://p00-caldav.icloud.com/published/2/REPLACE-WITH-YOURS"

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

MORNING = (5, 10)                     # calendar and weather lead
RACE_DAYS = (4, 5)                    # Mon=0, so Fri and Sat
RACE_WINDOW = (15, 23)                # flag screen dominates in here

# ---------------------------------------------------------------- paths

# Where fetched data lands. Defaults are sensible per platform; override
# only if you want it somewhere specific.
import sys as _sys, os as _os

if _sys.platform == "darwin":
    DATA_DIR = _os.path.expanduser("~/Library/Application Support/board/data")
else:
    DATA_DIR = "/var/www/html/data"
