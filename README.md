# board

A wall dashboard and a Pixoo-64 status board, both fed by one Raspberry Pi.

- **Wall display** — a 16:9 kiosk page: clock, weather, calendar, news, DirtCall, BathroomReport
- **Pixoo-64** — six rotating 64×64 screens with a flag bar that always answers "is something happening"

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
| `config.py` | Everything you edit. Nothing else should need changing. |
| `board.py` | Draws and pushes the Pixoo-64 screens |
| `nextevent.py` | Expands recurring events from a published .ics (Pi) |
| `nextevent-mac.py` | Reads Calendar.app directly via EventKit (Mac) |
| `fetch.py` | Pulls RSS feeds past CORS |
| `index.html` | The wall dashboard |
| `setup.sh` | Installs all of the above on a Pi |
| `setup-mac.sh` | Same, for a Mac mini (launchd instead of systemd) |

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

**DirtCall** — `state` (one of `racing`, `rained`, `watch`, `standby`),
`track`, `town`, `countdown`, `label`

**BathroomReport** — `locations`, `scans_today`, `scans_week` (7 numbers),
`pending`, `top_chain`

Change the mappings, not the drawing code.

## Notes

- The Pixoo dims to 12% between 10pm and 6am. Adjust in `config.py`.
- Screen rotation has three moods: mornings lead with calendar and weather,
  Friday and Saturday evenings hand 30 seconds to the flag, everything else
  spreads evenly.
- If the kiosk page comes up blank, check `http://localhost/data/next.json`
  loads on the Pi first. A 404 there means the cron job hasn't run yet.
