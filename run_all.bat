@echo off
REM Process every take in raw\ with all presets (+ per-voice layers) -> raw\out\
REM Needs: pip install -r requirements.txt, ffmpeg on PATH (only for non-WAV input)
cd /d "%~dp0"
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
for %%f in (raw\*.wav raw\*.m4a raw\*.mp3 raw\*.flac) do %PY% unknown_fx.py "%%f" -o raw\out -p all --layers
echo Done. Results in raw\out
pause
