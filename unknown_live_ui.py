"""
unknown_live_ui.py v2.1 — GUI for the live voice engine (unknown_live.py), Dear PyGui.

  * Preset browser: folder tree (presets\\<folder>\\<name>.json, any depth), search (name + description),
    favorites (star [*]) with a "favorites only" filter; click = load, all sliders jump to the preset
  * Top bar: current preset + description, prev / next / random, star, Save as "folder/name", Delete, Revert
  * Favorites bar: first 9 favorites on Ctrl+Alt+1..9 (global hotkeys); Ctrl+Alt+0 bypass,
    Ctrl+Alt+Left/Right prev/next, Ctrl+Alt+R random
  * Tabs: Voices (one sub-tab per voice), Mix, FX (Mod, Glitch, Space, Radio), Gate & Master
  * Right column (always visible): START/STOP, mic / output / monitor, FFT, buffer; then meters (in / out / gate / freeze), live voice mix bars + plot, CPU / xruns / latency / pitch
  * Remembers devices, favorites, last preset and your last slider state in live_settings.json

Deps:  pip install dearpygui numpy pedalboard sounddevice pynput soundfile   (+ VB-Cable)
Usage: python unknown_live_ui.py        (or run_live_ui.bat)
Tip:   Ctrl+click a slider to type an exact value. Factory presets: python make_presets.py (re)creates them.
Generic recipes, NOT reverse-engineered game presets. Personal/fan use only.
"""
import json, os, queue, random, time, traceback
import numpy as np
import dearpygui.dearpygui as dpg
import unknown_live as L

SETTINGS = os.path.join(L.HERE, "live_settings.json")
VOICE_COLORS = dict(beast=(214, 69, 65), fem=(232, 121, 249), glide=(250, 176, 5), robot=(56, 189, 248),
                    whisper=(148, 163, 184), demon=(153, 27, 27), child=(253, 186, 116), human=(74, 222, 128))
ACCENT = (250, 176, 5); MUTED = (148, 163, 184); GOOD = (163, 230, 53); BAD = (248, 113, 113)
HIST = 150
# layout of the parameter area: (top tab, [sub-tabs = SPEC tab names])
LAYOUT = [("Voices", [v.capitalize() for v in L.VOICES]), ("Mix", ["Mix"]),
          ("FX", ["Mod", "Glitch", "Space", "Radio"]), ("Gate & Master", ["Gate", "Master"])]
TAB_HELP = {
    "Mix": "How the voices are blended over time. Morph speed = how often the random mix changes; Hard switching = "
           "soft crossfades (0) vs one voice at a time (1). Radio amount crossfades into FX > Radio.",
    "Beast": "Low monster voice. Pitch target = where your median pitch is pushed. Formant < 1 = bigger head.",
    "Fem": "Robotic female voice. Autotune snap quantizes to semitones. Formant > 1 = smaller head. Comb + bitcrush = synthetic.",
    "Glide": "One voice sliding continuously between the low and the high end (pitch, formants and breath together).",
    "Robot": "Monotone: every syllable on one flat note, plus ring modulation.",
    "Whisper": "Pure breath version of your voice (no pitch).",
    "Demon": "Very low, very distorted. Great under other voices.",
    "Child": "High and small. Chipmunk at extreme settings.",
    "Human": "Your own (clean) voice inside the mix.",
    "Mod": "Vibrato / jitter move the pitch of all shifted voices; formant wobble moves their formants; tremolo and "
           "phaser act on the whole mix.",
    "Glitch": "Reverse chunks play the last bit of speech backwards; freeze holds one spectrum (a vowel); master "
              "bitcrush / downsample make it lo-fi.",
    "Space": "Echo and reverb come AFTER the gate, so their tails ring into the pauses.",
    "Radio": "Broken-transmission FX used when Mix > Radio amount > 0: band-pass, drive, bitcrush, random dropouts.",
    "Gate": "Silences everything between words. Raise the threshold if room noise opens it, lower it if quiet words get cut.",
    "Master": "AGC keeps every voice as loud as your dry voice. Input gain, 3-band EQ, compressor, makeup and output gain.",
}


class App:
    def __init__(self):
        self.s = self._load_settings()
        self.eng = L.Engine(n_fft=int(self.s.get("fft", 2048)))
        self.io = L.AudioIO(self.eng)
        self.io.monitor_vol = float(self.s.get("monitor_vol", 0.7))
        self.presets = L.all_presets(visible_only=True)
        self.favs = [f for f in (L.resolve_id(x, self.presets) for x in self.s.get("favorites", [])) if f]
        if not self.favs:
            self.favs = [p for p in ["unknown/morph classic", "unknown/glide classic", "unknown/radio classic",
                                     "unknown/whisper stalker", "unknown/hunting whisper", "built-in/bypass"] if p in self.presets]
        pid = L.resolve_id(self.s.get("preset", "unknown/morph classic"), self.presets) or next(iter(self.presets))
        self.cur = pid; self.cur_base = dict(self.presets[pid][0])
        self.eng.load(L.sanitize(self.s["params"]) if self.s.get("params") else self.cur_base, name=pid)
        self.req = queue.Queue()
        self.hist_w = np.zeros((len(L.VOICES), HIST)); self.hist_x = list(range(HIST))
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
        self.presets = L.all_presets(visible_only=True)
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
        dpg.set_value("save_name", "" if pid.startswith("built-in/") else pid)
        self._mark_star(); self._highlight()
        self.status(f"preset: {pid}")

    def sync_ui(self):
        for s in L.SPEC:
            if dpg.does_item_exist(s["key"]): dpg.set_value(s["key"], self.eng.p[s["key"]])
        self.update_dirty()

    def update_dirty(self):
        dirty = any(abs(float(self.eng.p[k]) - float(self.cur_base[k])) > 1e-6 for k in self.eng.p)
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
        pid = L.safe_id(dpg.get_value("save_name") or "")
        if not pid:
            self.status("type folder/name first, e.g. unknown/my voice", err=True); return
        if pid.startswith("built-in"):
            self.status("'built-in' is reserved, pick another folder", err=True); return
        existed = pid in self.presets
        desc = dpg.get_value("save_desc") or None
        pid, path = L.save_preset(pid, dict(self.eng.p), desc=desc)
        self.reload_presets()
        self.cur = pid; self.cur_base = dict(self.eng.p); self.update_dirty()
        dpg.set_value("cur_name", pid); dpg.set_value("cur_desc", self.desc(pid)); self._mark_star(); self._highlight()
        self.status(("overwrote " if existed else "saved ") + os.path.relpath(path, L.HERE))

    def on_delete(self, *_):
        if self.cur.startswith("built-in/"):
            self.status("built-in presets can't be deleted", err=True); return
        old = self.cur
        if L.delete_preset(old):
            if old in self.favs: self.favs.remove(old)
            self.reload_presets(); self.load_preset(L.resolve_id("morph", self.presets) or "built-in/bypass")
            self.status(f"deleted {old}")

    def on_revert(self, *_):
        self.eng.load(self.cur_base, name=self.cur); self.sync_ui(); self.status(f"reverted to {self.cur}")

    def on_reset_tab(self, sender, app_data, tab):
        for s in L.SPEC:
            if s["tab"] == tab: self.eng.set(s["key"], self.cur_base[s["key"]])
        self.sync_ui()

    def on_param(self, sender, value, key):
        self.eng.set(key, value); self.update_dirty()

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
        order_f = sorted(folders, key=lambda f: (f != "unknown", f.startswith("built-in"), f))
        self.order = [pid for f in order_f for _, pid in folders[f]]
        dpg.set_value("count", f"{len(self.order)} / {len(self.presets)} presets")
        for f in order_f:
            with dpg.tree_node(label=f"{f}  ({len(folders[f])})", parent="tree",
                               default_open=bool(q) or fav_only or f == "unknown" or self.cur.startswith(f + "/")):
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
            self.devs = L.list_devices(wasapi_only=not dpg.get_value("all_apis"))
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
                p, nm = dict(self.eng.p), self.eng.preset_name
                self.eng = L.Engine(n_fft=fft); self.eng.load(p, name=nm); self.io.eng = self.eng
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
            hk["<ctrl>+<alt>+0"] = lambda: self.req.put(("id", "built-in/bypass"))
            hk["<ctrl>+<alt>+<right>"] = lambda: self.req.put(("step", 1))
            hk["<ctrl>+<alt>+<left>"] = lambda: self.req.put(("step", -1))
            hk["<ctrl>+<alt>+r"] = lambda: self.req.put(("rand", 0))
            keyboard.GlobalHotKeys(hk).start()
            return "Ctrl+Alt+1..9 favorites | 0 bypass | Left/Right prev/next | R random"
        except Exception as e:
            return f"(global hotkeys unavailable: {e})"

    def poll_requests(self):
        while not self.req.empty():
            kind, v = self.req.get_nowait()
            if kind == "fav" and v < len(self.favs): self.load_preset(self.favs[v])
            elif kind == "id": self.load_preset(v)
            elif kind == "step": self.step(v)
            elif kind == "rand": self.rand()

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
                dpg.add_text(f"{s['key']}   range {s['lo']:g} .. {s['hi']:g}   default {s['default']:g}\nCtrl+click to type a value")

    def _param_page(self, tab):
        specs = [s for s in L.SPEC if s["tab"] == tab]
        bools = [s for s in specs if s["kind"] == "b"]
        dpg.add_spacer(height=4)
        if bools:
            with dpg.group(horizontal=True):
                for s in bools: self._widget(s, 0)
        for s in specs:
            if s["kind"] != "b": self._widget(s, 430)
        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True):
            dpg.add_button(label=f"Reset {tab} to preset", callback=self.on_reset_tab, user_data=tab)
        dpg.add_text(TAB_HELP.get(tab, ""), wrap=640, color=MUTED)

    def build(self):
        dpg.create_context()
        self._theme()
        dpg.create_viewport(title="Unknown Voice - live", width=1560, height=960, min_width=1200, min_height=700)
        with dpg.window(tag="main", no_scrollbar=True):
            # ---------- top bar
            with dpg.group(horizontal=True):
                dpg.add_button(label="<", width=28, callback=lambda: self.step(-1))
                dpg.add_button(label=">", width=28, callback=lambda: self.step(1))
                dpg.add_button(label="random", callback=self.rand)
                dpg.add_button(label="[ ] favorite", tag="star_btn", callback=lambda: self.toggle_fav())
                dpg.add_text(self.cur, tag="cur_name", color=ACCENT)
                dpg.add_text("", tag="dirty", color=ACCENT)
                dpg.add_text(self.desc(self.cur), tag="cur_desc", color=MUTED)
            with dpg.group(horizontal=True):
                dpg.add_input_text(tag="save_name", hint="folder/name  e.g. unknown/my voice", width=300,
                                   default_value="" if self.cur.startswith("built-in/") else self.cur)
                dpg.add_input_text(tag="save_desc", hint="description (optional)", width=320)
                dpg.add_button(label="Save", callback=self.on_save)
                dpg.add_button(label="Delete", callback=self.on_delete)
                dpg.add_button(label="Revert", callback=self.on_revert)
                dpg.add_button(label="Rescan presets", callback=lambda: (self.reload_presets(), self.status("presets rescanned")))
            dpg.add_group(horizontal=True, tag="favbar")
            dpg.add_separator()
            with dpg.group(horizontal=True):
                # ---------- left: preset browser
                with dpg.child_window(width=310, height=-26):
                    dpg.add_input_text(tag="search", hint="search presets...", width=-1, callback=self.build_tree)
                    with dpg.group(horizontal=True):
                        dpg.add_checkbox(label="favorites only", tag="fav_only", callback=self.build_tree)
                        dpg.add_text("", tag="count", color=MUTED)
                    dpg.add_child_window(tag="tree", height=-1, border=False)
                # ---------- middle: parameters
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
                                     "Echo Cancellation off, AGC off, Input Sensitivity manual/low.\n"
                                     "FFT 1024 = lower latency, rougher lows. Buffer 'high' if you hear crackles.")
                    dpg.add_separator()
                    dpg.add_text("METERS", color=ACCENT)
                    for tag, lab in [("m_in", "IN"), ("m_out", "OUT"), ("m_gate", "GATE")]:
                        with dpg.group(horizontal=True):
                            dpg.add_text(f"{lab:<5}")
                            dpg.add_progress_bar(tag=tag, width=-1, overlay="")
                    dpg.add_text("", tag="m_info"); dpg.add_text("", tag="m_info2"); dpg.add_text("", tag="m_frz", color=(56, 189, 248))
                    dpg.add_spacer(height=4)
                    dpg.add_text("VOICE MIX", color=ACCENT)
                    for v in L.VOICES:
                        with dpg.group(horizontal=True):
                            dpg.add_text(f"{v:<8}", color=VOICE_COLORS[v])
                            dpg.add_progress_bar(tag=f"w_{v}", width=-1, overlay="")
                    with dpg.plot(height=110, width=-1, no_menus=True, no_box_select=True, no_mouse_pos=True):
                        dpg.add_plot_axis(dpg.mvXAxis, no_tick_labels=True, tag="px")
                        with dpg.plot_axis(dpg.mvYAxis, tag="py"):
                            for v in L.VOICES:
                                dpg.add_line_series(self.hist_x, [0.0] * HIST, tag=f"ser_{v}")
                        dpg.set_axis_limits("py", 0, 1.05); dpg.set_axis_limits("px", 0, HIST - 1)
                    dpg.add_text(self.start_hotkeys(), wrap=300, color=MUTED)
            dpg.add_text("ready", tag="status", color=GOOD)
        for v in L.VOICES:
            with dpg.theme() as th:
                with dpg.theme_component(dpg.mvLineSeries):
                    dpg.add_theme_color(dpg.mvPlotCol_Line, VOICE_COLORS[v], category=dpg.mvThemeCat_Plots)
                with dpg.theme_component(dpg.mvProgressBar):
                    dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, VOICE_COLORS[v])
            dpg.bind_item_theme(f"ser_{v}", th); dpg.bind_item_theme(f"w_{v}", th)
        self.build_tree(); self.build_favbar(); self._mark_star()
        self.refresh_devices(); self.update_dirty()
        dpg.setup_dearpygui(); dpg.show_viewport(); dpg.set_primary_window("main", True)

    def _theme(self):
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (14, 14, 18))
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (20, 20, 26))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (34, 34, 44))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (52, 40, 60))
                dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, (232, 121, 249))
                dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, ACCENT)
                dpg.add_theme_color(dpg.mvThemeCol_Button, (60, 30, 70))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (110, 45, 120))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (160, 60, 170))
                dpg.add_theme_color(dpg.mvThemeCol_Tab, (40, 26, 48))
                dpg.add_theme_color(dpg.mvThemeCol_TabHovered, (110, 45, 120))
                dpg.add_theme_color(dpg.mvThemeCol_TabActive, (90, 38, 100))
                dpg.add_theme_color(dpg.mvThemeCol_Header, (90, 38, 100))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (110, 45, 120))
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
        self._font()

    def _font(self):
        """Use Segoe UI on Windows if present (nicer than the built-in bitmap font)."""
        for path in (r"C:\Windows\Fonts\segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            if os.path.isfile(path):
                try:
                    with dpg.font_registry():
                        f = dpg.add_font(path, 17)
                    dpg.bind_font(f); return
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
        dpg.set_value("m_frz", "FREEZE" if st.get("frozen") else "")
        w = np.asarray(st["weights"], float)
        if len(w) == len(L.VOICES):
            self.hist_w = np.roll(self.hist_w, -1, axis=1); self.hist_w[:, -1] = w
            for i, v in enumerate(L.VOICES):
                dpg.set_value(f"w_{v}", float(w[i])); dpg.configure_item(f"w_{v}", overlay=f"{w[i] * 100:3.0f}%")
                dpg.set_value(f"ser_{v}", [self.hist_x, self.hist_w[i].tolist()])
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
