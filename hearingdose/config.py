# -*- coding: utf-8 -*-
"""Settings: a commented .ini next to the app, same pattern as SafeTimeWidget."""

from __future__ import annotations

import configparser
import os

DEFAULTS = {
    # calibration
    "ceiling_db": 119.0,     # full-scale sine at MAX volume, dB SPL (your hardware)
    "offset_db": 0.0,        # manual nudge if your ears say the numbers are off
    # dose - accrual (NIOSH)
    "criterion_db": 85.0,
    "criterion_hours": 8.0,
    "exchange_db": 3.0,
    "threshold_db": 80.0,
    # dose - recovery (best-estimate log model)
    "recovery_hours": 16.0,
    "recovery_t1_min": 3.0,
    "recovery_ceiling_db": 70.0,
    # warnings
    "prewarn_at": 0.8,
    "warn_at": 1.0,
    # display
    "poll_ms": 1000,
    "graph_minutes": 30,
    "font_family": "Consolas",
    "opacity": 0.94,
    "always_on_top": True,
    "x": 80,
    "y": 80,
}

_TEMPLATE = """\
; ============================================================
;  Hearing Dose Meter  -  settings
;  Reads the PC's real audio output (WASAPI loopback), estimates
;  dBA at the ear, and tracks a running daily noise dose.
;  Right-click the window > Reload after editing.
; ============================================================

[calibration]
; Full-scale sine at MAX Windows volume, in dB SPL - your hardware ceiling.
; Same number as SafeTimeWidget (HEDD D1 + iFi GO Link 2 = 119).
ceiling_db = {ceiling_db}

; Manual fine-tune, added to every reading. Leave 0 unless you have reason.
offset_db = {offset_db}

[dose]
; --- Accrual: NIOSH REL. 100% dose = criterion_db for criterion_hours,
;     halving the allowed time every exchange_db. Below threshold_db, nothing
;     accrues. These are the standardised, defensible half of the model.
criterion_db = {criterion_db}
criterion_hours = {criterion_hours}
exchange_db = {exchange_db}
threshold_db = {threshold_db}

; --- Recovery: a BEST-ESTIMATE log-time model, not a standard. A full 100%
;     dose clears after ~recovery_hours of quiet, front-loaded (steep early,
;     long tail). recovery_t1_min sets how steep the early drop is (smaller =
;     steeper; ~3 min echoes the post-exposure recovery onset). Recovery only
;     progresses while the level is below recovery_ceiling_db; between that and
;     threshold_db the dose just holds (ears neither loaded nor truly resting).
;     This aims at the middle of the plausible range, NOT a safe over-estimate:
;     the point is a number you can trust near the limit. Reality may differ.
recovery_hours = {recovery_hours}
recovery_t1_min = {recovery_t1_min}
recovery_ceiling_db = {recovery_ceiling_db}

; Warn thresholds as a fraction of a full daily dose (1.0 = 100%).
prewarn_at = {prewarn_at}
warn_at = {warn_at}

[display]
; How often to re-measure, milliseconds. 1000 = once a second (plenty).
poll_ms = {poll_ms}

; Width of the rolling graph, in minutes of history.
graph_minutes = {graph_minutes}

font_family = {font_family}
opacity = {opacity}
always_on_top = {always_on_top}

[window]
; Saved automatically when you drag the window.
x = {x}
y = {y}
"""


def _num(cp, sec, key, default, cast):
    try:
        return cast(cp.get(sec, key))
    except Exception:
        return default


def _clamp_settings(s: dict) -> dict:
    """Keep hand-edited .ini values in safe ranges so a typo can't crash the app
    (e.g. exchange_db=0 -> divide-by-zero, or an out-of-range opacity)."""
    s["poll_ms"] = int(max(100, min(10000, s["poll_ms"])))
    s["graph_minutes"] = max(1, int(s["graph_minutes"]))
    s["opacity"] = max(0.2, min(1.0, float(s["opacity"])))
    s["exchange_db"] = max(0.1, float(s["exchange_db"]))
    s["criterion_hours"] = max(0.1, float(s["criterion_hours"]))
    s["recovery_hours"] = max(0.1, float(s["recovery_hours"]))
    s["recovery_t1_min"] = max(0.01, float(s["recovery_t1_min"]))
    s["warn_at"] = max(0.05, float(s["warn_at"]))
    s["prewarn_at"] = max(0.01, min(s["warn_at"], float(s["prewarn_at"])))
    return s


def load_settings(path: str) -> dict:
    s = dict(DEFAULTS)
    if not os.path.exists(path):
        save_settings(path, s)
        return s
    cp = configparser.ConfigParser()
    try:
        cp.read(path, encoding="utf-8")
    except Exception:
        return s
    for key, default in DEFAULTS.items():
        if isinstance(default, bool):
            sec = "display"
            try:
                s[key] = cp.getboolean(sec, key)
            except Exception:
                s[key] = default
            continue
        cast = type(default)
        # locate the section that holds this key
        for sec in ("calibration", "dose", "display", "window"):
            if cp.has_option(sec, key):
                s[key] = _num(cp, sec, key, default, cast)
                break
    return _clamp_settings(s)


def save_settings(path: str, s: dict) -> None:
    out = dict(s)
    out["always_on_top"] = "true" if s["always_on_top"] else "false"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(_TEMPLATE.format(**out))
    except Exception:
        pass
