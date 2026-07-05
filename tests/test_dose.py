# -*- coding: utf-8 -*-
"""Unit tests for the dose engine + calibration. Run: python tests/test_dose.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from hearingdose.dose import DoseParams, DoseModel
from hearingdose.audio import (AWeighter, dbfs_to_dba, a_weight_response,
                               SINE_DBFS, loopback_matches)

fails = []


def check(name, cond):
    print(("  ok  " if cond else " FAIL ") + name)
    if not cond:
        fails.append(name)


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol * max(1.0, abs(b))


# --- NIOSH accrual --------------------------------------------------------
p = DoseParams()
check("permitted(85) == 8h", approx(p.permitted_seconds(85), 8 * 3600))
check("permitted(88) == 4h", approx(p.permitted_seconds(88), 4 * 3600))
check("permitted(91) == 2h", approx(p.permitted_seconds(91), 2 * 3600))
check("permitted(82) == 16h", approx(p.permitted_seconds(82), 16 * 3600))
check("rate below threshold is 0", p.dose_rate_per_sec(79.9) == 0.0)
check("rate at 85 > 0", p.dose_rate_per_sec(85) > 0)


def accrue(dba, hours, dt=30.0):
    m = DoseModel()
    steps = int(hours * 3600 / dt)
    for _ in range(steps):
        m.update(dba, dt)
    return m.dose

check("8h @ 85 dBA -> ~100% dose", approx(accrue(85, 8), 1.0, tol=1e-3))
check("4h @ 88 dBA -> ~100% dose", approx(accrue(88, 4), 1.0, tol=1e-3))
check("2h @ 91 dBA -> ~100% dose", approx(accrue(91, 2), 1.0, tol=1e-3))
check("1h @ 79 dBA -> ~0% dose", accrue(79, 1) == 0.0)

# --- recovery -------------------------------------------------------------
m = DoseModel()
check("recovery_fraction(0) == 1", m.recovery_fraction(0) == 1.0)
check("recovery_fraction(recovery_hours) == 0",
      m.recovery_fraction(p.recovery_hours * 3600) == 0.0)
check("recovery_fraction(recovery_hours) ~ 0",
      m.recovery_fraction(p.recovery_hours * 3600) < 1e-6)
check("recovery is front-loaded (>40% gone by 1h)", m.recovery_fraction(3600) < 0.6)
check("recovery monotonic",
      m.recovery_fraction(600) > m.recovery_fraction(3600) > m.recovery_fraction(7200))

# accrue to ~50%, then a full quiet window -> back to ~0
m = DoseModel()
for _ in range(int(4 * 3600 / 30)):
    m.update(85, 30)          # 4h @ 85 -> ~50%
half = m.dose
check("4h @ 85 -> ~50%", approx(half, 0.5, tol=2e-3))
for _ in range(int(p.recovery_hours * 3600 / 30)):
    m.update(45, 30)          # quiet
check("full recovery window clears the dose", m.dose < 1e-3)

# --- hold zone (between recovery_ceiling and threshold): dose frozen ------
m = DoseModel()
m.dose = 0.5
m._dose_at_quiet_start = 0.5
before = m.dose
m.update(75, 300)             # 75 dBA: no accrual (<80), no recovery (>=70)
check("hold zone leaves dose unchanged", m.dose == before)

# --- partial recovery then re-exposure continues sensibly -----------------
m = DoseModel()
for _ in range(int(2 * 3600 / 30)):
    m.update(85, 30)          # 2h @ 85 -> ~25%
d1 = m.dose
for _ in range(int(3600 / 30)):
    m.update(40, 30)          # 1h quiet -> recovers ~half
d2 = m.dose
check("1h quiet recovers a chunk", d2 < d1 * 0.7)
m.update(88, 3600)            # then loud again -> dose rises from the recovered point
check("re-exposure accrues from recovered point", m.dose > d2)

# --- calibration ----------------------------------------------------------
check("A-weight is ~0 dB at 1 kHz", approx(float(a_weight_response(np.array([1000.0]))[0]), 1.0, tol=2e-3))
check("A-weight attenuates 100 Hz", float(a_weight_response(np.array([100.0]))[0]) < 0.15)
check("full-scale sine @ max vol -> ceiling dBA",
      approx(dbfs_to_dba(SINE_DBFS, 0.0, 119.0), 119.0))
check("parity: -7 dBFS stream @ -23 dB vol",
      approx(dbfs_to_dba(-7.0, -23.0, 119.0), 119 - 23 + (-7 - SINE_DBFS)))

rate = 48000
frame = 4800
t = np.arange(frame) / rate
sine = np.sin(2 * np.pi * 1000 * t).astype(np.float32)
weighter = AWeighter(rate, frame)
mono_ms = weighter.mean_square(sine)
stereo_same_ms = weighter.mean_square(np.column_stack([sine, sine]))
stereo_left_ms = weighter.mean_square(np.column_stack([sine, np.zeros_like(sine)]))
check("stereo identical preserves per-ear level", approx(stereo_same_ms, mono_ms, tol=1e-3))
check("stereo left-only preserves active-ear level", approx(stereo_left_ms, mono_ms, tol=1e-3))

# --- device / loopback name matching --------------------------------------
check("loopback matches its own render endpoint",
      loopback_matches("D1 (GO link 2)", "D1 (GO link 2) [Loopback]"))
check("loopback matches when the endpoint name is a substring",
      loopback_matches("Speakers", "Speakers (Realtek) [Loopback]"))
check("loopback rejects a different device",
      not loopback_matches("D1 (GO link 2)", "Yeti Output (Yeti) [Loopback]"))
check("loopback rejects a blank target", not loopback_matches("", "Anything [Loopback]"))
check("loopback rejects prefix collisions",
      not loopback_matches("D1", "D10 [Loopback]"))
check("loopback only matches loopback devices",
      not loopback_matches("Speakers", "Speakers (Realtek)"))

# --- downtime recovery ----------------------------------------------------
m = DoseModel()
m.dose = 0.6
m._dose_at_quiet_start = 0.6
m.apply_downtime(8 * 3600)    # 8h closed
check("downtime recovers dose", m.dose < 0.6)
check("downtime recovers a lot by 8h", m.dose < 0.15)

# --- persistence round-trip (survives close/crash/restart) ----------------
import json
import tempfile
import time as _time
from hearingdose.state import save_state, load_state

tmp = os.path.join(tempfile.gettempdir(), "hd_test_state.json")
try:
    src = DoseModel()
    src.dose = 0.7
    src._dose_at_quiet_start = 0.7
    save_state(tmp, src)

    # immediate reload preserves dose (near-zero downtime)
    dst = DoseModel()
    load_state(tmp, dst)
    check("state round-trips dose", approx(dst.dose, 0.7, tol=1e-3))

    # simulate a 2h gap by backdating the saved timestamp -> recovery applied
    with open(tmp, "r", encoding="utf-8") as f:
        st = json.load(f)
    st["saved_epoch"] = _time.time() - 2 * 3600
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    dst2 = DoseModel()
    down = load_state(tmp, dst2)
    check("reload after 2h gap reports downtime", down > 3600)
    check("reload after 2h gap recovers dose", dst2.dose < 0.7)

    # missing file is safe (fresh start at 0)
    dst3 = DoseModel()
    load_state(os.path.join(tempfile.gettempdir(), "hd_nope_%d.json" % os.getpid()), dst3)
    check("missing state file -> dose stays 0", dst3.dose == 0.0)
finally:
    try:
        os.remove(tmp)
    except OSError:
        pass

# --- graph render smoke test (guards the antialiased renderer) ------------
from hearingdose.app import render_dose_graph_image, _HAVE_PIL
if _HAVE_PIL:
    _cols = [(i, 60 + (i % 30), min(1.2, i / 250.0)) for i in range(0, 360, 3)]
    _img = render_dose_graph_image(378, 96, _cols, 1.25, "#4FC580", 1.0, 0.8)
    check("graph renders to the requested size", _img.size == (378, 96))
else:
    print("  skip  graph render (Pillow not installed)")

print()
if fails:
    print("{} FAILED: {}".format(len(fails), ", ".join(fails)))
    sys.exit(1)
print("all tests passed")
