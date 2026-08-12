#!/usr/bin/env bash
#
# setup-mac.sh — install the Pixoo board on a Mac mini.
#
#   git clone https://github.com/YOU/board.git
#   cd board && ./setup-mac.sh
#
# Installs into a venv inside the repo and runs everything through launchd.
# Safe to run again after a `git pull`.
#
# This sets up the Pixoo board and the data fetching. The wall dashboard is
# served at http://localhost:8080/ but a Mac can't sensibly sit in kiosk mode
# while you're also using it — if you want the wall display, run setup.sh on
# a Pi and point it at the same repo.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO/.venv"
PY="$VENV/bin/python3"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

say() { printf '\n\033[1;33m▸ %s\033[0m\n' "$1"; }

# ---------------------------------------------------------------- python

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Xcode command line tools first:"
  echo "  xcode-select --install"
  exit 1
fi

say "Creating virtualenv"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet \
  pillow requests pyobjc-framework-EventKit

# ---------------------------------------------------------------- data dir

DATA_DIR="$("$PY" -c 'import config; print(config.DATA_DIR)')"
say "Data directory: $DATA_DIR"
mkdir -p "$DATA_DIR"

# ---------------------------------------------------------------- refresh

cat > "$REPO/refresh.sh" <<EOF
#!/usr/bin/env bash
cd "$REPO"
"$PY" fetch.py
"$PY" nextevent-mac.py
EOF
chmod +x "$REPO/refresh.sh"

say "Fetching data for the first time"
echo "  macOS will ask for Calendar access — approve it. This prompt only"
echo "  appears when run from Terminal, which is why we do it here."
"$REPO/refresh.sh" || echo "  (calendar or news failed — see above)"

# ---------------------------------------------------------------- launchd

mkdir -p "$AGENTS"

write_agent() {
  local label="$1" plist="$AGENTS/$1.plist"
  shift
  cat > "$plist"
  # bootout first so re-runs pick up changes
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$plist"
  echo "  loaded $label"
}

say "Installing launch agents"

write_agent com.board.pixoo <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.board.pixoo</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$REPO/board.py</string>
    <string>--loop</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$REPO/pixoo.log</string>
  <key>StandardErrorPath</key><string>$REPO/pixoo.log</string>
</dict>
</plist>
EOF

write_agent com.board.refresh <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.board.refresh</string>
  <key>ProgramArguments</key>
  <array><string>$REPO/refresh.sh</string></array>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$REPO/refresh.log</string>
  <key>StandardErrorPath</key><string>$REPO/refresh.log</string>
</dict>
</plist>
EOF

write_agent com.board.web <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.board.web</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-m</string><string>http.server</string><string>8080</string>
    <string>--directory</string><string>$REPO/www</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
EOF

# web root: the page plus a symlink to wherever the data actually lives
mkdir -p "$REPO/www"
ln -sf "$REPO/index.html" "$REPO/www/index.html"
ln -sf "$DATA_DIR" "$REPO/www/data"

# ---------------------------------------------------------------- sleep

say "Checking sleep settings"
if pmset -g | grep -qE '^\s*sleep\s+0'; then
  echo "  sleep already disabled"
else
  echo "  This Mac can sleep, which will stop the board."
  echo "  Fix it with:  sudo pmset -a sleep 0 disksleep 0"
fi

cat <<EOF

────────────────────────────────────────────────
  Done.

  Wall dashboard   http://localhost:8080/
  Pixoo            running under launchd

  Set PIXOO_IP in config.py, then:
    git pull
    launchctl kickstart -k $DOMAIN/com.board.pixoo

  Check on things:
    tail -f $REPO/pixoo.log
    launchctl list | grep com.board
    $PY board.py --screen weather

  Stop it:
    launchctl bootout $DOMAIN/com.board.pixoo
────────────────────────────────────────────────
EOF
