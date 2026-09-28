"""
unknown_fx.py v2.5 — offline FX chain for an "Unknown"-style shifting monster voice (Track A, FX stage).

Idea: denoise -> WORLD vocoder analysis (pitch / spectral envelope / aperiodicity separated) ->
render several "voices" with independent pitch and formant control -> crossfade between them
over time with random, smoothly moving weights ("shimmering") -> glitch + reverse reverb -> limiter ->
activity gate (silence between phrases).

Voices (pitch shifts ADAPTIVE to the take's median f0):
  beast  : median f0 -> ~55 Hz (ratio clamped 0.45..0.75), formants x0.80, extra breath/rasp, light drive
  fem    : median f0 -> ~220 Hz (clamped +7..+19 st), snapped to semitones, formants x1.22, buzzy comb
  glide  : ONE voice sliding continuously beast <-> fem (pitch + formants + breath move together)
  robot  : flat monotone note in female range, ring mod 70 Hz, comb, 10-bit
  whisper: fully unvoiced (aperiodic) version, formants x0.92
  human  : denoised original (the "mimic" part)
Presets (3): morph glide radio  (see PRESETS; others removed 2026-09-24 after listening)

Deps:  pip install pedalboard soundfile numpy pyworld noisereduce "setuptools<81"   (+ ffmpeg for non-WAV input)
       (pyworld 0.3.5 imports pkg_resources -> needs setuptools<81 in a Python 3.13 venv)
Usage: python unknown_fx.py raw\\take01_normal.wav [-o raw\\out] [-p morph glide ... | all] [--seed 42]
       [--layers]                    each voice separately into <outdir>\\layers\\
       [--fem-hz 220] [--beast-hz 55] pitch targets
       [--bg-db -60]                 level between phrases (reverb tails/hiss); -60 near silent, 0 = gate off
       [--gate-thr -28]              expander threshold, dB under the loudest speech; quiet word onsets/tails
                                     chopped -> lower it (-35, -40); background still audible -> raise it (-22)
       [--gate-ratio 2]              dB of cut per dB under threshold (1 = gentle, 4 = hard)
       [--rrev 0.1]                  reverse-reverb amount multiplier: 1 = full, 0 = off (x preset rrev_mix)
       [--gate-pre 150] [--gate-hold 60]  ms kept open before / after the voice (look-ahead / hold)
Tips:  keep ~1 s of silence at the start of the take: it is used as the noise profile.
Generic monster-mimic recipe, NOT a reverse-engineered Behaviour preset.
"""
import argparse, os, subprocess, tempfile
import numpy as np, soundfile as sf, pyworld as pw, noisereduce as nr
from pedalboard import (Pedalboard, HighpassFilter, LowpassFilter, LowShelfFilter, PeakFilter,
                        Distortion, Chorus, Reverb, Bitcrush, Compressor, Limiter, Delay, Gain)

SR = 48000
FP = 5.0  # WORLD frame period, ms
TARGET = dict(fem_hz=220.0, beast_hz=55.0)
FX = dict(rrev_scale=0.1)   # global multipliers, see --rrev
GATE = dict(thr_rel_db=-28.0, ratio=2.0, pre_ms=150.0, post_ms=60.0)   # see activity_gate / --gate-*

# weights: relative presence of each voice (missing = 0); rate_hz: how fast the mix drifts;
# switch: 0 = soft blend, 1 = hard jumps; mode "sum" = all voices at once with fixed weights (no drift);
# radio: band-limited broken-transmission FX with dropouts
PRESETS = {
    "morph":   dict(weights=dict(beast=1.0, fem=1.0, whisper=0.5, human=0.3), rate_hz=1.6, switch=0.6,
                    stutter_n=5, seg_ms=55, rrev_mix=0.35),
    # continuous pitch+formant glide beast <-> fem inside one voice (no crossfade, it "melts")
    "glide":   dict(weights=dict(glide=1.5, whisper=0.3, human=0.15), rate_hz=0.7, switch=0.3,
                    stutter_n=3, seg_ms=50, rrev_mix=0.3),
    # broken radio transmission: band-limited, crushed, dropouts
    "radio":   dict(weights=dict(beast=0.8, fem=0.8, human=0.8, robot=0.5), rate_hz=1.5, switch=0.8,
                    stutter_n=6, seg_ms=45, rrev_mix=0.15, radio=True),
}

# ---------- io ----------
def _ffmpeg_to_wav(path):
    tmp = tempfile.mktemp(suffix=".wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", str(SR), tmp], check=True)
    return tmp

def load(path):
    if not os.path.isfile(path):
        raise SystemExit(f"Input not found: {os.path.abspath(path)}")
    try:
        x, sr = sf.read(path, dtype="float32", always_2d=True)
    except Exception as e:
        print(f"soundfile failed ({e}); falling back to ffmpeg")
        x, sr = sf.read(_ffmpeg_to_wav(path), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != SR:
        x, _ = sf.read(_ffmpeg_to_wav(path), dtype="float32")
    return x.astype(np.float32)

def run(board, x):
    return board(x[None, :].astype(np.float32), SR)[0]

def fit(y, n):
    return y[:n] if len(y) >= n else np.pad(y, (0, n - len(y)))

# ---------- cleanup ----------
def denoise(x):
    x = run(Pedalboard([HighpassFilter(80)]), x)
    lead = x[: int(SR * 0.8)]
    body_rms = np.sqrt(np.mean(x ** 2)) + 1e-9
    if np.sqrt(np.mean(lead ** 2)) < 0.3 * body_rms:          # quiet lead-in -> use as noise profile
        y = nr.reduce_noise(y=x, sr=SR, y_noise=lead, stationary=True, prop_decrease=0.95, n_fft=2048)
    else:
        y = nr.reduce_noise(y=x, sr=SR, stationary=False, prop_decrease=0.9, n_fft=2048)
    # downward expander: kill residual hiss between words
    env = np.sqrt(np.convolve(y ** 2, np.ones(480) / 480, mode="same"))
    thr = np.percentile(env, 30) * 2.0
    g = np.clip(env / (thr + 1e-9), 0, 1) ** 2
    g = np.convolve(g, np.ones(960) / 960, mode="same")      # 20 ms smoothing, no chatter
    return (y * g).astype(np.float32)

# ---------- WORLD voices ----------
def analyze(x):
    xd = x.astype(np.float64)
    f0, t = pw.harvest(xd, SR, f0_floor=50, f0_ceil=700, frame_period=FP)
    sp = pw.cheaptrick(xd, f0, t, SR)
    ap = pw.d4c(xd, f0, t, SR)
    return f0, sp, ap

def median_f0(f0, default=120.0):
    v = f0[f0 > 0]
    return float(np.median(v)) if len(v) > 20 else default

def warp_formants(sp, ratio):
    """ratio > 1 -> formants up (smaller/'female' head), < 1 -> down (bigger/'monster')."""
    bins = np.arange(sp.shape[1], dtype=np.float64)
    src = np.clip(bins / ratio, 0, sp.shape[1] - 1)
    return np.stack([np.interp(src, bins, fr) for fr in sp])

def synth(f0, sp, ap, n):
    return fit(pw.synthesize(f0, np.ascontiguousarray(sp), np.ascontiguousarray(ap), SR, frame_period=FP), n).astype(np.float32)

def snap_semitones(f0, ref=440.0):
    out = f0.copy(); v = f0 > 0
    out[v] = ref * 2 ** (np.round(12 * np.log2(f0[v] / ref)) / 12)
    return out

def v_beast(f0, sp, ap, n, med):
    r = float(np.clip(TARGET["beast_hz"] / med, 0.45, 0.75))
    y = synth(f0 * r, warp_formants(sp, 0.80), np.clip(ap + 0.25, 0, 1), n)
    return run(Pedalboard([LowShelfFilter(180, gain_db=5), Distortion(drive_db=9), LowpassFilter(5500)]), y)

def v_fem(f0, sp, ap, n, med):
    st = float(np.clip(np.round(12 * np.log2(TARGET["fem_hz"] / med)), 7, 19))
    f = snap_semitones(f0 * 2 ** (st / 12))
    y = synth(f, warp_formants(sp, 1.22), ap * 0.4, n)       # less breath = buzzier, more synthetic
    # metallic comb (short feedback delay) + slight crush = "robot"
    return run(Pedalboard([HighpassFilter(250), Delay(delay_seconds=0.0045, feedback=0.55, mix=0.45),
                           PeakFilter(3200, gain_db=4, q=1.2), Bitcrush(bit_depth=11),
                           Chorus(rate_hz=0.8, depth=0.25, mix=0.3)]), y)

def warp_formants_tv(sp, ratios):
    """Per-frame formant warp: ratios has one value per frame."""
    B = sp.shape[1]; bins = np.arange(B, dtype=np.float64)
    src = np.clip(bins[None, :] / ratios[:, None], 0, B - 1)
    i0 = np.floor(src).astype(int); i1 = np.minimum(i0 + 1, B - 1); fr = src - i0
    rows = np.arange(sp.shape[0])[:, None]
    return sp[rows, i0] * (1 - fr) + sp[rows, i1] * fr

def smooth_random(T, rate_hz, rng, jumpiness=0.3):
    """0..1 trajectory over T WORLD frames: slow drift plus occasional fast jumps."""
    k = max(2, int(T * FP / 1000 * rate_hz) + 2)
    knots = rng.random(k)
    knots = np.where(rng.random(k) < jumpiness, np.round(knots), knots)   # some knots snap to extremes
    tr = np.interp(np.arange(T), np.linspace(0, T - 1, k), knots)
    ker = np.hanning(9); ker /= ker.sum()                                # ~45 ms smoothing
    return np.clip(np.convolve(tr, ker, mode="same"), 0, 1)

def v_glide(f0, sp, ap, n, med, rng):
    """One voice that continuously slides beast <-> fem (pitch, formants, breathiness together)."""
    t = smooth_random(len(f0), 0.9, rng)
    r_b = float(np.clip(TARGET["beast_hz"] / med, 0.45, 0.75))
    r_f = TARGET["fem_hz"] / med
    ratio = np.exp(np.log(r_b) * (1 - t) + np.log(r_f) * t)
    fr = 0.80 * (1 - t) + 1.22 * t
    a = np.clip(ap + 0.25 * (1 - t)[:, None], 0, 1) * (1 - 0.5 * t)[:, None]
    y = synth(f0 * ratio, warp_formants_tv(sp, fr), a, n)
    return run(Pedalboard([Distortion(drive_db=6), PeakFilter(2800, gain_db=3, q=1.0), LowpassFilter(9000)]), y)

def v_robot(f0, sp, ap, n, med):
    """Flat monotone note (female range) + ring mod + comb: synthetic/robotic female."""
    note = 440 * 2 ** (np.round(12 * np.log2(TARGET["fem_hz"] / 440)) / 12)
    f = np.where(f0 > 0, note, 0.0)
    y = synth(f, warp_formants(sp, 1.18), ap * 0.2, n)
    tt = np.arange(n) / SR
    y = 0.6 * y + 0.4 * y * np.sin(2 * np.pi * 70 * tt).astype(np.float32)   # ring mod 70 Hz
    return run(Pedalboard([HighpassFilter(200), Delay(delay_seconds=0.003, feedback=0.6, mix=0.4),
                           Bitcrush(bit_depth=10), PeakFilter(2500, gain_db=4, q=1.5)]), y)

def v_whisper(f0, sp, ap, n):
    y = synth(f0, warp_formants(sp, 0.92), np.ones_like(ap), n)
    return run(Pedalboard([HighpassFilter(300), PeakFilter(5000, gain_db=3, q=0.8)]), y)

def rms_match(y, ref):
    return y * (np.sqrt(np.mean(ref ** 2)) / (np.sqrt(np.mean(y ** 2)) + 1e-9))

# ---------- morph / glitch ----------
def morph_weights(names, base_w, n, rate_hz, switch, rng):
    """Smooth random per-voice gains. switch -> 1 makes one voice dominate at a time (hard-ish jumps)."""
    k = max(2, int(n / SR * rate_hz) + 2)
    knots = np.linspace(0, n, k)
    curves = []
    for nm in names:
        r = rng.random(k) * base_w[nm]
        curves.append(np.interp(np.arange(n), knots, r))
    W = np.stack(curves)
    temp = 1.0 - 0.85 * switch                                 # softmax temperature
    E = np.exp((W - W.max(0)) / max(temp * W.max() + 1e-9, 1e-3))
    E *= np.array([base_w[nm] > 0 for nm in names], float)[:, None]
    E /= E.sum(0, keepdims=True) + 1e-9
    ker = np.hanning(int(SR * 0.06)); ker /= ker.sum()         # 60 ms crossfades
    return np.stack([np.convolve(e, ker, mode="same") for e in E])

def stutter(x, n, seg_ms, rng):
    if n <= 0: return x
    seg = int(SR * seg_ms / 1000); fade = int(SR * 0.004)
    env = np.ones(seg, np.float32); env[:fade] = np.linspace(0, 1, fade); env[-fade:] = np.linspace(1, 0, fade)
    rms = np.sqrt(np.convolve(x ** 2, np.ones(seg) / seg, mode="same"))
    cand = np.where(rms > 0.3 * rms.max())[0]
    if len(cand) == 0: return x
    out = x.copy()
    for pos in sorted(rng.choice(cand, size=min(n, len(cand)), replace=False)):
        chunk = x[pos:pos + seg]
        if len(chunk) < seg: continue
        for r in range(int(rng.integers(2, 5))):
            s = pos + r * seg
            if s + seg > len(out): break
            out[s:s + seg] = out[s:s + seg] * (1 - env) + chunk * env
    return out

def radio_fx(x, rng):
    y = run(Pedalboard([HighpassFilter(450), LowpassFilter(3000), Distortion(drive_db=14), Bitcrush(bit_depth=8)]), x)
    g = np.ones(len(y), np.float32); pos = 0
    while True:                                   # random 20-90 ms dropouts, ~2 per second
        pos += int(SR * rng.exponential(0.5))
        if pos >= len(y): break
        d = int(SR * rng.uniform(0.02, 0.09)); g[pos:pos + d] = 0.05
    g = np.convolve(g, np.ones(96) / 96, mode="same")
    return y * g

def activity_gate(ref, bg_db, thr_rel_db=None, ratio=None, pre_ms=None, post_ms=None):
    """Downward expander keyed by the DRY voice: below (p99 + thr_rel_db) the output drops `ratio` dB per dB,
    floored at bg_db. Look-ahead pre_ms keeps the reverse-reverb swell into words; post_ms keeps word tails.
    Kills reverb wash / stutter tails / hiss between words (pauses in normal speech are only 0.1-0.4 s)."""
    if bg_db is None: return np.ones(len(ref), np.float32)
    thr_rel_db = GATE["thr_rel_db"] if thr_rel_db is None else thr_rel_db
    ratio = GATE["ratio"] if ratio is None else ratio
    pre_ms = GATE["pre_ms"] if pre_ms is None else pre_ms
    post_ms = GATE["post_ms"] if post_ms is None else post_ms
    hop = 240                                                      # 5 ms control rate
    m = len(ref) // hop + 1
    e = np.sqrt(np.convolve(ref ** 2, np.ones(480) / 480, mode="same"))
    e = np.pad(e, (0, m * hop - len(e)))[: m * hop].reshape(m, hop).max(1)
    edb = 20 * np.log10(e + 1e-12)
    pre, post = int(pre_ms / 5), int(post_ms / 5)
    P = np.pad(edb, (post, pre), constant_values=-200)
    win = np.lib.stride_tricks.sliding_window_view(P, pre + post + 1).max(1)   # max over [t-post, t+pre]
    thr = np.percentile(edb, 99) + thr_rel_db
    gdb = np.clip(ratio * np.minimum(win - thr, 0), bg_db, 0)
    k = np.hanning(7); k /= k.sum()                                             # ~30 ms smoothing
    gdb = np.convolve(np.pad(gdb, 3, mode="edge"), k, mode="valid")
    g = 10 ** (np.repeat(gdb, hop)[: len(ref)] / 20)
    return g.astype(np.float32)

def reverse_reverb(x):
    tail = np.zeros(int(SR * 1.5), np.float32)
    r = run(Pedalboard([HighpassFilter(400), Reverb(room_size=0.9, damping=0.4, wet_level=1.0, dry_level=0.0)]),
            np.concatenate([tail, x])[::-1].copy())
    return r[::-1][len(tail):].copy()

# ---------- main ----------
def build_voices(x, seed=42, verbose=True):
    n = len(x)
    f0, sp, ap = analyze(x)
    med = median_f0(f0)
    if verbose:
        st = float(np.clip(np.round(12 * np.log2(TARGET["fem_hz"] / med)), 7, 19))
        r = float(np.clip(TARGET["beast_hz"] / med, 0.45, 0.75))
        print(f"median f0 {med:.0f} Hz, voiced {np.mean(f0 > 0) * 100:.0f}% -> fem +{st:.0f} st, beast x{r:.2f}")
    V = dict(beast=v_beast(f0, sp, ap, n, med), fem=v_fem(f0, sp, ap, n, med),
             whisper=v_whisper(f0, sp, ap, n), human=x.copy(),
             glide=v_glide(f0, sp, ap, n, med, np.random.default_rng(seed + 1)), robot=v_robot(f0, sp, ap, n, med))
    return {k: rms_match(v, x) for k, v in V.items()}

def render(V, p, rng, bg_db=-60.0):
    w = {nm: p["weights"].get(nm, 0.0) for nm in V}
    names = [nm for nm in V if w[nm] > 0]; n = len(V["human"])
    if p.get("mode") == "sum":
        tot = sum(w[nm] for nm in names)
        mix = sum(w[nm] / tot * V[nm] for nm in names).astype(np.float32) * 1.6
    else:
        W = morph_weights(names, w, n, p["rate_hz"], p["switch"], rng)
        mix = sum(W[i] * V[nm] for i, nm in enumerate(names)).astype(np.float32)
    mix = stutter(mix, p["stutter_n"], p["seg_ms"], rng)
    if p.get("radio"):
        mix = radio_fx(mix, rng)
    rr = p["rrev_mix"] * FX["rrev_scale"]
    if rr > 0:
        mix = mix + rr * reverse_reverb(mix)
    mix = run(Pedalboard([Compressor(threshold_db=-18, ratio=3, attack_ms=5, release_ms=80),
                          Gain(6), Limiter(threshold_db=-1.0, release_ms=60)]), mix)
    mix = mix * activity_gate(V["human"], bg_db)                  # silence between phrases
    return (mix / (np.abs(mix).max() + 1e-9) * 10 ** (-1 / 20)).astype(np.float32)  # hard -1 dBFS peak

if __name__ == "__main__":
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("input"); ap_.add_argument("-o", "--outdir", default=os.path.join("raw", "out"))
    ap_.add_argument("-p", "--preset", nargs="+", default=["all"])
    ap_.add_argument("--seed", type=int, default=42)
    ap_.add_argument("--layers", action="store_true", help="also write each voice separately")
    ap_.add_argument("--fem-hz", type=float, default=TARGET["fem_hz"])
    ap_.add_argument("--beast-hz", type=float, default=TARGET["beast_hz"])
    ap_.add_argument("--bg-db", type=float, default=-60.0,
                     help="level of everything between phrases (reverb tails, hiss), dB. -60 = near silent, 0 = gate off")
    ap_.add_argument("--gate-thr", type=float, default=GATE["thr_rel_db"],
                     help="expander threshold, dB under loudest speech. Chopped quiet words -> -35/-40")
    ap_.add_argument("--gate-ratio", type=float, default=GATE["ratio"], help="dB cut per dB under threshold")
    ap_.add_argument("--gate-pre", type=float, default=GATE["pre_ms"], help="look-ahead, ms (keeps reverse swell)")
    ap_.add_argument("--gate-hold", type=float, default=GATE["post_ms"], help="hold after voice, ms (keeps word tails)")
    ap_.add_argument("--rrev", type=float, default=FX["rrev_scale"], help="reverse-reverb multiplier (default 0.1): 1 = full, 0 = off")
    a = ap_.parse_args()
    FX.update(rrev_scale=max(a.rrev, 0.0))
    TARGET.update(fem_hz=a.fem_hz, beast_hz=a.beast_hz)
    GATE.update(thr_rel_db=a.gate_thr, ratio=max(a.gate_ratio, 0.1), pre_ms=max(a.gate_pre, 0), post_ms=max(a.gate_hold, 0))
    os.makedirs(a.outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(a.input))[0]
    x = denoise(load(a.input))
    V = build_voices(x, seed=a.seed)
    if a.layers:
        ld = os.path.join(a.outdir, "layers"); os.makedirs(ld, exist_ok=True)
        for k, v in V.items():
            sf.write(os.path.join(ld, f"{stem}__layer_{k}.wav"), v / (np.abs(v).max() + 1e-9) * 0.9, SR, subtype="PCM_24")
    for name in (list(PRESETS) if "all" in a.preset else a.preset):
        y = render(V, PRESETS[name], np.random.default_rng(a.seed), None if a.bg_db >= 0 else a.bg_db)
        dst = os.path.join(a.outdir, f"{stem}__{name}.wav"); sf.write(dst, y, SR, subtype="PCM_24"); print(dst)
