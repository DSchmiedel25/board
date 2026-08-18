#!/usr/bin/env bash
#
# screen.sh on | off | status — cut the backlight on a schedule.
#
#   screen.sh off     blank the panel, leave the Pi running
#   screen.sh on      wake it
#
# Blanking rather than a smart plug on the wall socket: the monitor drops to
# standby and wakes instantly, while the Pi stays up and reachable over SSH.
# Cutting mains power means a full boot every morning and a Pi that spends
# eight hours a day unpingable.
#
# Three display stacks ship across Pi OS releases and each blanks differently.
# Detection order matters — a Wayland session can still have DISPLAY set for
# Xwayland, so Wayland is checked first.

set -u
ACTION="${1:-status}"

# Autostart's environment isn't present when cron runs this, so rebuild the
# handful of variables the tools need.
UID_N="$(id -u)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$UID_N}"
if [ -z "${WAYLAND_DISPLAY:-}" ]; then
  for s in "$XDG_RUNTIME_DIR"/wayland-*; do
    case "$s" in *.lock) continue;; esac
    [ -S "$s" ] && export WAYLAND_DISPLAY="$(basename "$s")" && break
  done
fi
export DISPLAY="${DISPLAY:-:0}"

have() { command -v "$1" >/dev/null 2>&1; }

wayland_outputs() {
  wlr-randr 2>/dev/null | awk '/^[A-Za-z]/{print $1}'
}

case "$ACTION" in
  on|off)
    STATE="$ACTION"
    if [ -n "${WAYLAND_DISPLAY:-}" ] && have wlr-randr; then
      for out in $(wayland_outputs); do
        wlr-randr --output "$out" --"$STATE" 2>/dev/null
      done
    elif have xset; then
      if [ "$STATE" = "off" ]; then
        xset dpms force off
      else
        # force on alone can leave the timer armed; reset it explicitly.
        xset dpms force on
        xset s reset
      fi
    elif [ -e /sys/class/backlight/*/bl_power ] 2>/dev/null; then
      # DSI panels and some HATs, where neither of the above applies.
      [ "$STATE" = "off" ] && v=4 || v=0
      for b in /sys/class/backlight/*/bl_power; do echo "$v" | sudo tee "$b" >/dev/null; done
    else
      echo "no supported blanking method found" >&2
      exit 1
    fi
    ;;
  status)
    if [ -n "${WAYLAND_DISPLAY:-}" ]; then
      echo "wayland ($WAYLAND_DISPLAY)"
      have wlr-randr && wlr-randr 2>/dev/null | head -20
    else
      echo "x11 ($DISPLAY)"
      have xset && xset q | grep -A2 "DPMS"
    fi
    ;;
  *)
    echo "usage: screen.sh on|off|status" >&2
    exit 2
    ;;
esac
