"""
unknown_live_ui.py v1 — GUI for the live "Unknown" voice engine (unknown_live.py), Dear PyGui.

What it does:
  * every internal engine knob as a slider/checkbox, grouped in tabs (Mix, Beast, Fem, Glide, Robot, Whisper,
    Human, Radio, Gate, Master); changes apply instantly while you talk
  * picking a preset pulls ALL sliders to that preset; "*" marks unsaved changes; Save / Save as / Delete / Revert
    (user presets = presets\\<name>.json next to this script; built-ins: morph, glide, radio, bypass)
  * audio devices (mic / output / monitor), FFT size, buffer latency: switch and restart audio without quitting
  * meters: in / out / gate, live voice-mix plot, CPU, xruns, latency, detected median pitch
  * global hotkeys: Ctrl+Alt+1..9 = quick preset buttons in order, Ctrl+Alt+0 = bypass
  * remembers devices, last preset and your last slider state in live_settings.json

Deps:  pip install dearpygui numpy pedalboard sounddevice pynput soundfile   (+ VB-Cable)
Usage: python unknown_live_ui.py        (or run_live_ui.bat)
Tip:   Ctrl+click a slider to type an exact value.
Generic monster-mimic recipe, NOT a reverse-engineered Behaviour preset. Personal/fan use only.
"""
import json, os, queue, time, traceback
import numpy as np
import dearpygui.dearpygui as dpg
import unknown_live as L

SETTINGS = os.path.join(L.HERE, "live_settings.json")
TABS = ["Mix", "Beast", "Fem", "Glide", "Robot", "Whisper", "Human", "Radio", "Gate", "Master"]
VOICE_COLORS = dict(beast=(214, 69, 65), fem=(232, 121, 249), glide=(250, 176, 5), robot=(56, 189, 248),
                    whisper=(148, 163, 184), human=(74, 222, 128))
HIST = 180          # plot points (~6 s at 30 fps)


class App:
    def __init__(self):
        self.s = self._load_settings()
        self.eng = L.Engine(n_fft=int(self.s.get("fft", 2048)))
        self.io = L.AudioIO(self.eng)
        self.io.monitor_vol = float(self.s.get("monitor_vol", 0.7))
        self.presets = L.all_presets()
        name = self.s.get("preset", "morph")
        if name not in self.presets: name = "morph"
        self.cur_name = name
        self.cur_base = dict(self.presets[name])                  # what "Revert" goes back to
        start_params = L.sanitize(self.s["params"]) if self.s.get("params") else self.cur_base
        self.eng.load(start_params, name=name)
        self.req = queue.Queue()                                  # preset requests from hotkeys
        self.hist_w = np.zeros((len(L.VOICES), HIST)); self.hist_x = list(range(HIST))
        self.devs = []
        self.last_save = time.time()

    # ---------------- settings ----------------
    def _load_settings(self):
        try:
            with open(SETTINGS, encoding="utf-8") as fh: return json.load(fh)
        except Exception:
            return {}

    def _save_settings(self):
        self.s.update(preset=self.cur_name, params=self.eng.p, monitor_vol=self.io.monitor_vol,
                      mic=dpg.get_value("dev_in"), out=dpg.get_value("dev_out"), mon=dpg.get_value("dev_mon"),
                      fft=int(dpg.get_value("fft")), latency=dpg.get_value("latency"))
        try:
            with open(SETTINGS, "w", encoding="utf-8") as fh: json.dump(self.s, fh, indent=1)
        except Exception as e:
            print("settings save failed:", e)

    # ---------------- presets ----------------
    def preset_names(self):
        b = [n for n in L.BUILTIN]; u = sorted(n for n in self.presets if n not in L.BUILTIN)
        return b + u

    def refresh_presets(self, select=None):
        self.presets = L.all_presets()
        names = self.preset_names()
        dpg.configure_item("preset_combo", items=names)
        if select: dpg.set_value("preset_combo", select)
        self._build_quick_buttons()

    def load_preset(self, name):
        if name not in self.presets: return
        self.cur_name = name; self.cur_base = dict(self.presets[name])
        self.eng.load(self.cur_base, name=name)
        self.sync_ui()
        dpg.set_value("preset_combo", name)
        dpg.set_value("save_name", name if name not in L.BUILTIN else "")
        self.status(f"preset: {name}")

    def sync_ui(self):
        for s in L.SPEC:
            if dpg.does_item_exist(s["key"]): dpg.set_value(s["key"], self.eng.p[s["key"]])
        self.update_dirty()

    def update_dirty(self):
        dirty = any(abs(float(self.eng.p[k]) - float(self.cur_base[k])) > 1e-6 for k in self.eng.p)
        dpg.set_value("dirty", "* modified" if dirty else "")

    def on_save(self, sender=None, app_data=None, user_data=None):
        name = L.safe_name(dpg.get_value("save_name") or "")
        if not name:
            self.status("type a preset name first", err=True); return
        if name in L.BUILTIN:
            self.status(f"'{name}' is built-in, pick another name", err=True); return
        nm, path = L.save_preset(name, dict(self.eng.p))
        self.refresh_presets(select=nm)
        self.cur_name = nm; self.cur_base = dict(self.eng.p); self.update_dirty()
        self.status(f"saved {os.path.relpath(path, L.HERE)}")

    def on_delete(self, *_):
        nm = dpg.get_value("preset_combo")
        if nm in L.BUILTIN:
            self.status("built-in presets can't be deleted", err=True); return
        if L.delete_preset(nm):
            self.refresh_presets(select="morph"); self.load_preset("morph"); self.status(f"deleted {nm}")

    def on_revert(self, *_):
        self.eng.load(self.cur_base, name=self.cur_name); self.sync_ui(); self.status(f"reverted to {self.cur_name}")

    def on_reset_tab(self, sender, app_data, tab):
        for s in L.SPEC:
            if s["tab"] == tab: self.eng.set(s["key"], self.cur_base[s["key"]])
        self.sync_ui()

    def on_param(self, sender, value, key):
        self.eng.set(key, value); self.update_dirty()

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
            if fft != self.eng.N:                         # FFT size needs a new engine; keep params
                self.io.stop()
                p, nm = dict(self.eng.p), self.eng.preset_name
                self.eng = L.Engine(n_fft=fft); self.eng.load(p, name=nm); self.io.eng = self.eng
            lat = dpg.get_value("latency")
            mon = dpg.get_value("dev_mon")
            info = self.io.start(self._dev_index(dpg.get_value("dev_in")), self._dev_index(dpg.get_value("dev_out")),
                                 None if mon in (None, "", "(none)") else self._dev_index(mon), hops=2, latency=lat)
            self.status("running: " + info)
            self._save_settings()
        except Exception as e:
            traceback.print_exc(); self.status(f"audio start failed: {e}", err=True)

    def on_stop(self, *_):
        self.io.stop(); self.status("stopped")

    def on_monvol(self, sender, v, _):
        self.io.monitor_vol = float(v)

    # ---------------- hotkeys ----------------
    def start_hotkeys(self):
        try:
            from pynput import keyboard
            hk = {f"<ctrl>+<alt>+{i + 1}": (lambda i=i: self.req.put(("quick", i))) for i in range(9)}
            hk["<ctrl>+<alt>+0"] = lambda: self.req.put(("name", "bypass"))
            keyboard.GlobalHotKeys(hk).start()
            return "Hotkeys: Ctrl+Alt+1..9 = quick presets, Ctrl+Alt+0 = bypass"
        except Exception as e:
            return f"(global hotkeys unavailable: {e})"

    def poll_requests(self):
        while not self.req.empty():
            kind, v = self.req.get_nowait()
            names = self.preset_names()
            if kind == "quick" and v < len(names): self.load_preset(names[v])
            elif kind == "name": self.load_preset(v)

    # ---------------- UI ----------------
    def status(self, msg, err=False):
        dpg.set_value("status", msg); dpg.configure_item("status", color=(248, 113, 113) if err else (163, 230, 53))

    def _build_quick_buttons(self):
        if dpg.does_item_exist("quick_row"): dpg.delete_item("quick_row", children_only=True)
        else: return
        for i, nm in enumerate(self.preset_names()[:9]):
            b = dpg.add_button(label=f"{i + 1}  {nm}", parent="quick_row", width=110, height=34,
                               callback=lambda s, a, u: self.load_preset(u), user_data=nm)
            with dpg.tooltip(b): dpg.add_text(f"Ctrl+Alt+{i + 1}")

    def _slider(self, s):
        if s["kind"] == "b":
            dpg.add_checkbox(label=s["label"], tag=s["key"], default_value=bool(self.eng.p[s["key"]]),
                             callback=self.on_param, user_data=s["key"])
        else:
            dpg.add_slider_float(label=s["label"], tag=s["key"], default_value=float(self.eng.p[s["key"]]),
                                 min_value=float(s["lo"]), max_value=float(s["hi"]), format=s["fmt"], width=520,
                                 callback=self.on_param, user_data=s["key"])
            with dpg.tooltip(s["key"]):
                dpg.add_text(f"{s['key']}   range {s['lo']:g} .. {s['hi']:g}   default {s['default']:g}\nCtrl+click to type a value")

    def build(self):
        dpg.create_context()
        self._theme()
        dpg.create_viewport(title="Unknown Voice - live", width=1360, height=900, min_width=1000, min_height=640)
        with dpg.window(tag="main"):
            # --- top bar: presets
            with dpg.group(horizontal=True):
                dpg.add_text("PRESET", color=(250, 176, 5))
                dpg.add_combo(self.preset_names(), tag="preset_combo", default_value=self.cur_name, width=200,
                              callback=lambda s, a: self.load_preset(a))
                dpg.add_text("", tag="dirty", color=(250, 176, 5))
                dpg.add_spacer(width=12)
                dpg.add_input_text(tag="save_name", hint="new preset name", width=180,
                                   default_value="" if self.cur_name in L.BUILTIN else self.cur_name)
                dpg.add_button(label="Save", callback=self.on_save)
                dpg.add_button(label="Delete", callback=self.on_delete)
                dpg.add_button(label="Revert", callback=self.on_revert)
            dpg.add_group(horizontal=True, tag="quick_row")
            dpg.add_separator()
            with dpg.group(horizontal=True):
                # --- left column: audio + meters
                with dpg.child_window(width=430, height=-28):
                    dpg.add_text("AUDIO", color=(250, 176, 5))
                    dpg.add_combo([], tag="dev_in", label="Mic", width=330)
                    dpg.add_combo([], tag="dev_out", label="Output", width=330)
                    dpg.add_combo([], tag="dev_mon", label="Monitor", width=330)
                    dpg.add_checkbox(label="show non-WASAPI devices", tag="all_apis", callback=self.refresh_devices)
                    dpg.add_slider_float(label="Monitor vol", tag="monvol", default_value=self.io.monitor_vol,
                                         min_value=0, max_value=1.5, width=300, callback=self.on_monvol)
                    with dpg.group(horizontal=True):
                        dpg.add_combo(["2048", "1024"], tag="fft", default_value=str(self.s.get("fft", 2048)),
                                      label="FFT", width=80)
                        dpg.add_combo(["low", "high"], tag="latency", default_value=self.s.get("latency", "low"),
                                      label="Buffer", width=80)
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="START / APPLY", width=150, height=32, callback=self.on_start)
                        dpg.add_button(label="STOP", width=80, height=32, callback=self.on_stop)
                        dpg.add_button(label="Rescan", width=80, height=32, callback=self.refresh_devices)
                    dpg.add_separator()
                    dpg.add_text("METERS", color=(250, 176, 5))
                    for tag, lab in [("m_in", "IN"), ("m_out", "OUT"), ("m_gate", "GATE")]:
                        with dpg.group(horizontal=True):
                            dpg.add_text(f"{lab:<5}")
                            dpg.add_progress_bar(tag=tag, width=320, overlay="")
                    dpg.add_text("", tag="m_info")
                    dpg.add_text("", tag="m_info2")
                    dpg.add_spacer(height=4)
                    dpg.add_text("VOICE MIX (live)", color=(250, 176, 5))
                    for v in L.VOICES:
                        with dpg.group(horizontal=True):
                            dpg.add_text(f"{v:<8}", color=VOICE_COLORS[v])
                            dpg.add_progress_bar(tag=f"w_{v}", width=290, overlay="")
                    with dpg.plot(height=150, width=-1, no_menus=True, no_box_select=True, no_mouse_pos=True):
                        dpg.add_plot_axis(dpg.mvXAxis, no_tick_labels=True, tag="px")
                        with dpg.plot_axis(dpg.mvYAxis, tag="py"):
                            for v in L.VOICES:
                                dpg.add_line_series(self.hist_x, [0.0] * HIST, tag=f"ser_{v}", label=v)
                        dpg.set_axis_limits("py", 0, 1.05); dpg.set_axis_limits("px", 0, HIST - 1)
                    dpg.add_text(self.start_hotkeys(), wrap=380, color=(148, 163, 184))
                # --- right: parameter tabs
                with dpg.child_window(width=-1, height=-28):
                    with dpg.tab_bar(tag="tabs"):
                        for tab in TABS:
                            with dpg.tab(label=tab, tag=f"tab_{tab}"):
                                dpg.add_spacer(height=4)
                                specs = [s for s in L.SPEC if s["tab"] == tab]
                                bools = [s for s in specs if s["kind"] == "b"]
                                if bools:
                                    with dpg.group(horizontal=True):
                                        for s in bools: self._slider(s)
                                for s in specs:
                                    if s["kind"] != "b": self._slider(s)
                                dpg.add_spacer(height=8)
                                dpg.add_button(label=f"Reset {tab} to preset", callback=self.on_reset_tab, user_data=tab)
                                dpg.add_text(TAB_HELP.get(tab, ""), wrap=720, color=(148, 163, 184))
            dpg.add_text("ready", tag="status", color=(163, 230, 53))
        for v in L.VOICES:
            with dpg.theme() as th:
                with dpg.theme_component(dpg.mvLineSeries):
                    dpg.add_theme_color(dpg.mvPlotCol_Line, VOICE_COLORS[v], category=dpg.mvThemeCat_Plots)
                with dpg.theme_component(dpg.mvProgressBar):
                    dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, VOICE_COLORS[v])
            dpg.bind_item_theme(f"ser_{v}", th); dpg.bind_item_theme(f"w_{v}", th)
        self._build_quick_buttons()
        self.refresh_devices()
        self.update_dirty()
        dpg.setup_dearpygui(); dpg.show_viewport(); dpg.set_primary_window("main", True)

    def _theme(self):
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (14, 14, 18))
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (20, 20, 26))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (34, 34, 44))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (52, 40, 60))
                dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, (232, 121, 249))
                dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, (250, 176, 5))
                dpg.add_theme_color(dpg.mvThemeCol_Button, (60, 30, 70))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (110, 45, 120))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (160, 60, 170))
                dpg.add_theme_color(dpg.mvThemeCol_Tab, (40, 26, 48))
                dpg.add_theme_color(dpg.mvThemeCol_TabHovered, (110, 45, 120))
                dpg.add_theme_color(dpg.mvThemeCol_TabActive, (90, 38, 100))
                dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (250, 176, 5))
                dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, (163, 230, 53))
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
                dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 4)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 10, 8)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 6)
        dpg.bind_theme(t)

    # ---------------- frame update ----------------
    def tick(self):
        self.poll_requests()
        st = self.eng.stats

        def bar(tag, db, lo=-70.0):
            v = float(np.clip((db - lo) / -lo, 0, 1)); dpg.set_value(tag, v); dpg.configure_item(tag, overlay=f"{db:5.1f} dB")
        bar("m_in", st["in_db"]); bar("m_out", st["out_db"])
        dpg.set_value("m_gate", st["gate"]); dpg.configure_item("m_gate", overlay="open" if st["gate"] > 0.5 else "closed")
        w = np.asarray(st["weights"], float)
        self.hist_w = np.roll(self.hist_w, -1, axis=1); self.hist_w[:, -1] = w
        for i, v in enumerate(L.VOICES):
            dpg.set_value(f"w_{v}", float(w[i])); dpg.configure_item(f"w_{v}", overlay=f"{w[i] * 100:3.0f}%")
            dpg.set_value(f"ser_{v}", [self.hist_x, self.hist_w[i].tolist()])
        run = "RUNNING" if self.io.running else "stopped"
        dpg.set_value("m_info", f"{run}   cpu {st['load'] * 100:3.0f}%   xruns {st['xruns']}")
        dpg.set_value("m_info2", f"latency ~{self.io.latency_s * 1000:.0f} ms   your pitch ~{self.eng.f0_med:.0f} Hz")
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


TAB_HELP = {
    "Mix": "How the voices are blended over time. Morph speed = how often the random mix changes; Hard switching = "
           "soft crossfades (0) vs one voice at a time (1). Radio amount crossfades into the Radio tab's FX.",
    "Beast": "Low monster voice. Pitch target is where your median pitch is pushed (you are ~88 Hz). Formant < 1 = bigger head.",
    "Fem": "Robotic female voice. Pitch target ~220 Hz; Autotune snap quantizes to semitones (robotic). Formant > 1 = smaller head. "
           "The metal comb + bitcrush make it synthetic.",
    "Glide": "One voice that slides continuously between the low and the high end (pitch, formants and breath together).",
    "Robot": "Monotone: every syllable is sung on one flat note, plus ring modulation.",
    "Whisper": "Pure breath version of your voice (no pitch).",
    "Human": "Your own (clean) voice inside the mix.",
    "Radio": "Broken-transmission FX used when Mix > Radio amount > 0: band-pass, drive, bitcrush and random dropouts.",
    "Gate": "Silences everything between words. Raise the threshold if room noise opens it, lower it if quiet words get cut.",
    "Master": "AGC keeps every voice as loud as your dry voice (turn off to use Level trims only). Compressor + makeup + output gain.",
}


if __name__ == "__main__":
    App().run()
