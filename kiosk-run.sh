#!/usr/bin/env bash
#
# kiosk-run.sh — launch Chromium full screen on the board and keep it there.
#
# Started by ~/.config/autostart/board-kiosk.desktop once the desktop session
# is up. XDG autostart rather than a systemd unit on purpose: Pi OS ships
# X11 on older releases and Wayland (wayfire, then labwc) on Bookworm and
# later, and a systemd unit has to be told which display server it's joining.
# Autostart is honoured by all three and inherits the session's environment
# already set up, so the same file works on a 3B+ and a 4 without edits.

set -u

URL="${BOARD_URL:-http://localhost/}"
PROFILE="$HOME/.config/board-kiosk"

# Chromium notices an unclean shutdown and puts a "restore pages?" bar across
# the top of the board, which then sits there for days because there is no
# keyboard. Rewriting these two keys before every launch means a power cut
# comes back to the dashboard rather than to a dialog.
PREFS="$PROFILE/Default/Preferences"
if [ -f "$PREFS" ]; then
  sed -i 's/"exit_type":"[^"]*"/"exit_type":"Normal"/; s/"exited_cleanly":false/"exited_cleanly":true/' "$PREFS"
fi

# Blanking is handled by screen.sh on a schedule, so the session's own idle
# blanker has to be off or it fights it. Each of these exists on exactly one
# display server; failures are expected and ignored.
xset s off        2>/dev/null
xset -dpms        2>/dev/null
xset s noblank    2>/dev/null

# Hide the pointer. unclutter is X11-only; under Wayland the cursor hides on
# its own once nothing has moved, which for a wall panel is immediately.
if [ -n "${DISPLAY:-}" ] && command -v unclutter >/dev/null; then
  pgrep -x unclutter >/dev/null || unclutter -idle 0.5 -root &
fi

for BIN in chromium-browser chromium; do
  command -v "$BIN" >/dev/null && CHROME="$BIN" && break
done
: "${CHROME:?chromium not installed}"

# nginx and the network are usually not up yet when autostart fires. Wait
# rather than launching into an error page that never retries.
for _ in $(seq 1 30); do
  curl -sf -o /dev/null --max-time 3 "$URL" && break
  sleep 2
done

# Relaunch on exit. Chromium on a Pi will occasionally die on a GPU fault
# after weeks of uptime, and without this the wall goes to a grey desktop
# until someone notices.
while true; do
  "$CHROME" \
    --user-data-dir="$PROFILE" \
    --kiosk "$URL" \
    --start-fullscreen \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-features=Translate,TranslateUI \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --force-device-scale-factor=1 \
    --autoplay-policy=no-user-gesture-required \
    --check-for-update-interval=31536000 \
    --password-store=basic \
    >/dev/null 2>&1
  sleep 5
done
