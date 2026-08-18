# board

A wall dashboard and a Pixoo-64 status board, fed by one always-on machine —
a Mac mini or a Raspberry Pi.

- **Wall display** — a 16:9 kiosk page: clock, weather, calendar, news, DirtCheck, BathroomReport
- **Pixoo-64** — four rotating 64×64 screens, each with a bar that answers a
  different question

Both read from the same `data/` directory. One set of cron jobs feeds them.

---

## Fresh Pi, start to finish

### 1. Before writing the card

In Raspberry Pi Imager, click the gear icon and set:

- hostname → `board`
- enable SSH, paste your MacBook's public key
- wifi SSID and password
- username and locale

Choose **Pi OS Desktop** for the wall display, or **Lite** if you only want the Pixoo.

Doing this here means you never need a monitor or keyboard on the Pi.

### 2. Boot and connect

```bash
ssh you@board.local
```

### 3. Install

```bash
git clone https://github.com/YOU/board.git
cd board
./setup.sh
```

That installs nginx and the Python libraries, creates the web root, does a
first data pull, installs the cron job and the `pixoo` systemd service, and
sets up kiosk autostart if a desktop is present.

### 4. Fill in config.py

Edit it on GitHub, then on the Pi:

```bash
git pull && sudo systemctl restart pixoo
```

The two values that must be right before anything works:

| Value | Where to find it |
| --- | --- |
| `PIXOO_IP` | Divoom app → your device → settings |
| `ICS_URL` | Pi only. Apple Calendar → right-click calendar → Share Calendar → Public Calendar, then change `webcal://` to `https://`. Not needed on a Mac. |
| `PIHOLE_HOST` | Optional. Address of the Pi-hole box. Blank drops the screen from the rotation. No credential needed on v6 with no web password set. |

Give the Pixoo a DHCP reservation in your router so its IP doesn't move.

### 5. Start it

```bash
sudo systemctl start pixoo
```

Wall display is at `http://board.local/`, and the kiosk comes up on reboot.

---

## Running it on a Mac mini instead

If the Mac mini is already awake around the clock, it's a fine host for the
Pixoo half — no Pi needed.

```bash
git clone https://github.com/YOU/board.git
cd board && ./setup-mac.sh
```

That installs into a venv inside the repo and runs everything through
`launchd` instead of systemd and cron. Data lands in
`~/Library/Application Support/board/data`. The dashboard is served at
`http://localhost:8080/`.

**No published calendar needed.** On a Mac the board reads Calendar.app
directly through EventKit, so nothing goes on the public internet and it sees
subscribed and work calendars too. macOS will ask for Calendar access the
first time — `setup-mac.sh` triggers that prompt from Terminal on purpose,
because a launchd background agent can't display it. Approve it there and
it's done.

**Disable sleep first.** A sleeping Mac stops the board:

```bash
sudo pmset -a sleep 0 disksleep 0
```

**The wall display is still a Pi job.** A Mac can't sensibly sit in kiosk mode
on its only monitor while you're also using it. If you want both, run
`setup-mac.sh` on the mini for the Pixoo and `setup.sh` on a Pi for the wall.
They can point at the same repo.

Useful commands:

```bash
tail -f pixoo.log
launchctl list | grep com.board
launchctl kickstart -k gui/$(id -u)/com.board.pixoo   # restart after a pull
launchctl bootout gui/$(id -u)/com.board.pixoo        # stop
```

## Files

| File | What it does |
| --- | --- |
| `config.py` | Shared settings, tracked by git |
| `local_config.py` | Machine-specific values, never committed |
| `board.py` | Draws and pushes the Pixoo-64 screens |
| `index.html` | The wall dashboard |
| `control.py` | Web panel on :8081 — Pixoo rotation, uploads, and the two editors |
| `layout.py` | Wall slots, and the drag-and-drop layout editor |
| `wallmedia.py` | Wall-resolution photo/GIF/video processing |
| `wallpage.py` | Gallery page — uploads and per-image framing |
| `dirtcheck.py` | Reads DirtCheck's events + status into flag state and track rows |
| `nascar.py` | Next race per series |
| `jellyfin.py` | Now-playing state and artwork |
| `kuma.py` | Uptime Kuma service health |
| `pihole.py` | Pi-hole v6/v5 stats |
| `sysnet.py` | Pi health and network health; run standalone to inspect |
| `lifx_jf.py` | Drives a LIFX bulb from Jellyfin now-playing art |
| `nextevent.py` | Expands recurring events from a published .ics (Pi) |
| `nextevent-mac.py` | Reads Calendar.app directly via EventKit (Mac) |
| `fetch.py` | Pulls RSS feeds and NWS alerts past CORS |
| `pixoo_client.py` | One HTTP POST to the device |
| `setup.sh` | Installs all of the above on a Pi |
| `setup-kiosk.sh` | Chromium kiosk + screen schedule on the wall Pi |
| `kiosk-run.sh` / `screen.sh` | Kiosk launcher and blanking helper |
| `setup-mac.sh` | Same as setup.sh, for a Mac mini (launchd instead of systemd) |

## The wall board

Modules are arranged into **slots** — rectangles on a 12x18 grid holding an
ordered list of modules. Slots cannot overlap. Each has a mode:

- **One** — a single module
- **Takeover** — first module with something to show; list order is priority
- **Rotate** — cycle those with something to show, N seconds each

Edit at `:8081/layout`. Gallery uploads and per-photo framing at `:8081/gallery`.

| Module | Shows |
| --- | --- |
| Flag strip | Racing tonight, rained out, or standby |
| Racing | 3 dirt tracks and 3 NASCAR series |
| Weather | Now, three-day forecast, and NWS alerts |
| Radar | Rain moving in, with the three tracks pinned |
| Wire | Headlines from `NEWS_FEEDS` |
| Now playing | Jellyfin, only while something streams |
| Services | Uptime Kuma health |
| Pi-hole | Share of DNS blocked today, and traffic |
| Gallery | Photos, GIFs and clips you've uploaded |

## The screens

| Screen | Shows | Bar |
| --- | --- | --- |
| `flag` | all three tracks, next date and rain % | flag colour, or `DIRT CHK` when nothing's on |
| `weather` | high/low, temp, rain and wind | sky condition with a pixel sprite |
| `traffic` | active users and new users | direction against yesterday |
| `health` | engagement seconds and bot sessions | error count, or `NO ERRORS` |
| `pihole` | share of DNS blocked today, queries vs blocked, 24h sparkline | `BLOCKING`, or `PAUSED` when blocking is off |

Track and weather screens use a warm dark palette; the two BathroomReport
screens use that project's own navy and teal so they read as a different place.

Rotation has three moods: race nights give the tracks 30 seconds, mornings
lead with weather, and the rest of the day spreads evenly.

## Checking on things

```bash
systemctl status pixoo
journalctl -u pixoo -f
python3 board.py --screen weather      # force one screen
python3 board.py --preview out/        # render PNGs, no device needed
```

`--preview` works anywhere, including your MacBook. Useful for changing a
layout without touching the Pi.

## Wiring in real data

`board.py` ships with demo data and falls back to it whenever a fetch fails,
so it renders correctly before anything is connected. Two mappings in
`fetch()` convert your real JSON into what the screens expect:

Both are wired to the real files and need no mapping changes:

**DirtCheck** — `events.json` (season schedule + track metadata) and
`status.json` (per-event flag, rain probability). Handled in `dirtcheck.py`.

**BathroomReport** — `analytics-data.json` at the site root, GA4 daily figures
plus the Clarity quality metrics. Handled inside `board.py`.

If either moves, change `config.py`. If the shape changes, change the mapping
module, not the drawing code.

## Notes

- The Pixoo dims to 12% between 10pm and 6am. Adjust in `config.py`.
- Everything is drawn with a 3x5 pixel font written into `board.py`, plus
  11x11 weather sprites. No font files, no anti-aliasing. Edit the string art
  to change a glyph or an icon.
- The Pixoo client is `pixoo_client.py` rather than the PyPI `pixoo` package,
  which needs Python 3.10+. The device API is one HTTP POST.
- The calendar screen was removed from the Pixoo, but `nextevent-mac.py` still
  runs for the wall dashboard's calendar panel.
- If the kiosk page comes up blank, check `http://localhost/data/next.json`
  loads on the Pi first. A 404 there means the cron job hasn't run yet.
