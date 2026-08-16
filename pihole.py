"""
pihole.py — Pi-hole stats for the board.

Same split as dirtcheck.py and nascar.py: this module knows the API, board.py
knows the pixels. If Pi-hole changes its API, this is the only file that moves.

Handles both v6 (session auth, or open when no password is set) and v5 (token),
detected at call time rather than configured. Standard library only — the board
already carries requests, but there is nothing here that needs it.

Returns None on any failure so board.py can fall back to last-known-good, the
same way a failed DirtCheck pull does. Never returns partial or invented data:
a wrong "0% BLOCKED" on the wall is worse than an honest OFFLINE.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 6


def _json(url, method="GET", body=None, headers=None, timeout=TIMEOUT):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def clean_domain(d):
    """Strip the noise prefix so the footer shows the part that identifies who
    is phoning home, not fifteen characters of 'analytics.'."""
    if not d:
        return ""
    return re.sub(r"^(www|ssl|api|ads?|track(er)?|analytics|telemetry)\.",
                  "", d.strip().lower())


def client_name(name):
    """'Living-Room-TV.lan' -> 'LIVING-ROOM-TV'. A bare IP keeps its last two
    octets, since the first two are the same for everything on this LAN and
    spending characters on '192.168.' tells you nothing."""
    if not name:
        return "?"
    name = name.strip()
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", name):
        return ".".join(name.split(".")[2:])
    name = re.sub(r"\.(lan|local|home|localdomain)$", "", name, flags=re.I)
    return name.upper()


# ------------------------------------------------------------------ v6

class _V6:
    def __init__(self, base, password):
        self.base = base
        self.password = password
        self.sid = ""
        self.authed = False

    def auth(self):
        r = _json(f"{self.base}/api/auth", "POST", {"password": self.password})
        sess = r.get("session") or {}
        if not sess.get("valid", False):
            raise RuntimeError("password rejected")
        # No password configured -> valid session, null sid, open API.
        self.sid = sess.get("sid") or ""
        self.authed = True

    def get(self, path):
        if not self.authed:
            self.auth()
        head = {"X-FTL-SID": self.sid} if self.sid else {}
        try:
            return _json(f"{self.base}{path}", headers=head)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self.authed = False
                self.auth()
                head = {"X-FTL-SID": self.sid} if self.sid else {}
                return _json(f"{self.base}{path}", headers=head)
            raise

    def build(self):
        s = self.get("/api/stats/summary")
        q = s.get("queries", {})
        out = {
            "total": int(q.get("total", 0)),
            "blocked": int(q.get("blocked", 0)),
            "pct": float(q.get("percent_blocked", 0.0)),
            "domains": int(q.get("unique_domains", 0)),
            "forwarded": int(q.get("forwarded", 0)),
            "cached": int(q.get("cached", 0)),
            "clients": int((s.get("clients") or {}).get("active", 0)),
            "gravity": int((s.get("gravity") or {}).get("domains_being_blocked", 0)),
            "enabled": True,
            "top": "",
            "history": [],
        }
        try:
            b = self.get("/api/dns/blocking")
            out["enabled"] = str(b.get("blocking", "enabled")).lower() == "enabled"
        except Exception:
            pass
        try:
            t = self.get("/api/stats/top_domains?blocked=true&count=1")
            doms = t.get("domains") or []
            if doms:
                out["top"] = clean_domain(doms[0].get("domain", ""))
        except Exception:
            pass
        try:
            h = self.get("/api/history")
            out["history"] = [(int(e.get("total", 0)), int(e.get("blocked", 0)))
                              for e in (h.get("history") or [])]
        except Exception:
            pass
        try:
            c = self.get("/api/stats/top_clients?count=6")
            out["top_clients"] = [
                (client_name(e.get("name") or e.get("ip") or ""), int(e.get("count", 0)))
                for e in (c.get("clients") or [])
            ]
        except Exception:
            pass
        return out


# ------------------------------------------------------------------ v5

class _V5:
    def __init__(self, base, token):
        self.base = base
        self.token = token

    def get(self, params):
        return _json(f"{self.base}/admin/api.php?{params}"
                     f"&auth={urllib.parse.quote(self.token)}")

    def build(self):
        s = self.get("summaryRaw")
        out = {
            "total": int(s.get("dns_queries_today", 0)),
            "blocked": int(s.get("ads_blocked_today", 0)),
            "pct": float(s.get("ads_percentage_today", 0.0)),
            "domains": int(s.get("unique_domains", 0)),
            "forwarded": int(s.get("queries_forwarded", 0)),
            "cached": int(s.get("queries_cached", 0)),
            "clients": int(s.get("unique_clients", 0)),
            "gravity": int(s.get("domains_being_blocked", 0)),
            "enabled": str(s.get("status", "enabled")).lower() == "enabled",
            "top": "",
            "history": [],
        }
        try:
            t = self.get("topItems=1")
            ads = t.get("top_ads") or {}
            if ads:
                out["top"] = clean_domain(next(iter(ads)))
        except Exception:
            pass
        try:
            h = self.get("overTimeData10mins")
            dom = h.get("domains_over_time") or {}
            ad = h.get("ads_over_time") or {}
            keys = sorted(dom.keys(), key=lambda k: int(k))
            out["history"] = [(int(dom.get(k, 0)), int(ad.get(k, 0))) for k in keys]
        except Exception:
            pass
        try:
            c = self.get("getQuerySources=6")
            src = c.get("top_sources") or {}
            out["top_clients"] = [(client_name(k.split("|")[0]), int(v))
                                  for k, v in src.items()]
        except Exception:
            pass
        return out


# ------------------------------------------------------------------ public

def build(host, password="", token=""):
    """Fetch everything the Pi-hole screen needs. None if unreachable.

    `host` may be a bare address ("192.168.1.202") or a full base URL.
    """
    if not host:
        return None
    base = host if host.startswith("http") else f"http://{host}"
    base = base.rstrip("/")

    # v6 answers /api/auth; v5 404s it. Detect rather than make it a setting.
    client = None
    try:
        _json(f"{base}/api/auth", "POST", {"password": password or ""})
        client = _V6(base, password or "")
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            client = _V6(base, password or "")
        else:
            client = _V5(base, token or password or "")
    except Exception:
        client = _V5(base, token or password or "")

    try:
        out = client.build()
    except Exception:
        return None

    # A summary with no queries at all is more likely a broken read than a
    # genuinely silent network, so treat it as a miss and keep last-known-good.
    if not out or out.get("total", 0) <= 0 and not out.get("history"):
        return None
    return out
