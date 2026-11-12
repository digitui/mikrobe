#!/bin/bash
# Quick controls for the background bridge on macOS.
ID="com.mikrobe.bridge"
PLIST="$HOME/Library/LaunchAgents/$ID.plist"
LOG="$HOME/Library/Logs/Mikrobe.log"
PY="$HOME/Library/Application Support/Mikrobe/venv/bin/python"
BR="$HOME/Library/Application Support/Mikrobe/mikrobe.py"

echo "Mikro MK2 bridge"
echo " 1) Restart bridge      2) Stop bridge      3) Start bridge"
echo " 4) Show log            5) Hardware monitor (prints button/pad names)"
echo " 6) Screen test         7) LED test"
read -r -p "Choose: " c
stop() { launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true; }
start() { launchctl bootstrap "gui/$(id -u)" "$PLIST"; }
case "$c" in
  1) stop; sleep 1; start; echo "Restarted." ;;
  2) stop; echo "Stopped." ;;
  3) start; echo "Started." ;;
  4) tail -n 40 "$LOG" ;;
  5|6|7) stop; echo "(bridge paused - Ctrl+C to finish, it restarts afterwards)"
         case "$c" in 5) A="--monitor";; 6) A="--test display";; 7) A="--test leds";; esac
         trap 'echo' INT; "$PY" "$BR" $A; trap - INT; start; echo "Bridge restarted." ;;
esac
read -n 1 -s -r -p "Press any key to close..."
echo
