@echo off
REM Process every take in raw\ with all presets -> raw\out\
REM Needs: python + pip install -r requirements.txt, ffmpeg on PATH (only for non-WAV input)
cd /d "%~dp0"
for %%f in (raw\*.wav raw\*.m4a raw\*.mp3 raw\*.flac) do python unknown_fx.py "%%f" -o raw\out -p all
echo Done. Results in raw\out
pause
