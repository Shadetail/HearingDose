#!/usr/bin/env pythonw
# -*- coding: utf-8 -*-
"""
Safe-Time Widget
================
A tiny always-on-top window that shows ONLY the NIOSH "safe listening time"
for your current Windows volume, updated live. Same model as the web meter
(Volume_exposure_meter.html), driven by the real device dB read from the
Windows Core Audio API via pycaw.

    SPL  = ceiling_db + (source_lufs + sine_lufs) + windows_dB
    dBA  = SPL - weighting_db
    time = 8h at 85 dBA, halving every +3 dBA (no limit below 85)

Run: double-click this file, or `pythonw SafeTimeWidget.pyw`.
All tunables live in SafeTimeWidget.ini (created next to this file on first run).

Controls: drag = move · right-click = menu (Reload / Edit .ini / Quit) · Esc = quit
"""

import os
import sys
import configparser
import tkinter as tk

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
BASE = os.path.dirname(os.path.abspath(__file__))
INI_PATH = os.path.join(BASE, "SafeTimeWidget.ini")

# ----------------------------------------------------------------------------
# Palette (matches the web meter's zones)
# ----------------------------------------------------------------------------
BORDER = "#26323A"
PANEL = "#121A1E"
FAINT = "#5E6E76"
TRANSPARENT_KEY = "#010203"   # used only in panel=false (floating text) mode

ZONES = [   # (upper dBA bound, color)
    (75,  "#4FC580"),   # safe (green)
    (85,  "#B7D14E"),   # safe (lime)
    (90,  "#E9B23A"),   # time-limited (amber)
    (95,  "#E88A3A"),   # short only (orange)
    (100, "#E85A3A"),   # danger (red)
]
SEVERE = "#C93526"      # >= 100 dBA

# ----------------------------------------------------------------------------
# Defaults + INI template (comments are preserved across auto-saves)
# ----------------------------------------------------------------------------
DEFAULTS = {
    "ceiling_db": 119.0,
    "source_lufs": -7.0,
    "weighting_db": 0.0,
    "sine_lufs": 3.0,
    "poll_ms": 200,
    "font_family": "Consolas",
    "font_size": 40,
    "opacity": 0.92,
    "always_on_top": True,
    "panel": True,
    "x": 60,
    "y": 60,
}

INI_TEMPLATE = """\
; ============================================================
;  Safe-Time Widget  -  settings
;  Edit values, then right-click the widget > Reload settings
;  (or just restart it). Lines starting with ; are comments.
; ============================================================

[calibration]
; Full-scale tone at MAX volume, in dB SPL - your hardware ceiling.
; HEDD D1 + iFi GO Link 2 = 119.  (Matches the web meter's Setup tab.)
ceiling_db = {ceiling_db}

; Loudness of what you listen to, in LUFS. Bigger effect than you'd think:
;   YouTube Music (hot) = -7      Spotify / Tidal / YouTube = -14
;   Apple Music         = -16     (7 dB hotter  ~  5x the dose)
source_lufs = {source_lufs}

; Dose weighting. 0 = flat / conservative ceiling (recommended).
; 4 = A-weighted estimate for typical bass-carrying music (less strict).
weighting_db = {weighting_db}

; Model constant: a full-scale sine reads about -3 LUFS. Leave at 3.
sine_lufs = {sine_lufs}

[display]
; How often to re-read the Windows volume, in milliseconds. 200 = 5x/sec.
poll_ms = {poll_ms}

; Font + size of the number. Consolas is always available; you can use
; "JetBrains Mono" etc. if you have it installed.
font_family = {font_family}
font_size = {font_size}

; Window opacity, 0.2 - 1.0
opacity = {opacity}

; Keep the widget above other windows (true/false)
always_on_top = {always_on_top}

; true  = small dark panel you can drag with the mouse (recommended)
; false = just the floating number, click-through (edit x/y below to move it)
panel = {panel}

[window]
; Saved automatically when you drag the widget.
x = {x}
y = {y}
"""


def load_settings():
    """Read the .ini (creating it with defaults if missing) into a dict."""
    s = dict(DEFAULTS)
    if not os.path.exists(INI_PATH):
        save_settings(s)
        return s
    cp = configparser.ConfigParser()
    try:
        cp.read(INI_PATH, encoding="utf-8")
    except Exception:
        return s

    def num(sec, key, default, cast):
        try:
            return cast(cp.get(sec, key))
        except Exception:
            return default

    def bl(sec, key, default):
        try:
            return cp.getboolean(sec, key)
        except Exception:
            return default

    s["ceiling_db"]   = num("calibration", "ceiling_db",   DEFAULTS["ceiling_db"], float)
    s["source_lufs"]  = num("calibration", "source_lufs",  DEFAULTS["source_lufs"], float)
    s["weighting_db"] = num("calibration", "weighting_db", DEFAULTS["weighting_db"], float)
    s["sine_lufs"]    = num("calibration", "sine_lufs",    DEFAULTS["sine_lufs"], float)
    s["poll_ms"]      = max(50, num("display", "poll_ms",  DEFAULTS["poll_ms"], int))
    s["font_family"]  = num("display", "font_family",      DEFAULTS["font_family"], str)
    s["font_size"]    = max(8, num("display", "font_size", DEFAULTS["font_size"], int))
    s["opacity"]      = min(1.0, max(0.2, num("display", "opacity", DEFAULTS["opacity"], float)))
    s["always_on_top"] = bl("display", "always_on_top", DEFAULTS["always_on_top"])
    s["panel"]        = bl("display", "panel", DEFAULTS["panel"])
    s["x"]            = num("window", "x", DEFAULTS["x"], int)
    s["y"]            = num("window", "y", DEFAULTS["y"], int)
    return s


def save_settings(s):
    """Write the dict back through the commented template."""
    out = dict(s)
    out["always_on_top"] = "true" if s["always_on_top"] else "false"
    out["panel"] = "true" if s["panel"] else "false"
    try:
        with open(INI_PATH, "w", encoding="utf-8") as f:
            f.write(INI_TEMPLATE.format(**out))
    except Exception:
        pass


# ----------------------------------------------------------------------------
# The exposure model (parity with Volume_exposure_meter.html)
# ----------------------------------------------------------------------------
def safe_hours(dBA):
    if dBA < 85:
        return float("inf")
    return 8.0 / (2.0 ** ((dBA - 85.0) / 3.0))


def fmt_time(h):
    if h == float("inf"):
        return "∞"                      # infinity glyph
    mins = h * 60.0
    if mins >= 60:
        H = int(mins // 60)
        M = int(round(mins - H * 60))
        if M == 60:
            H, M = H + 1, 0
        return "{}h {:02d}m".format(H, M)
    if mins >= 1:
        return "{} min".format(int(round(mins))) if mins >= 10 else "{:.1f} min".format(mins)
    return "{} sec".format(max(1, int(round(mins * 60))))


def zone_color(dBA):
    for bound, color in ZONES:
        if dBA < bound:
            return color
    return SEVERE


# ----------------------------------------------------------------------------
# Windows volume reader (cached interface, re-resolves ~1x/sec for device swaps)
# ----------------------------------------------------------------------------
class VolumeReader:
    def __init__(self, poll_ms):
        from pycaw.utils import AudioUtilities   # imported here so --help etc. is cheap
        self._get = AudioUtilities.GetSpeakers
        self.iface = None
        self.count = 0
        self.reeval = max(1, round(1000 / poll_ms))

    def read(self):
        """Return (windows_dB, muted, ok)."""
        try:
            if self.iface is None or (self.count % self.reeval) == 0:
                self.iface = self._get().EndpointVolume
            self.count += 1
            db = float(self.iface.GetMasterVolumeLevel())
            muted = bool(self.iface.GetMute())
            return db, muted, True
        except Exception:
            self.iface = None
            return 0.0, False, False


# ----------------------------------------------------------------------------
# The widget
# ----------------------------------------------------------------------------
class Widget:
    def __init__(self, root, selftest=False):
        self.root = root
        self.selftest = selftest
        self.s = load_settings()
        self.reader = VolumeReader(self.s["poll_ms"])

        root.title("Safe Time")
        root.overrideredirect(True)

        self.border = tk.Frame(root, bg=BORDER)
        self.border.pack(fill="both", expand=True)
        self.label = tk.Label(self.border, text="...", padx=16, pady=6)
        self.label.pack(padx=1, pady=1)   # 1px border via the frame behind it

        self._build_menu()
        self.apply_style()

        # Drag to move
        for w in (root, self.border, self.label):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<Button-3>", self._popup)
        root.bind("<Escape>", lambda e: self.quit())

        root.geometry("+{}+{}".format(self.s["x"], self.s["y"]))

        if selftest:
            root.after(1200, self._selftest_done)
        self.tick()

    # -- styling ------------------------------------------------------------
    def apply_style(self):
        s = self.s
        self.root.attributes("-topmost", s["always_on_top"])
        self.root.attributes("-alpha", s["opacity"])
        if s["panel"]:
            self.root.attributes("-transparentcolor", "")
            self.border.configure(bg=BORDER)
            self.label.configure(bg=PANEL)
        else:
            # floating text only: key-color everything so background is invisible
            self.root.configure(bg=TRANSPARENT_KEY)
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
            self.border.configure(bg=TRANSPARENT_KEY)
            self.label.configure(bg=TRANSPARENT_KEY)
        self.label.configure(font=(s["font_family"], s["font_size"], "bold"))

    def _build_menu(self):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Reload settings", command=self.reload)
        m.add_command(label="Edit settings (.ini)...", command=self.edit_ini)
        m.add_separator()
        m.add_command(label="Quit", command=self.quit)
        self.menu = m

    # -- interactions -------------------------------------------------------
    def _popup(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _drag_start(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry("+{}+{}".format(e.x_root - self._dx, e.y_root - self._dy))

    def _drag_end(self, e):
        self.s["x"] = self.root.winfo_x()
        self.s["y"] = self.root.winfo_y()
        save_settings(self.s)

    def reload(self):
        self.s = load_settings()
        self.reader.reeval = max(1, round(1000 / self.s["poll_ms"]))
        self.apply_style()
        self.root.geometry("+{}+{}".format(self.s["x"], self.s["y"]))

    def edit_ini(self):
        try:
            os.startfile(INI_PATH)   # opens in the default text editor
        except Exception:
            pass

    def quit(self):
        try:
            self.s["x"] = self.root.winfo_x()
            self.s["y"] = self.root.winfo_y()
            save_settings(self.s)
        finally:
            self.root.destroy()

    # -- main loop ----------------------------------------------------------
    def tick(self):
        s = self.s
        db, muted, ok = self.reader.read()
        if not ok:
            self.label.configure(text="—", fg=FAINT)   # em dash: no device
        elif muted:
            self.label.configure(text="muted", fg=FAINT)
        else:
            w = min(db, 0.0)
            spl = s["ceiling_db"] + (s["source_lufs"] + s["sine_lufs"]) + w
            dBA = spl - s["weighting_db"]
            self.label.configure(text=fmt_time(safe_hours(dBA)), fg=zone_color(dBA))
        if s["always_on_top"]:
            self.root.attributes("-topmost", True)   # re-assert; cheap
        if not self.selftest:
            self.root.after(s["poll_ms"], self.tick)

    def _selftest_done(self):
        db, muted, ok = self.reader.read()
        print("selftest ok  ->  windows_dB={:.2f}  muted={}  read_ok={}".format(db, muted, ok))
        self.root.destroy()


def main():
    selftest = "--selftest" in sys.argv
    root = tk.Tk()
    try:
        Widget(root, selftest=selftest)
    except Exception as e:
        # Show the error instead of dying silently under pythonw
        import tkinter.messagebox as mb
        mb.showerror("Safe-Time Widget failed to start", repr(e))
        raise
    root.mainloop()


if __name__ == "__main__":
    main()
