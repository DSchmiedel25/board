#!/usr/bin/env bash
#
# setup-kiosk.sh — turn a Pi with a monitor into the wall display.
#
# Run this on the Pi that drives the screen. It is deliberately separate from
# setup.sh: that one installs the data side (nginx, cron, the pixoo service),
# and this one only installs the thing that shows it. On a one-Pi setup you
# run both. On a two-Pi setup this is all the wall machine needs, and
# BOARD_URL points at the other one.
#
#   ./setup-kiosk.sh                      # board is on this Pi
#   BOARD_URL=http://board.local/ ./setup-kiosk.sh
#
# Requires Pi OS Desktop. Lite has no display session for Chromium to join.

set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
URL="${BOARD_URL:-http://localhost/}"
OFF_HOUR="${OFF_HOUR:-23}"
ON_HOUR="${ON_HOUR:-6}"

say() { printf "\n\033[1;33m==> %s\033[0m\n" "$*"; }

# ------------------------------------------------------------------ checks
if [ ! -d /etc/xdg/autostart ] && [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  echo "No desktop session found. This needs Pi OS Desktop, not Lite." >&2
  exit 1
fi

say "Installing packages"
sudo apt-get update -qq
# unclutter is X11-only and absent from some Bookworm images; don't let a
# missing optional package abort the install.
sudo apt-get install -y chromium-browser curl || \
  sudo apt-get install -y chromium curl
sudo apt-get install -y unclutter  2>/dev/null || true
sudo apt-get install -y wlr-randr  2>/dev/null || true

chmod +x "$REPO/kiosk-run.sh" "$REPO/screen.sh"

# ------------------------------------------------------------------ url
# Written to a file rather than baked into the .desktop so changing which
# machine the wall points at is one line and no reinstall.
mkdir -p "$HOME/.config"
printf 'BOARD_URL=%s\n' "$URL" > "$HOME/.config/board-kiosk.env"
say "Board URL set to $URL"

# ------------------------------------------------------------------ autostart
say "Installing autostart entry"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/board-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Board kiosk
Exec=/bin/bash -lc 'set -a; . \$HOME/.config/board-kiosk.env; set +a; exec $REPO/kiosk-run.sh'
X-GNOME-Autostart-enabled=true
EOF

# ------------------------------------------------------------------ blanking
# The desktop's own screensaver has to go, or it blanks on its own schedule
# and fights screen.sh.
say "Disabling session screen blanking"
mkdir -p "$HOME/.config/wayfire"
if [ -f "$HOME/.config/wayfire.ini" ] && ! grep -q "idle" "$HOME/.config/wayfire.ini"; then
  printf '\n[idle]\ndpms_timeout = -1\nscreensaver_timeout = -1\n' >> "$HOME/.config/wayfire.ini"
fi
sudo raspi-config nonint do_blanking 1 2>/dev/null || true

say "Installing screen schedule (off ${OFF_HOUR}:00, on ${ON_HOUR}:00)"
CRON_OFF="0 $OFF_HOUR * * * $REPO/screen.sh off >/dev/null 2>&1"
CRON_ON="0 $ON_HOUR * * * $REPO/screen.sh on >/dev/null 2>&1"
( crontab -l 2>/dev/null | grep -v "screen.sh" ; echo "$CRON_OFF"; echo "$CRON_ON" ) | crontab -

say "Done"
cat <<EOF

Reboot to start the kiosk:

    sudo reboot

Afterwards:

    $REPO/screen.sh off       blank now
    $REPO/screen.sh on        wake now
    $REPO/screen.sh status    which display stack, and current state

To point the wall at a different machine, edit one line:

    nano ~/.config/board-kiosk.env

If the panel shows black bars or the edges are cut off, that is the TV/monitor
overscanning rather than the page. Fix it on the display first; only if that
fails, add to /boot/firmware/config.txt:

    disable_overscan=1

EOF
