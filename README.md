# unknown-voice

Turn two ElevenLabs takes (one male, one female, same phrase) into a layered
"The Unknown" (Dead by Daylight) chorus voice. Cross-platform: PowerShell on
Windows, bash on Linux/WSL/mac.

## Pipeline

```
ElevenLabs (male take + female take, identical text)
        |  export mp3/wav
     ffmpeg   normalize -> 44.1kHz / 16-bit / mono
        |
      SoX     4 layers -> desync (pad) -> pan mix (stereo) -> character
              -> pulse / rumble / crush / gated reverb beds -> preverb
        |
   unknown_chorus.wav  (stereo unless --wide 0)
```

Four layers do the work:

| Layer | Source | What it does |
|-------|--------|--------------|
| LOW   | male   | guttural bottom, formants shifted down (`speed`+`tempo`), overdrive |
| MID   | male   | dry base, keeps words intelligible |
| HIGH  | female | cracked whisper on top, formants up, highpass |
| GHOST | female | female dragged DOWN into male register — ear hears "male", formants are female, brain can't resolve it into a person. The trick. |

## Usage

### Linux / WSL / mac

```bash
sudo apt install sox libsox-fmt-all ffmpeg   # deps
./scripts/build-unknown-voice.sh -m male.mp3 -f fem.mp3 -d 1.4 --keep-stems
```

Autodetects `sox`/`ffmpeg` (override with `--sox`/`--ffmpeg` or `SOX_BIN`/`FFMPEG_BIN`).

### Windows (PowerShell 5.1+)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Build-UnknownVoice.ps1 `
    -MaleFile male.mp3 -FemaleFile fem.mp3 -Dread 1.4 -KeepStems
```

SoX on Windows can't read mp3 (no libmad) — the script routes through ffmpeg
regardless, so mp3 input is fine.

## Options

Both scripts expose the same knobs (bash long-flags mirror the PowerShell params).

| bash | PowerShell | range (default) | meaning |
|------|-----------|-----------------|---------|
| `-m`, `--male` | `-MaleFile` | — (required) | male source (mp3/wav) |
| `-f`, `--female` | `-FemaleFile` | — (required) | female source (mp3/wav) |
| `-o`, `--out` | `-OutFile` | `unknown_chorus.wav` | output wav |
| `-d`, `--dread` | `-Dread` | 0.1–3.0 (1.0) | overall intensity; useful 0.6–1.6 (speeds clamp past that) |
| `--femboost` | `-FemBoost` | 0–12 dB (4) | push the female layers (HIGH whisper + GHOST) forward |
| `--grit` | `-Grit` | 0–3 (1.0) | distortion baked into the character stage |
| `--drag` | `-Drag` | 0.75–1.0 (0.92) | drawl the delivery via `tempo` (keeps pitch) |
| `--pulse` | `-Pulse` | 0–3 (1.0), 0=off | periodic distortion; gated sidechain that "breaks up" |
| `--warble` | `-Warble` | 0–3 (1.0), 0=off | slow deep chorus = pitch drift ("can't hold a note") |
| `--wide` | `-Wide` | 0–1 (0.6), 0=mono | stereo width by panning layers (mono-compatible) |
| `--rumble` | `-Rumble` | 0–6 (1.0), 0=off | sub-bass bed derived from the voice |
| `--crush` | `-Crush` | 0–3 (0.0/off) | 8-bit + samplerate-decimated "broken transmission" texture |
| `--reverb` | `-Reverb` | 0–3 (1.0), 0=dry | occasional room: wet reverb gated by a slow LFO |
| `--preverb` | `-Preverb` | 0–3 (0.0/off) | reverse-reverb pre-swell ("about to speak"); delays onset |
| `--keep-stems` | `-KeepStems` | off | keep intermediate layers in `_stems/` |
| `--sox`, `--ffmpeg` | `-SoxPath`, `-FfmpegPath` | autodetect | override binary locations |

**Input phrases must be textually identical** across male/female, or the layers
drift into two different mumbling entities instead of one.

## Style presets

Render five contrasting styles side by side into `versions/` to pick a base:

```bash
./scripts/test-styles.sh                 # Linux/WSL/mac
```
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Test-Styles.ps1   # Windows
```

Presets: `signature` (balanced), `buried` (deep/slow/sub), `swarm` (wide/warbly/
female-forward), `broken` (bitcrush/glitch), `approaching` (spacious pre-swell).
See `test-lines.txt` for uncanny phrases tuned to survive the formant shifting.

## Dev

```bash
make check    # lint + test
make lint     # shellcheck
make test     # bats (stubbed sox/ffmpeg — no real audio needed)
```

Tests stub `sox` and `ffmpeg` (see `tests/fixtures/`) so the full pipeline logic
runs without the real binaries or any audio files.

## Notes

See `MEMORY.md` for the full flow, known bugs, and the `pitch` vs `speed+tempo`
distinction (why `pitch` alone just sounds like a man with a cold).
