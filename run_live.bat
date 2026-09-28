@echo off
REM Live Unknown voice -> VB-Cable -> Discord. Hotkeys: Ctrl+Alt+1 morph, 2 glide, 3 radio, 0 bypass, Q quit
REM Add  --monitor "Headphones"  to hear yourself; --list shows devices.
cd /d "%~dp0"
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
%PY% unknown_live.py --out "CABLE Input" -p morph %*
pause
