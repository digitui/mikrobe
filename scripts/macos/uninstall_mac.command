#!/bin/bash
# Removes the Mikro MK2 bridge and re-enables Native Instruments' hardware agent.
APP_DIR="$HOME/Library/Application Support/Mikrobe"
AGENT_PLIST="$HOME/Library/LaunchAgents/com.mikrobe.bridge.plist"

echo "Stopping the bridge..."
launchctl bootout "gui/$(id -u)" "$AGENT_PLIST" 2>/dev/null || true
rm -f "$AGENT_PLIST"
rm -rf "$APP_DIR"

echo "Re-enabling Native Instruments hardware agent..."
for p in /Library/LaunchAgents/com.native-instruments.NIHardwareAgent*.plist \
         /Library/LaunchAgents/com.native-instruments.NIHostIntegrationAgent*.plist; do
  [ -f "$p" ] || continue
  launchctl enable "gui/$(id -u)/$(basename "$p" .plist)" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$p" 2>/dev/null || true
done

echo
echo "Done. The Ableton script is still in your User Library's Remote Scripts/Mikrobe"
echo "folder - delete it by hand if you no longer want it."
read -n 1 -s -r -p "Press any key to close..."
echo
