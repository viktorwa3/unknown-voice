"""
unknown_live.py v3 — REALTIME "Unknown"-style voice engine for Discord (Track B, own DSP, no VSTs).
Every internal knob is a named parameter (see SPEC); a preset is just a snapshot of all of them.
GUI: unknown_live_ui.py (sliders, presets, devices). This file also works standalone (CLI).

Chain: mic -> HPF -> STFT voices (peak-locked pitch shift + formant warp per voice) -> per-voice FX ->
per-voice AGC (loudness match to the dry voice) -> random morph / glide / radio mix -> stutter ->
gate (silence between words) -> comp / makeup / limiter -> VB-Cable -> Discord.

Deps:   pip install numpy pedalboard sounddevice pynput soundfile   (+ VB-Cable)
Usage:  python unknown_live.py --list
        python unknown_live.py --in "Microphone (Razer" --out "CABLE Input" --monitor "Speakers (Razer" -p morph
        python unknown_live.py -p presets\\my_preset.json            # user preset file
        python unknown_live.py --file raw\\take01_normal.wav --out-file raw\\out\\live\\test.wav -p glide   # offline test
Hotkeys: Ctrl+Alt+1 morph | 2 glide | 3 radio | 0 bypass | Q quit (console: 1/2/3/0/q + Enter)
Generic monster-mimic recipe, NOT a reverse-engineered Behaviour preset. Personal/fan use only.
"""
import argparse, sys, threading, time, queue, json, os, glob
from collections import deque
import numpy as np
from pedalboard import (Pedalboard, HighpassFilter, LowpassFilter, LowShelfFilter, PeakFilter, Distortion,
                        Chorus, Bitcrush, Compressor, Limiter, Delay, Gain)

SR = 48000
VOICES = ["beast", "fem", "glide", "robot", "whisper", "human"]
# initial post-FX gains (dB) so every voice sits at the dry level from the first word; adapted live (AGC)
AGC_INIT_DB = dict(beast=-5.5, fem=9.7, glide=-2.3, robot=11.4, whisper=5.9, human=0.0)
HERE = os.path.dirname(os.path.abspath(__file__))
PRESET_DIR = os.path.join(HERE, "presets")

# ---------------------------------------------------------------- parameters
SPEC = []   # dicts: key, label, lo, hi, default, kind ('f','b'), fmt, tab

def _p(tab, key, label, lo=0.0, hi=1.0, default=0.0, fmt="%.2f", kind="f"):
    SPEC.append(dict(tab=tab, key=key, label=label, lo=lo, hi=hi, default=default, fmt=fmt, kind=kind))

_p("Mix", "mix.rate_hz", "Morph speed (Hz)", 0.05, 6.0, 1.6)
_p("Mix", "mix.switch", "Hard switching (0 blend, 1 jumps)", 0, 1, 0.6)
_p("Mix", "mix.stutter_ps", "Stutters per second", 0, 3, 0.35)
_p("Mix", "mix.stutter_ms", "Stutter length (ms)", 15, 200, 55, "%.0f")
_p("Mix", "mix.radio", "Radio amount", 0, 1, 0.0)
_p("Gate", "gate.thr_db", "Gate threshold (dBFS)", -80, -20, -50, "%.1f")
_p("Gate", "gate.floor_db", "Level between words (dB)", -90, 0, -60, "%.0f")
_p("Gate", "gate.ratio", "Expander ratio", 1, 6, 2.0, "%.1f")
_p("Gate", "gate.hold_ms", "Hold after voice (ms)", 0, 500, 64, "%.0f")
_p("Master", "master.agc", "Auto-level voices (AGC)", kind="b", default=True)
_p("Master", "master.in_hpf", "Input HPF (Hz)", 20, 300, 80, "%.0f")
_p("Master", "master.comp_thr", "Compressor threshold (dB)", -40, 0, -18, "%.1f")
_p("Master", "master.comp_ratio", "Compressor ratio", 1, 12, 3, "%.1f")
_p("Master", "master.makeup_db", "Makeup gain (dB)", 0, 18, 6, "%.1f")
_p("Master", "master.out_db", "Output gain (dB)", -24, 12, 0, "%.1f")
_p("Radio", "radio.hpf", "Radio HPF (Hz)", 100, 1500, 450, "%.0f")
_p("Radio", "radio.lpf", "Radio LPF (Hz)", 1000, 8000, 3000, "%.0f")
_p("Radio", "radio.drive", "Radio drive (dB)", 0, 30, 14, "%.1f")
_p("Radio", "radio.bits", "Radio bits", 4, 16, 8, "%.1f")
_p("Radio", "radio.drop_ps", "Dropouts per second", 0, 8, 2.0)
_p("Radio", "radio.drop_depth", "Dropout depth", 0, 1, 0.95)
_p("Radio", "radio.drop_min_ms", "Dropout min (ms)", 5, 200, 20, "%.0f")
_p("Radio", "radio.drop_max_ms", "Dropout max (ms)", 10, 400, 90, "%.0f")
for _v in VOICES:
    _t = _v.capitalize()
    _p(_t, f"{_v}.weight", "Weight in mix", 0, 2, 0.0)
    _p(_t, f"{_v}.level_db", "Level trim (dB)", -24, 12, 0.0, "%.1f")
    _p(_t, f"{_v}.mute", "Mute", kind="b", default=False)
    _p(_t, f"{_v}.solo", "Solo", kind="b", default=False)
_p("Beast", "beast.target_hz", "Pitch target (Hz)", 30, 150, 55, "%.0f")
_p("Beast", "beast.formant", "Formant shift (x)", 0.5, 1.5, 0.80)
_p("Beast", "beast.breath", "Breath / rasp", 0, 1, 0.25)
_p("Beast", "beast.hpf_hz", "HPF (Hz)", 20, 300, 60, "%.0f")
_p("Beast", "beast.shelf_db", "Low shelf 180 Hz (dB)", -12, 12, 1.5, "%.1f")
_p("Beast", "beast.drive_db", "Drive (dB)", 0, 30, 9, "%.1f")
_p("Beast", "beast.lpf_hz", "LPF (Hz)", 1000, 16000, 5500, "%.0f")
_p("Fem", "fem.target_hz", "Pitch target (Hz)", 120, 450, 220, "%.0f")
_p("Fem", "fem.snap", "Autotune snap (robotic)", kind="b", default=True)
_p("Fem", "fem.formant", "Formant shift (x)", 0.8, 1.8, 1.22)
_p("Fem", "fem.breath", "Breath", 0, 1, 0.05)
_p("Fem", "fem.hpf_hz", "HPF (Hz)", 20, 800, 250, "%.0f")
_p("Fem", "fem.comb_ms", "Metal comb delay (ms)", 0.5, 15, 4.5, "%.1f")
_p("Fem", "fem.comb_fb", "Metal comb feedback", 0, 0.95, 0.55)
_p("Fem", "fem.comb_mix", "Metal comb mix", 0, 1, 0.45)
_p("Fem", "fem.presence_hz", "Presence freq (Hz)", 800, 8000, 3200, "%.0f")
_p("Fem", "fem.presence_db", "Presence gain (dB)", -12, 12, 4, "%.1f")
_p("Fem", "fem.bits", "Bitcrush bits", 4, 16, 11, "%.1f")
_p("Fem", "fem.chorus_mix", "Chorus mix", 0, 1, 0.3)
_p("Fem", "fem.chorus_rate", "Chorus rate (Hz)", 0.05, 5, 0.8)
_p("Glide", "glide.low_hz", "Low end pitch (Hz)", 30, 150, 55, "%.0f")
_p("Glide", "glide.high_hz", "High end pitch (Hz)", 120, 450, 220, "%.0f")
_p("Glide", "glide.formant_lo", "Formant at low end (x)", 0.5, 1.3, 0.80)
_p("Glide", "glide.formant_hi", "Formant at high end (x)", 0.9, 1.8, 1.22)
_p("Glide", "glide.breath_lo", "Breath at low end", 0, 1, 0.25)
_p("Glide", "glide.breath_hi", "Breath at high end", 0, 1, 0.05)
_p("Glide", "glide.rate_hz", "Glide speed (Hz)", 0.1, 4, 0.9)
_p("Glide", "glide.jump", "Jumps to extremes", 0, 1, 0.3)
_p("Glide", "glide.drive_db", "Drive (dB)", 0, 30, 6, "%.1f")
_p("Glide", "glide.presence_db", "Presence 2.8k (dB)", -12, 12, 3, "%.1f")
_p("Glide", "glide.lpf_hz", "LPF (Hz)", 2000, 16000, 9000, "%.0f")
_p("Robot", "robot.note_hz", "Flat note (Hz)", 80, 600, 220, "%.0f")
_p("Robot", "robot.snap", "Snap note to semitone", kind="b", default=True)
_p("Robot", "robot.formant", "Formant shift (x)", 0.8, 1.8, 1.18)
_p("Robot", "robot.ring_hz", "Ring mod freq (Hz)", 5, 400, 70, "%.0f")
_p("Robot", "robot.ring_mix", "Ring mod mix", 0, 1, 0.4)
_p("Robot", "robot.hpf_hz", "HPF (Hz)", 20, 800, 200, "%.0f")
_p("Robot", "robot.comb_ms", "Comb delay (ms)", 0.5, 15, 3.0, "%.1f")
_p("Robot", "robot.comb_fb", "Comb feedback", 0, 0.95, 0.6)
_p("Robot", "robot.comb_mix", "Comb mix", 0, 1, 0.4)
_p("Robot", "robot.bits", "Bitcrush bits", 4, 16, 10, "%.1f")
_p("Robot", "robot.presence_db", "Presence 2.5k (dB)", -12, 12, 4, "%.1f")
_p("Whisper", "whisper.formant", "Formant shift (x)", 0.5, 1.5, 0.92)
_p("Whisper", "whisper.hpf_hz", "HPF (Hz)", 50, 2000, 300, "%.0f")
_p("Whisper", "whisper.air_db", "Air 5k (dB)", -12, 12, 3, "%.1f")

SPEC_BY_KEY = {s["key"]: s for s in SPEC}
DEFAULTS = {s["key"]: s["default"] for s in SPEC}

def _preset(**kw):
    p = dict(DEFAULTS)
    for k, v in kw.items(): p[k.replace("__", ".")] = v
    return p

BUILTIN = {
    "morph": _preset(beast__weight=1.0, fem__weight=1.0, whisper__weight=0.5, human__weight=0.3,
                     mix__rate_hz=1.6, mix__switch=0.6, mix__stutter_ps=0.35, mix__stutter_ms=55, mix__radio=0.0),
    "glide": _preset(glide__weight=1.5, whisper__weight=0.3, human__weight=0.15,
                     mix__rate_hz=0.7, mix__switch=0.3, mix__stutter_ps=0.22, mix__stutter_ms=50, mix__radio=0.0),
    "radio": _preset(beast__weight=0.8, fem__weight=0.8, human__weight=0.8, robot__weight=0.5,
                     mix__rate_hz=1.5, mix__switch=0.8, mix__stutter_ps=0.45, mix__stutter_ms=45, mix__radio=1.0),
    "bypass": _preset(human__weight=1.0, mix__rate_hz=0.5, mix__switch=0.0, mix__stutter_ps=0.0, mix__radio=0.0),
}

def sanitize(p):
    """Merge onto DEFAULTS, drop unknown keys, clamp to SPEC ranges (forward/backward compatible JSON)."""
    out = dict(DEFAULTS)
    for k, v in (p or {}).items():
        s = SPEC_BY_KEY.get(k)
        if s is None: continue
        out[k] = bool(v) if s["kind"] == "b" else float(np.clip(float(v), s["lo"], s["hi"]))
    return out

def user_presets():
    res = {}
    for f in sorted(glob.glob(os.path.join(PRESET_DIR, "*.json"))):
        try:
            with open(f, encoding="utf-8") as fh: res[os.path.splitext(os.path.basename(f))[0]] = sanitize(json.load(fh))
        except Exception as e:
            print(f"(skip broken preset {f}: {e})")
    return res

def all_presets():
    d = dict(BUILTIN); d.update(user_presets()); return d

def safe_name(name):
    return "".join(c for c in name if c.isalnum() or c in "-_ ").strip()

def save_preset(name, params):
    os.makedirs(PRESET_DIR, exist_ok=True)
    nm = safe_name(name) or "preset"
    path = os.path.join(PRESET_DIR, nm + ".json")
    with open(path, "w", encoding="utf-8") as fh: json.dump(params, fh, indent=1, sort_keys=True)
    return nm, path

def delete_preset(name):
    path = os.path.join(PRESET_DIR, name + ".json")
    if os.path.isfile(path): os.remove(path); return True
    return False


# ---------------------------------------------------------------- engine
class Engine:
    def __init__(self, n_fft=2048, seed=None, params=None):
        self.N, self.H = n_fft, n_fft // 4
        self.K = self.N // 2 + 1
        self.win = np.hanning(self.N + 1)[:-1].astype(np.float64)
        self.ola_norm = 1.0 / 1.5
        self.k = np.arange(self.K, dtype=np.float64)
        self.rng = np.random.default_rng(seed)
        self.inbuf = np.zeros(self.N)
        self.pk_state = {v: (np.zeros(0, int), np.zeros(0)) for v in VOICES}
        self.ola = {v: np.zeros(self.N) for v in VOICES}
        self.lifter = max(20, int(0.0016 * SR * self.N / 2048))
        self.agc_db = dict(AGC_INIT_DB)
        self.f0_hist = deque(maxlen=400); self.f0_med = 110.0
        # FX objects; their parameters are pushed from self.p in _apply()
        self.fx = {
            "beast": Pedalboard([HighpassFilter(60), LowShelfFilter(180, gain_db=1.5), Distortion(drive_db=9), LowpassFilter(5500)]),
            "fem": Pedalboard([HighpassFilter(250), Delay(delay_seconds=0.0045, feedback=0.55, mix=0.45),
                               PeakFilter(3200, gain_db=4, q=1.2), Bitcrush(bit_depth=11),
                               Chorus(rate_hz=0.8, depth=0.25, mix=0.3)]),
            "glide": Pedalboard([Distortion(drive_db=6), PeakFilter(2800, gain_db=3, q=1.0), LowpassFilter(9000)]),
            "robot": Pedalboard([HighpassFilter(200), Delay(delay_seconds=0.003, feedback=0.6, mix=0.4),
                                 Bitcrush(bit_depth=10), PeakFilter(2500, gain_db=4, q=1.5)]),
            "whisper": Pedalboard([HighpassFilter(300), PeakFilter(5000, gain_db=3, q=0.8)]),
            "human": Pedalboard([]),
        }
        self.in_hpf = Pedalboard([HighpassFilter(80)])
        self.radio_fx = Pedalboard([HighpassFilter(450), LowpassFilter(3000), Distortion(drive_db=14), Bitcrush(bit_depth=8)])
        self.master = Pedalboard([Compressor(threshold_db=-18, ratio=3, attack_ms=5, release_ms=80),
                                  Gain(6), Limiter(threshold_db=-1.0, release_ms=60)])
        self.ring_ph = 0.0
        self.g_t, self.g_from, self.g_to, self.g_pos, self.g_len = 0.5, 0.5, 0.5, 0, 1
        self.knots = {v: [0.0, 0.0] for v in VOICES}; self.knot_pos = 0; self.knot_len = 1
        self.w_prev = np.zeros(len(VOICES)); self.w_prev[VOICES.index("human")] = 1.0
        self.radio_amt = 0.0; self.drop_left = 0; self.drop_gain = 1.0
        self.hist = np.zeros(int(SR * 0.2)); self.st_seg = None; self.st_left = 0; self.st_pos = 0
        self.key_delay = deque([-200.0] * 3, maxlen=3)   # STFT latency = N - H = 3 hops
        self.hold = 0; self.g_prev = 0.0; self._talking = False
        self.stats = dict(in_db=-120.0, out_db=-120.0, gate=0.0, weights=np.zeros(len(VOICES)), load=0.0, xruns=0)
        self.preset_name = "morph"
        self.p = dict(BUILTIN["morph"]); self._dirty = True
        self.load(params if params is not None else BUILTIN["morph"], name="morph" if params is None else "custom")

    # ---------- parameters ----------
    def load(self, params, name="custom"):
        """Replace ALL parameters (preset load). Restarts the random morph so the new mix takes over at once."""
        self.p = sanitize(params); self.preset_name = name; self._dirty = True
        self._new_knots(first=True)

    def set(self, key, value):
        """Change one parameter live (from the UI)."""
        s = SPEC_BY_KEY[key]
        self.p[key] = bool(value) if s["kind"] == "b" else float(np.clip(float(value), s["lo"], s["hi"]))
        self._dirty = True

    def set_preset(self, name):
        self.load(all_presets()[name], name=name)

    def _apply(self):
        p = self.p; self._dirty = False
        b = self.fx["beast"]
        b[0].cutoff_frequency_hz = p["beast.hpf_hz"]; b[1].gain_db = p["beast.shelf_db"]
        b[2].drive_db = p["beast.drive_db"]; b[3].cutoff_frequency_hz = p["beast.lpf_hz"]
        f = self.fx["fem"]
        f[0].cutoff_frequency_hz = p["fem.hpf_hz"]; f[1].delay_seconds = p["fem.comb_ms"] / 1000
        f[1].feedback = p["fem.comb_fb"]; f[1].mix = p["fem.comb_mix"]
        f[2].cutoff_frequency_hz = p["fem.presence_hz"]; f[2].gain_db = p["fem.presence_db"]
        f[3].bit_depth = p["fem.bits"]; f[4].mix = p["fem.chorus_mix"]; f[4].rate_hz = p["fem.chorus_rate"]
        g = self.fx["glide"]
        g[0].drive_db = p["glide.drive_db"]; g[1].gain_db = p["glide.presence_db"]; g[2].cutoff_frequency_hz = p["glide.lpf_hz"]
        r = self.fx["robot"]
        r[0].cutoff_frequency_hz = p["robot.hpf_hz"]; r[1].delay_seconds = p["robot.comb_ms"] / 1000
        r[1].feedback = p["robot.comb_fb"]; r[1].mix = p["robot.comb_mix"]; r[2].bit_depth = p["robot.bits"]
        r[3].gain_db = p["robot.presence_db"]
        w = self.fx["whisper"]; w[0].cutoff_frequency_hz = p["whisper.hpf_hz"]; w[1].gain_db = p["whisper.air_db"]
        self.in_hpf[0].cutoff_frequency_hz = p["master.in_hpf"]
        rf = self.radio_fx
        rf[0].cutoff_frequency_hz = p["radio.hpf"]; rf[1].cutoff_frequency_hz = p["radio.lpf"]
        rf[2].drive_db = p["radio.drive"]; rf[3].bit_depth = p["radio.bits"]
        m = self.master
        m[0].threshold_db = p["master.comp_thr"]; m[0].ratio = p["master.comp_ratio"]
        m[1].gain_db = p["master.makeup_db"] + p["master.out_db"]

    def _eff_weights(self):
        p = self.p
        solo = any(p[f"{v}.solo"] for v in VOICES)
        return np.array([0.0 if (p[f"{v}.mute"] or (solo and not p[f"{v}.solo"])) else p[f"{v}.weight"] for v in VOICES])

    # ---------- morph / glide control ----------
    def _new_knots(self, first=False):
        for v in VOICES:
            a = self.knots[v][1] if not first else self.rng.random()
            self.knots[v] = [a, self.rng.random()]
        self.knot_len = max(1, int(SR / self.H / max(self.p["mix.rate_hz"], 0.05)))
        self.knot_pos = 0

    def _weights(self):
        """softmax over smoothly moving random knots (same math as unknown_fx.morph_weights); weights are live."""
        we = self._eff_weights()
        a = self.knot_pos / self.knot_len
        W = np.array([self.knots[v][0] * (1 - a) + self.knots[v][1] * a for v in VOICES]) * we
        self.knot_pos += 1
        if self.knot_pos >= self.knot_len: self._new_knots()
        wmax = we.max()
        if wmax <= 0:
            return np.zeros(len(VOICES))
        temp = 1.0 - 0.85 * self.p["mix.switch"]
        E = np.exp((W - W.max()) / max(temp * wmax, 1e-3))
        E *= (we > 0).astype(float)
        return E / (E.sum() + 1e-9)

    def _glide_t(self):
        if self.g_pos >= self.g_len:
            self.g_from = self.g_t
            r = self.rng.random()
            self.g_to = round(r) if self.rng.random() < self.p["glide.jump"] else r
            self.g_len = max(4, int(SR / self.H / max(self.p["glide.rate_hz"], 0.05) * (0.5 + self.rng.random()))); self.g_pos = 0
        a = self.g_pos / self.g_len; a = a * a * (3 - 2 * a)
        self.g_t = self.g_from + (self.g_to - self.g_from) * a; self.g_pos += 1
        return self.g_t

    # ---------- analysis ----------
    def _f0(self, frame, energy_db):
        if energy_db < self.p["gate.thr_db"]: return 0.0
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
        """Envelope through the tops of the harmonics (log-linear between prominent peaks, light smoothing)."""
        lm = np.log(mag + 1e-9)
        w = 5
        pad = np.pad(lm, w // 2, mode="edge")
        mx = np.lib.stride_tricks.sliding_window_view(pad, w).max(1)
        pk = np.where((lm >= mx) & (lm > lm.max() - 18.4))[0]
        if pk.size < 2:
            c = np.fft.irfft(lm); L = self.lifter; c[L:self.N - L] = 0
            return np.exp(np.fft.rfft(c).real)
        e = np.interp(self.k, pk, lm[pk])
        e = np.convolve(np.pad(e, 2, mode="edge"), np.ones(5) / 5, mode="valid")
        return np.exp(e)

    def _peaks(self, mag):
        m = mag[1:-1]
        pk = np.where((m > mag[:-2]) & (m >= mag[2:]) & (m > mag.max() * 1e-4))[0] + 1
        if pk.size == 0: pk = np.array([1])
        mid = (pk[:-1] + pk[1:]) / 2.0
        owner = np.searchsorted(mid, self.k, side="right")
        return pk, owner

    def _shift(self, v, Xf, env, r, fr, noise, target_energy):
        """Peak-locked pitch shift (Laroche-Dolson) + formant envelope re-applied (warped by fr)."""
        K = self.K
        envw = np.interp(self.k / fr, self.k, env, right=env[-1] * 1e-3)
        if noise >= 1.0:
            Y = envw * np.exp(1j * self.rng.uniform(-np.pi, np.pi, K))
        else:
            pk, owner = self._pk, self._owner
            d = np.rint(pk * r).astype(int) - pk
            ppk, prot = self.pk_state[v]
            if ppk.size:
                j = np.clip(np.searchsorted(ppk, pk), 1, ppk.size - 1) if ppk.size > 1 else np.zeros(pk.size, int)
                if ppk.size > 1:
                    j = np.where(np.abs(ppk[j - 1] - pk) <= np.abs(ppk[j] - pk), j - 1, j)
                base = prot[j]
            else:
                base = np.zeros(pk.size)
            rot = np.mod(base + 2 * np.pi * self.H * d / self.N + np.pi, 2 * np.pi) - np.pi
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

    @staticmethod
    def _snap(f):
        return 440 * 2 ** (np.round(12 * np.log2(f / 440)) / 12)

    # ---------- one hop ----------
    def _hop(self, hop_in):
        N, H, p = self.N, self.H, self.p
        self.inbuf = np.concatenate([self.inbuf[H:], hop_in])
        X = np.fft.rfft(self.inbuf * self.win)
        mag = np.abs(X)
        energy = np.sum(mag ** 2)
        lvl_db = 10 * np.log10(np.mean(hop_in ** 2) + 1e-12)
        f0 = self._f0(self.inbuf[-N:], lvl_db)
        env = self._env(mag); Xf = X / (env + 1e-12)
        self._pk, self._owner = self._peaks(mag)
        med = self.f0_med
        rb = float(np.clip(p["beast.target_hz"] / med, 0.2, 1.2))
        st = float(np.clip(np.round(12 * np.log2(p["fem.target_hz"] / med)), 0, 30)); rf = 2 ** (st / 12)
        if p["fem.snap"]:
            rf_eff = self._snap(f0 * rf) / f0 if f0 > 0 else rf
        else:
            rf_eff = p["fem.target_hz"] / med
        t = self._glide_t()
        rgl = float(np.clip(p["glide.low_hz"] / med, 0.2, 1.2))
        rg = float(np.exp(np.log(rgl) * (1 - t) + np.log(p["glide.high_hz"] / med) * t))
        note = self._snap(p["robot.note_hz"]) if p["robot.snap"] else p["robot.note_hz"]
        rr = note / f0 if f0 > 0 else rf
        specs = {
            "beast": self._shift("beast", Xf, env, rb, p["beast.formant"], p["beast.breath"], energy),
            "fem": self._shift("fem", Xf, env, rf_eff, p["fem.formant"], p["fem.breath"], energy),
            "glide": self._shift("glide", Xf, env, rg, p["glide.formant_lo"] * (1 - t) + p["glide.formant_hi"] * t,
                                 p["glide.breath_lo"] * (1 - t) + p["glide.breath_hi"] * t, energy),
            "robot": self._shift("robot", Xf, env, rr, p["robot.formant"], 0.0, energy),
            "whisper": self._shift("whisper", Xf, env, 1.0, p["whisper.formant"], 1.0, energy),
            "human": X,
        }
        outs = {}
        for v, Y in specs.items():
            y = np.fft.irfft(Y, N) * self.win * self.ola_norm
            buf = self.ola[v]; buf += y
            outs[v] = buf[:H].copy()
            self.ola[v] = np.concatenate([buf[H:], np.zeros(H)])
        self.key_delay.append(lvl_db)
        return outs, self.key_delay[0], lvl_db

    # ---------- block ----------
    def process(self, x):
        """x: mono float block, len multiple of H. Returns processed mono float32 block."""
        if self._dirty: self._apply()
        p = self.p; H = self.H; n = len(x)
        x = self.in_hpf(np.asarray(x, np.float32)[None, :], SR, reset=False)[0].astype(np.float64)
        voices = {v: np.zeros(n) for v in VOICES}
        wts = np.zeros((len(VOICES), n)); gate = np.zeros(n)
        thr = p["gate.thr_db"]; floor = p["gate.floor_db"]; ratio = p["gate.ratio"]
        hold_hops = int(round(p["gate.hold_ms"] / (1000 * H / SR)))
        in_db = -120.0
        for i in range(0, n, H):
            outs, key_db, lvl = self._hop(x[i:i + H]); in_db = max(in_db, lvl)
            for v in VOICES: voices[v][i:i + H] = outs[v]
            w = self._weights()
            ramp = np.linspace(0, 1, H, endpoint=False)[None, :]
            wts[:, i:i + H] = self.w_prev[:, None] * (1 - ramp) + w[:, None] * ramp; self.w_prev = w
            if key_db > thr: self.hold = hold_hops
            elif self.hold > 0: self.hold -= 1
            gdb = 0.0 if (key_db > thr or self.hold > 0) else max(floor, ratio * (key_db - thr))
            g = 10 ** (gdb / 20)
            gate[i:i + H] = np.linspace(self.g_prev, g, H, endpoint=False) if g < self.g_prev else g
            self.g_prev = g
        rh, rm = p["robot.ring_hz"], p["robot.ring_mix"]
        tt = self.ring_ph + 2 * np.pi * rh * np.arange(n) / SR; self.ring_ph = float(tt[-1] + 2 * np.pi * rh / SR) % (2 * np.pi)
        voices["robot"] = (1 - rm) * voices["robot"] + rm * voices["robot"] * np.sin(tt)
        mix = np.zeros(n)
        ref_db = 10 * np.log10(np.mean(voices["human"] ** 2) + 1e-12)
        self._talking = ref_db > thr + 6
        for j, v in enumerate(VOICES):
            if wts[j].max() < 1e-4 and v != "human":
                self.fx[v](np.zeros((1, n), np.float32), SR, reset=False)       # keep FX state/timing warm
                continue
            y = self.fx[v](voices[v].astype(np.float32)[None, :], SR, reset=False)[0].astype(np.float64)
            y = self._agc(v, y, ref_db) * 10 ** (p[f"{v}.level_db"] / 20)
            mix += wts[j] * y
        tgt = p["mix.radio"]; a = np.linspace(self.radio_amt, tgt, n) if abs(tgt - self.radio_amt) > 1e-3 else np.full(n, tgt)
        self.radio_amt = self.radio_amt + (tgt - self.radio_amt) * min(1.0, n / (0.15 * SR))
        rad = self.radio_fx(mix.astype(np.float32)[None, :], SR, reset=False)[0]
        dg = np.ones(n); depth = 1 - p["radio.drop_depth"]
        dmin, dmax = sorted((p["radio.drop_min_ms"], p["radio.drop_max_ms"]))
        for i in range(0, n, 96):
            if self.drop_left <= 0 and self.rng.random() < p["radio.drop_ps"] * 96 / SR:
                self.drop_left = int(SR * self.rng.uniform(dmin / 1000, dmax / 1000))
            tgt_g = depth if self.drop_left > 0 else 1.0
            self.drop_left -= 96
            dg[i:i + 96] = np.linspace(self.drop_gain, tgt_g, min(96, n - i), endpoint=False); self.drop_gain = tgt_g
        mix = mix * (1 - a) + rad * dg * a
        mix = self._stutter(mix, gate)
        mix = mix * gate
        y = self.master(mix.astype(np.float32)[None, :], SR, reset=False)[0]
        st = self.stats
        st["in_db"] = in_db; st["out_db"] = 10 * np.log10(np.mean(y ** 2) + 1e-12); st["gate"] = float(gate[-1])
        st["weights"] = self.w_prev.copy()
        return y

    def _agc(self, v, y, ref_db):
        """Slow loudness match of a voice (post-FX) to the dry voice. Updates only while talking."""
        g0 = self.agc_db[v]
        if v != "human" and self._talking and self.p["master.agc"]:
            v_db = 10 * np.log10(np.mean(y ** 2) + 1e-12)
            if v_db > -90:
                a = 1 - np.exp(-len(y) / (0.6 * SR))
                self.agc_db[v] = float(np.clip(g0 + a * ((ref_db - v_db) - g0), -24, 24))
        return y * 10 ** (np.linspace(g0, self.agc_db[v], len(y)) / 20)

    def _stutter(self, mix, gate):
        n = len(mix); out = mix.copy()
        seg = int(SR * self.p["mix.stutter_ms"] / 1000); fade = int(SR * 0.004)
        i = 0
        while i < n:
            if self.st_left <= 0:
                talking = gate[i] > 0.99 and np.sqrt(np.mean(mix[i:i + 256] ** 2)) > 1e-3
                pr = self.p["mix.stutter_ps"] * min(256, n - i) / SR
                if talking and self.rng.random() < pr:
                    self.st_seg = np.concatenate([self.hist, mix[:i]])[-seg:].copy()
                    env = np.ones(seg); env[:fade] = np.linspace(0, 1, fade); env[-fade:] = np.linspace(1, 0, fade)
                    self.st_seg *= env; self.st_left = seg * int(self.rng.integers(2, 5)); self.st_pos = 0
                i += 256; continue
            seg_now = len(self.st_seg)
            m = min(self.st_left, n - i)
            idx = (self.st_pos + np.arange(m)) % seg_now
            out[i:i + m] = self.st_seg[idx]
            self.st_pos += m; self.st_left -= m; i += m
        self.hist = np.concatenate([self.hist, mix])[-len(self.hist):]
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
    """Owns the sounddevice streams. start()/stop() can be called repeatedly (device switch without restart)."""
    def __init__(self, eng):
        self.eng = eng; self.streams = []; self.running = False; self.monitor_vol = 0.7; self.info = ""
        self.latency_s = 0.0

    def start(self, inp=None, out="CABLE Input", monitor=None, hops=2, latency="low"):
        import sounddevice as sd
        self.stop()
        eng = self.eng
        dout = find_dev(out, "output"); din = find_dev(inp, "input")
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
        mon_q = queue.Queue(maxsize=8) if monitor else None
        B = eng.H * hops

        def cb(indata, outdata, frames, t, status):
            if status: eng.stats["xruns"] += 1
            s = time.perf_counter()
            try:
                y = eng.process(indata.mean(axis=1))
            except Exception as e:           # never kill the audio thread
                print("engine error:", e); y = np.zeros(frames, np.float32)
            outdata[:] = np.repeat(y[:, None], cout, axis=1)
            if mon_q is not None:
                try: mon_q.put_nowait(y.copy())
                except queue.Full: pass
            eng.stats["load"] = 0.9 * eng.stats["load"] + 0.1 * (time.perf_counter() - s) / (frames / SR)

        st = sd.Stream(samplerate=SR, blocksize=B, device=(din, dout), channels=(cin, cout), dtype="float32",
                       latency=latency, callback=cb, extra_settings=(ex, ex) if ex else None)
        self.streams = [st]
        mon_name = ""
        if monitor:
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
        self.latency_s = lat + (eng.N - eng.H) / SR
        self.info = f"IN: {di['name']} | OUT: {do['name']}" + (f" | MON: {mon_name}" if monitor else "")
        self.running = True
        return self.info

    def stop(self):
        for s in self.streams:
            try: s.stop(); s.close()
            except Exception: pass
        self.streams = []; self.running = False


# ---------------------------------------------------------------- CLI
def run_file(a, eng):
    import soundfile as sf
    x, sr = sf.read(a.file, dtype="float32", always_2d=True); x = x.mean(1)
    if sr != SR: raise SystemExit(f"--file must be {SR} Hz (got {sr})")
    B = eng.H * 2; pad = (-len(x)) % B; x = np.pad(x, (0, pad + eng.N))
    out = []; t0 = time.perf_counter(); worst = 0.0
    for i in range(0, len(x), B):
        s = time.perf_counter(); out.append(eng.process(x[i:i + B])); worst = max(worst, time.perf_counter() - s)
    dt = time.perf_counter() - t0; y = np.concatenate(out)[eng.N - eng.H:]
    sf.write(a.out_file, y / (np.abs(y).max() + 1e-9) * 0.89, SR, subtype="PCM_24")
    print(f"{a.out_file}: realtime factor {dt / (len(x) / SR):.2f}, worst block {worst * 1000:.1f} ms of {B / SR * 1000:.1f} ms")


def run_cli(a, eng):
    io = AudioIO(eng); io.monitor_vol = a.monitor_vol
    print(io.start(a.inp, a.out, a.monitor, a.hops, a.latency))
    stop = threading.Event()
    names = ["morph", "glide", "radio"]

    def sel(nm):
        eng.set_preset(nm); print(f"\r[preset] {nm}            ")
    try:
        from pynput import keyboard
        hk = {f"<ctrl>+<alt>+{i + 1}": (lambda nm=nm: sel(nm)) for i, nm in enumerate(names)}
        hk.update({"<ctrl>+<alt>+0": lambda: sel("bypass"), "<ctrl>+<alt>+q": stop.set})
        keyboard.GlobalHotKeys(hk).start(); print("Hotkeys: Ctrl+Alt+1 morph | 2 glide | 3 radio | 0 bypass | Q quit")
    except Exception as e:
        print(f"(global hotkeys unavailable: {e}; use the console)")

    def console():
        m = {"1": "morph", "2": "glide", "3": "radio", "0": "bypass"}
        while not stop.is_set():
            try: c = input().strip().lower()
            except EOFError: return
            if c == "q": stop.set()
            elif c in m: sel(m[c])
    threading.Thread(target=console, daemon=True).start()
    try:
        while not stop.is_set():
            time.sleep(1.0)
            s = eng.stats
            print(f"\r[{eng.preset_name}] cpu {s['load'] * 100:4.0f}%  xruns {s['xruns']}  latency ~{io.latency_s * 1000:.0f} ms  "
                  f"f0med {eng.f0_med:.0f} Hz   ", end="", flush=True)
    except KeyboardInterrupt:
        pass
    io.stop(); print("\nbye")


def resolve_preset(name):
    if name.lower().endswith(".json"):
        with open(name, encoding="utf-8") as fh: return sanitize(json.load(fh)), os.path.splitext(os.path.basename(name))[0]
    ps = all_presets()
    if name not in ps: raise SystemExit(f"Unknown preset '{name}'. Available: {', '.join(ps)}")
    return ps[name], name


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list audio devices and exit")
    ap.add_argument("--in", dest="inp", default=None, help="mic name substring or index (default: system mic)")
    ap.add_argument("--out", default="CABLE Input", help="output device (VB-Cable input)")
    ap.add_argument("--monitor", default=None, help="also play to this device (your headphones)")
    ap.add_argument("--monitor-vol", type=float, default=0.7)
    ap.add_argument("-p", "--preset", default="morph", help="built-in/user preset name or a .json file")
    ap.add_argument("--fft", type=int, default=2048, choices=[1024, 2048])
    ap.add_argument("--hops", type=int, default=2, help="hops per audio block (block = hops * fft/4)")
    ap.add_argument("--latency", default="low", help="'low', 'high' or seconds, e.g. 0.03")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--file"); ap.add_argument("--out-file", default="live_test.wav")
    a = ap.parse_args()
    if a.list:
        for d in list_devices():
            print(f"{d['index']:3d}  in{d['ins']:2d} out{d['outs']:2d}  {d['api']:<22} {d['name']}")
        sys.exit(0)
    try: a.latency = float(a.latency)
    except ValueError: pass
    params, pname = resolve_preset(a.preset)
    eng = Engine(n_fft=a.fft, seed=a.seed if a.seed is not None else (42 if a.file else None))
    eng.load(params, name=pname)
    run_file(a, eng) if a.file else run_cli(a, eng)
