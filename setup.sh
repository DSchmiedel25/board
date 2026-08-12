#!/usr/bin/env bash
#
# setup.sh — clone-and-run install for a fresh Raspberry Pi.
#
#   git clone https://github.com/YOU/board.git
#   cd board && ./setup.sh
#
# Safe to run again after a `git pull`.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="$(whoami)"
DATA_DIR="$(python3 -c 'import config; print(config.DATA_DIR)' 2>/dev/null || echo /var/www/html/data)"
WEB_DIR="$(dirname "$DATA_DIR")"

say() { printf '\n\033[1;33m▸ %s\033[0m\n' "$1"; }

# ---------------------------------------------------------------- packages

say "Installing packages"
sudo apt-get update -qq
sudo apt-get install -y -qq nginx python3-pip git

say "Installing Python libraries"
pip3 install --break-system-packages -q \
  pillow requests icalendar recurring-ical-events

# ---------------------------------------------------------------- web root

say "Setting up $WEB_DIR"
sudo mkdir -p "$DATA_DIR"
sudo chown -R "$USER_NAME":"$USER_NAME" "$WEB_DIR"
ln -sf "$REPO/index.html" "$WEB_DIR/index.html"

# ---------------------------------------------------------------- first pull

say "Fetching data for the first time"
cd "$REPO"
python3 fetch.py || echo "  (news fetch failed — check NEWS_FEEDS in config.py)"
python3 nextevent.py || echo "  (calendar failed — set ICS_URL in config.py)"

# ---------------------------------------------------------------- cron

say "Installing cron job"
CRON_LINE="*/15 * * * * cd $REPO && /usr/bin/python3 fetch.py >/dev/null 2>&1; /usr/bin/python3 nextevent.py >/dev/null 2>&1"
( crontab -l 2>/dev/null | grep -v "cd $REPO" ; echo "$CRON_LINE" ) | crontab -

# ---------------------------------------------------------------- pixoo

say "Installing pixoo.service"
sudo tee /etc/systemd/system/pixoo.service >/dev/null <<EOF
[Unit]
Description=Pixoo-64 board
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$REPO
ExecStart=/usr/bin/python3 $REPO/board.py --loop
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable pixoo >/dev/null

# ---------------------------------------------------------------- kiosk

if [ -n "${DISPLAY:-}" ] || [ -d "$HOME/.config" ] && command -v chromium-browser >/dev/null 2>&1; then
  say "Installing kiosk autostart"
  cat > "$HOME/kiosk.sh" <<'EOF'
#!/bin/bash
xset s off -dpms 2>/dev/null || true
chromium-browser --kiosk --noerrdialogs --disable-infobars \
  --disable-session-crashed-bubble --check-for-update-interval=31536000 \
  http://localhost/
EOF
  chmod +x "$HOME/kiosk.sh"

  if [ -f "$HOME/.config/wayfire.ini" ]; then
    grep -q kiosk.sh "$HOME/.config/wayfire.ini" || \
      printf '\n[autostart]\nkiosk = %s/kiosk.sh\n' "$HOME" >> "$HOME/.config/wayfire.ini"
  else
    mkdir -p "$HOME/.config/labwc"
    grep -q kiosk.sh "$HOME/.config/labwc/autostart" 2>/dev/null || \
      echo "$HOME/kiosk.sh &" >> "$HOME/.config/labwc/autostart"
  fi
else
  say "No desktop found — skipping kiosk (Pixoo-only install)"
fi

# ---------------------------------------------------------------- done

cat <<EOF

────────────────────────────────────────────────
  Done.

  Wall dashboard   http://$(hostname).local/
  Pixoo            sudo systemctl start pixoo

  Before the Pixoo will work, set PIXOO_IP in
  config.py, then:  git pull && sudo systemctl restart pixoo

  Check on things:
    systemctl status pixoo
    journalctl -u pixoo -f
    python3 board.py --screen weather
────────────────────────────────────────────────
EOF
