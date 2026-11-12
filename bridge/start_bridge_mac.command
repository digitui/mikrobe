#!/bin/bash
# Double-click to start the Mikro MK2 bridge (macOS)
cd "$(dirname "$0")"
pkill -x NIHardwareAgent 2>/dev/null
python3 mikrobe.py
