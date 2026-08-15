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

# Apple Calendar -> right-click the calendar -> Share Calendar -> Public
# Calendar -> copy link, then change webcal:// to https://
# Leave blank if you don't use the calendar.
ICS_URL = ""
