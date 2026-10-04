"""
twin_ui.py v1 — GUI for the "Unknown Twin" live engine (twin_engine.py), Dear PyGui. Separate app: own presets
(twin\\presets\\), own settings (twin\\twin_settings.json); the old unknown_live_ui.py is untouched.

  * Left: preset browser (folders, search, favorites). Top: prev / next / random, favorite, Save as "folder/name", Delete, Revert.
  * Middle tabs: Twin (Male, Female, Motion), Clear, Texture, Horror, Space, Gate & Master.
  * Right column (ALWAYS visible): START / STOP, CLEAR toggle, devices, FFT, buffer; meters, M<->F balance, clear, plot.
  * Hotkeys (global): Ctrl+Alt+1..9 favorites, 0 bypass, Left/Right prev/next, R random, C = toggle CLEAR (Weakened) mode.
    The old app uses the same Ctrl+Alt+digits: don't run both at once.

Deps:  same .venv as the old app (dearpygui, numpy, pedalboard, sounddevice, pynput)
Usage: run_twin.bat   (or: ..\\.venv\\Scripts\\python.exe twin_ui.py)
Personal/fan use only.
"""
import json, os, queue, random, time, traceback
import numpy as np
import dearpygui.dearpygui as dpg
import twin_engine as T

SETTINGS = os.path.join(T.HERE, "twin_settings.json")
ACCENT = (250, 176, 5); MUTED = (148, 163, 184); GOOD = (163, 230, 53); BAD = (248, 113, 113)
MALE_C = (96, 165, 250); FEM_C = (232, 121, 249); CLEAR_C = (250, 250, 250)
HIST = 200
LAYOUT = [("Twin", ["Male", "Female", "Motion"]), ("Clear", ["Clear"]), ("Game", ["Game FX", "Mumble"]), ("Texture", ["Texture"]), ("Horror", ["Horror"]),
          ("Space & Tone", ["Space", "Tone"]), ("Gate & Master", ["Gate", "Master"])]
TAB_HELP = {
    "Male": "Male layer = your voice (pitch offset, formant). Drift = slow random detune, so the two layers never lock "
            "(bad mimicry). Frequency shift (Bode) moves every harmonic by the same Hz -> metallic, inhuman, still clear speech. "
            "Keep it 8-25 Hz, mix 20-40 %.",
    "Female": "Female layer: follows your melody at 'pitch target', hard-tune pulls it to notes. Strength 1 + retune 0 ms = "
              "robotic female; 0.3 + 40 ms = natural. Ring mod 30-70 Hz at 10-20 % = synthetic edge (above 25 % = Dalek).",
    "Motion": "Both layers ALWAYS sound; this sets the balance. Slow sway (LFO + noise), random jumps to one side "
              "(attack / hold), flips on syllable onsets, and the female lag behind the male (two throats, not one).",
    "Clear": "In the game a Weakened survivor hears ONE voice clearly with much less distortion. Clear mode does that: "
             "one layer dominates (the other at 'residual'), motion / texture / horror fade out. It is triggered by "
             "the slider, the CLEAR button / Ctrl+Alt+C, or randomly per phrase ('chance').",
    "Texture": "Whisper = noise version of your voice above the HPF, under everything. Blur = vowels smear for a moment. "
               "Stutter repeats a few ms and stops as soon as you stop talking (pauses stay clean).",
    "Horror": "Analog-horror tape bus after the mix: band-pass, tape drive, wow / flutter (pitch wobble), dropouts and "
              "hiss that only exists while you talk. 'Amount' scales all of it and is cut by clear mode.",
    "Space": "Distance = darker and quieter, occlusion = behind a wall, small room = tight reverb that rings a bit "
             "into pauses (after the gate).",
    "Game FX": "Taken from the game's Init.bnk: every Unknown voice bus is pitched -200 cents (global pitch -2 = resample-like, "
               "formants move too); an aux 'harmonizer' adds dark shadow copies at -2 / -11 st (LPF 3 kHz / 500 Hz) plus a -4.5 st "
               "pitch shifter 45 ms late; an aux stereo delay (440 / 620 ms, LPF ~5 kHz) folded to mono. 1 = game levels.",
    "Mumble": "Outside Weakened the game plays no words: 0.2-0.3 s syllables over growl / hiss / rasp textures. 'Chance' turns a whole "
              "phrase into syllable bursts (gaps at 'gap depth'); the growl layer is a low noisy copy of your voice, louder in "
              "mumbled phrases. Clear mode switches both off.",
    "Tone": "Master EQ. Measured on the game files (KLR_35): energy peaks around 250 Hz, a dip around 4 kHz and a lot of "
            "'air' at 8-12 kHz. The game voice is wide-band, NOT a narrow radio band.",
    "Gate": "Silences everything between words. Raise the threshold if room noise opens it, lower it if quiet words get cut.",
    "Master": "AGC keeps both layers as loud as your dry voice. 'Your usual pitch' is the start value until the engine "
              "has heard a few seconds of you.",
}


class App:
    def __init__(self):
        T.ensure_factory()
        self.s = self._load_settings()
        self.eng = T.Engine(n_fft=int(self.s.get("fft", 2048)))
        self.io = T.AudioIO(self.eng)
        self.io.monitor_vol = float(self.s.get("monitor_vol", 0.7))
        self.presets = T.all_presets()
        self.favs = [f for f in self.s.get("favorites", []) if f in self.presets]
        if not self.favs:
            self.favs = [p for p in ["unknown/game twin", "unknown/weakened clear male", "unknown/weakened clear female",
                                     "unknown/mumbling hunt", "unknown/found footage", "unknown/bad mimic",
                                     "utility/bypass"] if p in self.presets]
        pid = T.resolve_id(self.s.get("preset", "unknown/game twin"), self.presets) or next(iter(self.presets))
        self.cur = pid; self.cur_base = dict(self.presets[pid][0])
        self.eng.load(T.sanitize(self.s["params"]) if self.s.get("params") else self.cur_base, name=pid)
        self.req = queue.Queue()
        self.hist_b = [0.5] * HIST; self.hist_c = [0.0] * HIST; self.hist_x = list(range(HIST))
        self.devs = []; self.last_save = time.time(); self.order = []

    # ---------------- settings ----------------
    def _load_settings(self):
        try:
            with open(SETTINGS, encoding="utf-8") as fh: return json.load(fh)
        except Exception:
            return {}

    def _save_settings(self):
        self.s.update(preset=self.cur, params=self.eng.p, monitor_vol=self.io.monitor_vol, favorites=self.favs,
                      mic=dpg.get_value("dev_in"), out=dpg.get_value("dev_out"), mon=dpg.get_value("dev_mon"),
                      fft=int(dpg.get_value("fft")), latency=dpg.get_value("latency"))
        try:
            with open(SETTINGS, "w", encoding="utf-8") as fh: json.dump(self.s, fh, indent=1)
        except Exception as e:
            print("settings save failed:", e)

    # ---------------- presets ----------------
    def reload_presets(self):
        self.presets = T.all_presets()
        self.favs = [f for f in self.favs if f in self.presets]
        self.build_tree(); self.build_favbar()

    def desc(self, pid):
        return self.presets.get(pid, (None, ""))[1] or ""

    def load_preset(self, pid):
        if pid not in self.presets: return
        self.cur = pid; self.cur_base = dict(self.presets[pid][0])
        self.eng.load(self.cur_base, name=pid)
        self.sync_ui()
        dpg.set_value("cur_name", pid); dpg.set_value("cur_desc", self.desc(pid))
        dpg.set_value("save_name", pid)
        self._mark_star(); self._highlight()
        self.status(f"preset: {pid}")

    def sync_ui(self):
        p = self.eng.p
        for s in T.SPEC:
            if dpg.does_item_exist(s["key"]): dpg.set_value(s["key"], p[s["key"]])
        self.update_dirty()

    def update_dirty(self):
        p = self.eng.p
        dirty = any(abs(float(p[k]) - float(self.cur_base[k])) > 1e-6 for k in p)
        dpg.set_value("dirty", "* modified" if dirty else "")

    def step(self, d):
        order = self.order or sorted(self.presets)
        i = order.index(self.cur) if self.cur in order else -1
        self.load_preset(order[(i + d) % len(order)])

    def rand(self, *_):
        order = [p for p in (self.order or sorted(self.presets)) if p != self.cur]
        if order: self.load_preset(random.choice(order))

    def toggle_fav(self, pid=None):
        pid = pid or self.cur
        if pid in self.favs: self.favs.remove(pid)
        else: self.favs.append(pid)
        self.build_favbar(); self.build_tree(); self._mark_star(); self._save_settings()

    def _mark_star(self):
        dpg.configure_item("star_btn", label="[*] favorite" if self.cur in self.favs else "[ ] favorite")

    def on_save(self, *_):
        pid = T.safe_id(dpg.get_value("save_name") or "")
        if not pid:
            self.status("type folder/name first, e.g. mine/my twin", err=True); return
        existed = pid in self.presets
        pid, path = T.save_preset(pid, dict(self.eng.p), desc=dpg.get_value("save_desc") or None)
        self.reload_presets()
        self.cur = pid; self.cur_base = dict(self.presets[pid][0]); self.update_dirty()
        dpg.set_value("cur_name", pid); dpg.set_value("cur_desc", self.desc(pid)); self._mark_star(); self._highlight()
        self.status(("overwrote " if existed else "saved ") + os.path.relpath(path, T.HERE))

    def on_delete(self, *_):
        path = T.preset_path(self.cur)
        if not os.path.isfile(path):
            self.status("nothing to delete", err=True); return
        old = self.cur
        try:
            os.remove(path)
        except Exception as e:
            self.status(f"delete failed: {e}", err=True); return
        if old in self.favs: self.favs.remove(old)
        self.reload_presets(); self.load_preset(T.resolve_id("game twin", self.presets) or next(iter(self.presets)))
        self.status(f"deleted {old} (factory presets come back on next start)")

    def on_revert(self, *_):
        self.eng.load(self.cur_base, name=self.cur); self.sync_ui(); self.status(f"reverted to {self.cur}")

    def on_reset_tab(self, sender, app_data, tab):
        for s in T.SPEC:
            if s["tab"] == tab: self.eng.set(s["key"], self.cur_base[s["key"]])
        self.sync_ui()

    def on_param(self, sender, value, key):
        self.eng.set(key, value); self.update_dirty()

    def toggle_clear(self, *_):
        self.eng.manual_clear = not self.eng.manual_clear
        dpg.configure_item("btn_clear", label="CLEAR: ON" if self.eng.manual_clear else "CLEAR: off")
        dpg.bind_item_theme("btn_clear", self.th_clear_on if self.eng.manual_clear else 0)

    # ---------------- browser ----------------
    def build_tree(self, *_):
        if not dpg.does_item_exist("tree"): return
        dpg.delete_item("tree", children_only=True)
        q = (dpg.get_value("search") or "").strip().lower()
        fav_only = dpg.get_value("fav_only")
        folders = {}
        for pid in sorted(self.presets):
            if fav_only and pid not in self.favs: continue
            if q and q not in pid.lower() and q not in self.desc(pid).lower(): continue
            folder, _, name = pid.rpartition("/")
            folders.setdefault(folder or "(root)", []).append((name, pid))
        order_f = sorted(folders, key=lambda f: (f != "unknown", f))
        self.order = [pid for f in order_f for _, pid in folders[f]]
        dpg.set_value("count", f"{len(self.order)} / {len(self.presets)} presets")
        for f in order_f:
            with dpg.tree_node(label=f"{f}  ({len(folders[f])})", parent="tree", default_open=True):
                for name, pid in folders[f]:
                    with dpg.group(horizontal=True):
                        b = dpg.add_button(label="*" if pid in self.favs else ".", width=22, user_data=pid,
                                           callback=lambda s, a, u: self.toggle_fav(u))
                        if pid in self.favs: dpg.bind_item_theme(b, self.th_fav)
                        sel = dpg.add_selectable(label=name, tag=f"sel::{pid}", default_value=(pid == self.cur),
                                                 user_data=pid, callback=lambda s, a, u: self.load_preset(u), width=230)
                        d = self.desc(pid)
                        if d:
                            with dpg.tooltip(sel): dpg.add_text(d, wrap=320)

    def _highlight(self):
        for pid in self.presets:
            tag = f"sel::{pid}"
            if dpg.does_item_exist(tag): dpg.set_value(tag, pid == self.cur)

    def build_favbar(self):
        if not dpg.does_item_exist("favbar"): return
        dpg.delete_item("favbar", children_only=True)
        dpg.add_text("FAVORITES", color=ACCENT, parent="favbar")
        if not self.favs:
            dpg.add_text("(click . next to a preset to star it)", color=MUTED, parent="favbar"); return
        for i, pid in enumerate(self.favs[:12]):
            lab = (f"{i + 1} " if i < 9 else "") + pid.split("/")[-1]
            b = dpg.add_button(label=lab, parent="favbar", height=28, user_data=pid, callback=lambda s, a, u: self.load_preset(u))
            with dpg.tooltip(b): dpg.add_text(pid + (f"\nCtrl+Alt+{i + 1}" if i < 9 else "") + ("\n" + self.desc(pid) if self.desc(pid) else ""), wrap=320)

    # ---------------- audio ----------------
    def _dev_label(self, d):
        if dpg.does_item_exist("all_apis") and dpg.get_value("all_apis"):
            return f"{d['name']}  [{d['api'].replace('Windows ', '')}]"
        return d["name"]

    def refresh_devices(self, *_):
        try:
            self.devs = T.list_devices(wasapi_only=not dpg.get_value("all_apis"))
        except Exception as e:
            self.devs = []; self.status(f"device list failed: {e}", err=True)
        ins = [self._dev_label(d) for d in self.devs if d["ins"] > 0]
        outs = [self._dev_label(d) for d in self.devs if d["outs"] > 0]
        dpg.configure_item("dev_in", items=ins); dpg.configure_item("dev_out", items=outs)
        dpg.configure_item("dev_mon", items=["(none)"] + outs)

        def pick(tag, items, saved, guess):
            cur = dpg.get_value(tag) or saved
            if cur in items: dpg.set_value(tag, cur); return
            for it in items:
                if guess and guess.lower() in it.lower(): dpg.set_value(tag, it); return
            if items: dpg.set_value(tag, items[0])
        pick("dev_in", ins, self.s.get("mic"), "Microphone")
        pick("dev_out", outs, self.s.get("out"), "CABLE Input")
        pick("dev_mon", ["(none)"] + outs, self.s.get("mon"), None)

    def _dev_index(self, label):
        for d in self.devs:
            if self._dev_label(d) == label: return d["index"]
        return None

    def on_start(self, *_):
        try:
            fft = int(dpg.get_value("fft"))
            if fft != self.eng.N:
                self.io.stop()
                p, nm, mc = dict(self.eng.p), self.eng.preset_name, self.eng.manual_clear
                self.eng = T.Engine(n_fft=fft); self.eng.load(p, name=nm); self.eng.manual_clear = mc; self.io.eng = self.eng
            mon = dpg.get_value("dev_mon")
            info = self.io.start(self._dev_index(dpg.get_value("dev_in")), self._dev_index(dpg.get_value("dev_out")),
                                 None if mon in (None, "", "(none)") else self._dev_index(mon), hops=2,
                                 latency=dpg.get_value("latency"))
            self.status("running: " + info); self._save_settings()
        except Exception as e:
            traceback.print_exc(); self.status(f"audio start failed: {e}", err=True)

    def on_stop(self, *_):
        self.io.stop(); self.status("stopped")

    # ---------------- hotkeys ----------------
    def start_hotkeys(self):
        try:
            from pynput import keyboard
            hk = {f"<ctrl>+<alt>+{i + 1}": (lambda i=i: self.req.put(("fav", i))) for i in range(9)}
            hk["<ctrl>+<alt>+0"] = lambda: self.req.put(("id", "utility/bypass"))
            hk["<ctrl>+<alt>+<right>"] = lambda: self.req.put(("step", 1))
            hk["<ctrl>+<alt>+<left>"] = lambda: self.req.put(("step", -1))
            hk["<ctrl>+<alt>+r"] = lambda: self.req.put(("rand", 0))
            hk["<ctrl>+<alt>+c"] = lambda: self.req.put(("clear", 0))
            keyboard.GlobalHotKeys(hk).start()
            return "Ctrl+Alt+1..9 favorites | 0 bypass | Left/Right | R random | C clear mode"
        except Exception as e:
            return f"(global hotkeys unavailable: {e})"

    def poll_requests(self):
        while not self.req.empty():
            kind, v = self.req.get_nowait()
            if kind == "fav" and v < len(self.favs): self.load_preset(self.favs[v])
            elif kind == "id": self.load_preset(v)
            elif kind == "step": self.step(v)
            elif kind == "rand": self.rand()
            elif kind == "clear": self.toggle_clear()

    # ---------------- UI ----------------
    def status(self, msg, err=False):
        dpg.set_value("status", msg); dpg.configure_item("status", color=BAD if err else GOOD)

    def _widget(self, s, width):
        if s["kind"] == "b":
            dpg.add_checkbox(label=s["label"], tag=s["key"], default_value=bool(self.eng.p[s["key"]]),
                             callback=self.on_param, user_data=s["key"])
        else:
            dpg.add_slider_float(label=s["label"], tag=s["key"], default_value=float(self.eng.p[s["key"]]),
                                 min_value=float(s["lo"]), max_value=float(s["hi"]), format=s["fmt"], width=width,
                                 callback=self.on_param, user_data=s["key"])
            with dpg.tooltip(s["key"]):
                dpg.add_text(f"{s['key']}   range {s['lo']:g} .. {s['hi']:g}   default {s['default']:g}\n"
                             + (s["help"] + "\n" if s["help"] else "") + "Ctrl+click to type a value")

    def _param_page(self, tab):
        specs = [s for s in T.SPEC if s["tab"] == tab]
        bools = [s for s in specs if s["kind"] == "b"]
        dpg.add_spacer(height=4)
        if bools:
            with dpg.group(horizontal=True):
                for s in bools: self._widget(s, 0)
        for s in specs:
            if s["kind"] != "b": self._widget(s, 430)
        dpg.add_spacer(height=6)
        dpg.add_button(label=f"Reset {tab} to preset", callback=self.on_reset_tab, user_data=tab)
        dpg.add_text(TAB_HELP.get(tab, ""), wrap=640, color=MUTED)

    def build(self):
        dpg.create_context()
        self._theme()
        dpg.create_viewport(title="Unknown Twin - live", width=1560, height=960, min_width=1200, min_height=700)
        with dpg.window(tag="main", no_scrollbar=True):
            with dpg.group(horizontal=True):
                dpg.add_button(label="<", width=28, callback=lambda: self.step(-1))
                dpg.add_button(label=">", width=28, callback=lambda: self.step(1))
                dpg.add_button(label="random", callback=self.rand)
                dpg.add_button(label="[ ] favorite", tag="star_btn", callback=lambda: self.toggle_fav())
                dpg.add_text(self.cur, tag="cur_name", color=ACCENT)
                dpg.add_text("", tag="dirty", color=ACCENT)
                dpg.add_text(self.desc(self.cur), tag="cur_desc", color=MUTED)
            with dpg.group(horizontal=True):
                dpg.add_input_text(tag="save_name", hint="folder/name  e.g. mine/my twin", width=300, default_value=self.cur)
                dpg.add_input_text(tag="save_desc", hint="description (optional)", width=320)
                dpg.add_button(label="Save", callback=self.on_save)
                dpg.add_button(label="Delete", callback=self.on_delete)
                dpg.add_button(label="Revert", callback=self.on_revert)
                dpg.add_button(label="Rescan presets", callback=lambda: (self.reload_presets(), self.status("presets rescanned")))
            dpg.add_group(horizontal=True, tag="favbar")
            dpg.add_separator()
            with dpg.group(horizontal=True):
                with dpg.child_window(width=310, height=-26):
                    dpg.add_input_text(tag="search", hint="search presets...", width=-1, callback=self.build_tree)
                    with dpg.group(horizontal=True):
                        dpg.add_checkbox(label="favorites only", tag="fav_only", callback=self.build_tree)
                        dpg.add_text("", tag="count", color=MUTED)
                    dpg.add_child_window(tag="tree", height=-1, border=False)
                with dpg.child_window(width=-400, height=-26):
                    with dpg.tab_bar(tag="tabs"):
                        for top, subs in LAYOUT:
                            with dpg.tab(label=top, tag=f"tab_{top}"):
                                if len(subs) == 1:
                                    self._param_page(subs[0])
                                else:
                                    with dpg.tab_bar(tag=f"sub_{top}"):
                                        for sub in subs:
                                            with dpg.tab(label=sub, tag=f"tab_{sub}"):
                                                self._param_page(sub)
                # ---------- right: audio (always visible) + meters
                with dpg.child_window(width=-1, height=-26):
                    with dpg.group(horizontal=True):
                        dpg.add_text("AUDIO", color=ACCENT)
                        dpg.add_text("stopped", tag="run_state", color=BAD)
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="START / APPLY", tag="btn_start", width=150, height=34, callback=self.on_start)
                        dpg.add_button(label="STOP", width=80, height=34, callback=self.on_stop)
                        dpg.add_button(label="Rescan", width=-1, height=34, callback=self.refresh_devices)
                    dpg.add_button(label="CLEAR: off", tag="btn_clear", width=-1, height=34, callback=self.toggle_clear)
                    with dpg.tooltip("btn_clear"):
                        dpg.add_text("Weakened mode: one voice almost clean. Ctrl+Alt+C toggles it from inside the game.")
                    dpg.add_text("Mic", color=MUTED); dpg.add_combo([], tag="dev_in", width=-1)
                    dpg.add_text("Output (-> Discord)", color=MUTED); dpg.add_combo([], tag="dev_out", width=-1)
                    dpg.add_text("Monitor (hear yourself)", color=MUTED); dpg.add_combo([], tag="dev_mon", width=-1)
                    dpg.add_slider_float(label="Monitor vol", tag="monvol", default_value=self.io.monitor_vol,
                                         min_value=0, max_value=1.5, width=220,
                                         callback=lambda s, v: setattr(self.io, "monitor_vol", float(v)))
                    with dpg.group(horizontal=True):
                        dpg.add_combo(["2048", "1024"], tag="fft", default_value=str(self.s.get("fft", 2048)), label="FFT", width=70)
                        dpg.add_combo(["low", "high"], tag="latency", default_value=self.s.get("latency", "low"), label="Buffer", width=70)
                    dpg.add_checkbox(label="show non-WASAPI devices", tag="all_apis", callback=self.refresh_devices)
                    with dpg.tooltip("btn_start"):
                        dpg.add_text("Starts audio, or restarts it with the devices / FFT / buffer picked below.\n"
                                     "Discord: Input Device = CABLE Output (VB-Audio Virtual Cable), Noise Suppression None,\n"
                                     "Echo Cancellation off, AGC off, Input Sensitivity manual/low.")
                    dpg.add_separator()
                    dpg.add_text("METERS", color=ACCENT)
                    for tag, lab in [("m_in", "IN"), ("m_out", "OUT"), ("m_gate", "GATE")]:
                        with dpg.group(horizontal=True):
                            dpg.add_text(f"{lab:<6}")
                            dpg.add_progress_bar(tag=tag, width=-1, overlay="")
                    with dpg.group(horizontal=True):
                        dpg.add_text("M <> F", color=FEM_C)
                        dpg.add_progress_bar(tag="m_bal", width=-1, overlay="")
                    with dpg.group(horizontal=True):
                        dpg.add_text("CLEAR ", color=CLEAR_C)
                        dpg.add_progress_bar(tag="m_clear", width=-1, overlay="")
                    dpg.add_text("", tag="m_info"); dpg.add_text("", tag="m_info2")
                    with dpg.plot(height=140, width=-1, no_menus=True, no_box_select=True, no_mouse_pos=True):
                        dpg.add_plot_axis(dpg.mvXAxis, no_tick_labels=True, tag="px")
                        with dpg.plot_axis(dpg.mvYAxis, tag="py"):
                            dpg.add_line_series(self.hist_x, self.hist_b, tag="ser_b", label="female share")
                            dpg.add_line_series(self.hist_x, self.hist_c, tag="ser_c", label="clear")
                        dpg.set_axis_limits("py", 0, 1.05); dpg.set_axis_limits("px", 0, HIST - 1)
                    dpg.add_text("0 = male only, 1 = female only", color=MUTED)
                    dpg.add_text(self.start_hotkeys(), wrap=300, color=MUTED)
            dpg.add_text("ready", tag="status", color=GOOD)
        for tag, col in (("ser_b", FEM_C), ("ser_c", CLEAR_C)):
            with dpg.theme() as th:
                with dpg.theme_component(dpg.mvLineSeries):
                    dpg.add_theme_color(dpg.mvPlotCol_Line, col, category=dpg.mvThemeCat_Plots)
            dpg.bind_item_theme(tag, th)
        with dpg.theme() as th:
            with dpg.theme_component(dpg.mvProgressBar):
                dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, FEM_C); dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (40, 70, 120))
        dpg.bind_item_theme("m_bal", th)
        self.build_tree(); self.build_favbar(); self._mark_star()
        self.refresh_devices(); self.update_dirty()
        dpg.setup_dearpygui(); dpg.show_viewport(); dpg.set_primary_window("main", True)

    def _theme(self):
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (12, 13, 18))
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (18, 20, 28))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (32, 36, 48))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (44, 52, 72))
                dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, FEM_C)
                dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, ACCENT)
                dpg.add_theme_color(dpg.mvThemeCol_Button, (36, 50, 80))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (60, 80, 130))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (90, 110, 170))
                dpg.add_theme_color(dpg.mvThemeCol_Tab, (30, 36, 54))
                dpg.add_theme_color(dpg.mvThemeCol_TabHovered, (60, 80, 130))
                dpg.add_theme_color(dpg.mvThemeCol_TabActive, (50, 66, 110))
                dpg.add_theme_color(dpg.mvThemeCol_Header, (50, 66, 110))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (60, 80, 130))
                dpg.add_theme_color(dpg.mvThemeCol_CheckMark, ACCENT)
                dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, GOOD)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
                dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 4)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 10, 8)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 5)
        dpg.bind_theme(t)
        with dpg.theme() as self.th_fav:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Text, ACCENT)
                dpg.add_theme_color(dpg.mvThemeCol_Button, (80, 55, 10))
        with dpg.theme() as self.th_clear_on:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Text, (10, 10, 10))
                dpg.add_theme_color(dpg.mvThemeCol_Button, (235, 235, 235))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (255, 255, 255))
        for path in (r"C:\Windows\Fonts\segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            if os.path.isfile(path):
                try:
                    with dpg.font_registry():
                        f = dpg.add_font(path, 17)
                    dpg.bind_font(f); break
                except Exception as e:
                    print("font load failed:", e)

    # ---------------- frame update ----------------
    def tick(self):
        self.poll_requests()
        st = self.eng.stats

        def bar(tag, db, lo=-70.0):
            dpg.set_value(tag, float(np.clip((db - lo) / -lo, 0, 1))); dpg.configure_item(tag, overlay=f"{db:5.1f} dB")
        bar("m_in", st["in_db"]); bar("m_out", st["out_db"])
        dpg.set_value("m_gate", st["gate"]); dpg.configure_item("m_gate", overlay="open" if st["gate"] > 0.5 else "closed")
        b = float(st["balance"]); c = float(st["clear"])
        fs = float(np.sin(b * np.pi / 2) ** 2)                 # power share of the female layer
        dpg.set_value("m_bal", fs); dpg.configure_item("m_bal", overlay=f"M {100 - fs * 100:3.0f}%  /  F {fs * 100:3.0f}%")
        who = "female" if st.get("clear_fem") else "male"
        src = "button" if self.eng.manual_clear else ("phrase" if st.get("phrase_clear") else ("slider" if self.eng.p["clear.amount"] > 0 else ""))
        dpg.set_value("m_clear", c); dpg.configure_item("m_clear", overlay=f"{c * 100:3.0f}%  {who if c > 0.05 else ''} {src if c > 0.05 else ''}")
        self.hist_b = self.hist_b[1:] + [fs]; self.hist_c = self.hist_c[1:] + [c]
        dpg.set_value("ser_b", [self.hist_x, self.hist_b]); dpg.set_value("ser_c", [self.hist_x, self.hist_c])
        dpg.set_value("run_state", "RUNNING" if self.io.running else "stopped")
        dpg.configure_item("run_state", color=GOOD if self.io.running else BAD)
        dpg.set_value("m_info", f"cpu {st['load'] * 100:3.0f}%   xruns {st['xruns']}")
        dpg.set_value("m_info2", f"latency ~{self.io.latency_s * 1000:.0f} ms   pitch ~{self.eng.f0_med:.0f} Hz")
        if time.time() - self.last_save > 10:
            self.last_save = time.time(); self._save_settings()

    def run(self):
        self.build()
        if self.s.get("autostart", True) and self.s.get("out"):
            self.on_start()
        while dpg.is_dearpygui_running():
            self.tick()
            dpg.render_dearpygui_frame()
        self._save_settings(); self.io.stop(); dpg.destroy_context()


if __name__ == "__main__":
    App().run()
