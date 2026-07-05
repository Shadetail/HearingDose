# Hearing Dose Meter

A realtime hearing-damage dosimeter for the PC. It reads your computer's
**actual audio output** (WASAPI loopback — the same stream OBS records),
estimates the **dBA at your ear**, and tracks a running **daily noise dose**:
how much of a safe day's listening you've spent, with a live graph and a
warning when you hit the limit.

This is the "budget + cooldown" evolution of `SafeTimeWidget.pyw`. Where the
widget answers *"at this volume, how long is safe?"* (an instantaneous rate),
this answers *"how much have I already spent, and how fast right now?"* (the
running integral).

![concept](docs-placeholder)

## How it works

```
dBA  = ceiling_db + master_volume_dB + (measured_A_weighted_dBFS + 3.01) + offset_db
dose += dt / T(dBA)          T(dBA) = 8h / 2**((dBA-85)/3)     # NIOSH
```

1. **Capture** the output stream via WASAPI loopback (`pyaudiowpatch`).
2. **A-weight** it with a real IEC-61672 filter and compute the level — so the
   result is a true dBA, not the flat-vs-A fudge the widget needed.
3. **Add back the volume slider.** Loopback captures the mix *before* Windows
   master volume (verified on this machine), so we read the slider via `pycaw`
   and add it. Your `ceiling_db` (119) anchors full-scale-at-max to SPL.
4. **Integrate** into a NIOSH dose. 100% = 85 dBA for 8 h, 3 dB exchange rate.
5. **Recover** during quiet, front-loaded and log-shaped (see below).

## Two halves, two confidence levels

**Accrual (spending the budget) — standardised.** NIOSH REL, the same model the
widget already uses. Integrated over the real signal, so quiet passages cost
little and loud drops cost a lot. Trust this.

**Recovery (refunding the budget) — a best estimate, not a standard.** Temporary
threshold shift recovers roughly linearly with the *logarithm* of quiet time:
steep early, long flat tail. The occupational standards don't actually specify a
continuous recovery curve — they just assume ~16 h of quiet resets a day's dose.
We keep that ~16 h window but give it the empirical log shape.

This is deliberately aimed at the **middle of the plausible range**, not a
conservative over-estimate. An over-hedged number you don't believe just makes
you listen longer; a best-estimate you respect makes you wary near the limit.
Tune `recovery_hours`, `recovery_t1_min`, and `recovery_ceiling_db` in the ini.

> Full audiometric recovery can still hide synaptic damage ("hidden hearing
> loss"). The budget is guidance, not a guarantee.

## Limitations (read these)

- **PC audio only.** Loopback hears what the computer plays, nothing in the
  room. Take the headphones off for a loud environment and the meter is blind.
- **The absolute dBA is only as good as `ceiling_db`.** That per-headphone,
  per-fit calibration can be several dB off and is the dominant error. The
  *temporal* accounting (accrual vs recovery over time) is the trustworthy part.
- **Any gain stage Windows can't read breaks calibration** (an analog volume
  knob after the DAC). Your GO Link is Windows-controlled, so you're fine.
- **Downtime is assumed quiet.** Close the app and reopen, and it recovers the
  dose for the elapsed real time.
- **Numbers run lower than the old widget** for bass-heavy music — because real
  A-weighting correctly discounts bass, which the widget's flat default did not.

## Run

```
pip install PyAudioWPatch pycaw numpy Pillow   # numpy/pycaw usually present;
                                               # Pillow = antialiased graph
                                               # (falls back to plain Tk if absent)
pythonw HearingDose.pyw                     # double-click also works
python  HearingDose.pyw --selftest          # one reading to stdout, then exit
python  tests/test_dose.py                  # unit tests
```

Controls: **drag** = move · **right-click** = menu (Reload / Edit .ini / Reset
dose / Quit) · **Esc** = quit.

## Files

- `HearingDose.pyw` — launcher
- `hearingdose/dose.py` — dose engine (NIOSH accrual + log recovery), pure & tested
- `hearingdose/audio.py` — loopback capture, A-weighting, calibration → dBA
- `hearingdose/config.py` — the commented `.ini`
- `hearingdose/state.py` — dose persistence across restarts
- `hearingdose/app.py` — the tkinter GUI
- `tests/test_dose.py` — unit tests
- `SafeTimeWidget.pyw` — the original instantaneous meter (reference)
