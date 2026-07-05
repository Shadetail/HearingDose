# -*- coding: utf-8 -*-
"""
Dose engine
===========
Turns a stream of instantaneous A-weighted levels (dBA at the ear) into a
running "daily dose" - the fraction of a safe day's noise exposure you have
spent - and models how that dose recovers during quiet time.

Two halves, with very different confidence levels:

ACCRUAL  (spending the budget)  -- standardised, defensible.
    NIOSH REL: 100% dose = 85 dBA for 8 hours, 3 dB exchange rate.
    Permitted time  T(L) = 8h / 2**((L - 85)/3).
    Dose accrues at rate 1/T(L) per second, for any level above a floor
    (default 80 dBA, the NIOSH measurement threshold). Integrated over the
    real signal, so quiet passages cost little and loud drops cost a lot.

RECOVERY (refunding the budget) -- a best-estimate MODEL, not a standard.
    Temporary threshold shift recovers roughly linearly with the *logarithm*
    of quiet time: front-loaded, steep early, long flat tail. The occupational
    standards don't actually specify a continuous recovery curve - they just
    assume ~16 h of quiet resets a day's dose. We keep that ~16 h full-reset
    window but give it the empirically-observed log shape instead of a cliff.

    This is deliberately a real best estimate, not a conservative hedge: it
    aims at the middle of the plausible range so the number stays trustworthy
    near the limit. Reality could land either side of it. See RECOVERY_NOTE.

Nothing here touches audio or the GUI - it's all pure arithmetic so it can be
unit-tested (see tests/test_dose.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


RECOVERY_NOTE = (
    "Recovery is a best-estimate log-time model, not a measured standard. "
    "Full audiometric recovery can still hide synaptic damage, so treat the "
    "budget as guidance, not a guarantee."
)


# ----------------------------------------------------------------------------
# Accrual: NIOSH permitted time and dose rate
# ----------------------------------------------------------------------------
@dataclass
class DoseParams:
    # --- NIOSH accrual criterion ---
    criterion_db: float = 85.0      # level that gives 100% dose in criterion_hours
    criterion_hours: float = 8.0    # ... over this many hours
    exchange_db: float = 3.0        # every +exchange_db halves the permitted time
    threshold_db: float = 80.0      # below this, no dose accrues at all

    # --- recovery (best-estimate log model) ---
    recovery_hours: float = 16.0        # quiet time to fully clear a 100% dose
    recovery_t1_min: float = 3.0        # front-load constant (minutes); smaller = steeper early
    recovery_ceiling_db: float = 70.0   # recovery only progresses below this level;
                                        # between it and threshold_db the dose just holds

    def permitted_seconds(self, dba: float) -> float:
        """NIOSH permitted duration at a constant level, in seconds."""
        return self.criterion_hours * 3600.0 * (
            2.0 ** (-(dba - self.criterion_db) / self.exchange_db)
        )

    def dose_rate_per_sec(self, dba: float) -> float:
        """Fraction of a full daily dose accrued per second at this level."""
        if dba < self.threshold_db:
            return 0.0
        return 1.0 / self.permitted_seconds(dba)


# ----------------------------------------------------------------------------
# The stateful accumulator
# ----------------------------------------------------------------------------
@dataclass
class DoseModel:
    """
    Feed it (dba, dt) samples; read .dose (1.0 == 100% of a safe day).

    State machine per update, by current level:
      >= threshold_db          -> ACCRUE  (spend budget, reset recovery clock)
      <  recovery_ceiling_db    -> RECOVER (log-time refund toward zero)
      in between                -> HOLD    (neither; ears neither loaded nor resting)
    """
    params: DoseParams = field(default_factory=DoseParams)

    dose: float = 0.0                 # current dose fraction
    _dose_at_quiet_start: float = 0.0  # snapshot when the last quiet period began
    quiet_seconds: float = 0.0        # accumulated quiet time feeding the recovery curve

    # lightweight running stats for the UI (not used by the model itself)
    peak_dose: float = 0.0
    seconds_accrued: float = 0.0      # wall-clock spent above threshold this session

    def update(self, dba: float, dt: float) -> float:
        p = self.params
        if dt <= 0:
            return self.dose

        if dba >= p.threshold_db:
            # ACCRUE
            self.dose += dt * p.dose_rate_per_sec(dba)
            self.quiet_seconds = 0.0
            self._dose_at_quiet_start = self.dose
            self.seconds_accrued += dt
        elif dba < p.recovery_ceiling_db:
            # RECOVER
            self.quiet_seconds += dt
            self.dose = self._recovered(self._dose_at_quiet_start, self.quiet_seconds)
        # else: HOLD -- leave dose and the recovery clock frozen

        if self.dose > self.peak_dose:
            self.peak_dose = self.dose
        return self.dose

    def _recovered(self, dose0: float, quiet_s: float) -> float:
        """Log-time recovery of dose0 after quiet_s seconds of quiet."""
        if dose0 <= 0.0:
            return 0.0
        return dose0 * self.recovery_fraction(quiet_s)

    def recovery_fraction(self, quiet_s: float) -> float:
        """
        Fraction of dose *remaining* after quiet_s of continuous quiet.
        1.0 at t=0, 0.0 at recovery_hours, log-shaped (front-loaded) between.
        """
        p = self.params
        if quiet_s <= 0.0:
            return 1.0
        t1 = p.recovery_t1_min * 60.0
        T = p.recovery_hours * 3600.0
        span = math.log10(T / t1)
        if span <= 0:
            return 0.0
        frac = 1.0 - math.log10((t1 + quiet_s) / t1) / span
        return min(1.0, max(0.0, frac))

    # -- helpers for the UI ------------------------------------------------
    def seconds_to_full(self, dba: float, target: float = 1.0) -> float:
        """At a *constant* dba, seconds until dose reaches `target` (inf if never)."""
        rate = self.params.dose_rate_per_sec(dba)
        if rate <= 0 or self.dose >= target:
            return float("inf")
        return (target - self.dose) / rate

    def seconds_to_clear(self, to_fraction: float = 0.05) -> float:
        """
        From the current point on the recovery curve, seconds of further quiet
        needed to fall to `to_fraction` of the dose that started this quiet
        period. Returns 0 if already there.
        """
        p = self.params
        if self._dose_at_quiet_start <= 0:
            return 0.0
        t1 = p.recovery_t1_min * 60.0
        T = p.recovery_hours * 3600.0
        span = math.log10(T / t1)
        # quiet time at which recovery_fraction == to_fraction
        target_quiet = t1 * (10.0 ** ((1.0 - to_fraction) * span)) - t1
        return max(0.0, target_quiet - self.quiet_seconds)

    def reset(self) -> None:
        self.dose = 0.0
        self._dose_at_quiet_start = 0.0
        self.quiet_seconds = 0.0
        self.peak_dose = 0.0
        self.seconds_accrued = 0.0

    # -- persistence -------------------------------------------------------
    def to_state(self) -> dict:
        return {
            "dose": self.dose,
            "dose_at_quiet_start": self._dose_at_quiet_start,
            "quiet_seconds": self.quiet_seconds,
            "peak_dose": self.peak_dose,
            "seconds_accrued": self.seconds_accrued,
        }

    def load_state(self, st: dict) -> None:
        self.dose = float(st.get("dose", 0.0))
        self._dose_at_quiet_start = float(st.get("dose_at_quiet_start", self.dose))
        self.quiet_seconds = float(st.get("quiet_seconds", 0.0))
        self.peak_dose = float(st.get("peak_dose", self.dose))
        self.seconds_accrued = float(st.get("seconds_accrued", 0.0))

    def apply_downtime(self, seconds: float) -> None:
        """Account for `seconds` the app wasn't running, treated as quiet."""
        if seconds <= 0:
            return
        self.quiet_seconds += seconds
        self.dose = self._recovered(self._dose_at_quiet_start, self.quiet_seconds)
