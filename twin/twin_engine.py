"""
twin_engine.py v1 — "Unknown Twin": realtime voice engine built around how the in-game voice is described
(DBD wiki): TWO performances (male + female) speak AT THE SAME TIME and are distorted; survivors in the
Weakened state hear ONE of them clearly with much less distortion. Separate app; does not touch unknown_live.py.

Chain (per STFT hop, N=2048 / hop N/4):
  mic -> input gain -> HPF -> STFT [spectral blur events]
      -> MALE layer   (pitch offset, formant, slow drift, optional Bode freq-shift / ring mod)
      -> FEMALE layer (pitch target + hard-tune w/ retune speed, formant, drift, Bode / ring, HPF, presence)
      -> WHISPER layer (noise-excited envelope above N kHz, follows the voice)
  -> per-layer AGC (each layer at the dry level) -> female desync delay (two throats, not one)
  -> equal-power balance M<->F: slow LFO + random JUMPS + onset flips  (both layers always sound)
  -> stutter -> HORROR bus (band-pass, tanh drive, wow/flutter, dropouts) -> gate (keyed on dry voice)
  -> hiss (only while talking) -> space (distance LPF, occlusion, small room) -> comp / limiter -> soft knee.
CLEAR mode (0..1, manual / hotkey / random per phrase): one layer dominates (other at clear.residual_db),
motion, texture and horror are faded out. Hard-coded nothing: every knob is in SPEC.

Deps:  numpy, pedalboard, sounddevice, soundfile (same .venv as the old app)
Usage: python twin_engine.py --list                                   # audio devices
       python twin_engine.py --file ..\\raw\\take01_normal.wav --out-file ..\\raw\\out\\twin\\t.wav -p "unknown/game twin"
       python twin_engine.py -p "unknown/game twin" --out "CABLE Input" --monitor "Speakers (Razer"
       python twin_engine.py --write-factory                          # (re)write factory presets
GUI:   twin_ui.py.  Generic reconstruction by ear, NOT a reverse-engineered game preset. Personal/fan use only.
"""
import argparse, sys, time, queue, json, os, glob
from collections import deque
import numpy as np
from pedalboard import (Pedalboard, HighpassFilter, LowpassFilter, PeakFilter, LowShelfFilter, HighShelfFilter,
                        Compressor, Limiter, Gain, Reverb)

SR = 48000
HERE = os.path.dirname(os.path.abspath(__file__))
PRESET_DIR = os.path.join(HERE, "presets")
LAYERS = ["male", "female", "whisper"]
SHADOWS = [f"sh{k}_{L}" for L in ("m", "f") for k in (1, 2, 3)]
SYN = LAYERS + SHADOWS + ["growl"]
# initial post-FX AGC gains (dB) so layers sit at the dry level from the first word (measured on take01, --calibrate)
AGC_INIT_DB = dict(male=5.2, female=4.9, whisper=18.6, growl=0.0)
PENT = np.array([0, 3, 5, 7, 10])            # A minor pentatonic, semitones above A

# ---------------------------------------------------------------- parameters
SPEC = []

def _p(tab, key, label, lo=0.0, hi=1.0, default=0.0, fmt="%.2f", kind="f", help=""):
    SPEC.append(dict(tab=tab, key=key, label=label, lo=lo, hi=hi, default=default, fmt=fmt, kind=kind, help=help))

# Clear (Weakened)
_p("Clear", "clear.amount", "Clear mode (manual)", 0, 1, 0.0, help="1 = one voice almost clean, like a Weakened survivor hears it")
_p("Clear", "clear.chance", "Chance a phrase comes out clear", 0, 0.8, 0.15)
_p("Clear", "clear.voice", "Clear voice: 0 male .. 1 female (between = random)", 0, 1, 0.5)
_p("Clear", "clear.residual_db", "Other voice level in clear mode (dB)", -40, 0, -18, "%.0f")
_p("Clear", "clear.gap_ms", "Silence that starts a new phrase (ms)", 150, 1500, 400, "%.0f")
_p("Clear", "clear.fade_ms", "Clear fade in/out (ms)", 20, 600, 120, "%.0f")
# Male layer
_p("Male", "male.pitch_st", "Pitch shift (semitones)", -12, 12, 0, "%.1f")
_p("Male", "male.formant", "Formant shift (x)", 0.6, 1.4, 0.97)
_p("Male", "male.breath", "Breath", 0, 1, 0.04)
_p("Male", "male.drift_cents", "Pitch drift (cents)", 0, 100, 25, "%.0f")
_p("Male", "male.bode_hz", "Frequency shift (Hz, Bode)", -60, 60, 12, "%.1f")
_p("Male", "male.bode_mix", "Frequency shift mix", 0, 1, 0.25)
_p("Male", "male.ring_hz", "Ring mod freq (Hz)", 5, 200, 45, "%.0f")
_p("Male", "male.ring_mix", "Ring mod mix", 0, 1, 0.0)
_p("Male", "male.lpf_hz", "LPF (Hz)", 2000, 16000, 12000, "%.0f")
_p("Male", "male.level_db", "Level trim (dB)", -24, 12, 0, "%.1f")
_p("Male", "male.mute", "Mute", kind="b", default=False)
# Female layer
_p("Female", "female.target_hz", "Pitch target (Hz)", 120, 450, 215, "%.0f")
_p("Female", "female.formant", "Formant shift (x)", 0.9, 1.6, 1.20)
_p("Female", "female.tune", "Hard-tune strength", 0, 1, 0.6)
_p("Female", "female.retune_ms", "Retune speed (ms, 0 = robotic)", 0, 200, 25, "%.0f")
_p("Female", "female.pentatonic", "Snap to pentatonic (else chromatic)", kind="b", default=False)
_p("Female", "female.breath", "Breath", 0, 1, 0.04)
_p("Female", "female.drift_cents", "Pitch drift (cents)", 0, 100, 25, "%.0f")
_p("Female", "female.bode_hz", "Frequency shift (Hz, Bode)", -60, 60, -8, "%.1f")
_p("Female", "female.bode_mix", "Frequency shift mix", 0, 1, 0.0)
_p("Female", "female.ring_hz", "Ring mod freq (Hz)", 5, 200, 45, "%.0f")
_p("Female", "female.ring_mix", "Ring mod mix", 0, 1, 0.12)
_p("Female", "female.hpf_hz", "HPF (Hz)", 20, 600, 140, "%.0f")
_p("Female", "female.presence_db", "Presence 3 kHz (dB)", -12, 12, 2, "%.1f")
_p("Female", "female.level_db", "Level trim (dB)", -24, 12, 0, "%.1f")
_p("Female", "female.mute", "Mute", kind="b", default=False)
# Motion (balance between the two layers)
_p("Motion", "motion.bias", "Balance centre (-1 male .. +1 female)", -1, 1, 0.0)
_p("Motion", "motion.depth", "Slow sway depth", 0, 1, 0.6)
_p("Motion", "motion.rate_hz", "Slow sway speed (Hz)", 0.03, 1.5, 0.2)
_p("Motion", "motion.jumps_ps", "Random jumps per second", 0, 2, 0.4)
_p("Motion", "motion.jump_amount", "Jump strength (0.85 = 85/15)", 0.5, 1, 0.85)
_p("Motion", "motion.jump_ms", "Jump attack (ms)", 5, 300, 30, "%.0f")
_p("Motion", "motion.hold_min_ms", "Jump hold min (ms)", 50, 3000, 300, "%.0f")
_p("Motion", "motion.hold_max_ms", "Jump hold max (ms)", 50, 4000, 1200, "%.0f")
_p("Motion", "motion.onset_flip", "Flip on syllable onsets (chance)", 0, 1, 0.25)
_p("Motion", "motion.desync_ms", "Female lag behind male (ms)", 0, 80, 15, "%.0f")
_p("Motion", "motion.desync_drift", "Lag drift", 0, 1, 0.4)
# Texture
_p("Texture", "whisper.level_db", "Whisper under the voice (dB, -40 = off)", -40, 0, -18, "%.0f")
_p("Texture", "whisper.hpf_hz", "Whisper HPF (Hz)", 300, 6000, 1800, "%.0f")
_p("Texture", "whisper.formant", "Whisper formant (x)", 0.7, 1.4, 1.0)
_p("Texture", "blur.ps", "Spectral blur events per second", 0, 2, 0.1)
_p("Texture", "blur.ms", "Blur length (ms)", 40, 800, 180, "%.0f")
_p("Texture", "blur.frames", "Blur width (frames)", 2, 8, 5, "%.0f")
_p("Texture", "stutter.ps", "Stutters per second", 0, 2, 0.1)
_p("Texture", "stutter.ms", "Stutter length (ms)", 15, 200, 50, "%.0f")
# Horror bus (analog-horror tape / found footage)
_p("Horror", "horror.amount", "Horror bus amount", 0, 1, 0.6)
_p("Horror", "horror.band", "Band-pass amount", 0, 1, 0.7)
_p("Horror", "horror.hpf", "Band HPF (Hz)", 80, 800, 250, "%.0f")
_p("Horror", "horror.lpf", "Band LPF (Hz)", 1500, 9000, 3500, "%.0f")
_p("Horror", "horror.drive_db", "Tape drive (dB)", 0, 24, 6, "%.1f")
_p("Horror", "horror.wow_cents", "Wow depth (cents)", 0, 40, 5, "%.1f")
_p("Horror", "horror.wow_hz", "Wow rate (Hz)", 0.2, 3, 0.8)
_p("Horror", "horror.flutter_cents", "Flutter depth (cents)", 0, 15, 2, "%.1f")
_p("Horror", "horror.flutter_hz", "Flutter rate (Hz)", 4, 15, 8, "%.1f")
_p("Horror", "horror.drop_pct", "Dropouts (% of time)", 0, 12, 2, "%.1f")
_p("Horror", "horror.drop_min_ms", "Dropout min (ms)", 10, 300, 20, "%.0f")
_p("Horror", "horror.drop_max_ms", "Dropout max (ms)", 10, 400, 120, "%.0f")
_p("Horror", "horror.drop_db", "Dropout depth (dB)", -60, 0, -30, "%.0f")
_p("Horror", "horror.hiss_db", "Hiss while talking (dBFS, -70 = off)", -70, -30, -52, "%.0f")
# Space
_p("Space", "space.room_mix", "Small room mix", 0, 1, 0.15)
_p("Space", "space.room", "Room size", 0, 1, 0.25)
_p("Space", "space.damping", "Room damping", 0, 1, 0.6)
_p("Space", "space.distance", "Distance (far = darker, quieter)", 0, 1, 0.0)
_p("Space", "space.occlusion", "Behind a wall (muffle)", 0, 1, 0.0)
# Tone (master EQ before the compressor). Game reference (KLR_35 LTAS): peak ~250 Hz, dip ~4 kHz, "air" 8-12 kHz
_p("Tone", "tone.low_db", "Low shelf 250 Hz (dB)", -12, 12, 0, "%.1f")
_p("Tone", "tone.dip_db", "Mid dip (dB)", -15, 6, 0, "%.1f")
_p("Tone", "tone.dip_hz", "Mid dip freq (Hz)", 1500, 7000, 4000, "%.0f")
_p("Tone", "tone.air_db", "Air shelf 8 kHz (dB)", -12, 12, 0, "%.1f")
# Game FX — measured in Init.bnk (2026-10-04): voice buses Pitch -200 cents; aux "Harmonizer" (-2 st LPF 3k +3 dB,
# -11 st LPF 500 +2 dB, wet -6) -> Pitch Shifter (-4.5 st, 45 ms, LPF 500); aux Stereo Delay L 440 / R 620 ms, fb ~-12 dB, LPF 4.9k, wet -9.
# Sends: male take harmonizer -6 / delay -12 dB, female take -12 / -8 dB. mix = 1 -> game levels.
_p("Game FX", "game.pitch_st", "Global pitch, resample-like (st; game = -2)", -4, 2, 0, "%.1f")
_p("Game FX", "shadow.mix", "Shadow harmonizer return (1 = game)", 0, 2, 0.0)
_p("Game FX", "shadow.send_m_db", "Shadow send from male (dB)", -30, 0, -6, "%.1f")
_p("Game FX", "shadow.send_f_db", "Shadow send from female (dB)", -30, 0, -12, "%.1f")
_p("Game FX", "shadow.st1", "Shadow voice 1 (st)", -12, 0, -2, "%.1f")
_p("Game FX", "shadow.st2", "Shadow voice 2 (st)", -24, 0, -11, "%.1f")
_p("Game FX", "shadow.st3", "Shadow pitch shifter (st, 45 ms late)", -12, 0, -4.5, "%.1f")
_p("Game FX", "shadow.lpf1", "Shadow voice 1 LPF (Hz)", 500, 8000, 3000, "%.0f")
_p("Game FX", "shadow.lpf2", "Shadow voice 2 / shifter LPF (Hz)", 100, 3000, 500, "%.0f")
_p("Game FX", "echo.mix", "Echo return (1 = game)", 0, 2, 0.0)
_p("Game FX", "echo.send_m_db", "Echo send from male (dB)", -30, 0, -12, "%.1f")
_p("Game FX", "echo.send_f_db", "Echo send from female (dB)", -30, 0, -8, "%.1f")
_p("Game FX", "echo.l_ms", "Echo tap 1 (ms)", 50, 1500, 440, "%.0f")
_p("Game FX", "echo.r_ms", "Echo tap 2 (ms)", 50, 1500, 620, "%.0f")
_p("Game FX", "echo.fb_db", "Echo feedback (dB)", -40, -3, -12, "%.1f")
_p("Game FX", "echo.lpf", "Echo LPF (Hz)", 1000, 12000, 4950, "%.0f")
_p("Game FX", "echo.tail_ms", "Echo tail after you stop (ms, 2000 = game)", 0, 2000, 60, "%.0f")
# Mumble — non-Weakened game lines are 0.2-0.3 s syllables + growl / hiss / rasp textures, not words
_p("Mumble", "mumble.chance", "Chance a phrase is mumbled (chopped)", 0, 1, 0.0)
_p("Mumble", "mumble.on_ms", "Syllable length (ms)", 80, 600, 250, "%.0f")
_p("Mumble", "mumble.off_ms", "Gap between syllables (ms)", 30, 600, 160, "%.0f")
_p("Mumble", "mumble.depth_db", "Gap depth (dB)", -60, 0, -40, "%.0f")
_p("Mumble", "growl.level_db", "Growl texture (dB, -40 = off)", -40, 0, -40, "%.0f")
_p("Mumble", "growl.hz", "Growl pitch (Hz)", 35, 150, 70, "%.0f")
_p("Mumble", "growl.breath", "Growl rasp / noise", 0, 1, 0.6)
_p("Mumble", "growl.lpf", "Growl LPF (Hz)", 800, 6000, 2500, "%.0f")
_p("Mumble", "growl.mumble_boost_db", "Growl boost in mumbled phrases (dB)", 0, 18, 6, "%.0f")
# Gate
_p("Gate", "gate.thr_db", "Gate threshold (dBFS)", -80, -20, -50, "%.1f")
_p("Gate", "gate.floor_db", "Level between words (dB)", -90, 0, -60, "%.0f")
_p("Gate", "gate.ratio", "Expander ratio", 1, 6, 2.0, "%.1f")
_p("Gate", "gate.hold_ms", "Hold after voice (ms)", 0, 500, 90, "%.0f")
# Master
_p("Master", "master.agc", "Auto-level layers (AGC)", kind="b", default=True)
_p("Master", "master.voice_hz", "Your usual pitch (Hz, start value)", 60, 250, 90, "%.0f")
_p("Master", "master.in_gain_db", "Input gain (dB)", -24, 24, 0, "%.1f")
_p("Master", "master.in_hpf", "Input HPF (Hz)", 20, 300, 80, "%.0f")
_p("Master", "master.comp_thr", "Compressor threshold (dB)", -40, 0, -18, "%.1f")
_p("Master", "master.comp_ratio", "Compressor ratio", 1, 12, 3, "%.1f")
_p("Master", "master.makeup_db", "Makeup gain (dB)", 0, 18, 6, "%.1f")
_p("Master", "master.out_db", "Output gain (dB)", -24, 12, 0, "%.1f")

SPEC_BY_KEY = {s["key"]: s for s in SPEC}
DEFAULTS = {s["key"]: s["default"] for s in SPEC}


def sanitize(p):
    """Merge onto DEFAULTS, drop unknown keys, clamp to SPEC ranges."""
    out = dict(DEFAULTS)
    for k, v in (p or {}).items():
        s = SPEC_BY_KEY.get(k)
        if s is None: continue
        try:
            out[k] = bool(v) if s["kind"] == "b" else float(np.clip(float(v), s["lo"], s["hi"]))
        except (TypeError, ValueError):
            pass
    return out


# ---------------------------------------------------------------- factory presets
# Overrides vs DEFAULTS. DEFAULTS themselves = "unknown/game twin".
_DRY = {"whisper.level_db": -40, "blur.ps": 0, "stutter.ps": 0, "horror.amount": 0, "space.room_mix": 0,
        "male.bode_mix": 0, "male.ring_mix": 0, "female.bode_mix": 0, "female.ring_mix": 0, "female.tune": 0,
        "male.drift_cents": 0, "female.drift_cents": 0, "clear.chance": 0}
FACTORY = {
    "unknown/game twin": ({}, "Main one. Male + female at once, slow sway with jumps, ~15% of phrases come out clear."),
    "unknown/weakened clear male": ({"clear.amount": 1.0, "clear.voice": 0.0},
                                    "What a Weakened survivor hears: the male voice, almost clean."),
    "unknown/weakened clear female": ({"clear.amount": 1.0, "clear.voice": 1.0},
                                      "What a Weakened survivor hears: the female voice, almost clean."),
    "unknown/mumbling hunt": ({"motion.depth": 0.8, "motion.jumps_ps": 0.8, "motion.desync_ms": 35, "motion.desync_drift": 0.7,
                               "blur.ps": 0.4, "blur.ms": 260, "whisper.level_db": -13, "horror.amount": 0.85,
                               "horror.drop_pct": 4, "male.bode_mix": 0.4, "female.ring_mix": 0.2, "clear.chance": 0.1,
                               "stutter.ps": 0.25},
                              "Chasing: muddier, two throats out of sync, more blur and dropouts. Clear phrases rare."),
    "unknown/found footage": ({"horror.amount": 1.0, "horror.band": 1.0, "horror.hpf": 300, "horror.lpf": 3200,
                               "horror.wow_cents": 14, "horror.flutter_cents": 4, "horror.drop_pct": 5, "horror.drive_db": 10,
                               "horror.hiss_db": -45, "space.room_mix": 0.05},
                              "Analog-horror tape: narrow band, wow/flutter, dropouts, hiss only while talking."),
    "unknown/behind the wall": ({"space.occlusion": 0.8, "space.distance": 0.5, "space.room_mix": 0.3, "space.room": 0.45,
                                 "horror.amount": 0.3, "master.out_db": 5},
                                "Far away and muffled, like it is in the next room."),
    "unknown/bad mimic": ({"motion.onset_flip": 0.7, "motion.jumps_ps": 0.9, "motion.jump_ms": 12, "female.tune": 1.0,
                           "female.retune_ms": 0, "male.bode_hz": 22, "male.bode_mix": 0.45, "male.drift_cents": 60,
                           "female.drift_cents": 45, "stutter.ps": 0.35, "clear.chance": 0.25},
                          "It tries to sound human and fails: flips voice on syllables, hard robotic tune, metallic shift."),
    "unknown/her voice": ({"motion.bias": 0.55, "motion.depth": 0.4, "female.tune": 1.0, "female.retune_ms": 5,
                           "female.ring_mix": 0.18, "clear.voice": 1.0},
                          "Mostly the robotic female voice, the male one shimmering underneath."),
    "unknown/his voice": ({"motion.bias": -0.55, "motion.depth": 0.4, "male.bode_mix": 0.35, "clear.voice": 0.0,
                           "male.pitch_st": -2},
                          "Mostly the male voice, the female one bleeding through."),
    "unknown/two throats": ({"motion.depth": 0.2, "motion.jumps_ps": 0.15, "motion.desync_ms": 45, "motion.desync_drift": 0.8,
                             "female.tune": 0.3, "horror.amount": 0.35, "whisper.level_db": -22},
                            "Both voices nearly equal all the time, the female one noticeably late: two people in one mouth."),
    # fitted 2026-10-04 to the game files (KLR_35 Weakened lines incl. bus pitch -200 c; Init.bnk aux FX): LTAS dist 1.0 dB (match)
    "game/match": ({'male.pitch_st': 11.0, 'male.formant': 0.95, 'male.lpf_hz': 16000.0, 'female.target_hz': 300.0, 'female.formant': 1.4, 'female.presence_db': 0.0, 'blur.ps': 0.05, 'stutter.ps': 0.05, 'horror.amount': 0.25, 'horror.band': 0.0, 'horror.hiss_db': -70.0, 'tone.low_db': 6.0, 'tone.dip_db': -4.0, 'tone.air_db': 9.0, 'game.pitch_st': -2.0, 'shadow.mix': 1.0, 'echo.mix': 1.0},
                   "Fitted to the real game: both takes at once, global -2 st, shadow harmonizer + 440/620 ms echo, wide-band tone."),
    "game/match weakened female": ({'clear.amount': 1.0, 'clear.voice': 1.0, 'male.pitch_st': 11.0, 'male.formant': 0.95, 'male.lpf_hz': 16000.0, 'female.target_hz': 300.0, 'female.formant': 1.4, 'female.presence_db': 0.0, 'blur.ps': 0.05, 'stutter.ps': 0.05, 'horror.amount': 0.25, 'horror.band': 0.0, 'horror.hiss_db': -70.0, 'tone.low_db': 12.0, 'tone.dip_db': -4.0, 'tone.air_db': 3.0, 'game.pitch_st': -2.0, 'shadow.mix': 1.0, 'echo.mix': 1.0},
                                   "Fitted Weakened view: the female take almost alone (game Weakened lines, LTAS dist 3.0 dB)."),
    "game/match weakened male": ({'clear.amount': 1.0, 'clear.voice': 0.0, 'male.pitch_st': 11.0, 'male.formant': 1.08, 'male.lpf_hz': 16000.0, 'female.target_hz': 300.0, 'female.formant': 1.4, 'female.presence_db': 0.0, 'blur.ps': 0.05, 'stutter.ps': 0.05, 'horror.amount': 0.25, 'horror.band': 0.0, 'horror.hiss_db': -70.0, 'tone.low_db': 3.0, 'tone.dip_db': -4.0, 'tone.dip_hz': 3000.0, 'tone.air_db': 9.0, 'game.pitch_st': -2.0, 'shadow.mix': 1.0, 'echo.mix': 1.0},
                                 "Fitted Weakened view: the male take almost alone (LTAS dist 2.9 dB)."),
    "game/mumble": ({'male.pitch_st': 11.0, 'male.formant': 0.95, 'male.lpf_hz': 16000.0, 'female.target_hz': 300.0, 'female.formant': 1.4, 'female.presence_db': 0.0, 'whisper.level_db': -16.0, 'blur.ps': 0.05, 'stutter.ps': 0.05, 'horror.amount': 0.25, 'horror.band': 0.0, 'horror.hiss_db': -70.0, 'tone.low_db': 6.0, 'tone.dip_db': -4.0, 'tone.air_db': 9.0, 'game.pitch_st': -2.0, 'shadow.mix': 1.0, 'echo.mix': 1.0, 'mumble.chance': 0.85, 'growl.level_db': -14.0},
                    "Like a non-Weakened survivor hears it: phrases chopped into syllables over growl + hiss; ~15 % come out clear."),
    "utility/dry twin": (dict(_DRY, **{"motion.depth": 0.7, "motion.jumps_ps": 0.5}),
                         "Just the two layers swaying, no FX at all (reference)."),
    "utility/bypass": (dict(_DRY, **{"female.mute": True, "motion.bias": -1, "motion.depth": 0, "motion.jumps_ps": 0,
                                     "motion.onset_flip": 0, "male.formant": 1.0, "clear.residual_db": -40}),
                       "Your clean voice."),
}


def _pid(path):
    return os.path.relpath(path, PRESET_DIR)[:-5].replace(os.sep, "/")


def preset_path(pid):
    return os.path.join(PRESET_DIR, *pid.split("/")) + ".json"


def ensure_factory(force=False):
    """Write factory presets that are missing (or all of them with force). Returns number written."""
    n = 0
    for pid, (ov, desc) in FACTORY.items():
        path = preset_path(pid)
        if os.path.isfile(path) and not force: continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = dict(ov); data["_meta"] = {"desc": desc}
        with open(path, "w", encoding="utf-8") as fh: json.dump(data, fh, indent=1, sort_keys=True)
        n += 1
    return n


def all_presets():
    """id -> (params, desc) for presets/**/*.json (factory defaults if the folder is empty)."""
    res = {}
    for f in sorted(glob.glob(os.path.join(PRESET_DIR, "**", "*.json"), recursive=True)):
        try:
            with open(f, encoding="utf-8") as fh: raw = json.load(fh)
            desc = (raw.get("_meta") or {}).get("desc", "") if isinstance(raw, dict) else ""
            res[_pid(f)] = (sanitize(raw), desc)
        except Exception as e:
            print(f"(skip broken preset {f}: {e})")
    if not res:
        res = {pid: (sanitize(ov), d) for pid, (ov, d) in FACTORY.items()}
    return res


def safe_id(pid):
    parts = [("".join(c for c in s if c.isalnum() or c in "-_ .()&'").strip().strip(".")) for s in pid.replace("\\", "/").split("/")]
    return "/".join(p for p in parts if p)


def save_preset(pid, params, desc=None):
    """Saves only the keys that differ from DEFAULTS (small, forward-compatible JSON)."""
    pid = safe_id(pid) or "preset"
    path = preset_path(pid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {k: v for k, v in params.items() if k in DEFAULTS and v != DEFAULTS[k]}
    if desc is None and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh: desc = (json.load(fh).get("_meta") or {}).get("desc")
        except Exception: pass
    if desc: data["_meta"] = {"desc": desc}
    with open(path, "w", encoding="utf-8") as fh: json.dump(data, fh, indent=1, sort_keys=True)
    return pid, path


def resolve_id(name, presets):
    if name in presets: return name
    hits = [k for k in presets if k.split("/")[-1].lower() == str(name).lower()]
    return hits[0] if hits else None


# ---------------------------------------------------------------- small helpers
def db2a(db): return 10.0 ** (db / 20.0)


class Drift:
    """Smooth random walk in [-1, 1]: new random knot every ~1/rate s, smoothstep between knots (per hop)."""
    def __init__(self, rng, rate):
        self.rng, self.rate = rng, rate; self.a = 0.0; self.b = rng.uniform(-1, 1); self.pos = 0; self.len = 1

    def step(self, hops_per_s):
        if self.pos >= self.len:
            self.a = self.b; self.b = self.rng.uniform(-1, 1); self.pos = 0
            self.len = max(1, int(hops_per_s / self.rate * self.rng.uniform(0.6, 1.4)))
        t = self.pos / self.len; t = t * t * (3 - 2 * t); self.pos += 1
        return self.a + (self.b - self.a) * t


class VarDelay:
    """Fractional delay line with per-sample delay (samples). Used for desync and wow/flutter."""
    def __init__(self, max_s):
        self.buf = np.zeros(int(SR * max_s) + 4)

    def __call__(self, x, d):
        L = len(self.buf); n = len(x)
        ext = np.concatenate([self.buf, x]); self.buf = ext[-L:]
        pos = L + np.arange(n) - np.clip(d, 1.0, L - 2)
        i0 = np.floor(pos).astype(int); fr = pos - i0
        return ext[i0] * (1 - fr) + ext[i0 + 1] * fr


# ---------------------------------------------------------------- engine
class Engine:
    def __init__(self, n_fft=2048, seed=None, params=None):
        self.N, self.H = n_fft, n_fft // 4
        self.K = self.N // 2 + 1
        self.hps = SR / self.H                                  # hops per second
        self.win = np.hanning(self.N + 1)[:-1]
        self.ola_norm = 1.0 / 1.5
        self.k = np.arange(self.K, dtype=np.float64)
        self.freqs = self.k * SR / self.N
        self.nN = np.arange(self.N)
        self.rng = np.random.default_rng(seed)
        self.inbuf = np.zeros(self.N)
        self.pk_state = {v: (np.zeros(0, int), np.zeros(0)) for v in SYN}
        self.ola = {v: np.zeros(self.N) for v in SYN}
        self.bode_ph = {v: 0.0 for v in SYN}
        self.ring_ph = {v: 0.0 for v in LAYERS}
        self.lifter = max(20, int(0.0016 * SR * self.N / 2048))
        self.agc_db = dict(AGC_INIT_DB)
        self.f0_hist = deque(maxlen=400); self.f0_med = 110.0
        self.drift = {v: Drift(self.rng, r) for v, r in (("male", 1.1), ("female", 0.8))}
        self.sway_noise = Drift(self.rng, 0.15)
        self.corr = 0.0                                         # hard-tune correction (semitones), smoothed
        self.mag_hist = deque(maxlen=8); self.blur_left = 0
        self.fx = {"male": Pedalboard([LowpassFilter(12000)]),
                   "female": Pedalboard([HighpassFilter(140), PeakFilter(3000, gain_db=2, q=1.0)]),
                   "whisper": Pedalboard([])}
        self.in_hpf = Pedalboard([HighpassFilter(80)])
        self.band = Pedalboard([HighpassFilter(250), HighpassFilter(250), LowpassFilter(3500), LowpassFilter(3500)])
        self.desync = VarDelay(0.2); self.desync_g = VarDelay(0.2); self.wow = VarDelay(0.1)
        self.sh3_delay = {L: VarDelay(0.1) for L in ("m", "f")}
        self.echo_buf = np.zeros(int(SR * 1.6) + 8); self.echo_out = np.zeros(int(SR * 1.6) + 8)
        self.echo_lpf = Pedalboard([LowpassFilter(4950), LowpassFilter(4950)])
        self.phrase_mumble = False; self.chop_on = True; self.chop_left = 0; self.chop_g = 1.0; self._chop = 1.0
        self.echo_hold = 0; self.echo_g = 0.0
        self.dist = Pedalboard([LowpassFilter(16000)])
        self.occl = Pedalboard([LowpassFilter(1800), LowpassFilter(1800)])
        self.room = Pedalboard([Reverb(room_size=0.25, damping=0.6, wet_level=0.0, dry_level=0.5, width=0.0)])
        self.tone = Pedalboard([LowShelfFilter(250, gain_db=0), PeakFilter(4000, gain_db=0, q=1.0), HighShelfFilter(8000, gain_db=0)])
        self.master = Pedalboard([Compressor(threshold_db=-18, ratio=3, attack_ms=5, release_ms=80),
                                  Gain(6), Limiter(threshold_db=-1.0, release_ms=60)])
        # motion / clear state
        self.t = 0.0; self.sway_ph = self.rng.uniform(0, 2 * np.pi); self.ds_ph = 0.0; self.wow_ph = 0.0; self.flt_ph = 0.0
        self.jw = 0.0; self.jval = 0.5; self.jhold = 0; self.jcool = 0
        self.b_prev = 0.5; self.c = 0.0; self.c_prev = 0.0
        self.phrase_clear = False; self.clear_fem = False; self.sil_hops = 10 ** 6
        self.key_hist = deque([-120.0] * 4, maxlen=4); self.onset_cool = 0
        self.key_delay = deque([-200.0] * 3, maxlen=3)          # STFT latency = N - H = 3 hops
        self.hold = 0; self.g_prev = 0.0; self._talking = False
        self.hist = np.zeros(int(SR * 0.4)); self.st_seg = None; self.st_left = 0; self.st_pos = 0
        self.drop_left = 0; self.drop_gain = 1.0; self.env = 0.0
        self.manual_clear = False                               # hotkey toggle (UI), OR-ed with clear.amount
        self.stats = dict(in_db=-120.0, out_db=-120.0, gate=0.0, balance=0.5, clear=0.0, load=0.0, xruns=0,
                          phrase_clear=False, clear_fem=False, f0=0.0)
        self.p = sanitize(params); self._reset = True; self._dirty = True
        self.preset_name = "custom"
        self.f0_med = self.p["master.voice_hz"]

    # ---------- parameters (UI thread) ----------
    def load(self, params, name="custom"):
        """Swap ALL parameters atomically (one dict assignment); motion state is reset in the audio thread."""
        self.p = sanitize(params); self.preset_name = name; self._dirty = True; self._reset = True

    def set(self, key, value):
        s = SPEC_BY_KEY[key]
        self.p[key] = bool(value) if s["kind"] == "b" else float(np.clip(float(value), s["lo"], s["hi"]))
        self._dirty = True

    def _apply(self, p):
        self._dirty = False
        self.fx["male"][0].cutoff_frequency_hz = p["male.lpf_hz"]
        f = self.fx["female"]; f[0].cutoff_frequency_hz = p["female.hpf_hz"]; f[1].gain_db = p["female.presence_db"]
        self.in_hpf[0].cutoff_frequency_hz = p["master.in_hpf"]
        b = self.band; lo, hi = sorted((p["horror.hpf"], p["horror.lpf"]))
        b[0].cutoff_frequency_hz = lo; b[1].cutoff_frequency_hz = lo; b[2].cutoff_frequency_hz = hi; b[3].cutoff_frequency_hz = hi
        self.echo_lpf[0].cutoff_frequency_hz = p["echo.lpf"]; self.echo_lpf[1].cutoff_frequency_hz = p["echo.lpf"]
        self.dist[0].cutoff_frequency_hz = float(16000 * (4500 / 16000) ** p["space.distance"])
        r = self.room[0]; r.room_size = p["space.room"]; r.damping = p["space.damping"]
        t = self.tone
        t[0].gain_db = p["tone.low_db"]; t[1].gain_db = p["tone.dip_db"]; t[1].cutoff_frequency_hz = p["tone.dip_hz"]
        t[2].gain_db = p["tone.air_db"]
        m = self.master
        m[0].threshold_db = p["master.comp_thr"]; m[0].ratio = p["master.comp_ratio"]
        m[1].gain_db = p["master.makeup_db"] + p["master.out_db"]

    def _do_reset(self, p):
        self._reset = False
        self.jw = 0.0; self.jhold = 0; self.jcool = 0
        self.f0_med = p["master.voice_hz"] if len(self.f0_hist) < 20 else self.f0_med
        self.phrase_clear = False
        self.clear_fem = self.rng.random() < p["clear.voice"]

    # ---------- analysis ----------
    def _f0(self, frame, energy_db, thr):
        if energy_db < thr: return 0.0
        x = frame[::4] * np.hanning(len(frame) // 4 + 1)[:-1]
        n = len(x); X = np.fft.rfft(x, 2 * n); ac = np.fft.irfft(np.abs(X) ** 2)[:n]
        if ac[0] <= 0: return 0.0
        ac = ac / ac[0]
        lo, hi = int(12000 / 700), min(int(12000 / 55), n - 2)
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] < 0.45: return 0.0
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]; d = (a - c) / (2 * (a - 2 * b + c) + 1e-12)
        f0 = 12000 / (lag + d)
        self.f0_hist.append(f0)
        if len(self.f0_hist) > 20: self.f0_med = float(np.median(self.f0_hist))
        return f0

    def _env(self, mag):
        lm = np.log(mag + 1e-9)
        pad = np.pad(lm, 2, mode="edge")
        mx = np.lib.stride_tricks.sliding_window_view(pad, 5).max(1)
        pk = np.where((lm >= mx) & (lm > lm.max() - 18.4))[0]
        if pk.size < 2:
            c = np.fft.irfft(lm); L = self.lifter; c[L:self.N - L] = 0
            return np.exp(np.fft.rfft(c).real)
        e = np.interp(self.k, pk, lm[pk])
        e = np.convolve(np.pad(e, 2, mode="edge"), np.ones(5) / 5, mode="valid")
        return np.exp(e)

    def _peaks(self, mag):
        """Peak bins, owner peak of every bin, and the true (fractional, quadratic-interpolated) peak positions."""
        m = mag[1:-1]
        pk = np.where((m > mag[:-2]) & (m >= mag[2:]) & (m > mag.max() * 1e-4))[0] + 1
        if pk.size == 0: pk = np.array([1])
        mid = (pk[:-1] + pk[1:]) / 2.0
        lm = np.log(mag + 1e-12); a, b, c = lm[pk - 1], lm[pk], lm[np.minimum(pk + 1, self.K - 1)]
        den = a - 2 * b + c
        pf = pk + np.clip(np.where(np.abs(den) > 1e-9, 0.5 * (a - c) / np.where(np.abs(den) > 1e-9, den, 1), 0), -0.5, 0.5)
        return pk, np.searchsorted(mid, self.k, side="right"), pf

    def _shift(self, v, Xf, env, r, fr, noise, target_energy):
        """Peak-locked pitch shift (Laroche-Dolson) with FRACTIONAL shifts (exact harmonics) + formant envelope (warped by fr)."""
        K = self.K
        envw = np.interp(self.k / fr, self.k, env, right=env[-1] * 1e-3)
        if noise >= 1.0:
            Y = envw * np.exp(1j * self.rng.uniform(-np.pi, np.pi, K))
        else:
            pk, owner = self._pk, self._owner
            dfr = self._pf * (r - 1)                   # exact shift in bins (fractional) -> phase advance
            d = np.rint(dfr).astype(int)               # magnitude goes to the nearest bin
            ppk, prot = self.pk_state[v]
            if ppk.size > 1:
                j = np.clip(np.searchsorted(ppk, pk), 1, ppk.size - 1)
                j = np.where(np.abs(ppk[j - 1] - pk) <= np.abs(ppk[j] - pk), j - 1, j)
                base = prot[j]
            elif ppk.size == 1:
                base = np.full(pk.size, prot[0])
            else:
                base = np.zeros(pk.size)
            rot = np.mod(base + 2 * np.pi * self.H * dfr / self.N + np.pi, 2 * np.pi) - np.pi
            self.pk_state[v] = (pk, rot)
            dst = self.k.astype(int) + d[owner]; ok = (dst >= 0) & (dst < K)
            Y = np.zeros(K, complex)
            np.add.at(Y, dst[ok], (Xf * np.exp(1j * rot[owner]))[ok])
            Y *= envw
            if noise > 0:
                Nz = envw * np.exp(1j * self.rng.uniform(-np.pi, np.pi, K))
                ey, en = np.sum(np.abs(Y) ** 2), np.sum(np.abs(Nz) ** 2)
                if en > 0 and ey > 0:
                    Y = np.sqrt(1 - noise) * Y + np.sqrt(noise) * Nz * np.sqrt(ey / en)
        e = np.sum(np.abs(Y) ** 2)
        if e > 0: Y *= np.sqrt(target_energy / e)
        return Y

    def _synth(self, v, Y, bode_hz=0.0, bode_mix=0.0):
        """OLA synthesis; optional Bode frequency shift done exactly on the frame's analytic signal."""
        N, H, K = self.N, self.H, self.K
        if Y is None:
            frame = None
        elif bode_mix > 1e-3 and abs(bode_hz) > 0.05:
            Z = np.zeros(N, complex); Z[0] = Y[0]; Z[1:K - 1] = 2 * Y[1:K - 1]; Z[K - 1] = Y[K - 1]
            z = np.fft.ifft(Z)
            ph = self.bode_ph[v] + 2 * np.pi * bode_hz * self.nN / SR
            frame = np.real(z * ((1 - bode_mix) + bode_mix * np.exp(1j * ph)))
        else:
            frame = np.fft.irfft(Y, N)
        self.bode_ph[v] = (self.bode_ph[v] + 2 * np.pi * bode_hz * H / SR) % (2 * np.pi)
        buf = self.ola[v]
        if frame is not None: buf += frame * self.win * self.ola_norm
        out = buf[:H].copy()
        self.ola[v] = np.concatenate([buf[H:], np.zeros(H)])
        return out

    def _hardtune(self, f0, rf, p, c):
        """Female pitch ratio: follow the contour at target_hz, pull towards scale notes (retune smoothing)."""
        if f0 <= 0: return rf * 2 ** (self.corr / 12)
        desired = 12 * np.log2(f0 * rf / 440.0)
        if p["female.pentatonic"]:
            octv = np.floor(desired / 12) * 12; deg = desired - octv
            cand = np.concatenate([PENT, [12]]); snapped = octv + cand[np.argmin(np.abs(cand - deg))]
        else:
            snapped = np.round(desired)
        tgt = p["female.tune"] * (1 - 0.7 * c) * (snapped - desired)
        tau = p["female.retune_ms"] / 1000
        a = 1.0 if tau <= 0 else 1 - np.exp(-(self.H / SR) / tau)
        self.corr += a * (tgt - self.corr)
        return rf * 2 ** (self.corr / 12)

    # ---------- one hop ----------
    def _hop(self, hop_in, p, c):
        N, H = self.N, self.H
        self.inbuf = np.concatenate([self.inbuf[H:], hop_in])
        X = np.fft.rfft(self.inbuf * self.win)
        lvl_db = 10 * np.log10(np.mean(hop_in ** 2) + 1e-12)
        f0 = self._f0(self.inbuf, lvl_db, p["gate.thr_db"])
        mag = np.abs(X); self.mag_hist.append(mag)
        # spectral blur: smear magnitudes over the last k frames (keeps current phase) for blur.ms
        if self.blur_left <= 0 and f0 > 0 and self.rng.random() < p["blur.ps"] * (1 - c) / self.hps:
            self.blur_left = max(1, int(p["blur.ms"] / 1000 * self.hps))
        if self.blur_left > 0:
            self.blur_left -= 1
            k = int(round(p["blur.frames"])); m = np.mean(list(self.mag_hist)[-k:], axis=0)
            X = m * np.exp(1j * np.angle(X)); mag = m
        energy = np.sum(mag ** 2)
        env = self._env(mag); Xf = X / (env + 1e-12)
        self._pk, self._owner, self._pf = self._peaks(mag)
        med = self.f0_med
        cents_m = p["male.drift_cents"] * (1 - c) * self.drift["male"].step(self.hps)
        cents_f = p["female.drift_cents"] * (1 - c) * self.drift["female"].step(self.hps)
        Ym = Yf = Yw = Yg = None
        gp = 2 ** (p["game.pitch_st"] / 12)                     # resample-like: pitch AND formants move together
        lay_rf = {}
        if not p["male.mute"]:
            rm = 2 ** ((p["male.pitch_st"] + cents_m / 100) / 12) * gp
            Ym = self._shift("male", Xf, env, rm, p["male.formant"] * gp, p["male.breath"], energy)
            lay_rf["m"] = (rm, p["male.formant"] * gp)
        if not p["female.mute"]:
            rf = self._hardtune(f0, float(np.clip(p["female.target_hz"] / med, 0.5, 4.0)), p, c) * 2 ** (cents_f / 1200) * gp
            rf = float(np.clip(rf, 0.25, 4.0))
            Yf = self._shift("female", Xf, env, rf, p["female.formant"] * gp, p["female.breath"], energy)
            lay_rf["f"] = (rf, p["female.formant"] * gp)
        # shadow harmonizer (game aux): shifted copies of each layer, formants move with the pitch (no preservation), low-passed
        sh_out = {}
        for L in ("m", "f"):
            for kk, (stk, fc) in enumerate(((p["shadow.st1"], p["shadow.lpf1"]), (p["shadow.st2"], p["shadow.lpf2"]),
                                            (p["shadow.st3"], p["shadow.lpf2"])), 1):
                key = f"sh{kk}_{L}"; Y = None
                if p["shadow.mix"] > 1e-3 and L in lay_rf:
                    r0, f0r = lay_rf[L]; q = 2 ** (stk / 12)
                    Y = self._shift(key, Xf, env, float(np.clip(r0 * q, 0.1, 4.0)), f0r * q, 0.0, energy)
                    Y = Y / np.sqrt(1 + (self.freqs / fc) ** 4)
                sh_out[key] = self._synth(key, Y)
        if p["growl.level_db"] > -39.9 and c < 0.999:
            Yg = self._shift("growl", Xf, env, float(np.clip(p["growl.hz"] / med, 0.15, 2.0)), 0.75, p["growl.breath"], energy)
            Yg = Yg / np.sqrt(1 + (self.freqs / p["growl.lpf"]) ** 4)
        if p["whisper.level_db"] > -39.9 and c < 0.999:
            Yw = self._shift("whisper", Xf, env, 1.0, p["whisper.formant"], 1.0, energy)
            Yw[self.freqs < p["whisper.hpf_hz"]] = 0
        k = 1 - c
        om = self._synth("male", Ym, p["male.bode_hz"], p["male.bode_mix"] * k)
        of = self._synth("female", Yf, p["female.bode_hz"], p["female.bode_mix"] * k)
        ow = self._synth("whisper", Yw)
        og = self._synth("growl", Yg)
        self.key_delay.append(lvl_db)
        self.stats["f0"] = f0
        return om, of, ow, self.key_delay[0], lvl_db, sh_out, og

    # ---------- motion / clear control (per hop) ----------
    def _control(self, key_db, p):
        thr = p["gate.thr_db"]; talking = key_db > thr
        # phrases: a new one starts after clear.gap_ms of silence
        if talking:
            if self.sil_hops * 1000 / self.hps >= p["clear.gap_ms"]:
                self.phrase_clear = self.rng.random() < p["clear.chance"]
                self.clear_fem = self.rng.random() < p["clear.voice"]
                self.phrase_mumble = (not self.phrase_clear) and self.rng.random() < p["mumble.chance"]
                self.chop_on = True; self.chop_left = 0
            self.sil_hops = 0
        else:
            self.sil_hops += 1
        tgt_c = max(p["clear.amount"], 1.0 if (self.phrase_clear or self.manual_clear) else 0.0)
        a = 1 - np.exp(-1 / (max(p["clear.fade_ms"], 1) / 1000 * self.hps))
        self.c += a * (tgt_c - self.c)
        c = self.c
        # onsets: level jumps >= 10 dB above the recent minimum
        onset = talking and self.onset_cool <= 0 and key_db - min(self.key_hist) > 10
        self.key_hist.append(key_db)
        if onset: self.onset_cool = int(0.15 * self.hps)
        self.onset_cool -= 1
        # slow sway
        self.t += 1 / self.hps
        self.sway_ph = (self.sway_ph + 2 * np.pi * p["motion.rate_hz"] / self.hps) % (2 * np.pi)
        sway = 0.7 * np.sin(self.sway_ph) + 0.3 * self.sway_noise.step(self.hps)
        b_lfo = float(np.clip(0.5 + 0.5 * p["motion.bias"] + 0.5 * p["motion.depth"] * sway, 0, 1))
        # jumps: fast move to one side, hold, release
        trig = None
        if c < 0.5 and self.jhold <= 0 and self.jw < 0.05:
            if talking and self.rng.random() < p["motion.jumps_ps"] / self.hps: trig = "jump"
            elif onset and self.rng.random() < p["motion.onset_flip"]: trig = "onset"
        if trig:
            cur = self.b_prev; ja = p["motion.jump_amount"]
            self.jval = ja if cur < 0.5 else 1 - ja
            lo, hi = sorted((p["motion.hold_min_ms"], p["motion.hold_max_ms"]))
            if trig == "onset": lo, hi = 150, 450
            self.jhold = int(self.rng.uniform(lo, hi) / 1000 * self.hps)
        if self.jhold > 0:
            self.jw = min(1.0, self.jw + 1 / max(1e-3, p["motion.jump_ms"] / 1000 * self.hps)); self.jhold -= 1
        else:
            self.jw = max(0.0, self.jw - 1 / (0.15 * self.hps))
        b_mot = b_lfo * (1 - self.jw) + self.jval * self.jw
        # clear: dominant layer, the other at residual_db
        r = float(np.arcsin(min(1.0, db2a(p["clear.residual_db"]))) * 2 / np.pi)
        b_clear = 1 - r if self.clear_fem else r
        if p["female.mute"]: b_mot, b_clear = 0.0, 0.0
        if p["male.mute"]: b_mot, b_clear = 1.0, 1.0
        # mumble chop: syllable-sized on/off while talking in a mumbled phrase (off when clear)
        chop = 1.0
        if not talking and self.sil_hops > int(0.12 * self.hps):
            self.chop_on = True; self.chop_left = 0          # after a pause, always start with a syllable
        if self.phrase_mumble and c < 0.5 and not self.manual_clear and talking:
            if self.chop_left <= 0:
                self.chop_on = not self.chop_on
                base = p["mumble.on_ms"] if self.chop_on else p["mumble.off_ms"]
                self.chop_left = max(1, int(base * self.rng.uniform(0.6, 1.4) / 1000 * self.hps))
            self.chop_left -= 1
            chop = 1.0 if self.chop_on else db2a(p["mumble.depth_db"])
        elif self.phrase_mumble and not self.chop_on:
            chop = db2a(p["mumble.depth_db"])
        self._chop = chop
        return b_mot * (1 - c) + b_clear * c, c, talking

    # ---------- block ----------
    def process(self, x):
        """x: mono float block, len multiple of H. Returns processed mono float32 block."""
        p = self.p                                    # one reference for the whole block (UI may swap self.p)
        if self._reset: self._do_reset(p)
        if self._dirty: self._apply(p)
        H = self.H; n = len(x)
        x = np.asarray(x, np.float32) * np.float32(db2a(p["master.in_gain_db"]))
        x = self.in_hpf(x[None, :], SR, reset=False)[0].astype(np.float64)
        lay = {v: np.zeros(n) for v in LAYERS + SHADOWS + ["growl"]}
        chop = np.ones(n)
        bal = np.zeros(n); cc = np.zeros(n); gate = np.zeros(n); talk = np.zeros(n, bool)
        thr = p["gate.thr_db"]; floor = p["gate.floor_db"]; ratio = p["gate.ratio"]
        hold_hops = int(round(p["gate.hold_ms"] / 1000 * self.hps))
        ramp = np.linspace(0, 1, H, endpoint=False)
        in_db = -120.0
        for i in range(0, n, H):
            om, of, ow, key_db, lvl, sh, og = self._hop(x[i:i + H], p, self.c); in_db = max(in_db, lvl)
            lay["male"][i:i + H] = om; lay["female"][i:i + H] = of; lay["whisper"][i:i + H] = ow; lay["growl"][i:i + H] = og
            for kk, v in sh.items(): lay[kk][i:i + H] = v
            b, c, _ = self._control(key_db, p)
            chop[i:i + H] = self.chop_g + (self._chop - self.chop_g) * ramp; self.chop_g = self._chop
            bal[i:i + H] = self.b_prev + (b - self.b_prev) * ramp; self.b_prev = b
            cc[i:i + H] = self.c_prev + (c - self.c_prev) * ramp; self.c_prev = c
            talk[i:i + H] = key_db > thr
            if key_db > thr: self.hold = hold_hops
            elif self.hold > 0: self.hold -= 1
            gdb = 0.0 if (key_db > thr or self.hold > 0) else max(floor, ratio * (key_db - thr))
            g = db2a(gdb)
            gate[i:i + H] = np.linspace(self.g_prev, g, H, endpoint=False) if g < self.g_prev else g
            self.g_prev = g
        ref_db = 10 * np.log10(np.mean(x ** 2) + 1e-12)        # dry reference (not delayed; AGC is slow anyway)
        self._talking = ref_db > thr + 6
        cm = float(cc.mean()); k = 1 - cm
        # per-layer: ring mod -> filters -> AGC -> trim
        for v in ("male", "female"):
            y = lay[v]
            rm = p[f"{v}.ring_mix"] * k
            tt = self.ring_ph[v] + 2 * np.pi * p[f"{v}.ring_hz"] * np.arange(n) / SR
            self.ring_ph[v] = float(tt[-1] + 2 * np.pi * p[f"{v}.ring_hz"] / SR) % (2 * np.pi)
            if rm > 1e-3: y = y * (1 - rm + rm * np.sin(tt))
            y = self.fx[v](y.astype(np.float32)[None, :], SR, reset=False)[0].astype(np.float64)
            g_before = self.agc_db[v]
            lay[v] = self._agc(v, y, ref_db) * db2a(p[f"{v}.level_db"])
            # shadow harmonizer return for this layer: send -> aux bus (-4 vol, -4 makeup) -> voices +3 / +2 dB, wet -6;
            # pitch shifter wet -6 dB, 45 ms late. Uses the layer's AGC gain so it tracks the layer level.
            if p["shadow.mix"] > 1e-3:
                L = v[0]; sg = db2a(p[f"shadow.send_{L}_db"]) * db2a(-8) * p["shadow.mix"] * db2a(self.agc_db[v] + p[f"{v}.level_db"])
                s3 = self.sh3_delay[L](lay[f"sh3_{L}"], np.full(n, 0.045 * SR))
                lay[v] = lay[v] + sg * (db2a(3 - 6) * lay[f"sh1_{L}"] + db2a(2 - 6) * lay[f"sh2_{L}"] + db2a(-5 - 6 + 6) * 0.5 * s3)
        lay["whisper"] = self._agc("whisper", lay["whisper"], ref_db)
        lay["growl"] = self._agc("growl", lay["growl"], ref_db)
        # desync: the female throat lags behind
        self.ds_ph = (self.ds_ph + 2 * np.pi * 0.13 * n / SR) % (2 * np.pi)
        dms = p["motion.desync_ms"] * (1 + p["motion.desync_drift"] * 0.6 * np.sin(self.ds_ph + 2 * np.pi * 0.13 * np.arange(n) / SR))
        fem = self.desync(lay["female"], dms / 1000 * SR)
        gate = np.maximum(gate, self.desync_g(gate, dms / 1000 * SR))   # keep the late female word tails
        gm = np.cos(bal * np.pi / 2); gf = np.sin(bal * np.pi / 2)
        wl = db2a(p["whisper.level_db"]) if p["whisper.level_db"] > -39.9 else 0.0
        voice = (gm * lay["male"] + gf * fem) * chop
        gl = db2a(p["growl.level_db"] + (p["growl.mumble_boost_db"] if self.phrase_mumble else 0)) if p["growl.level_db"] > -39.9 else 0.0
        mix = voice + wl * (1 - cc) * lay["whisper"] + gl * (1 - cc) * lay["growl"]
        echo_in = (gm * lay["male"] * db2a(p["echo.send_m_db"]) + gf * fem * db2a(p["echo.send_f_db"])) * chop
        mix = self._stutter(mix, talk, p, k)
        self.hist = np.concatenate([self.hist, mix])[-len(self.hist):]
        mix = self._horror(mix, gate, p, cm)
        mix = mix * gate
        if p["echo.mix"] > 1e-3 or np.abs(self.echo_out).max() > 1e-6:
            # echo tail: kept for echo.tail_ms after the dry voice stops, then faded (150 ms) -> clean pauses
            self.echo_hold = int(p["echo.tail_ms"] / 1000 * SR) if talk.any() else self.echo_hold - n
            tg = 1.0 if (self.echo_hold > 0 or p["echo.tail_ms"] >= 1999) else 0.0
            g1 = float(np.clip(self.echo_g + np.sign(tg - self.echo_g) * n / (0.15 * SR), 0, 1)) if tg != self.echo_g else tg
            eg = np.linspace(self.echo_g, g1, n, endpoint=False); self.echo_g = g1
            mix = mix + self._echo(echo_in * gate, p) * db2a(-9) * p["echo.mix"] * eg
        # hiss only while talking (envelope of the gated mix), pauses stay clean
        ha = p["horror.amount"] * (1 - 0.85 * cm)
        if p["horror.hiss_db"] > -69.9 and ha > 1e-3:
            e = np.abs(mix); a_att, a_rel = 1 - np.exp(-1 / (0.005 * SR)), 1 - np.exp(-1 / (0.08 * SR))
            env = np.empty(n); s = self.env
            blk = 64
            for j in range(0, n, blk):
                m_ = e[j:j + blk].max(); s += (a_att if m_ > s else a_rel * blk) * (m_ - s); env[j:j + blk] = s
            self.env = s
            hz = np.diff(self.rng.standard_normal(n + 1)) * 0.5
            mix = mix + hz * db2a(p["horror.hiss_db"]) * ha * np.clip(env / 0.05, 0, 1) * gate
        # space: distance / occlusion / small room (after the gate: the room rings into pauses a bit)
        y = mix.astype(np.float32)[None, :]
        d = p["space.distance"]; o = p["space.occlusion"]
        if d > 1e-3: y = self.dist(y, SR, reset=False) * np.float32(db2a(-6 * d))
        if o > 1e-3:
            yo = self.occl(y, SR, reset=False); y = y * np.float32(1 - o) + yo * np.float32(o * db2a(-4 * o))
        rmix = p["space.room_mix"] * (1 - 0.6 * cm)
        rv = self.room[0]; rv.wet_level = 0.33 * rmix; rv.dry_level = 0.5 * (1 - 0.4 * rmix)
        if rmix > 1e-3: y = self.room(y, SR, reset=False)
        if abs(p["tone.low_db"]) + abs(p["tone.dip_db"]) + abs(p["tone.air_db"]) > 0.05:
            y = self.tone(y, SR, reset=False)
        y = self.master(y, SR, reset=False)[0].astype(np.float64)
        a_ = np.abs(y); k0 = 0.7; over = a_ > k0
        if over.any(): y[over] = np.sign(y[over]) * (k0 + 0.25 * np.tanh((a_[over] - k0) / 0.25))
        y = y.astype(np.float32)
        st = self.stats
        st["in_db"] = in_db; st["out_db"] = 10 * np.log10(np.mean(y ** 2) + 1e-12); st["gate"] = float(gate[-1])
        st["balance"] = float(self.b_prev); st["clear"] = float(self.c)
        st["phrase_clear"] = self.phrase_clear; st["clear_fem"] = self.clear_fem
        return y

    def _echo(self, x, p):
        """Game aux Stereo Delay folded to mono: two taps (L/R) with feedback, low-passed. Delays > block, so no intra-block recursion."""
        n = len(x); L = len(self.echo_buf)
        self.echo_buf = np.concatenate([self.echo_buf[n:], x])
        fb = db2a(p["echo.fb_db"]); out = np.zeros(n)
        for ms in (p["echo.l_ms"], p["echo.r_ms"]):
            d = int(ms / 1000 * SR)
            t = np.arange(n) - d                              # time relative to this block's first sample (< 0)
            out += 0.5 * (self.echo_buf[L - n + t] + fb * self.echo_out[L + t])   # echo_out not yet shifted: index L-1 = time -1
        self.echo_out = np.concatenate([self.echo_out[n:], out])
        return self.echo_lpf(out.astype(np.float32)[None, :], SR, reset=False)[0].astype(np.float64)

    def _agc(self, v, y, ref_db):
        g0 = self.agc_db[v]
        if self._talking and self.p["master.agc"]:
            v_db = 10 * np.log10(np.mean(y ** 2) + 1e-12)
            if v_db > -90:
                a = 1 - np.exp(-len(y) / (0.6 * SR))
                self.agc_db[v] = float(np.clip(g0 + a * ((ref_db - v_db) - g0), -24, 24))
        return y * 10 ** (np.linspace(g0, self.agc_db[v], len(y)) / 20)

    def _horror(self, mix, gate, p, cm):
        a = p["horror.amount"] * (1 - 0.85 * cm); n = len(mix)
        yb = self.band(mix.astype(np.float32)[None, :], SR, reset=False)[0].astype(np.float64)   # keep filter state warm
        if a <= 1e-3:
            self.wow(mix, np.full(n, 2.0)); return mix
        bm = a * p["horror.band"]
        y = mix * (1 - bm) + yb * bm * 1.6                      # ~+4 dB: band-pass loses energy
        g = db2a(p["horror.drive_db"] * a)
        if g > 1.01: y = 0.25 * np.tanh(g * y / 0.25) / np.sqrt(g)
        # wow / flutter: delay modulation; depth D (samples) for +-cents at rate f: D = (2^(c/1200)-1) * SR / (2 pi f)
        t = np.arange(n) / SR
        Dw = (2 ** (p["horror.wow_cents"] * a / 1200) - 1) * SR / (2 * np.pi * p["horror.wow_hz"])
        Df = (2 ** (p["horror.flutter_cents"] * a / 1200) - 1) * SR / (2 * np.pi * p["horror.flutter_hz"])
        d = 2 + Dw + Df + Dw * np.sin(self.wow_ph + 2 * np.pi * p["horror.wow_hz"] * t) \
            + Df * np.sin(self.flt_ph + 2 * np.pi * p["horror.flutter_hz"] * t)
        self.wow_ph = (self.wow_ph + 2 * np.pi * p["horror.wow_hz"] * n / SR) % (2 * np.pi)
        self.flt_ph = (self.flt_ph + 2 * np.pi * p["horror.flutter_hz"] * n / SR) % (2 * np.pi)
        y = self.wow(y, d)
        # dropouts: drop_pct % of the time, random length, smoothed 3 ms
        pct = p["horror.drop_pct"] * a / 100
        dmin, dmax = sorted((p["horror.drop_min_ms"], p["horror.drop_max_ms"]))
        rate = pct / max((dmin + dmax) / 2000, 1e-3)            # events per second
        depth = db2a(p["horror.drop_db"]); dg = np.ones(n); blk = 96
        for i in range(0, n, blk):
            if self.drop_left <= 0 and gate[i] > 0.5 and self.rng.random() < rate * blk / SR:
                self.drop_left = int(SR * self.rng.uniform(dmin, dmax) / 1000)
            tg = depth if self.drop_left > 0 else 1.0
            self.drop_left -= blk
            m = min(blk, n - i)
            dg[i:i + m] = np.linspace(self.drop_gain, tg, m, endpoint=False); self.drop_gain = tg
        return y * dg

    def _stutter(self, mix, talk, p, k):
        """Repeat the last stutter.ms 2-3 times while talking; stops (4 ms fade) as soon as the dry voice stops."""
        ps = p["stutter.ps"] * k
        if ps <= 0 and self.st_left <= 0: return mix
        n = len(mix); out = mix.copy()
        seg = int(SR * p["stutter.ms"] / 1000); fade = int(SR * 0.004)
        i = 0
        while i < n:
            if self.st_left <= 0:
                if talk[i] and np.sqrt(np.mean(mix[i:i + 256] ** 2)) > 1e-3 and self.rng.random() < ps * min(256, n - i) / SR:
                    s = np.concatenate([self.hist, mix[:i]])[-seg:].copy()
                    env = np.ones(seg); env[:fade] = np.linspace(0, 1, fade); env[-fade:] = np.linspace(1, 0, fade)
                    self.st_seg = s * env; self.st_left = seg * int(self.rng.integers(2, 4)); self.st_pos = 0
                i += 256; continue
            m = min(self.st_left, n - i, 256)
            idx = (self.st_pos + np.arange(m)) % len(self.st_seg)
            if not talk[i]:                                  # voice ended: fade the repeat out, back to the real signal
                f = min(fade, m); r = np.ones(m); r[:f] = np.linspace(1, 0, f); r[f:] = 0
                out[i:i + m] = self.st_seg[idx] * r + mix[i:i + m] * (1 - r)
                self.st_left = 0; i += m; continue
            out[i:i + m] = self.st_seg[idx]
            self.st_pos += m; self.st_left -= m; i += m
        return out


# ---------------------------------------------------------------- audio I/O
def list_devices(wasapi_only=False):
    import sounddevice as sd
    apis = sd.query_hostapis(); res = []
    for i, d in enumerate(sd.query_devices()):
        api = apis[d["hostapi"]]["name"]
        if wasapi_only and "WASAPI" not in api: continue
        res.append(dict(index=i, name=d["name"], api=api, ins=d["max_input_channels"], outs=d["max_output_channels"]))
    return res


def find_dev(name, kind):
    import sounddevice as sd
    if name is None or name == "": return None
    if isinstance(name, int) or str(name).isdigit(): return int(name)
    devs, apis = sd.query_devices(), sd.query_hostapis()
    cands = [(i, d) for i, d in enumerate(devs) if str(name).lower() in d["name"].lower() and d[f"max_{kind}_channels"] > 0]
    if not cands: raise RuntimeError(f"No {kind} device matching '{name}'")
    wasapi = [c for c in cands if "WASAPI" in apis[c[1]["hostapi"]]["name"]]
    return (wasapi or cands)[0][0]


class AudioIO:
    """Owns the sounddevice streams. start()/stop() can be called repeatedly."""
    def __init__(self, eng):
        self.eng = eng; self.streams = []; self.running = False; self.monitor_vol = 0.7; self.info = ""; self.latency_s = 0.0

    def start(self, inp=None, out="CABLE Input", monitor=None, hops=2, latency="low"):
        import sounddevice as sd
        self.stop()
        dout = find_dev(out, "output"); din = find_dev(inp, "input")
        if dout is None: raise RuntimeError("pick an output device")
        if din is None:
            api = sd.query_devices(dout)["hostapi"]; din = sd.query_hostapis(api)["default_input_device"]
        di, do = sd.query_devices(din), sd.query_devices(dout)
        if di["hostapi"] != do["hostapi"]:
            raise RuntimeError("Mic and output are on different host APIs; pick both from Windows WASAPI.")
        cin, cout = min(2, di["max_input_channels"]), min(2, do["max_output_channels"])

        def wasapi_extra(dev):
            if "WASAPI" in sd.query_hostapis(sd.query_devices(dev)["hostapi"])["name"]:
                try: return sd.WasapiSettings(auto_convert=True)
                except TypeError: return None
            return None
        ex = wasapi_extra(din)
        mon_q = queue.Queue(maxsize=8) if monitor not in (None, "") else None
        B = self.eng.H * hops

        def cb(indata, outdata, frames, t, status):
            eng = self.eng
            if status: eng.stats["xruns"] += 1
            s = time.perf_counter()
            try:
                y = eng.process(indata.mean(axis=1))
            except Exception as e:           # never kill the audio thread
                print("engine error:", repr(e)); y = np.zeros(frames, np.float32)
            outdata[:] = np.repeat(y[:, None], cout, axis=1)
            if mon_q is not None:
                try: mon_q.put_nowait(y.copy())
                except queue.Full: pass
            eng.stats["load"] = 0.9 * eng.stats["load"] + 0.1 * (time.perf_counter() - s) / (frames / SR)

        st = sd.Stream(samplerate=SR, blocksize=B, device=(din, dout), channels=(cin, cout), dtype="float32",
                       latency=latency, callback=cb, extra_settings=(ex, ex) if ex else None)
        self.streams = [st]; mon_name = ""
        if mon_q is not None:
            dmon = find_dev(monitor, "output"); cm = min(2, sd.query_devices(dmon)["max_output_channels"])
            mon_name = sd.query_devices(dmon)["name"]

            def mcb(outdata, frames, t, status):
                try:
                    y = mon_q.get_nowait(); outdata[:] = np.repeat(y[:frames, None], cm, axis=1) * self.monitor_vol
                except queue.Empty:
                    outdata.fill(0)
            self.streams.append(sd.OutputStream(samplerate=SR, blocksize=B, device=dmon, channels=cm, dtype="float32",
                                                latency=latency, callback=mcb, extra_settings=wasapi_extra(dmon)))
        for s in self.streams: s.start()
        lat = st.latency; lat = sum(lat) if isinstance(lat, (tuple, list)) else lat
        self.latency_s = lat + (self.eng.N - self.eng.H) / SR
        self.info = f"IN: {di['name']} | OUT: {do['name']}" + (f" | MON: {mon_name}" if mon_name else "")
        self.running = True
        return self.info

    def stop(self):
        for s in self.streams:
            try: s.stop(); s.close()
            except Exception: pass
        self.streams = []; self.running = False


# ---------------------------------------------------------------- CLI
def render(eng, x, block_hops=2):
    """Offline render through the realtime path (same block size as live). Returns (y, rt_factor, worst_ms, trace)."""
    B = eng.H * block_hops; pad = (-len(x)) % B; x = np.pad(x, (0, pad + eng.N))
    out = []; trace = []; t0 = time.perf_counter(); worst = 0.0
    for i in range(0, len(x), B):
        s = time.perf_counter(); out.append(eng.process(x[i:i + B])); worst = max(worst, time.perf_counter() - s)
        st = eng.stats; trace.append((st["balance"], st["clear"], st["gate"]))
    dt = time.perf_counter() - t0
    return np.concatenate(out)[eng.N - eng.H:], dt / (len(x) / SR), worst * 1000, np.array(trace)


def run_cli(a, eng):
    io = AudioIO(eng); io.monitor_vol = a.monitor_vol
    print(io.start(a.inp, a.out, a.monitor, a.hops, a.latency))
    print("Ctrl+C to quit. Type 'c' + Enter to toggle clear mode.")
    import threading

    def console():
        while True:
            try: cmd = input().strip().lower()
            except EOFError: return
            if cmd == "c": eng.manual_clear = not eng.manual_clear; print("clear:", eng.manual_clear)
    threading.Thread(target=console, daemon=True).start()
    try:
        while True:
            time.sleep(1.0); s = eng.stats
            print(f"\r[{eng.preset_name}] cpu {s['load'] * 100:4.0f}%  xruns {s['xruns']}  bal {s['balance']:.2f}  "
                  f"clear {s['clear']:.2f}  f0med {eng.f0_med:.0f} Hz   ", end="", flush=True)
    except KeyboardInterrupt:
        pass
    io.stop(); print("\nbye")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list audio devices and exit")
    ap.add_argument("--list-presets", action="store_true")
    ap.add_argument("--write-factory", action="store_true", help="(re)write factory presets into presets\\")
    ap.add_argument("--in", dest="inp", default=None)
    ap.add_argument("--out", default="CABLE Input")
    ap.add_argument("--monitor", default=None)
    ap.add_argument("--monitor-vol", type=float, default=0.7)
    ap.add_argument("-p", "--preset", default="unknown/game twin")
    ap.add_argument("--fft", type=int, default=2048, choices=[1024, 2048])
    ap.add_argument("--hops", type=int, default=2)
    ap.add_argument("--latency", default="low")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--file"); ap.add_argument("--out-file", default="twin_test.wav")
    a = ap.parse_args()
    if a.write_factory:
        print(f"wrote {ensure_factory(force=True)} presets to {PRESET_DIR}"); sys.exit(0)
    if a.list:
        for d in list_devices(): print(f"{d['index']:3d}  in{d['ins']:2d} out{d['outs']:2d}  {d['api']:<22} {d['name']}")
        sys.exit(0)
    ensure_factory()
    ps = all_presets()
    if a.list_presets:
        for k, (_, desc) in ps.items(): print(f"{k:<36} {desc}")
        sys.exit(0)
    pid = resolve_id(a.preset, ps)
    if pid is None: raise SystemExit(f"Unknown preset '{a.preset}'. Available: {', '.join(ps)}")
    try: a.latency = float(a.latency)
    except ValueError: pass
    eng = Engine(n_fft=a.fft, seed=a.seed if a.seed is not None else (42 if a.file else None))
    eng.load(ps[pid][0], name=pid)
    if a.file:
        import soundfile as sf
        x, sr = sf.read(a.file, dtype="float32", always_2d=True); x = x.mean(1)
        if sr != SR: raise SystemExit(f"--file must be {SR} Hz (got {sr})")
        y, rtf, worst, _ = render(eng, x, a.hops)
        os.makedirs(os.path.dirname(os.path.abspath(a.out_file)), exist_ok=True)
        sf.write(a.out_file, y, SR, subtype="PCM_24")
        print(f"{a.out_file}: realtime factor {rtf:.2f}, worst block {worst:.1f} ms of {a.hops * eng.H / SR * 1000:.1f} ms")
    else:
        run_cli(a, eng)
