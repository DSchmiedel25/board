"""
sysnet.py — what the Pi and its network are doing.

Two questions, one collector, because they share the expensive part: reading
counters that only mean something as a delta. CPU percentage and interface
throughput are both (now - then) / elapsed, and the "then" has to survive
between runs, so both live behind the same cache file.

Everything degrades rather than raises. A missing vcgencmd, a ping binary that
isn't there, a mount that's gone — each drops its own field and leaves the
rest. A health card that goes blank because one probe failed is worse than no
health card, since a blank card looks like the Pi is fine.

The design rule for the wall: numbers are secondary. The card leads with
whether anything is wrong, in words, and the readings are there to answer
"how bad" once you've walked over. Same idea as the flag strip.
"""

import json
import os
import re
import subprocess
import time

try:
    from config import DATA_DIR
except ImportError:
    DATA_DIR = "/var/www/html/data"

CACHE = os.path.join(DATA_DIR, "sysnet-prev.json")

# Pings are the only slow part. Local metrics are free and refresh every call;
# the network probe reuses its last answer until this many seconds have gone.
NET_EVERY = 60

# Thresholds. These are the Pi's actual limits, not round numbers: Broadcom
# soft-throttles at 80C and hard-throttles at 85, so 80 is the point where
# something is already happening rather than a guess at "warm".
TEMP_WARN, TEMP_BAD = 70.0, 80.0
DISK_WARN, DISK_BAD = 85, 93
MEM_WARN, MEM_BAD = 85, 94
WAN_WARN, WAN_BAD = 120.0, 250.0     # ms
LOSS_WARN, LOSS_BAD = 1, 20          # percent


# --------------------------------------------------------------- utilities

def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _run(cmd, timeout=6):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _cache_load():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _cache_save(doc):
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f)
        os.replace(tmp, CACHE)
    except OSError:
        pass


# ------------------------------------------------------------------- the pi

def temp_c():
    raw = _read("/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(int(raw) / 1000.0, 1)
    except (TypeError, ValueError):
        return None


def throttled():
    """Under-voltage is the Pi failure mode, and it is invisible otherwise.

    A tired phone charger or a cheap USB-C cable browns out under load and the
    firmware quietly drops the clock. Everything gets slow and nothing in any
    log says why. The 'ever' bits persist since boot, which is exactly what
    you want on a wall: the dip happened at 3am and you were asleep.
    """
    out = _run(["vcgencmd", "get_throttled"])
    if not out or "=" not in out:
        return None
    try:
        bits = int(out.split("=")[1].strip(), 16)
    except ValueError:
        return None
    return {
        "uv_now":    bool(bits & 0x1),
        "cap_now":   bool(bits & 0x2),
        "thr_now":   bool(bits & 0x4),
        "soft_now":  bool(bits & 0x8),
        "uv_ever":   bool(bits & 0x10000),
        "thr_ever":  bool(bits & 0x40000),
        "raw":       bits,
    }


def _cpu_counters():
    line = (_read("/proc/stat") or "").split("\n")[0].split()
    if len(line) < 5 or line[0] != "cpu":
        return None
    vals = [int(v) for v in line[1:] if v.isdigit()]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)   # idle + iowait
    return sum(vals), idle


def cpu_pct(prev, now_t):
    """Busy percentage since the previous call, not since boot.

    Reading /proc/stat once gives the average since power-on, which on a
    machine with 40 days of uptime is a flat line that never moves. The delta
    is the only version of this number that means anything on a dashboard.
    """
    cur = _cpu_counters()
    if not cur:
        return None, prev
    total, idle = cur
    out = None
    p = prev.get("cpu")
    if p:
        dt_total, dt_idle = total - p[0], idle - p[1]
        if dt_total > 0:
            out = round(max(0.0, min(100.0, (1 - dt_idle / dt_total) * 100)), 1)
    prev["cpu"] = [total, idle]
    return out, prev


def memory():
    txt = _read("/proc/meminfo") or ""
    kb = {}
    for line in txt.split("\n"):
        parts = line.split(":")
        if len(parts) == 2:
            m = re.match(r"\s*(\d+)", parts[1])
            if m:
                kb[parts[0]] = int(m.group(1))
    total = kb.get("MemTotal")
    if not total:
        return None
    # MemAvailable is the kernel's own estimate of what a new process could
    # actually get. Total-minus-free counts the page cache as used, which on a
    # media server reads as 95% and panics people for no reason.
    avail = kb.get("MemAvailable", kb.get("MemFree", 0))
    used = total - avail
    out = {
        "total_mb": round(total / 1024),
        "used_mb": round(used / 1024),
        "pct": round(used / total * 100, 1),
    }
    st, sf = kb.get("SwapTotal", 0), kb.get("SwapFree", 0)
    if st:
        out["swap_pct"] = round((st - sf) / st * 100, 1)
    return out


def disks(mounts):
    out = []
    for m in mounts:
        try:
            s = os.statvfs(m)
        except OSError:
            continue
        total = s.f_blocks * s.f_frsize
        if total <= 0:
            continue
        # f_bavail, not f_bfree: the difference is the root reserve, which the
        # things writing to this disk cannot touch.
        free = s.f_bavail * s.f_frsize
        out.append({
            "mount": m,
            "free_gb": round(free / 1e9, 1),
            "total_gb": round(total / 1e9, 1),
            "pct": round((total - free) / total * 100, 1),
        })
    return out


def uptime_s():
    raw = _read("/proc/uptime")
    try:
        return int(float(raw.split()[0]))
    except (AttributeError, IndexError, ValueError):
        return None


# -------------------------------------------------------------- the network

def default_iface_and_gw():
    """Parse /proc/net/route rather than shelling out to `ip route`.

    Same answer, no subprocess, and it works identically whether or not
    iproute2 is installed.
    """
    txt = _read("/proc/net/route") or ""
    for line in txt.split("\n")[1:]:
        f = line.split()
        if len(f) >= 3 and f[1] == "00000000":
            gw_hex = f[2]
            try:
                octets = [int(gw_hex[i:i + 2], 16) for i in (6, 4, 2, 0)]
                return f[0], ".".join(str(o) for o in octets)
            except ValueError:
                return f[0], None
    return None, None


def link(iface):
    if not iface:
        return {}
    out = {"iface": iface,
           "state": _read("/sys/class/net/%s/operstate" % iface) or "unknown"}
    sp = _read("/sys/class/net/%s/speed" % iface)
    try:
        # Wireless returns -1 or errors here; a negative speed is not a speed.
        if sp is not None and int(sp) > 0:
            out["speed_mbps"] = int(sp)
    except ValueError:
        pass
    out["wifi"] = os.path.isdir("/sys/class/net/%s/wireless" % iface)
    return out


def _bytes(iface):
    rx = _read("/sys/class/net/%s/statistics/rx_bytes" % iface)
    tx = _read("/sys/class/net/%s/statistics/tx_bytes" % iface)
    try:
        return int(rx), int(tx)
    except (TypeError, ValueError):
        return None


def throughput(iface, prev, now_t):
    """Bits per second, from the same delta trick as CPU."""
    if not iface:
        return None, prev
    cur = _bytes(iface)
    if not cur:
        return None, prev
    out = None
    p = prev.get("net")
    if p and now_t > p[2]:
        el = now_t - p[2]
        drx, dtx = cur[0] - p[0], cur[1] - p[1]
        # Counter wrapped or the interface was reset: report nothing rather
        # than a fictional 400Gbps spike.
        if drx >= 0 and dtx >= 0:
            out = {"rx_bps": round(drx * 8 / el), "tx_bps": round(dtx * 8 / el)}
    prev["net"] = [cur[0], cur[1], now_t]
    return out, prev


def ping(host, count=2):
    """Latency and loss to one host.

    -W is per-reply in iputils and total in some others, so keep it generous
    and let the subprocess timeout be the real ceiling.
    """
    if not host:
        return None
    out = _run(["ping", "-n", "-q", "-c", str(count), "-i", "0.3",
                "-W", "1", host], timeout=count * 2 + 3)
    if out is None:
        return {"host": host, "loss": 100, "ms": None}
    res = {"host": host}
    m = re.search(r"(\d+(?:\.\d+)?)% packet loss", out)
    res["loss"] = round(float(m.group(1))) if m else None
    # min/avg/max/mdev — avg is the second field.
    m = re.search(r"=\s*[\d.]+/([\d.]+)/", out)
    res["ms"] = round(float(m.group(1)), 1) if m else None
    if res.get("loss") == 100:
        res["ms"] = None
    return res


def tailscale():
    out = _run(["tailscale", "status", "--json"], timeout=5)
    if not out:
        return None
    try:
        doc = json.loads(out)
    except ValueError:
        return None
    peers = (doc.get("Peer") or {}).values()
    return {
        "up": (doc.get("BackendState") == "Running"),
        "self": ((doc.get("Self") or {}).get("DNSName") or "").rstrip("."),
        "online": sum(1 for p in peers if p.get("Online")),
        "peers": len(list(peers)),
    }


def probe_net(iface, gw, wan_host, prev, now_t, want_tailscale=True):
    net = dict(link(iface))
    if gw:
        net["gw"] = ping(gw)
    if wan_host:
        net["wan"] = ping(wan_host)
    if want_tailscale:
        ts = tailscale()
        if ts:
            net["tailscale"] = ts
    net["at"] = now_t
    return net


# ----------------------------------------------------------------- verdict

def verdict(doc):
    """Turn readings into a headline.

    Returns (level, list of short phrases). Level is what colours the card;
    the phrases are what it says. Ordered worst-first so the card can show
    one line and have it be the one that matters.
    """
    bad, warn = [], []
    hot = False

    t = doc.get("temp_c")
    if t is not None:
        if t >= TEMP_BAD:
            bad.append("CPU %g\u00b0C \u2014 throttling" % t)
            hot = True
        elif t >= TEMP_WARN:
            warn.append("CPU %g\u00b0C" % t)

    th = doc.get("throttled") or {}
    if th.get("uv_now"):
        bad.append("Under-voltage now \u2014 check the supply")
    elif th.get("thr_now") and not hot:
        # If the temperature line already said "throttling", saying it twice
        # just pushes the second real problem off a one-line card.
        bad.append("Throttled now")
    elif th.get("uv_ever"):
        warn.append("Under-voltage since boot")

    for d in doc.get("disks") or []:
        name = "Disk" if d["mount"] == "/" else d["mount"]
        if d["pct"] >= DISK_BAD:
            bad.append("%s %g%% full" % (name, d["pct"]))
        elif d["pct"] >= DISK_WARN:
            warn.append("%s %g%% full" % (name, d["pct"]))

    m = doc.get("mem") or {}
    if m.get("pct") is not None:
        if m["pct"] >= MEM_BAD:
            bad.append("Memory %g%%" % m["pct"])
        elif m["pct"] >= MEM_WARN:
            warn.append("Memory %g%%" % m["pct"])

    net = doc.get("net") or {}
    if net.get("state") not in (None, "up", "unknown"):
        bad.append("%s down" % (net.get("iface") or "Link"))
    for key, label in (("gw", "Gateway"), ("wan", "Internet")):
        p = net.get(key) or {}
        loss = p.get("loss")
        if loss is not None:
            if loss >= 100:
                bad.append("%s unreachable" % label)
            elif loss >= LOSS_BAD:
                bad.append("%s %d%% loss" % (label, loss))
            elif loss >= LOSS_WARN:
                warn.append("%s %d%% loss" % (label, loss))
    wan = net.get("wan") or {}
    if wan.get("ms") is not None and not wan.get("loss"):
        if wan["ms"] >= WAN_BAD:
            bad.append("Internet %gms" % wan["ms"])
        elif wan["ms"] >= WAN_WARN:
            warn.append("Internet %gms" % wan["ms"])
    ts = net.get("tailscale")
    if ts and not ts.get("up"):
        warn.append("Tailscale down")

    if bad:
        return "bad", bad + warn
    if warn:
        return "warn", warn
    # "Everything is fine" has to be earned. With no temperature, no disk and
    # no reachability answer there is nothing behind a green card, and a green
    # card that means "I couldn't tell" is worse than an honest blank one.
    seen = (doc.get("temp_c") is not None
            or bool(doc.get("disks"))
            or (doc.get("mem") or {}).get("pct") is not None
            or (net.get("gw") or {}).get("loss") is not None
            or (net.get("wan") or {}).get("loss") is not None)
    return ("ok" if seen else "unknown"), []


# -------------------------------------------------------------------- build

def build(mounts=("/",), wan_host="1.1.1.1", want_tailscale=True,
          net_every=NET_EVERY, now=None):
    """One dict describing the Pi and its network.

    Local readings are current every call. The network probe is rate-limited
    and reused from cache in between, because pinging twice per rotation step
    would put the Pixoo's animation at the mercy of a slow gateway.
    """
    now_t = now if now is not None else time.time()
    prev = _cache_load()

    doc = {"at": now_t}
    doc["temp_c"] = temp_c()
    th = throttled()
    if th:
        doc["throttled"] = th
    cpu, prev = cpu_pct(prev, now_t)
    if cpu is not None:
        doc["cpu_pct"] = cpu
    la = os.getloadavg() if hasattr(os, "getloadavg") else None
    if la:
        doc["load"] = [round(x, 2) for x in la]
        doc["cores"] = os.cpu_count()
    mem = memory()
    if mem:
        doc["mem"] = mem
    dk = disks(mounts)
    if dk:
        doc["disks"] = dk
    up = uptime_s()
    if up is not None:
        doc["uptime_s"] = up

    iface, gw = default_iface_and_gw()
    tp, prev = throughput(iface, prev, now_t)

    cached_net = prev.get("net_probe") or {}
    fresh = (now_t - (cached_net.get("at") or 0)) < net_every
    if fresh:
        net = dict(cached_net)
        net["cached"] = True
    else:
        net = probe_net(iface, gw, wan_host, prev, now_t, want_tailscale)
        prev["net_probe"] = net
    if tp:
        net.update(tp)                    # throughput is always live
    if gw:
        net.setdefault("gw_ip", gw)
    doc["net"] = net

    _cache_save(prev)

    level, notes = verdict(doc)
    doc["level"], doc["notes"] = level, notes
    return doc


if __name__ == "__main__":
    import sys as _s
    mounts = _s.argv[1:] or ["/"]
    print(json.dumps(build(mounts=mounts), indent=1))
