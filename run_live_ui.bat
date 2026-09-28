@echo off
REM Live Unknown voice with GUI (sliders, presets, devices). Hotkeys: Ctrl+Alt+1..9 quick presets, Ctrl+Alt+0 bypass
cd /d "%~dp0"
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
%PY% unknown_live_ui.py %*
if errorlevel 1 pause
