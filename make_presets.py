"""
make_presets.py — generates the factory preset library for unknown_live.py into presets\\<folder>\\<name>.json.
Each file holds only the overrides vs DEFAULTS + "_meta": {"desc": ...} (missing keys = engine defaults).
Deps:  unknown_live.py next to it (for validation of keys/ranges).
Usage: python make_presets.py                       # writes/overwrites the factory files (your own presets are
                                                     # untouched unless they have the same folder/name)
       python make_presets.py --dry-run             # just validate
       python make_presets.py --calibrate take.wav  # also set master.out_db per preset so all speak at TARGET_DB
Levels in the shipped files were calibrated on raw\\take01_normal.wav (48 kHz) to TARGET_DB.
"""
import json, os, sys
import unknown_live as L

V = {}   # "folder/name" -> (desc, overrides)

def P(pid, desc, **kw):
    V[pid] = (desc, {k.replace("__", "."): v for k, v in kw.items()})

# ------------------------------------------------------------------ unknown
P("unknown/morph classic", "The original: beast, robotic female and whisper drifting and jumping.",
  beast__weight=1.0, fem__weight=1.0, whisper__weight=0.5, human__weight=0.3, mix__rate_hz=1.6, mix__switch=0.6,
  mix__stutter_ps=0.35, mix__stutter_ms=55)
P("unknown/glide classic", "One voice melting between monster and female.",
  glide__weight=1.5, whisper__weight=0.3, human__weight=0.15, mix__rate_hz=0.7, mix__switch=0.3, mix__stutter_ps=0.22, mix__stutter_ms=50)
P("unknown/radio classic", "Broken transmission, all voices jumping.",
  beast__weight=0.8, fem__weight=0.8, human__weight=0.8, robot__weight=0.5, mix__rate_hz=1.5, mix__switch=0.8,
  mix__stutter_ps=0.45, mix__stutter_ms=45, mix__radio=1.0)
P("unknown/whisper stalker", "Mostly breath, a beast underneath, words sometimes run backwards.",
  whisper__weight=1.4, beast__weight=0.6, glide__weight=0.3, human__weight=0.1, mix__rate_hz=0.8, mix__switch=0.4,
  mix__stutter_ps=0.15, glitch__reverse_ps=0.3, glitch__reverse_ms=150, eq__high_db=2, space__reverb_mix=0.12, space__room=0.6)
P("unknown/mimic survivor", "Sounds like you, then suddenly isn't. Hard jumps into the other voices.",
  human__weight=1.5, fem__weight=0.5, beast__weight=0.5, glide__weight=0.3, mix__rate_hz=1.0, mix__switch=0.95,
  mix__stutter_ps=0.4, mix__stutter_ms=45, mod__jitter_st=0.3)
P("unknown/hollow choir", "Female, child and glide voices blended into a soft hollow choir.",
  fem__weight=0.9, child__weight=0.8, glide__weight=0.6, whisper__weight=0.3, mix__rate_hz=0.5, mix__switch=0.1,
  mix__stutter_ps=0.1, mod__vibrato_st=0.25, mod__vibrato_hz=4.5, space__reverb_mix=0.25, space__room=0.8, fem__chorus_mix=0.5)
P("unknown/broken tape", "Chewed cassette: crushed, downsampled, chunks played backwards.",
  beast__weight=0.8, fem__weight=0.8, human__weight=0.6, mix__rate_hz=1.2, mix__switch=0.7, mix__stutter_ps=0.6, mix__stutter_ms=70,
  glitch__downsample=3, glitch__crush_bits=9, glitch__reverse_ps=0.5, glitch__reverse_ms=180, mod__tremolo=0.2, mod__tremolo_hz=8, eq__high_db=-4)
P("unknown/static entity", "Something talking through interference, freezing on vowels.",
  beast__weight=1.0, fem__weight=0.8, robot__weight=0.6, human__weight=0.3, mix__radio=0.7, mix__rate_hz=1.4, mix__switch=0.7,
  glitch__freeze_ps=0.4, glitch__freeze_ms=400, mod__jitter_st=0.5, mod__jitter_hz=6, radio__drop_ps=3)
P("unknown/deep hunger", "Low and heavy: demon plus beast, slow drift.",
  demon__weight=1.2, beast__weight=0.8, whisper__weight=0.4, mix__rate_hz=0.6, mix__switch=0.5, mix__stutter_ps=0.2,
  eq__low_db=3, demon__drive_db=20)
P("unknown/laughing thing", "High, wobbly, unstable. Child and female voices giggling through the words.",
  child__weight=1.0, fem__weight=0.8, glide__weight=0.4, human__weight=0.2, mix__rate_hz=2.0, mix__switch=0.6,
  mix__stutter_ps=0.5, mix__stutter_ms=40, mod__vibrato_st=0.6, mod__vibrato_hz=7, mod__jitter_st=1.2, mod__jitter_hz=5)
P("unknown/feral glitch", "All voices, switching 5 times a second, heavy stutter and reverse.",
  beast__weight=1.0, fem__weight=1.0, whisper__weight=0.6, human__weight=0.6, robot__weight=0.6, glide__weight=0.6, demon__weight=0.4,
  mix__rate_hz=5.0, mix__switch=1.0, mix__stutter_ps=2.0, mix__stutter_ms=35, glitch__reverse_ps=0.8, glitch__reverse_ms=80)
P("unknown/distant echo", "Whispering from down the corridor.",
  whisper__weight=1.0, glide__weight=0.6, fem__weight=0.4, mix__rate_hz=0.6, mix__switch=0.4, mix__stutter_ps=0.1,
  space__echo_mix=0.35, space__echo_ms=380, space__echo_fb=0.45, space__reverb_mix=0.3, space__room=0.85, eq__high_db=-3)
P("unknown/lullaby", "Child and robot humming on one note, soft vibrato.",
  child__weight=1.0, robot__weight=0.7, fem__weight=0.4, robot__note_hz=262, mix__rate_hz=0.5, mix__switch=0.2, mix__stutter_ps=0.05,
  mod__vibrato_st=0.35, mod__vibrato_hz=5.5, space__reverb_mix=0.2, mod__tremolo=0.15, mod__tremolo_hz=4)
P("unknown/fracture", "Formants and pitch cracking constantly, one voice falling apart.",
  glide__weight=1.2, beast__weight=0.6, fem__weight=0.6, mix__rate_hz=2.5, mix__switch=0.7, mix__stutter_ps=0.5,
  mod__formant_lfo=0.35, mod__formant_lfo_hz=1.5, mod__jitter_st=2.0, mod__jitter_hz=8)
P("unknown/wet throat", "Gurgling rasp: breathy beast with a swirling phaser.",
  beast__weight=1.3, demon__weight=0.5, whisper__weight=0.4, beast__breath=0.6, beast__drive_db=16, mix__rate_hz=0.9, mix__switch=0.5,
  mod__phaser_mix=0.5, mod__phaser_rate=0.3, mod__phaser_fb=0.6, mix__stutter_ps=0.2)
P("unknown/cold machine woman", "The robotic female side, dominant and quantized.",
  robot__weight=1.0, fem__weight=1.2, human__weight=0.1, robot__ring_hz=50, robot__ring_mix=0.5, mix__rate_hz=1.2, mix__switch=0.5,
  glitch__crush_bits=12, eq__high_db=2, mix__stutter_ps=0.25)
P("unknown/shifting skin", "Glide on steroids: fast slides that snap to the extremes.",
  glide__weight=1.5, whisper__weight=0.2, glide__rate_hz=2.5, glide__jump=0.8, mod__formant_lfo=0.15, mod__formant_lfo_hz=0.7,
  mix__rate_hz=0.8, mix__switch=0.2, mix__stutter_ps=0.25)
P("unknown/hunting whisper", "Breath, backwards syllables and sudden freezes. Silence between words.",
  whisper__weight=1.2, beast__weight=0.4, demon__weight=0.3, glitch__reverse_ps=1.0, glitch__reverse_ms=220, mix__stutter_ps=0.3,
  glitch__freeze_ps=0.3, glitch__freeze_ms=250, gate__floor_db=-70, mix__rate_hz=0.9, mix__switch=0.5)
P("unknown/frozen scream", "Female/child voices that get stuck on a vowel.",
  fem__weight=1.0, child__weight=0.6, glide__weight=0.5, beast__weight=0.4, glitch__freeze_ps=1.0, glitch__freeze_ms=700,
  mix__stutter_ps=0.3, space__reverb_mix=0.2, mix__rate_hz=1.3, mix__switch=0.6)
P("unknown/close and quiet", "Subtle, intimate version: less FX, more breath.",
  beast__weight=0.6, fem__weight=0.6, whisper__weight=0.8, human__weight=0.5, mix__rate_hz=1.0, mix__switch=0.5,
  mix__stutter_ps=0.15, eq__low_db=-2, gate__hold_ms=40)
P("unknown/many mouths", "Every voice at once, slowly breathing in and out of the mix.",
  beast__weight=0.7, fem__weight=0.7, glide__weight=0.7, robot__weight=0.5, whisper__weight=0.7, demon__weight=0.5, child__weight=0.6,
  human__weight=0.5, mix__rate_hz=0.3, mix__switch=0.0, mix__stutter_ps=0.1, mod__vibrato_st=0.15, mod__vibrato_hz=3.5)
P("unknown/the hunt begins", "Aggressive: demon and beast over a radio, reverse and freezes.",
  beast__weight=1.0, demon__weight=0.6, fem__weight=0.6, mix__radio=0.35, mix__rate_hz=1.8, mix__switch=0.8,
  mix__stutter_ps=0.6, glitch__reverse_ps=0.4, glitch__freeze_ps=0.3, mod__jitter_st=0.6)
P("unknown/sick lullaby radio", "A child singing through a dying radio.",
  child__weight=0.9, robot__weight=0.6, human__weight=0.2, mix__radio=0.8, radio__lpf=2500, radio__drop_ps=3,
  robot__note_hz=247, mod__vibrato_st=0.3, mix__rate_hz=0.8, mix__switch=0.4)

# ------------------------------------------------------------------ monsters
P("monsters/ogre", "Big dumb giant: low, wide, a bit of grit.",
  beast__weight=1.4, human__weight=0.2, beast__target_hz=60, beast__formant=0.72, beast__drive_db=12, beast__breath=0.15,
  mix__switch=0.0, mix__stutter_ps=0, eq__low_db=3)
P("monsters/demon lord", "Hell's middle management. Very low, huge room.",
  demon__weight=1.3, beast__weight=0.6, human__weight=0.1, demon__target_hz=38, mix__rate_hz=0.5, mix__switch=0.2,
  mix__stutter_ps=0, space__reverb_mix=0.25, space__room=0.7, eq__low_db=2)
P("monsters/zombie", "Rotten lungs, slurred and unstable.",
  beast__weight=1.0, whisper__weight=0.7, human__weight=0.3, beast__breath=0.6, beast__formant=0.85, beast__target_hz=70,
  mod__jitter_st=0.8, mod__jitter_hz=2, mix__stutter_ps=0.4, mix__stutter_ms=90, eq__high_db=-4, mix__switch=0.4)
P("monsters/ghoul", "Hissing scavenger.",
  whisper__weight=1.0, demon__weight=0.5, child__weight=0.3, mod__jitter_st=0.5, mod__phaser_mix=0.4, glitch__reverse_ps=0.3,
  mix__rate_hz=1.2, mix__switch=0.6, mix__stutter_ps=0.2)
P("monsters/troll", "Slow, heavy, a little wobbly.",
  beast__weight=1.3, human__weight=0.3, beast__target_hz=65, beast__formant=0.75, beast__drive_db=6, mix__switch=0.3,
  mix__rate_hz=0.5, mix__stutter_ps=0, mod__tremolo=0.15, mod__tremolo_hz=3)
P("monsters/orc grunt", "Shouty warrior, lots of distortion.",
  beast__weight=1.2, demon__weight=0.3, human__weight=0.2, beast__target_hz=75, beast__formant=0.8, beast__drive_db=18,
  mix__switch=0.3, mix__stutter_ps=0, master__comp_ratio=5, eq__mid_db=3, eq__mid_hz=900)
P("monsters/goblin", "Small, nasal, twitchy.",
  child__weight=1.0, human__weight=0.3, child__target_hz=280, child__formant=1.35, mod__jitter_st=0.6, mod__jitter_hz=6,
  eq__mid_db=4, eq__mid_hz=1800, mix__switch=0.2, mix__stutter_ps=0.1)
P("monsters/werewolf", "Half man, half snarl.",
  demon__weight=0.9, beast__weight=1.0, whisper__weight=0.5, human__weight=0.3, mod__jitter_st=1.0, mod__jitter_hz=4,
  beast__drive_db=16, demon__drive_db=24, mix__rate_hz=1.5, mix__switch=0.6, mix__stutter_ps=0.2)
P("monsters/dragon", "Ancient, enormous, echoing in a cave.",
  demon__weight=1.2, beast__weight=0.6, demon__target_hz=35, demon__formant=0.55, space__reverb_mix=0.35, space__room=0.9,
  eq__low_db=4, mix__switch=0.2, mix__rate_hz=0.4, mix__stutter_ps=0)

# ------------------------------------------------------------------ robots
P("robots/classic robot", "Monotone metal man.",
  robot__weight=1.4, robot__note_hz=110, robot__formant=1.0, robot__ring_hz=50, robot__ring_mix=0.6, robot__comb_ms=5,
  robot__comb_fb=0.5, mix__switch=0.0, mix__stutter_ps=0)
P("robots/calm ai", "Soft synthetic assistant voice.",
  fem__weight=1.0, human__weight=0.2, fem__target_hz=200, fem__snap=False, fem__formant=1.15, fem__comb_mix=0.15, fem__bits=16,
  fem__chorus_mix=0.15, mix__switch=0.0, mix__stutter_ps=0, space__reverb_mix=0.08, eq__high_db=2)
P("robots/ring-mod tyrant", "Harsh buzzing ring-modulated overlord.",
  robot__weight=1.4, human__weight=0.2, robot__note_hz=130, robot__ring_hz=30, robot__ring_mix=0.9, robot__comb_mix=0.7,
  robot__bits=8, eq__mid_db=5, eq__mid_hz=1500, mix__switch=0.0, mix__stutter_ps=0)
P("robots/broken android", "Malfunctioning: crushed, stuttering, freezing.",
  robot__weight=1.0, fem__weight=0.6, human__weight=0.4, mix__switch=1.0, mix__rate_hz=3.0, mix__stutter_ps=0.8, mix__stutter_ms=40,
  glitch__crush_bits=8, glitch__downsample=2, glitch__freeze_ps=0.3)
P("robots/cyborg soldier", "Half human, compressed and metallic.",
  beast__weight=0.8, robot__weight=0.6, human__weight=0.5, beast__target_hz=80, beast__formant=0.9, robot__note_hz=98,
  master__comp_thr=-26, master__comp_ratio=5, eq__mid_db=3, eq__mid_hz=1200, glitch__crush_bits=11, mix__switch=0.3, mix__stutter_ps=0)
P("robots/vocoder bot", "Singing machine: robot note plus female layer.",
  robot__weight=1.2, fem__weight=0.5, whisper__weight=0.3, robot__note_hz=175, mix__switch=0.1, mix__rate_hz=0.5,
  mix__stutter_ps=0, fem__chorus_mix=0.5)
P("robots/old computer", "8-bit terminal from 1983.",
  robot__weight=1.0, robot__note_hz=147, glitch__downsample=6, glitch__crush_bits=6, eq__high_db=-6, mix__switch=0.0, mix__stutter_ps=0)

# ------------------------------------------------------------------ horror
P("horror/possessed", "Your voice fighting something inside it.",
  human__weight=0.8, demon__weight=0.9, whisper__weight=0.4, mix__switch=0.9, mix__rate_hz=1.8, mod__jitter_st=0.5,
  mix__stutter_ps=0.4, glitch__reverse_ps=0.3)
P("horror/ghost", "Breathy, far away, fading in and out.",
  whisper__weight=1.2, fem__weight=0.5, space__reverb_mix=0.45, space__room=0.9, space__damping=0.3, space__echo_mix=0.2,
  space__echo_ms=420, space__echo_fb=0.5, eq__low_db=-6, mod__tremolo=0.2, mod__tremolo_hz=2, mix__switch=0.3, mix__stutter_ps=0)
P("horror/witch", "Old crone cackle.",
  child__weight=0.5, fem__weight=0.6, whisper__weight=0.5, human__weight=0.2, child__target_hz=300, child__formant=1.25,
  fem__target_hz=260, fem__snap=False, fem__formant=1.1, fem__comb_mix=0.1, mod__vibrato_st=0.3, mod__vibrato_hz=6,
  mod__jitter_st=0.6, mix__switch=0.4, mix__stutter_ps=0.1)
P("horror/creepy child", "A small voice that shouldn't be there.",
  child__weight=1.3, whisper__weight=0.3, child__target_hz=360, child__formant=1.5, mod__vibrato_st=0.2,
  space__reverb_mix=0.2, space__room=0.6, mix__switch=0.2, mix__stutter_ps=0.05)
P("horror/shadow whisperer", "Low whisper right behind your ear.",
  whisper__weight=1.3, demon__weight=0.4, whisper__formant=0.8, space__echo_mix=0.3, space__echo_ms=300, space__echo_fb=0.35,
  glitch__reverse_ps=0.3, mix__switch=0.4, mix__stutter_ps=0)
P("horror/backwards speaker", "Most syllables come out reversed.",
  human__weight=0.8, whisper__weight=0.5, glide__weight=0.3, glitch__reverse_ps=2.5, glitch__reverse_ms=260,
  mix__stutter_ps=0.2, space__reverb_mix=0.15, mix__switch=0.5)
P("horror/the thing in the vents", "Metallic duct echo, whispers and clicks.",
  whisper__weight=1.0, child__weight=0.4, beast__weight=0.4, space__echo_mix=0.4, space__echo_ms=90, space__echo_fb=0.6,
  eq__mid_db=5, eq__mid_hz=2500, space__reverb_mix=0.2, space__room=0.3, mix__switch=0.5, mix__stutter_ps=0.3)

# ------------------------------------------------------------------ comms
P("comms/pilot radio", "Clean aviation radio.",
  human__weight=1.0, mix__radio=1.0, radio__hpf=400, radio__lpf=3200, radio__drive=8, radio__bits=12, radio__drop_ps=0.3,
  mix__stutter_ps=0)
P("comms/walkie talkie", "Cheap handheld radio, crunchy and dropping.",
  human__weight=1.0, mix__radio=1.0, radio__hpf=600, radio__lpf=2600, radio__drive=16, radio__bits=7, radio__drop_ps=1.2,
  radio__drop_depth=0.9, mix__stutter_ps=0)
P("comms/telephone", "Landline call.",
  human__weight=1.0, mix__radio=1.0, radio__hpf=300, radio__lpf=3400, radio__drive=4, radio__bits=16, radio__drop_ps=0, mix__stutter_ps=0)
P("comms/megaphone", "Loud, horn-y, slightly echoing.",
  human__weight=1.0, mix__radio=1.0, radio__hpf=700, radio__lpf=4000, radio__drive=22, radio__bits=16, radio__drop_ps=0,
  eq__mid_db=4, eq__mid_hz=1500, space__echo_mix=0.15, space__echo_ms=120, mix__stutter_ps=0)
P("comms/space helmet", "Talking inside a helmet on comms.",
  human__weight=1.0, mix__radio=0.6, radio__hpf=250, radio__lpf=5000, radio__drive=3, radio__bits=14, radio__drop_ps=0,
  space__reverb_mix=0.15, space__room=0.15, space__damping=0.8, space__echo_mix=0.15, space__echo_ms=25, space__echo_fb=0.5, mix__stutter_ps=0)
P("comms/dying transmission", "Last message from a doomed ship.",
  human__weight=0.7, whisper__weight=0.4, mix__radio=1.0, radio__drop_ps=5, radio__drop_depth=1.0, radio__drop_max_ms=250,
  glitch__freeze_ps=0.4, glitch__freeze_ms=300, glitch__crush_bits=6, mix__switch=0.5, mix__stutter_ps=0.3)

# ------------------------------------------------------------------ fun
P("fun/chipmunk", "Classic sped-up squeak.",
  child__weight=1.4, child__target_hz=450, child__formant=1.6, child__chorus_mix=0.0, mix__switch=0.0, mix__stutter_ps=0)
P("fun/giant", "Huge and slow, fee-fi-fo.",
  beast__weight=1.3, beast__target_hz=45, beast__formant=0.6, beast__drive_db=3, beast__shelf_db=4, beast__breath=0.1,
  mix__switch=0.0, mix__stutter_ps=0, space__reverb_mix=0.2, space__room=0.8)
P("fun/helium", "Balloon voice.",
  fem__weight=1.3, fem__target_hz=300, fem__snap=False, fem__formant=1.6, fem__comb_mix=0.0, fem__bits=16, fem__chorus_mix=0.1,
  mix__switch=0.0, mix__stutter_ps=0)
P("fun/slow-mo", "Slowed-down movie voice.",
  beast__weight=1.0, beast__target_hz=60, beast__formant=0.7, beast__drive_db=0, beast__breath=0.1, beast__lpf_hz=9000,
  mix__switch=0.0, mix__stutter_ps=0, eq__high_db=-3)
P("fun/alien", "Wobbly extraterrestrial.",
  robot__weight=0.7, child__weight=0.6, glide__weight=0.5, robot__note_hz=330, robot__ring_hz=150, mod__vibrato_st=1.0,
  mod__vibrato_hz=8, mod__phaser_mix=0.6, mod__phaser_rate=1.5, mix__switch=0.4, mix__rate_hz=1.0, mix__stutter_ps=0.1)
P("fun/cave echo", "Hello... hello... hello.",
  human__weight=1.0, space__echo_mix=0.35, space__echo_ms=220, space__echo_fb=0.5, space__reverb_mix=0.4, space__room=0.9, mix__stutter_ps=0)
P("fun/stadium announcer", "Big booming arena voice.",
  human__weight=1.0, beast__weight=0.3, beast__target_hz=75, beast__formant=0.9, beast__drive_db=2, beast__breath=0.05,
  space__echo_mix=0.25, space__echo_ms=450, space__echo_fb=0.35, space__reverb_mix=0.3, space__room=0.95,
  eq__low_db=2, eq__high_db=2, mix__switch=0.0, mix__stutter_ps=0)
P("fun/autotune pop", "Hard-tuned pop vocal.",
  fem__weight=1.2, human__weight=0.2, fem__target_hz=190, fem__snap=True, fem__formant=1.05, fem__comb_mix=0.0, fem__bits=16,
  fem__chorus_mix=0.35, mix__switch=0.0, mix__stutter_ps=0, space__reverb_mix=0.15)
P("fun/drunk", "Wobbling pitch and slurred formants.",
  human__weight=1.0, glide__weight=0.4, glide__low_hz=70, glide__high_hz=140, mod__jitter_st=1.5, mod__jitter_hz=1.2,
  mod__vibrato_st=0.3, mod__vibrato_hz=0.8, mod__formant_lfo=0.1, mix__switch=0.2, mix__stutter_ps=0)

# ------------------------------------------------------------------ utility
P("utility/subtle deeper", "A bit deeper and bigger, still natural.",
  beast__weight=1.0, beast__target_hz=70, beast__formant=0.9, beast__drive_db=0, beast__breath=0.05, beast__shelf_db=2,
  beast__hpf_hz=40, beast__lpf_hz=12000, mix__switch=0.0, mix__stutter_ps=0)
P("utility/subtle brighter", "A few semitones up, slightly smaller head.",
  fem__weight=1.0, fem__target_hz=110, fem__snap=False, fem__formant=1.08, fem__comb_mix=0.0, fem__bits=16, fem__chorus_mix=0.0,
  fem__presence_db=2, fem__hpf_hz=60, mix__switch=0.0, mix__stutter_ps=0)
P("utility/broadcast voice", "Radio-host compression and EQ.",
  human__weight=1.0, master__comp_thr=-26, master__comp_ratio=4, master__makeup_db=9, eq__low_db=3, eq__mid_db=-2, eq__mid_hz=400,
  eq__high_db=3, mix__stutter_ps=0)
P("utility/noisy room", "Clean voice with an aggressive gate for noisy rooms.",
  human__weight=1.0, gate__thr_db=-42, gate__floor_db=-80, gate__ratio=3, mix__stutter_ps=0)


TARGET_DB = -20.0   # speech level (mean of the loudest third of 100 ms frames), dBFS

def speech_level(params, x, seed=5):
    import numpy as np
    e = L.Engine(seed=seed); e.load(params); B = e.H * 2
    y = np.concatenate([e.process(x[i:i + B]) for i in range(0, len(x) - B, B)])
    fr = 4800; lv = np.array([10 * np.log10(np.mean(y[i:i + fr] ** 2) + 1e-12) for i in range(0, len(y) - fr, fr)])
    return float(np.mean(np.sort(lv)[-len(lv) // 3:]))

def calibrate(presets, wav):
    import numpy as np, soundfile as sf
    x, sr = sf.read(wav, dtype="float32", always_2d=True); x = x.mean(1)[: sr * 8]
    if sr != L.SR: raise SystemExit("calibration wav must be 48 kHz")
    for pid, data in presets.items():
        ov = {k: v for k, v in data.items() if k != "_meta"}
        for _ in range(2):                                   # 2 passes: comp/soft-knee make it slightly non-linear
            full = L._preset(); full.update(ov)
            lvl = speech_level(full, x)
            ov["master.out_db"] = float(np.clip(round(ov.get("master.out_db", 0.0) + (TARGET_DB - lvl), 1), -24, 12))
        data.update(ov); print(f"  {pid:<34} out_db {ov['master.out_db']:+5.1f}")

CAL = {  # master.out_db per preset, from --calibrate raw\\take01_normal.wav (TARGET_DB); applied by build()
    'comms/dying transmission': -4.8,
    'comms/megaphone': -11.4,
    'comms/pilot radio': -4.7,
    'comms/space helmet': -0.0,
    'comms/telephone': -2.2,
    'comms/walkie talkie': -8.2,
    'fun/alien': 5.4,
    'fun/autotune pop': 0.9,
    'fun/cave echo': 1.9,
    'fun/chipmunk': -2.0,
    'fun/drunk': 0.8,
    'fun/giant': -0.6,
    'fun/helium': -2.9,
    'fun/slow-mo': -0.4,
    'fun/stadium announcer': 4.1,
    'horror/backwards speaker': 5.8,
    'horror/creepy child': 1.5,
    'horror/ghost': 5.3,
    'horror/possessed': 1.4,
    'horror/shadow whisperer': 2.7,
    'horror/the thing in the vents': 3.5,
    'horror/witch': 3.2,
    'monsters/demon lord': 3.4,
    'monsters/dragon': 1.2,
    'monsters/ghoul': 5.1,
    'monsters/goblin': -0.5,
    'monsters/ogre': 0.3,
    'monsters/orc grunt': 0.4,
    'monsters/troll': 1.6,
    'monsters/werewolf': 3.1,
    'monsters/zombie': 3.4,
    'robots/broken android': -0.2,
    'robots/calm ai': 0.0,
    'robots/classic robot': -2.2,
    'robots/cyborg soldier': 2.7,
    'robots/old computer': -1.7,
    'robots/ring-mod tyrant': -1.5,
    'robots/vocoder bot': 2.3,
    'unknown/broken tape': 3.5,
    'unknown/close and quiet': 3.3,
    'unknown/cold machine woman': 1.7,
    'unknown/deep hunger': 1.9,
    'unknown/distant echo': 6.1,
    'unknown/feral glitch': 3.2,
    'unknown/fracture': 1.1,
    'unknown/frozen scream': 4.9,
    'unknown/glide classic': 1.9,
    'unknown/hollow choir': 4.6,
    'unknown/hunting whisper': 2.9,
    'unknown/laughing thing': 3.1,
    'unknown/lullaby': 4.4,
    'unknown/many mouths': 6.6,
    'unknown/mimic survivor': 1.7,
    'unknown/morph classic': 3.6,
    'unknown/radio classic': -5.8,
    'unknown/shifting skin': 0.7,
    'unknown/sick lullaby radio': -4.4,
    'unknown/static entity': -4.3,
    'unknown/the hunt begins': -2.2,
    'unknown/wet throat': 4.1,
    'unknown/whisper stalker': 4.0,
    'utility/broadcast voice': -2.3,
    'utility/noisy room': -1.6,
    'utility/subtle brighter': -3.0,
    'utility/subtle deeper': -0.8,
}

def build():
    out = {}
    for pid, (desc, ov) in V.items():
        for k, v in ov.items():
            s = L.SPEC_BY_KEY.get(k)
            if s is None: raise SystemExit(f"{pid}: unknown key {k}")
            if s["kind"] != "b" and not (s["lo"] <= float(v) <= s["hi"]): raise SystemExit(f"{pid}: {k}={v} out of range")
        full = L._preset(); full.update(ov)                                    # default stutter comes from DEFAULTS
        if not any(full[f"{v}.weight"] > 0 for v in L.VOICES): raise SystemExit(f"{pid}: no voice")
        data = dict(ov)
        if pid in CAL and "master.out_db" not in data: data["master.out_db"] = CAL[pid]
        data["_meta"] = {"desc": desc}
        out[pid] = data
    return out


if __name__ == "__main__":
    presets = build()
    print(f"{len(presets)} presets OK")
    if "--dry-run" in sys.argv: sys.exit(0)
    if "--calibrate" in sys.argv: calibrate(presets, sys.argv[sys.argv.index("--calibrate") + 1])
    for pid, data in presets.items():
        path = os.path.join(L.PRESET_DIR, *pid.split("/")) + ".json"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh: json.dump(data, fh, indent=1, sort_keys=True)
    print("written to", L.PRESET_DIR)
