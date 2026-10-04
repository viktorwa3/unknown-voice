# Unknown Twin — separate live app (v1, 2026-10-03)

New engine built on what is known about the in-game voice (DBD wiki): **two performances — male and female —
speak at the same time** and are distorted; a Weakened survivor hears **one of them clearly** with much less
distortion. The old app (`unknown_live.py` / `unknown_live_ui.py`, its presets and settings) is untouched.
Everything about the FX chain is a reconstruction by ear, not a reverse-engineered game preset. Personal/fan use only.

## Run
```powershell
.\twin\run_twin.bat                         # GUI (uses ..\.venv)
cd twin; ..\.venv\Scripts\python.exe twin_engine.py --file ..\raw\take01_normal.wav --out-file ..\raw\out\twin\t.wav -p "unknown/game twin"
..\.venv\Scripts\python.exe twin_engine.py --write-factory    # restore factory presets (overwrites same names)
```
Don't run the old GUI at the same time: same Ctrl+Alt hotkeys, same VB-Cable.

## What is different from the old engine
| | Old (`unknown_live.py`) | Twin |
|---|---|---|
| Voices | 9 types, random switching | 2 layers (male + female) **always both audible**, equal-power balance |
| Motion | softmax morph | slow sway + random jumps (85/15, 30 ms attack, hold) + flips on syllable onsets |
| Two throats | — | female lags 0–80 ms behind the male, drifting |
| Clear (Weakened) | — | button / Ctrl+Alt+C / slider / random per phrase (15 %): one voice dominates, FX fade out |
| Inhuman texture | comb, bitcrush | Bode frequency shift (exact, analytic signal), ring mod, hard-tune with retune speed, whisper under the voice, spectral blur |
| Horror bus | radio | analog-horror tape: band-pass, tanh drive, wow/flutter, dropouts, hiss only while talking |
| Space | echo / reverb | distance (darker + quieter), occlusion (behind a wall), small room |
| Pitch shift | integer-bin peaks (inharmonic, "digital") | **fractional** peak shift: harmonics exact (verified 220/440/660 Hz) |
| Stutter | can repeat into pauses | stops (4 ms fade) as soon as the dry voice stops |
| Preset load | swaps state under the audio thread | atomic dict swap, state reset inside the audio thread |

## Presets (`twin\presets\`, written on first start if missing)
| Preset | Idea |
|---|---|
| unknown/game twin | main one: both voices, sway + jumps, ~15 % of phrases clear |
| unknown/weakened clear male / female | what a Weakened survivor hears |
| unknown/mumbling hunt | chase: out of sync, blur, dropouts, rare clear phrases |
| unknown/found footage | heavy tape: narrow band, wow/flutter, hiss |
| unknown/behind the wall | far + muffled (+5 dB out to stay audible in Discord) |
| unknown/bad mimic | flips voice on syllables, robotic tune, metallic shift |
| unknown/her voice / his voice | one layer dominant, the other bleeding through |
| unknown/two throats | both equal, female 45 ms late |
| utility/dry twin, utility/bypass | references |

Saved presets store only the keys that differ from the defaults.

## Discord (must)
Input Device `CABLE Output (VB-Audio Virtual Cable)`; Noise Suppression None; Echo Cancellation Off; AGC Off;
Input Sensitivity manual, low. Otherwise Discord eats the stutter, dropouts, whisper and the clear/distorted contrast.
