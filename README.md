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
python unknown_fx.py raw\take02_whisper.wav -p morph glide --seed 7 --fem-hz 240 # selected presets, other stutter pattern
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

## Presets (v2.5, 3 kept after listening)
| Preset | Idea |
|---|---|
| morph | beast + robotic fem + whisper drift/jump over time, main one |
| glide | one voice sliding continuously beast <-> fem (pitch+formants together), "melting" |
| radio | broken transmission: band-limited, crushed, dropouts |

Removed 2026-09-24: fem, beast, mimic, robot, swarm, glitch, whisper (their params are archived in the project MEMORY.md; renders in raw\out\v2_2\).

Pitch shifts are adaptive to the take's median f0: fem -> ~220 Hz (`--fem-hz`), beast -> ~55 Hz (`--beast-hz`).
Between phrases everything (reverb wash, stutter tails, hiss) is pushed down by an expander keyed on the dry voice:
`--bg-db -60` (default, near silent), `--bg-db -30` (audible ambience), `--bg-db 0` (off).
Reverse reverb: `--rrev 0.1` default (v2.5), `--rrev 1` full wash, `--rrev 0` off (dead silent pauses, ~-90 dB).

Expander knobs (v2.3):
| Flag | Default | When to touch |
|---|---|---|
| `--gate-thr` | -28 | dB under the loudest speech. Quiet word onsets/tails chopped -> `-35`/`-40`. Background still audible -> `-22` |
| `--gate-ratio` | 2 | dB cut per dB under threshold. `1` gentle, `4` hard |
| `--gate-pre` | 150 | ms opened before the voice (keeps the reverse-reverb swell) |
| `--gate-hold` | 60 | ms kept open after the voice (word tails). Tails cut -> `120` |
`--layers` writes each voice into `<outdir>\layers\`. Mix knobs live in `PRESETS` at the top of `unknown_fx.py`.

## Live (Discord) — `unknown_live.py`
Own realtime DSP engine (STFT phase vocoder with independent pitch/formant shift, same voices and presets as offline),
no VSTs, no GPU. Latency ~60-90 ms total. No reverse reverb live (it needs future audio).

Setup (once):
1. Install VB-Cable: https://vb-audio.com/Cable/ (run `VBCABLE_Setup_x64.exe` as admin, reboot).
2. `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`
3. `.\.venv\Scripts\python.exe unknown_live.py --list` -> find your mic and "CABLE Input" (prefer the `Windows WASAPI` rows).
4. Discord -> Settings -> Voice & Video: Input Device = `CABLE Output (VB-Audio Virtual Cable)`,
   Noise Suppression = None, Echo Cancellation = Off, Automatic Gain Control = Off,
   Input Sensitivity: manual, slider low (our engine gates silence itself).
5. Windows: mic "Audio enhancements" off.

Run:
```powershell
.\run_live.bat                                         # default mic -> CABLE Input, preset morph
.\run_live.bat --in "Microphone" --monitor "Headphones" # pick mic, hear yourself in headphones
```
Hotkeys (global): Ctrl+Alt+1 morph | Ctrl+Alt+2 glide | Ctrl+Alt+3 radio | Ctrl+Alt+0 bypass | Ctrl+Alt+Q quit.
Console fallback: 1/2/3/0/q + Enter.

Knobs: `--gate-thr -50` (dBFS, raise to -40 if room noise opens the gate, lower to -55 if quiet words get cut),
`--gain 0` (dB), `--fft 1024` (lower latency, rougher low end), `--latency high` (if you hear crackles / xruns grow).
Offline check of the live engine: `unknown_live.py --file raw\take01_normal.wav --out-file raw\out\live\test.wav -p glide`.

## Live GUI — `unknown_live_ui.py` v2 (recommended)
`.\run_live_ui.bat` (first time: `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`).
- **Preset browser (left):** folders = sub-folders of `presets\` (any depth), search by name or description,
  `.` / `*` = star a favorite, "favorites only" filter. Click a preset: every slider jumps to it.
- **Top:** `<` `>` prev/next, random, favorite toggle, `* modified`. Save as `folder/name` (new folders are created),
  optional description, Delete, Revert, Rescan presets (after copying JSON files in by hand).
- **Favorites bar + global hotkeys:** Ctrl+Alt+1..9 = first 9 favorites, Ctrl+Alt+0 bypass,
  Ctrl+Alt+Left/Right prev/next, Ctrl+Alt+R random.
- **Tabs:** Voices (beast, fem, glide, robot, whisper, demon, child, human), Mix, FX (Mod, Glitch, Space, Radio),
  Gate & Master (incl. input gain + 3-band EQ), Audio (devices, FFT, buffer, START/APPLY without restarting).
- **Factory library:** 65 presets in unknown/, monsters/, robots/, horror/, comms/, fun/, utility/, all level-matched
  to about -20 dB speech on take01. `python make_presets.py` rewrites them (same names only; your own files stay).
- Engine v4 extras: vibrato, pitch jitter, formant wobble, tremolo, phaser, reverse chunks, spectral freeze,
  master bitcrush / downsample, echo + reverb (after the gate, so tails ring out), input gain, EQ, soft-knee output.
- `live_settings.json` (gitignored): devices, favorites, last preset + last slider state.
- CLI: `unknown_live.py -p "unknown/whisper stalker"` or `--list-presets`.
