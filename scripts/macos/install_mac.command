#!/bin/bash
# =====================================================================
#  Maschine Mikro MK2 -> Ableton Live : macOS installer
#  Double-click (or: bash install_mac.command)
# =====================================================================
set -e
HERE="$(cd "$(dirname "$0")/../.." && pwd)"   # repo root
APP_DIR="$HOME/Library/Application Support/Mikrobe"
AGENT_ID="com.mikrobe.bridge"
AGENT_PLIST="$HOME/Library/LaunchAgents/$AGENT_ID.plist"
LOG="$HOME/Library/Logs/Mikrobe.log"
DEFAULT_SCRIPTS="$HOME/Music/Ableton/User Library/Remote Scripts"

say()  { printf "\n\033[1;36m==> %s\033[0m\n" "$1"; }
warn() { printf "\033[1;33m%s\033[0m\n" "$1"; }

clear
echo "Mikrobe - Maschine Mikro MK2 for Ableton Live"
echo "----------------------------------------------"

# ---------------------------------------------------------------- Python
say "Checking Python 3"
PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /usr/bin/python3; do
  if [ -x "$c" ] && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  warn "Python 3.8+ not found."
  echo "Install it from https://www.python.org/downloads/macos/ (or run: xcode-select --install)"
  echo "then run this installer again."
  read -n 1 -s -r -p "Press any key to close..."; exit 1
fi
echo "Using $PY ($("$PY" --version))"

# ---------------------------------------------------------------- remove pre-rename install
OLD_PLIST="$HOME/Library/LaunchAgents/com.mikromk2.bridge.plist"
if [ -f "$OLD_PLIST" ] || [ -d "$HOME/Library/Application Support/MikroMK2Bridge" ]; then
  say "Removing the old 'MikroMK2' version (it would block the controller)"
  launchctl bootout "gui/$(id -u)" "$OLD_PLIST" 2>/dev/null || true
  rm -f "$OLD_PLIST"
  rm -rf "$HOME/Library/Application Support/MikroMK2Bridge"
  echo "Old background bridge removed."
fi
pkill -f "MikroMK2Bridge/mikro_bridge.py" 2>/dev/null || true

# ---------------------------------------------------------------- bridge files + venv
say "Installing the bridge to: $APP_DIR"
mkdir -p "$APP_DIR"
cp "$HERE/bridge/mikrobe.py" "$HERE/bridge/requirements.txt" "$APP_DIR/"
[ -f "$APP_DIR/config.json" ] || cp "$HERE/bridge/config.json" "$APP_DIR/"   # keep user tweaks
"$PY" -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/python" -m pip install --quiet --upgrade pip
"$APP_DIR/venv/bin/python" -m pip install --quiet -r "$APP_DIR/requirements.txt"
echo "Python packages installed."

# ---------------------------------------------------------------- Ableton remote script
say "Installing the Ableton control surface script"
SCRIPTS_DIR="$DEFAULT_SCRIPTS"
if [ ! -d "$HOME/Music/Ableton/User Library" ]; then
  warn "Default Ableton User Library not found at ~/Music/Ableton/User Library."
  echo "If you moved it, drag your 'User Library' folder into this window and press Return,"
  read -r -p "or just press Return to create the default location: " CUSTOM
  CUSTOM="${CUSTOM%/}"; CUSTOM="${CUSTOM//\\ / }"; CUSTOM="${CUSTOM#\'}"; CUSTOM="${CUSTOM%\'}"
  [ -n "$CUSTOM" ] && SCRIPTS_DIR="$CUSTOM/Remote Scripts"
fi
mkdir -p "$SCRIPTS_DIR"
rm -rf "$SCRIPTS_DIR/Mikrobe" "$SCRIPTS_DIR/MikroMK2"   # MikroMK2 = old name
cp -R "$HERE/ableton/Mikrobe" "$SCRIPTS_DIR/Mikrobe"
echo "Copied to: $SCRIPTS_DIR/Mikrobe"

# ---------------------------------------------------------------- NI Hardware Agent
say "Native Instruments hardware agent"
echo "NIHardwareAgent locks the Mikro so nothing else can use it."
echo "Disabling it means the Maschine software won't see the controller until you re-enable it"
echo "(run uninstall_mac.command, or log out and back in after deleting $AGENT_PLIST)."
read -r -p "Disable NIHardwareAgent now? [Y/n] " A
if [[ ! "$A" =~ ^[Nn] ]]; then
  for p in /Library/LaunchAgents/com.native-instruments.NIHardwareAgent*.plist \
           /Library/LaunchAgents/com.native-instruments.NIHostIntegrationAgent*.plist; do
    [ -f "$p" ] || continue
    launchctl bootout "gui/$(id -u)" "$p" 2>/dev/null || true
    launchctl disable "gui/$(id -u)/$(basename "$p" .plist)" 2>/dev/null || true
    echo "Disabled $(basename "$p")"
  done
  pkill -x NIHardwareAgent 2>/dev/null || true
  pkill -x NIHostIntegrationAgent 2>/dev/null || true
  echo "Done."
else
  warn "Skipped. Quit NIHardwareAgent in Activity Monitor before using the bridge."
fi

# ---------------------------------------------------------------- LaunchAgent (auto-start)
say "Setting the bridge to start automatically at login"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$AGENT_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$AGENT_ID</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP_DIR/venv/bin/python</string>
    <string>-u</string>
    <string>$APP_DIR/mikrobe.py</string>
    <string>--config</string>
    <string>$APP_DIR/config.json</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)" "$AGENT_PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENT_PLIST"
sleep 2
if launchctl print "gui/$(id -u)/$AGENT_ID" >/dev/null 2>&1; then
  echo "Bridge is running. Log: $LOG"
else
  warn "Bridge did not start - see $LOG"
fi
tail -n 5 "$LOG" 2>/dev/null || true

# ---------------------------------------------------------------- next steps
cat <<'EOF'

==========================================================
 Installed!  Last step, in Ableton Live:

 1. Restart Live.
 2. Settings > Link, Tempo & MIDI
      Control Surface:  Mikrobe
      Input:            Mikrobe Controls
      Output:           Mikrobe Feedback
 3. In the MIDI Ports list switch on:
      In:  Mikrobe Controls    -> Track + Remote
      Out: Mikrobe Feedback  -> Remote
==========================================================
EOF
read -n 1 -s -r -p "Press any key to close..."
echo
