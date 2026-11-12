@echo off
REM Double-click to start the Mikro MK2 bridge (Windows)
cd /d "%~dp0"
taskkill /IM NIHardwareAgent.exe /F >nul 2>&1
python mikrobe.py
pause
