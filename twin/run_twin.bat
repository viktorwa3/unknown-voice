@echo off
REM Unknown Twin live GUI (separate app, own presets in twin\presets). Hotkeys: Ctrl+Alt+1..9, 0 bypass, C clear mode
cd /d "%~dp0"
set PY=python
if exist "..\.venv\Scripts\python.exe" set PY=..\.venv\Scripts\python.exe
%PY% twin_ui.py %*
if errorlevel 1 pause
