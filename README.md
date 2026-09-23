# Unknown voice — FX chain (Track A, FX stage)

Offline pedalboard chain that turns a dry voice take into an "Unknown"-style monster voice.
Generic monster-mimic recipe, not a reverse-engineered Behaviour preset. Personal/fan use only.

## Setup
```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# ffmpeg on PATH only needed for m4a/mp3 input: winget install Gyan.FFmpeg
```

## Usage
```powershell
python unknown_fx.py raw\take01_normal.wav -o raw\out -p all      # all presets
python unknown_fx.py raw\take02_whisper.wav -p deep mimic --seed 7 # selected presets, other stutter pattern
.\run_all.bat                                                     # everything in raw\
```
Output: `raw\out\<take>__<preset>.wav`, 48 kHz mono 24-bit, peak -1 dBFS.

## Recording spec (put into `raw\`)
| File | Content | Length |
|---|---|---|
| take01_normal.wav | normal calm speech | 10–15 s |
| take02_whisper.wav | low raspy whisper | 10–15 s |
| take03_growl.wav | throat growl / vocal fry | 5–10 s |
| take04_scream.wav | scream or laugh | 3–5 s |
WAV 48 kHz mono, no mic noise suppression/AGC, peaks around -6 dB, 1 s silence at start, short phrases with pauses.

## Presets
| Preset | Idea |
|---|---|
| base | baseline: -5 st low layer + drive, +7 st chorus layer ×0.35, stutter 6×60 ms, reverse reverb ×0.4, 10-bit |
| deep | -7 st, more drive, ring mod 35 Hz, 9-bit |
| mimic | 45% dry voice kept, +12 st layer, more stutter — "almost human" |
| glitch | ring mod 70 Hz, 14 stutters × 40 ms, 8-bit |

Knobs live in `PRESETS` at the top of `unknown_fx.py`. Pitch below -7 st starts producing artifacts.
