#!/usr/bin/env python3
"""
fetch.py — pull the things the browser can't, because of CORS.

Weather is fetched by the page itself (Open-Meteo sends CORS headers).
News and calendar can't be, so they land on disk here and the dashboard
reads them from localhost.
"""

import os
import sys

import requests

from config import NEWS_FEEDS, DATA_DIR

os.makedirs(DATA_DIR, exist_ok=True)

for i, url in enumerate(NEWS_FEEDS, start=1):
    dest = f"{DATA_DIR}/news{i}.xml"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "board/1.0"})
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)
        print(f"ok   {url}")
    except Exception as e:
        # leave the last good copy in place rather than blanking the panel
        print(f"fail {url} ({e})", file=sys.stderr)
