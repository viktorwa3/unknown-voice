"""
unknown_fx.py — offline FX chain for "Unknown"-style monster voice (Track A, FX stage).
Deps:  pip install pedalboard soundfile numpy   (+ ffmpeg on PATH for non-WAV input)
Usage: python unknown_fx.py input.(wav|m4a|mp3) [-o outdir] [-p preset ...|all] [--seed 42]
Out:   <outdir>/<stem>__<preset>.wav, 48 kHz mono, peak-normalized to -1 dBFS.
Generic monster-mimic recipe, NOT a reverse-engineered Behaviour preset.
"""
import argparse, subprocess, tempfile, os
import numpy as np, soundfile as sf
from pedalboard import (Pedalboard, PitchShift, LowShelfFilter, HighpassFilter, LowpassFilter,
                        Distortion, Chorus, Reverb, Bitcrush, Compressor, Gain, NoiseGate)

SR = 48000

PRESETS = {
    # baseline from MEMORY.md
    "base":    dict(low_st=-5, low_drive=12, high_st=7, high_mix=0.35, stutter_n=6, seg_ms=60,
                    rrev_mix=0.4, bits=10, ring_hz=0, ring_mix=0.0, dry_mix=0.0),
    # heavier, darker, less chipmunk layer
    "deep":    dict(low_st=-7, low_drive=16, high_st=5, high_mix=0.2, stutter_n=4, seg_ms=80,
                    rrev_mix=0.35, bits=9, ring_hz=35, ring_mix=0.25, dry_mix=0.0),
    # "mimic" — more of the human voice left, creepier because it's almost normal
    "mimic":   dict(low_st=-3, low_drive=8, high_st=12, high_mix=0.25, stutter_n=8, seg_ms=45,
                    rrev_mix=0.5, bits=12, ring_hz=0, ring_mix=0.0, dry_mix=0.45),
    # full chaos: ring mod + heavy glitch
    "glitch":  dict(low_st=-6, low_drive=14, high_st=7, high_mix=0.4, stutter_n=14, seg_ms=40,
                    rrev_mix=0.45, bits=8, ring_hz=70, ring_mix=0.35, dry_mix=0.0),
}

def load(path):
    if not path.lower().endswith(".wav"):
        tmp = tempfile.mktemp(suffix=".wav")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", str(SR), tmp], check=True)
        path = tmp
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != SR:
        tmp = tempfile.mktemp(suffix=".wav")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", str(SR), tmp], check=True)
        x, _ = sf.read(tmp, dtype="float32")
    return x

def run(board, x):
    return board(x[None, :], SR)[0]

def fit(y, n):
    return y[:n] if len(y) >= n else np.pad(y, (0, n - len(y)))

def stutter(x, n, seg_ms, rng):
    """Random short-segment repeats (2–4x) at n random onsets, with 3 ms fades to avoid clicks."""
    if n <= 0: return x
    seg = int(SR * seg_ms / 1000); fade = int(SR * 0.003)
    env = np.ones(seg, np.float32); env[:fade] = np.linspace(0, 1, fade); env[-fade:] = np.linspace(1, 0, fade)
    out = x.copy()
    # only stutter where there's actual signal
    rms = np.sqrt(np.convolve(x**2, np.ones(seg)/seg, mode="same"))
    cand = np.where(rms > 0.25 * rms.max())[0]
    if len(cand) == 0: return x
    for pos in sorted(rng.choice(cand, size=min(n, len(cand)), replace=False)):
        chunk = x[pos:pos+seg]
        if len(chunk) < seg: continue
        reps = rng.integers(2, 5)
        for r in range(reps):
            s = pos + r*seg
            if s + seg > len(out): break
            out[s:s+seg] = out[s:s+seg]*(1-env) + chunk*env
    return out

def reverse_reverb(x):
    tail = np.zeros(int(SR*1.5), np.float32)
    r = run(Pedalboard([Reverb(room_size=0.9, damping=0.3, wet_level=1.0, dry_level=0.0, width=1.0)]),
            np.concatenate([tail, x])[::-1].copy())
    return r[::-1][len(tail):].copy()  # swell arrives BEFORE each word

def ring_mod(x, hz):
    t = np.arange(len(x)) / SR
    return x * np.sin(2*np.pi*hz*t).astype(np.float32)

def process(x, p, rng):
    n = len(x)
    pre = Pedalboard([HighpassFilter(70), NoiseGate(threshold_db=-50, ratio=4, release_ms=120),
                      Compressor(threshold_db=-22, ratio=4, attack_ms=5, release_ms=80)])
    x = run(pre, x)

    low = fit(run(Pedalboard([PitchShift(p["low_st"]), LowShelfFilter(200, gain_db=6),
                              Distortion(drive_db=p["low_drive"]), LowpassFilter(6000)]), x), n)
    high = fit(run(Pedalboard([PitchShift(p["high_st"]), HighpassFilter(1500),
                               Chorus(rate_hz=3, depth=0.8, mix=0.7)]), x), n)
    mix = low + p["high_mix"]*high + p["dry_mix"]*x
    if p["ring_mix"] > 0:
        mix = (1-p["ring_mix"])*mix + p["ring_mix"]*ring_mod(mix, p["ring_hz"])

    mix = stutter(mix, p["stutter_n"], p["seg_ms"], rng)
    mix = mix + p["rrev_mix"]*reverse_reverb(mix)
    mix = run(Pedalboard([Bitcrush(bit_depth=p["bits"]),
                          Compressor(threshold_db=-14, ratio=3, attack_ms=3, release_ms=60)]), mix)
    peak = np.max(np.abs(mix)) or 1.0
    return (mix / peak * 10**(-1/20)).astype(np.float32)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input"); ap.add_argument("-o", "--outdir", default="out")
    ap.add_argument("-p", "--preset", nargs="+", default=["all"])
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    x = load(a.input); stem = os.path.splitext(os.path.basename(a.input))[0]
    names = list(PRESETS) if "all" in a.preset else a.preset
    for name in names:
        y = process(x, PRESETS[name], np.random.default_rng(a.seed))
        dst = os.path.join(a.outdir, f"{stem}__{name}.wav"); sf.write(dst, y, SR, subtype="PCM_24"); print(dst)
