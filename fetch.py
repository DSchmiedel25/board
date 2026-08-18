#!/usr/bin/env python3
"""
fetch.py — pull the things the browser can't, because of CORS.

Weather is fetched by the page itself (Open-Meteo sends CORS headers). RSS
almost never does, so the feeds land here instead.

This writes one normalized news.json rather than raw XML per feed. Two
reasons. The page stays dumb — it reads a list of objects, exactly like it
reads state.json, and never learns what an Atom namespace is. And the messy
part happens once here in Python instead of six times in JavaScript on a Pi:
RSS says <item>/<pubDate>, Atom says <entry>/<updated>, dates arrive in two
incompatible formats, and several of these feeds wrap titles in CDATA with
HTML entities inside.

Failure is per-feed. A source that times out keeps its last good headlines
rather than blanking, and only disappears once those age out.
"""

import html
import json
import os
import re
import sys
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests

from config import NEWS_FEEDS, NEWS_KEEP, NEWS_MAX_AGE_HOURS, DATA_DIR

DEST = os.path.join(DATA_DIR, "news.json")
TIMEOUT = 20
UA = {"User-Agent": "board/1.0 (+wall dashboard)"}


# ElementTree reports tags as {namespace}tag. Every feed here is either RSS 2.0
# (no namespace on the core elements) or Atom, so stripping the brace prefix
# and matching on the local name covers all of them without a namespace map.
def tag(el):
    return el.tag.rsplit("}", 1)[-1].lower()


def child(el, *names):
    """First direct child matching any local name."""
    for c in el:
        if tag(c) in names:
            return c
    return None


def text(el):
    if el is None:
        return ""
    # itertext() so <title>Foo <b>bar</b></title> doesn't truncate at "Foo ".
    s = "".join(el.itertext())
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", "", s)          # stray markup inside CDATA
    return re.sub(r"\s+", " ", s).strip()


def link_of(entry):
    """RSS puts the URL in <link>'s text. Atom puts it in an href attribute."""
    for c in entry:
        if tag(c) != "link":
            continue
        rel = (c.get("rel") or "alternate").lower()
        if c.get("href") and rel == "alternate":
            return c.get("href").strip()
        if (c.text or "").strip():
            return c.text.strip()
    t = text(child(entry, "guid", "id"))
    return t if t.startswith("http") else ""


def when(entry):
    """Epoch seconds, or None. RSS uses RFC 822, Atom uses ISO 8601."""
    raw = text(child(entry, "pubdate", "published", "updated", "date"))
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).timestamp()
    except Exception:
        pass
    try:
        # fromisoformat can't read a trailing Z before 3.11.
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


# The same story from several outlets is the normal case with this feed list,
# so dedupe on a flattened title as well as on URL. Leading kickers and
# trailing " - Outlet" suffixes are stripped first.
STOP = {"the", "a", "an", "of", "to", "in", "on", "for",
        "and", "is", "as", "at", "with", "after", "over"}


def fingerprint(title):
    s = title.lower()
    s = re.sub(r"^\s*(watch|exclusive|breaking|report|opinion|video|poll)\s*[:\-\u2013]\s*", "", s)
    s = re.sub(r"\s+[\-\u2013\u2014|]\s+[^\-\u2013\u2014|]{0,28}$", "", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return " ".join([w for w in s.split() if w not in STOP][:9])


def parse(blob, label):
    try:
        root = ET.fromstring(blob)
    except ET.ParseError as e:
        raise ValueError("malformed XML (%s)" % e)

    out = []
    for e in [el for el in root.iter() if tag(el) in ("item", "entry")]:
        title = text(child(e, "title"))
        url = link_of(e)
        if not title or not url:
            continue
        out.append({"source": label, "title": title, "url": url, "ts": when(e)})
    if not out:
        raise ValueError("no items found")
    return out


def load_previous():
    """Last good run, keyed by source, so a failed fetch degrades rather than
    vanishing."""
    try:
        with open(DEST) as f:
            doc = json.load(f)
    except Exception:
        return {}
    prev = {}
    for it in doc.get("items", []):
        prev.setdefault(it.get("source"), []).append(it)
    return prev


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    prev = load_previous()
    now = time.time()
    floor = now - NEWS_MAX_AGE_HOURS * 3600

    by_source, failed = {}, []
    for label, url in NEWS_FEEDS:
        try:
            r = requests.get(url, timeout=TIMEOUT, headers=UA)
            r.raise_for_status()
            by_source[label] = parse(r.content, label)
            print("ok   %-6s %3d items" % (label, len(by_source[label])))
        except Exception as e:
            failed.append(label)
            if prev.get(label):
                by_source[label] = prev[label]
            print("fail %-6s %s" % (label, e), file=sys.stderr)

    for label in by_source:
        by_source[label].sort(key=lambda i: i.get("ts") or 0, reverse=True)

    # Take one from each source in turn rather than sorting everything by
    # time. Straight chronological order lets whichever outlet posts most
    # often own the panel; round-robin means the card is never one masthead.
    merged, seen_url, seen_fp = [], set(), set()
    order = [lbl for lbl, _u in NEWS_FEEDS if lbl in by_source]
    depth = max((len(v) for v in by_source.values()), default=0)
    for i in range(depth):
        if len(merged) >= NEWS_KEEP:
            break
        for label in order:
            bucket = by_source[label]
            if i >= len(bucket):
                continue
            it = bucket[i]
            ts = it.get("ts")
            if ts and ts < floor:
                continue
            fp = fingerprint(it["title"])
            if it["url"] in seen_url or (fp and fp in seen_fp):
                continue
            seen_url.add(it["url"])
            seen_fp.add(fp)
            merged.append({"source": it["source"], "title": it["title"],
                           "url": it["url"], "ts": ts})
            if len(merged) >= NEWS_KEEP:
                break

    doc = {"ts": int(now), "items": merged, "sources": order, "failed": failed}
    tmp = DEST + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, DEST)          # atomic: the page never reads a half file
    print("wrote %d headlines to %s" % (len(merged), DEST))
    if failed and not merged:
        sys.exit(1)


if __name__ == "__main__":
    main()
