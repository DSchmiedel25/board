"""
local_config.example.py — copy to local_config.py and fill in.

    cp local_config.example.py local_config.py

local_config.py is in .gitignore, so it never reaches GitHub and never gets
overwritten by a pull. Anything set here wins over config.py.
"""

# Divoom app -> your device -> settings. Give it a DHCP reservation in your
# router so it doesn't move.
PIXOO_IP = "192.168.1.xxx"

# Jellyfin. Use localhost if the board runs on the Jellyfin box itself.
JELLYFIN_URL = "http://localhost:8096"
JELLYFIN_KEY = ""

# Pi-hole. Address of the box running it. Leave blank to drop the screen from
# the rotation entirely. On v6 with no web password set, no credential is
# needed — the API is open. Set PIHOLE_PASSWORD to an app password if you add
# one later (Settings -> Web interface / API -> Configure app password).
PIHOLE_HOST = "192.168.1.202"
PIHOLE_PASSWORD = ""
PIHOLE_TOKEN = ""                # v5 only

# Health cards. SYS_MOUNTS is what gets a fullness bar — an unmounted path is
# skipped rather than erroring, so listing the media mount is safe even when
# the Mac Mini is off. Point SYS_WAN_HOST at something that answers ICMP fast
# and isn't the thing you're diagnosing: not your router, not a Tailscale peer.
SYS_MOUNTS = ["/", "/mnt/media"]
SYS_WAN_HOST = "1.1.1.1"
SYS_TAILSCALE = True
SYS_NET_EVERY = 60               # seconds between pings

# Apple Calendar -> right-click the calendar -> Share Calendar -> Public
# Calendar -> copy link, then change webcal:// to https://
# Leave blank if you don't use the calendar.
ICS_URL = ""

# Uptime Kuma. Only set these if Kuma is NOT on this box at port 3001, or if
# your published status page uses a slug other than "board".
# KUMA_URL = "http://192.168.1.xxx:3001"
# KUMA_SLUG = "board"

# National Weather Service asks for a contact address and returns 403 without
# a plausible one. Only needed if NWS_ALERTS is on.
NWS_CONTACT = "you@example.com"

# LIFX bulb, if you run lifx_jf.py. Blank LIFX_IP leaves it off.
LIFX_IP = ""

# HomeKit. Generate HOMEKIT_TOKEN once with:
#   python3 -c "import secrets; print(secrets.token_hex(16))"
# and paste the same value into the Shortcut's request body. Anyone who
# doesn't send it gets a 403.
HOMEKIT_TOKEN = ""

# Lights/switches read directly over HAP once paired (see README — pair with
# `python3 -m aiohomekit`, then find aid/iid with `accessories -o compact`).
# HOMEKIT_LIGHTS = [
#     {"label": "Living Room", "aid": 5,  "iid": 10},
#     {"label": "Office",      "aid": 12, "iid": 8},
# ]
HOMEKIT_LIGHTS = []
