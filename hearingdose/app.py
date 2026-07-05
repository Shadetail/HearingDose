# -*- coding: utf-8 -*-
"""
Hearing Dose Meter - GUI
========================
Always-on-top panel: live dBA from the real audio stream, a running daily-dose
"tank", a rolling graph that shows the dose accumulating and (log-shaped)
recovering, and a warning when you spend the day's budget.

Drag = move · right-click = menu (Reload / Reset / Quit)
"""

from __future__ import annotations

import collections
import os
import time
import tkinter as tk

try:
    from PIL import Image, ImageDraw, ImageTk
    _LANCZOS = getattr(Image, "Resampling", Image).LANCZOS
    _HAVE_PIL = True
except Exception:
    _HAVE_PIL = False

from .config import load_settings, save_settings
from .dose import DoseModel, DoseParams, RECOVERY_NOTE
from .audio import LoopbackMeter
from .state import load_state, save_state

BASE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(BASE)
INI_PATH = os.path.join(APP_DIR, "HearingDose.ini")
STATE_PATH = os.path.join(APP_DIR, "HearingDose.state.json")

# palette (matches SafeTimeWidget's zones)
BORDER = "#26323A"
PANEL = "#121A1E"
PANEL2 = "#0C1215"
FAINT = "#5E6E76"
TEXT = "#D7E2E7"
WARN_BG = "#3A1512"

DBA_ZONES = [(75, "#4FC580"), (85, "#B7D14E"), (90, "#E9B23A"),
             (95, "#E88A3A"), (100, "#E85A3A")]
DBA_SEVERE = "#C93526"


def dba_color(dba):
    for bound, c in DBA_ZONES:
        if dba < bound:
            return c
    return DBA_SEVERE


def dose_color(frac):
    if frac < 0.5:
        return "#4FC580"
    if frac < 0.8:
        return "#B7D14E"
    if frac < 1.0:
        return "#E9B23A"
    if frac < 1.25:
        return "#E85A3A"
    return DBA_SEVERE


def fmt_dur(seconds):
    if seconds == float("inf"):
        return "∞"
    seconds = max(0, seconds)
    if seconds >= 3600:
        h = int(seconds // 3600)
        m = int(round((seconds - h * 3600) / 60))
        if m == 60:
            h, m = h + 1, 0
        return "{}h {:02d}m".format(h, m)
    if seconds >= 60:
        return "{} min".format(int(round(seconds / 60)))
    return "{} sec".format(int(round(seconds)))


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _blend(rgb, bg, a):
    """rgb over bg at opacity a -> solid rgb (Tk canvas has no real alpha)."""
    return tuple(int(rgb[i] * a + bg[i] * (1.0 - a)) for i in range(3))


PANEL2_RGB = _hex_to_rgb(PANEL2)


def render_dose_graph_image(W, H, cols, top, dose_hex, warn_at, prewarn_at, S=3):
    """Render the rolling graph to an antialiased PIL image (supersample+LANCZOS).

    `cols` is a list of (x_column, dba_min, dba_max, dose) left->right. The dBA
    range is drawn as a faint envelope band; the dose is a filled area + line.
    Pure (no Tk) so it can be rendered to a PNG and eyeballed in tests.
    """
    img = Image.new("RGB", (W * S, H * S), PANEL2_RGB)
    d = ImageDraw.Draw(img)
    pad = 4 * S
    span = (H * S) - 2 * pad
    base = (H * S) - pad

    def cx(col):
        return int((col + 0.5) * S)

    def y_dose(v):
        return base - min(1.0, max(0.0, v / top)) * span

    def y_dba(db):
        return base - (min(100.0, max(40.0, db)) - 40.0) / 60.0 * span

    # dBA envelope band (min..max), faint, behind everything
    band = _blend((110, 140, 165), PANEL2_RGB, 0.32)
    top_e = [(cx(col), y_dba(mx)) for col, mn, mx, _ in cols]
    bot_e = [(cx(col), y_dba(mn)) for col, mn, mx, _ in reversed(cols)]
    d.polygon(top_e + bot_e, fill=band)

    # warn / prewarn gridlines
    for frac, rgb in ((warn_at, (185, 88, 74)), (prewarn_at, (150, 138, 70))):
        if frac <= top:
            y = int(y_dose(frac))
            d.line([(0, y), (W * S, y)], fill=rgb, width=max(1, S // 2))

    # dose filled area + line (the hero)
    dose_rgb = _hex_to_rgb(dose_hex)
    line_pts = [(cx(col), y_dose(dose)) for col, _, _, dose in cols]
    d.polygon([(line_pts[0][0], base)] + line_pts + [(line_pts[-1][0], base)],
              fill=_blend(dose_rgb, PANEL2_RGB, 0.34))
    d.line(line_pts, fill=dose_rgb, width=2 * S, joint="curve")

    return img.resize((W, H), _LANCZOS)


class App:
    def __init__(self, root, selftest=False):
        self.root = root
        self.selftest = selftest
        self.s = load_settings(INI_PATH)

        params = DoseParams(
            criterion_db=self.s["criterion_db"],
            criterion_hours=self.s["criterion_hours"],
            exchange_db=self.s["exchange_db"],
            threshold_db=self.s["threshold_db"],
            recovery_hours=self.s["recovery_hours"],
            recovery_t1_min=self.s["recovery_t1_min"],
            recovery_ceiling_db=self.s["recovery_ceiling_db"],
        )
        self.model = DoseModel(params=params)
        self.downtime = load_state(STATE_PATH, self.model)

        self.meter = LoopbackMeter(self.s["ceiling_db"], self.s["offset_db"],
                                   self.s["poll_ms"])

        self.history = collections.deque()   # (t, dba, dose)
        self.last_tick = time.time()
        self.last_save = 0.0
        self.warned_pre = False
        self.warned_full = False
        self.flash = 0

        root.title("Hearing Dose")
        root.overrideredirect(True)
        self.build_ui()
        self.apply_style()
        self.bind_events()
        root.geometry("+{}+{}".format(self.s["x"], self.s["y"]))

        if selftest:
            root.after(1500, self._selftest_done)
        self.tick()

    # -- UI construction ----------------------------------------------------
    def build_ui(self):
        s = self.s
        fam = s["font_family"]
        self.border = tk.Frame(self.root, bg=BORDER)
        self.border.pack(fill="both", expand=True)
        self.panel = tk.Frame(self.border, bg=PANEL, padx=12, pady=10)
        self.panel.pack(padx=2, pady=2, fill="both", expand=True)

        # header: dBA (left) + dose % (right)
        head = tk.Frame(self.panel, bg=PANEL)
        head.pack(fill="x")
        left = tk.Frame(head, bg=PANEL)
        left.pack(side="left")
        self.dba_lbl = tk.Label(left, text="--", bg=PANEL, fg=TEXT,
                                font=(fam, 30, "bold"))
        self.dba_lbl.pack(anchor="w")
        tk.Label(left, text="dBA  (at the ear)", bg=PANEL, fg=FAINT,
                 font=(fam, 8)).pack(anchor="w")
        right = tk.Frame(head, bg=PANEL)
        right.pack(side="right")
        self.dose_lbl = tk.Label(right, text="0%", bg=PANEL, fg=TEXT,
                                 font=(fam, 30, "bold"))
        self.dose_lbl.pack(anchor="e")
        tk.Label(right, text="daily dose", bg=PANEL, fg=FAINT,
                 font=(fam, 8)).pack(anchor="e")

        # dose bar
        self.bar = tk.Canvas(self.panel, height=14, bg=PANEL2, highlightthickness=0)
        self.bar.pack(fill="x", pady=(8, 4))

        # status line
        self.status = tk.Label(self.panel, text="", bg=PANEL, fg=FAINT,
                               font=(fam, 10), anchor="w")
        self.status.pack(fill="x")

        # rolling graph
        self.graph = tk.Canvas(self.panel, height=96, bg=PANEL2, highlightthickness=0)
        self.graph.pack(fill="x", pady=(6, 2))

        # footer (device / warnings)
        self.footer = tk.Label(self.panel, text="", bg=PANEL, fg=FAINT,
                               font=(fam, 8), anchor="w")
        self.footer.pack(fill="x")

        self._build_menu()

    def _build_menu(self):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Reload settings", command=self.reload)
        m.add_command(label="Edit settings (.ini)...", command=self.edit_ini)
        m.add_separator()
        m.add_command(label="Reset dose to 0%", command=self.reset_dose)
        m.add_separator()
        m.add_command(label="Quit", command=self.quit)
        self.menu = m

    def bind_events(self):
        for w in (self.root, self.border, self.panel, self.dba_lbl,
                  self.dose_lbl, self.status, self.footer):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<Button-3>", self._popup)
        # deliberately NO Escape-to-quit: a stray Esc must not kill an
        # always-on monitor. Quit only via the right-click menu.

    # -- styling ------------------------------------------------------------
    def apply_style(self):
        self.root.attributes("-topmost", self.s["always_on_top"])
        self.root.attributes("-alpha", self.s["opacity"])
        # size to fit content so nothing clips (fixed min width)
        self.root.update_idletasks()
        w = max(360, self.root.winfo_reqwidth())
        h = self.root.winfo_reqheight()
        self.root.geometry("{}x{}".format(w, h))

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
        save_settings(INI_PATH, self.s)

    def reload(self):
        self.s = load_settings(INI_PATH)
        self.model.params = DoseParams(
            criterion_db=self.s["criterion_db"], criterion_hours=self.s["criterion_hours"],
            exchange_db=self.s["exchange_db"], threshold_db=self.s["threshold_db"],
            recovery_hours=self.s["recovery_hours"], recovery_t1_min=self.s["recovery_t1_min"],
            recovery_ceiling_db=self.s["recovery_ceiling_db"],
        )
        self.meter.ceiling_db = self.s["ceiling_db"]
        self.meter.offset_db = self.s["offset_db"]
        self.apply_style()

    def edit_ini(self):
        try:
            os.startfile(INI_PATH)
        except Exception:
            pass

    def reset_dose(self):
        import tkinter.messagebox as mb
        if mb.askyesno("Reset dose", "Reset today's dose to 0%?"):
            self.model.reset()
            self.history.clear()
            self.warned_pre = self.warned_full = False
            self.set_alarm(False)
            save_state(STATE_PATH, self.model)

    def quit(self):
        try:
            self.s["x"] = self.root.winfo_x()
            self.s["y"] = self.root.winfo_y()
            save_settings(INI_PATH, self.s)
            save_state(STATE_PATH, self.model)
            self.meter.close()
        finally:
            self.root.destroy()

    # -- main loop ----------------------------------------------------------
    def tick(self):
        # The whole body is guarded so a transient error (audio device drop,
        # COM hiccup) can never kill an always-on monitor: log it and keep
        # ticking. The next poll re-opens a dropped stream.
        try:
            now = time.time()
            dt = now - self.last_tick
            self.last_tick = now
            dt = min(dt, 5.0)   # guard against long stalls / sleep

            r = self.meter.poll()
            if r.ok:
                dba_for_model = r.dba if not r.silent else 0.0
                self.model.update(dba_for_model, dt)

            # history for the graph (1 point per tick)
            graph_dba = r.dba if (r.ok and not r.silent) else 0.0
            self.history.append((now, graph_dba, self.model.dose))
            window = max(1.0, self.s["graph_minutes"] * 60)
            while self.history and now - self.history[0][0] > window:
                self.history.popleft()

            self.render(r)
            self.check_warnings()

            if now - self.last_save > 10:
                save_state(STATE_PATH, self.model)
                self.last_save = now

            if self.s["always_on_top"]:
                self.root.attributes("-topmost", True)
        except Exception as e:
            try:
                self.footer.configure(text="recovering from error: {!r}".format(e))
            except Exception:
                pass
        finally:
            if not self.selftest:
                self.root.after(self.s["poll_ms"], self.tick)

    def render(self, r):
        dose = self.model.dose
        # header numbers
        if not r.ok:
            self.dba_lbl.configure(text="--", fg=FAINT)
        elif r.muted:
            self.dba_lbl.configure(text="muted", fg=FAINT)
        elif r.silent:
            self.dba_lbl.configure(text="quiet", fg=FAINT)
        else:
            self.dba_lbl.configure(text="{:.0f}".format(r.dba), fg=dba_color(r.dba))
        self.dose_lbl.configure(text="{:.0f}%".format(dose * 100), fg=dose_color(dose))

        # status line
        p = self.model.params
        if not r.ok:
            self.status.configure(text="-- holding", fg=FAINT)
        elif (not r.silent) and (not r.muted) and r.dba >= p.threshold_db:
            t = self.model.seconds_to_full(r.dba, self.s["warn_at"])
            self.status.configure(
                text="▲ spending · full at {}".format(
                    "∞" if t == float("inf") else "~" + fmt_dur(t)),
                fg=dose_color(dose))
        elif dose > 0.005 and ((r.silent or r.muted) or r.dba < p.recovery_ceiling_db):
            t = self.model.seconds_to_clear()
            self.status.configure(
                text="▼ recovering · clears ~{}".format(fmt_dur(t)),
                fg="#6FB0C8")
        else:
            self.status.configure(text="— holding", fg=FAINT)

        # footer
        peak = self.model.peak_dose
        extra = ""
        if not r.ok:
            extra = "  · no audio device"
        self.footer.configure(
            text="peak {:.0f}%  ·  vol {:.0f} dB{}".format(
                peak * 100, r.master_db, extra))

        self.draw_bar(dose)
        self.draw_graph()

    def draw_bar(self, dose):
        c = self.bar
        c.delete("all")
        w = c.winfo_width() or 336
        h = int(c["height"])
        top = max(1.25, dose * 1.05)
        # prewarn / warn ticks
        for frac, col in ((self.s["prewarn_at"], "#7A6A2A"), (self.s["warn_at"], "#7A2A22")):
            x = int(frac / top * w)
            c.create_line(x, 0, x, h, fill=col)
        fillw = int(min(dose, top) / top * w)
        c.create_rectangle(0, 0, fillw, h, fill=dose_color(dose), width=0)

    def draw_graph(self):
        c = self.graph
        W = c.winfo_width() or 336
        H = int(c["height"])
        if W < 8 or H < 8:
            return
        pts = list(self.history)
        if len(pts) < 2:
            c.delete("all")
            c.create_text(W // 2, H // 2, text="collecting…", fill=FAINT)
            return
        now = time.time()
        window = max(1.0, self.s["graph_minutes"] * 60)
        top = max(1.25, max(d for _, _, d in pts) * 1.15)
        # collapse the per-second points into one bin per horizontal pixel:
        # dBA keeps its min & max (a smooth envelope instead of a noisy line),
        # dose keeps the latest value in the bin.
        cols = self._bin_columns(pts, now, window, W)
        if len(cols) < 2:
            return
        if _HAVE_PIL:
            self._draw_graph_pil(c, W, H, cols, top)
        else:
            self._draw_graph_tk(c, W, H, cols, top)

    def _bin_columns(self, pts, now, window, W):
        cols = {}
        for t, dba, dose in pts:
            col = int((t - (now - window)) / window * W)
            col = 0 if col < 0 else (W - 1 if col > W - 1 else col)
            e = cols.get(col)
            if e is None:
                cols[col] = [dba, dba, dose, t]
            else:
                if dba < e[0]:
                    e[0] = dba
                if dba > e[1]:
                    e[1] = dba
                if t >= e[3]:
                    e[2], e[3] = dose, t
        return [(col, cols[col][0], cols[col][1], cols[col][2]) for col in sorted(cols)]

    def _draw_graph_pil(self, c, W, H, cols, top):
        img = render_dose_graph_image(
            W, H, cols, top, dose_color(self.model.dose),
            self.s["warn_at"], self.s["prewarn_at"])
        self._graph_photo = ImageTk.PhotoImage(img)
        c.delete("all")
        c.create_image(0, 0, anchor="nw", image=self._graph_photo)

    def _draw_graph_tk(self, c, W, H, cols, top):
        c.delete("all")
        pad = 4
        span = H - 2 * pad
        base = H - pad

        def y_dose(v):
            return base - min(1.0, max(0.0, v / top)) * span

        def y_dba(db):
            return base - (min(100.0, max(40.0, db)) - 40.0) / 60.0 * span

        env = []
        for col, mn, mx, _ in cols:
            env += [col, y_dba(mx)]
        for col, mn, mx, _ in reversed(cols):
            env += [col, y_dba(mn)]
        if len(env) >= 6:
            c.create_polygon(*env, fill="#20343E", outline="")
        for frac, col in ((self.s["warn_at"], "#5A2A24"), (self.s["prewarn_at"], "#54501F")):
            if frac <= top:
                y = y_dose(frac)
                c.create_line(0, y, W, y, fill=col, dash=(3, 3))
        line_pts = [(col, y_dose(dose)) for col, _, _, dose in cols]
        area = [line_pts[0][0], base]
        for x, y in line_pts:
            area += [x, y]
        area += [line_pts[-1][0], base]
        c.create_polygon(*area, fill="#123039", outline="")
        dl = []
        for x, y in line_pts:
            dl += [x, y]
        c.create_line(*dl, fill=dose_color(self.model.dose), width=2)

    def check_warnings(self):
        dose = self.model.dose
        pre = self.s["prewarn_at"]
        full = self.s["warn_at"]
        if dose < pre * 0.9:
            self.warned_pre = self.warned_full = False
            self.set_alarm(False)
        if dose >= full and not self.warned_full:
            self.warned_full = True
            self.set_alarm(True)
            self.notify("Daily dose reached ({:.0f}%)".format(dose * 100),
                        "You've spent today's safe listening budget. "
                        "Give your ears real quiet to recover.\n\n" + RECOVERY_NOTE)
        elif dose >= pre and not self.warned_pre:
            self.warned_pre = True
            self.notify("{:.0f}% of daily dose".format(dose * 100),
                        "Approaching the limit — consider a break or lower level.")

    def set_alarm(self, on):
        if on:
            self.border.configure(bg=DBA_SEVERE)
            self.panel.configure(bg=WARN_BG)
        else:
            self.border.configure(bg=BORDER)
            self.panel.configure(bg=PANEL)

    def notify(self, title, body):
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        # non-blocking toast window so metering keeps running
        try:
            t = tk.Toplevel(self.root)
            t.overrideredirect(True)
            t.attributes("-topmost", True)
            t.configure(bg=DBA_SEVERE)
            x = self.root.winfo_x()
            y = self.root.winfo_y() + self.root.winfo_height() + 6
            t.geometry("360x92+{}+{}".format(x, y))
            f = tk.Frame(t, bg=PANEL, padx=12, pady=8)
            f.pack(padx=2, pady=2, fill="both", expand=True)
            tk.Label(f, text=title, bg=PANEL, fg="#F0C0B8",
                     font=(self.s["font_family"], 11, "bold"),
                     anchor="w").pack(fill="x")
            tk.Label(f, text=body, bg=PANEL, fg=TEXT, justify="left", wraplength=330,
                     font=(self.s["font_family"], 8), anchor="w").pack(fill="x")
            t.bind("<Button-1>", lambda e: t.destroy())
            t.after(9000, t.destroy)
        except Exception:
            pass

    def _selftest_done(self):
        r = self.meter.poll()
        self.root.update_idletasks()
        print("selftest -> dba={} silent={} ok={} dose={:.4f} downtime={:.0f}s size={}x{}".format(
            "sil" if r.silent else round(r.dba, 1), r.silent, r.ok,
            self.model.dose, self.downtime,
            self.root.winfo_width(), self.root.winfo_height()))
        self.meter.close()
        self.root.destroy()


def main():
    import sys
    selftest = "--selftest" in sys.argv
    root = tk.Tk()
    try:
        App(root, selftest=selftest)
    except Exception as e:
        import tkinter.messagebox as mb
        mb.showerror("Hearing Dose Meter failed to start", repr(e))
        raise
    root.mainloop()
