# -*- coding: utf-8 -*-
"""
Audio capture + level measurement
=================================
Captures the PC's actual output stream via WASAPI loopback (the same thing OBS
records), A-weights it, and turns it into an estimated dBA at the ear.

Calibration (validated empirically on this machine):
    * WASAPI loopback captures the digital mix BEFORE the Windows master volume
      slider, so we read that slider separately via pycaw and add it back.
    * A full-scale 1 kHz sine measures -3.01 dBFS and, at max volume, hits
      `ceiling_db` SPL (your hardware ceiling, e.g. 114).
    * A-weighting is applied to the real signal, so the result is a true dBA -
      no flat-vs-A fudge factor like the old widget needed.

        dBA = ceiling_db + master_db + (dbfs_A + 3.01)

Only PC audio is measured - not ambient room noise. When nothing plays, WASAPI
delivers no data and we correctly read it as silence (ears recovering).
"""

from __future__ import annotations

import collections
import math
from dataclasses import dataclass

import numpy as np

SINE_DBFS = 20.0 * math.log10(0.5 ** 0.5)   # -3.0103 dBFS, a full-scale sine
SILENCE_DBA = 0.0                            # what we report when nothing is playing


# ----------------------------------------------------------------------------
# A-weighting (IEC 61672), pure
# ----------------------------------------------------------------------------
def a_weight_response(freqs: np.ndarray) -> np.ndarray:
    """Linear A-weighting gain per frequency (0 dB / gain 1.0 at 1 kHz)."""
    f = np.asarray(freqs, dtype=np.float64)
    f2 = f * f
    num = (12194.0 ** 2) * (f2 ** 2)
    den = ((f2 + 20.6 ** 2)
           * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2))
           * (f2 + 12194.0 ** 2))
    with np.errstate(divide="ignore", invalid="ignore"):
        r_a = np.where(den > 0, num / den, 0.0)
    # +2.00 dB normalisation makes the response exactly 0 dB at 1 kHz
    return r_a * (10.0 ** (2.0 / 20.0))


def dbfs_to_dba(dbfs_a: float, master_db: float, ceiling_db: float) -> float:
    """Map an A-weighted digital level to estimated dBA at the ear."""
    return ceiling_db + master_db + (dbfs_a - SINE_DBFS)


# ----------------------------------------------------------------------------
# Fixed-frame A-weighted energy meter
# ----------------------------------------------------------------------------
class AWeighter:
    """Precomputes A-weight gains for a fixed frame size and rate."""

    def __init__(self, rate: int, frame: int):
        self.rate = rate
        self.frame = frame
        freqs = np.fft.rfftfreq(frame, 1.0 / rate)
        self.gains = a_weight_response(freqs)
        # one-sided energy coefficients (Parseval): DC and Nyquist once, rest twice
        c = np.full(freqs.shape, 2.0)
        c[0] = 1.0
        if frame % 2 == 0:
            c[-1] = 1.0
        self._c = c
        self._n2 = float(frame) * float(frame)

    def mean_square(self, mono_frame: np.ndarray) -> float:
        """A-weighted mean square of one frame (relative to full scale)."""
        frame = np.asarray(mono_frame)
        if frame.ndim == 1:
            X = np.fft.rfft(frame)
            mag2 = (np.abs(X) * self.gains) ** 2
            return float(np.sum(self._c * mag2) / self._n2)
        X = np.fft.rfft(frame, axis=0)
        mag2 = (np.abs(X) * self.gains[:, None]) ** 2
        channel_ms = np.sum(self._c[:, None] * mag2, axis=0) / self._n2
        return float(np.max(channel_ms))


@dataclass
class LevelResult:
    dba: float          # estimated dBA at the ear (SILENCE_DBA when silent)
    dbfs_a: float       # raw A-weighted digital level (-inf when silent)
    master_db: float    # Windows master volume, dB (<= 0)
    silent: bool        # nothing playing this interval
    muted: bool         # endpoint muted
    ok: bool            # capture + volume read succeeded


# ----------------------------------------------------------------------------
# Loopback meter: owns the capture stream + the volume reader
# ----------------------------------------------------------------------------
class LoopbackMeter:
    def __init__(self, ceiling_db: float, poll_ms: int = 1000):
        self.ceiling_db = ceiling_db
        self._pa = None
        self._stream = None
        self._weigher = None
        self._pyaudio = None
        self._rate = 48000
        self._channels = 2
        self._frame = 4800
        self._buf = collections.deque()      # np.float32 blocks, shape (samples, channels)
        self._carry = np.zeros((0, self._channels), np.float32)  # leftover < one frame

        # volume reader (pycaw), re-resolved periodically for device swaps
        self._get_spk = None
        self._vol = None
        self._vol_count = 0
        self._vol_reeval = max(1, round(1000 / max(1, poll_ms)))
        self._open_volume()
        self._open_stream()

    # -- pycaw master volume ------------------------------------------------
    def _open_volume(self):
        try:
            from pycaw.utils import AudioUtilities
            self._get_spk = AudioUtilities.GetSpeakers
            self._vol = self._get_spk().EndpointVolume
        except Exception:
            self._vol = None

    def _read_volume(self):
        try:
            if self._vol is None or (self._vol_count % self._vol_reeval) == 0:
                if self._get_spk is None:
                    self._open_volume()
                if self._get_spk is None:
                    raise RuntimeError("pycaw volume endpoint unavailable")
                self._vol = self._get_spk().EndpointVolume
            self._vol_count += 1
            return float(self._vol.GetMasterVolumeLevel()), bool(self._vol.GetMute()), True
        except Exception:
            self._vol = None
            return 0.0, False, False

    # -- loopback stream ----------------------------------------------------
    def _open_stream(self):
        try:
            import pyaudiowpatch as pyaudio
            self._pyaudio = pyaudio
            self._pa = pyaudio.PyAudio()
            wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            spk = self._pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            lb = spk
            if not spk.get("isLoopbackDevice", False):
                for d in self._pa.get_loopback_device_info_generator():
                    if spk["name"] in d["name"]:
                        lb = d
                        break
            self._rate = int(lb["defaultSampleRate"])
            self._channels = max(1, int(lb["maxInputChannels"]))
            self._buf.clear()
            self._carry = np.zeros((0, self._channels), np.float32)
            frame = max(256, int(self._rate * 0.1))   # 100 ms analysis frame
            self._weigher = AWeighter(self._rate, frame)
            self._frame = frame
            self._device_name = lb["name"]
            self._stream = self._pa.open(
                format=pyaudio.paFloat32, channels=self._channels, rate=self._rate,
                input=True, input_device_index=lb["index"],
                frames_per_buffer=int(self._rate * 0.05),
                stream_callback=self._callback,
            )
            self._stream.start_stream()
            return True
        except Exception:
            self._teardown_stream()
            return False

    def _callback(self, in_data, frame_count, time_info, status):
        x = np.frombuffer(in_data, np.float32)
        x = x.reshape(-1, self._channels).copy()
        self._buf.append(x)
        return (None, self._pyaudio.paContinue)

    def _teardown_stream(self):
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        except Exception:
            pass
        try:
            if self._pa is not None:
                self._pa.terminate()
        except Exception:
            pass
        self._stream = None
        self._pa = None
        self._weigher = None
        self._buf.clear()
        self._carry = np.zeros((0, max(1, self._channels)), np.float32)

    # -- the per-tick read --------------------------------------------------
    def poll(self) -> LevelResult:
        master_db, muted, vol_ok = self._read_volume()

        # reopen a dead stream (device swap, sleep/wake, etc.)
        try:
            if self._stream is None or not self._stream.is_active():
                self._teardown_stream()
                self._open_stream()
            stream_ok = self._stream is not None and self._stream.is_active()
        except Exception:
            self._teardown_stream()
            stream_ok = False
        ok = vol_ok and stream_ok

        # drain everything the callback buffered since last poll
        blocks = []
        while self._buf:
            blocks.append(self._buf.popleft())
        samples = np.concatenate([self._carry] + blocks) if blocks else self._carry

        # bound memory: if the UI stalled (e.g. a modal dialog left open) the
        # callback keeps buffering. Never carry more than ~10 s of audio - dose
        # only advances by the clamped tick dt anyway, so old audio is moot.
        cap = self._rate * 10
        if len(samples) > cap:
            samples = samples[-cap:]

        frame = getattr(self, "_frame", 4800)
        weigher = self._weigher
        n_full = len(samples) // frame
        if weigher is None or n_full == 0:
            self._carry = samples
            if muted:
                return LevelResult(SILENCE_DBA, float("-inf"), master_db, True, True, ok)
            # no audio this interval -> silence (ears recovering)
            return LevelResult(SILENCE_DBA, float("-inf"), master_db, True, False, ok)

        total_ms = 0.0
        for i in range(n_full):
            total_ms += weigher.mean_square(samples[i * frame:(i + 1) * frame])
        self._carry = samples[n_full * frame:]
        mean_ms = total_ms / n_full

        if mean_ms <= 0 or muted:
            return LevelResult(SILENCE_DBA, float("-inf"), master_db, True, muted, ok)

        dbfs_a = 10.0 * math.log10(mean_ms)
        dba = dbfs_to_dba(dbfs_a, master_db, self.ceiling_db)
        return LevelResult(dba, dbfs_a, master_db, False, False, ok)

    def close(self):
        self._teardown_stream()
