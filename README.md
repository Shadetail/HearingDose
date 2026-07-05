# Hearing Dose Meter

A realtime hearing-safety meter for Windows. It listens to your PC's **actual
audio output** (WASAPI loopback — the same stream OBS records), estimates the
**dBA at your ear**, and tracks a running **daily noise dose**: how much of a
safe day's listening you've spent, with a live graph and a warning when you hit
the limit. Dose accrues while you listen and recovers (front-loaded, log-shaped)
during quiet — and it survives closing the app, crashes, and restarts.

![the dose graph: a filled loudness envelope with the daily-dose area rising and recovering](docs/graph.png)

---

## ⚠️ First: calibrate it to *your* gear (about 2 minutes)

The app measures the digital signal and your Windows volume, but it **cannot know
how physically loud your specific headphones + DAC/amp get** — that's the one
thing you tell it, via a single number: **`ceiling_db`**.

> **`ceiling_db`** = the level in **dB SPL at your ear** when a maximum (0 dBFS)
> signal plays at **100% Windows volume** through your gear. It's your hardware's
> loudness ceiling. Everything else — the live volume slider, the actual loudness
> of what's playing — the app measures for you.

### Easiest way to set it — match a phone SPL meter

1. **Run the app once** (see *Install* below) so it creates `HearingDose.ini`,
   then right-click the panel → **Edit settings**.
2. Install a sound-level-meter app on your phone (**NIOSH SLM** on iOS,
   **Decibel X** on Android) and set its frequency weighting to **A** so it
   reads **dBA**. (Most generic "Sound Meter" apps only show an unlabeled dB
   with no weighting choice — that's usually unweighted SPL, which over-reads
   bass; Decibel X exposes the A-weighting toggle for free.)
3. Play steady music you know at a **normal-to-loud** level. Press the phone's
   mic against one earcup, right over the driver, and read its dBA.
4. Edit **`ceiling_db`** in the `.ini` and right-click → **Reload** until the
   app's big dBA number matches the phone. Higher `ceiling_db` → higher reading.
   (There's also `offset_db` if you prefer to leave `ceiling_db` on a spec value
   and just nudge the final number.)

That's it — the app now scales correctly across your whole volume range.

### Or estimate it from spec sheets

```
ceiling_db  ≈  headphone sensitivity (dB SPL per volt)  +  20·log10(amp max output, Vrms)
```

Example: 100 dB/V headphones on a DAC that swings 2 Vrms →
`100 + 20·log10(2) ≈ 106 dB`. Use it as a starting point, then verify with the
phone method — sensitivity specs and ear coupling vary enough that ±several dB is
normal.

> **Safety:** don't calibrate by playing a full-scale tone at 100% volume — that
> is the loudest your rig can physically produce. Calibrate at a normal listening
> level; the model scales up from there.

The default in the `.ini` (`ceiling_db = 119`) is for the author's HEDD D1 + iFi
GO Link 2. **It is almost certainly wrong for your gear — change it first.**

---

## How it works

```
dBA  = ceiling_db + windows_volume_dB + (measured_A_weighted_dBFS + 3.01) + offset_db
dose += dt / T(dBA)          T(dBA) = 8h / 2**((dBA - 85) / 3)     # NIOSH
```

1. **Capture** the output stream via WASAPI loopback (`pyaudiowpatch`).
2. **A-weight** it with a real IEC-61672 filter → a true dBA.
3. **Add back the volume slider.** Loopback captures the mix *before* Windows
   master volume, so the app reads that slider via `pycaw` and adds it.
4. **Integrate** a NIOSH dose: 100% = 85 dBA for 8 h, 3 dB exchange rate.
5. **Recover** during quiet with a front-loaded, log-shaped curve.

## Install

```
pip install PyAudioWPatch pycaw numpy Pillow
```
(`Pillow` powers the antialiased graph; it falls back to plain Tk if absent.)

```
pythonw HearingDose.pyw            # run it (double-click also works)
python  HearingDose.pyw --selftest # one reading to stdout, then exit
python  tests/test_dose.py         # unit tests
```

Controls: **drag** = move · **right-click** = menu (Reload / Edit .ini / Reset
dose / Quit).

### Run at login (optional)

Create a shortcut in your Startup folder
(`shell:startup`) whose **Target** is your `pythonw.exe` with the script as an
argument (this avoids the flaky `.pyw` file association):

```
Target:  C:\path\to\pythonw.exe  "C:\path\to\HearingDose.pyw"
Start in: C:\path\to\HearingDamage
```

## The model — two confidence levels

**Accrual (spending the budget) — standardised.** NIOSH REL, integrated over the
real signal, so quiet passages cost little and loud drops cost a lot. Trust this.

**Recovery (refunding the budget) — a best estimate, not a standard.** Temporary
threshold shift recovers roughly linearly with the *logarithm* of quiet time:
steep early, long tail. The standards only assume ~16 h of quiet resets a day's
dose; this keeps that window but gives it the empirical log shape. It aims at the
**middle of the plausible range**, not a safe over-estimate — a number you can
trust near the limit. Tune `recovery_hours`, `recovery_t1_min`,
`recovery_ceiling_db` in the `.ini`.

> Full audiometric recovery can still hide synaptic damage ("hidden hearing
> loss"). Treat the budget as guidance, not a guarantee.

## Notes & limitations

- **Calibrate first.** The absolute dBA is only as good as `ceiling_db`; that's
  the dominant uncertainty. The *temporal* accounting (accrual vs recovery over
  time) is the trustworthy part regardless.
- **PC audio only.** Loopback hears what the computer plays, nothing in the room.
- **Short tones integrate correctly.** The meter uses A-weighted energy (Leq),
  and the 3 dB rule is equal-energy, so a 1-second tone contributes its full
  energy regardless of how it lines up with the ~1 s sampling window. For sparse
  or quieter test tones, lower `poll_ms` (e.g. `250`) so each sample aligns more
  tightly with the tone and stays above the accrual threshold.
- **Any gain stage Windows can't read breaks calibration** (e.g. an analog knob
  after the DAC). Keep volume software-controlled.
- **Downtime is assumed quiet:** close and reopen and it recovers the dose for
  the elapsed real time.
- Numbers run lower than a flat SPL meter for bass-heavy music — real A-weighting
  correctly discounts bass.

## Files

- `HearingDose.pyw` — launcher (with single-instance guard)
- `hearingdose/dose.py` — dose engine (NIOSH accrual + log recovery), pure & tested
- `hearingdose/audio.py` — loopback capture, A-weighting, calibration → dBA
- `hearingdose/config.py` — the commented, clamped `.ini`
- `hearingdose/state.py` — dose persistence across restarts
- `hearingdose/app.py` — the tkinter GUI + antialiased graph
- `tests/test_dose.py` — unit tests

## License

MIT — see [LICENSE](LICENSE).
