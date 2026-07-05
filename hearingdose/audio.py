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

Device selection
    Calibration (`ceiling_db`) is only valid for ONE playback device - the gear
    it was measured on. So the meter can lock onto a specific output device: it
    captures the loopback of that device and reads that device's own volume
    slider. Audio played to any other device produces no loopback frames on the
    locked device, so it's read as silence and never adds to the dose. With no
    device locked it falls back to following the current Windows default output.
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


def _name_prefix_match(prefix: str, name: str) -> bool:
    """True if `prefix` starts `name` and stops on a word boundary.

    This keeps the loose device-name fallback useful for names like
    "Speakers" -> "Speakers (Realtek)" without letting "D1" match "D10".
    """
    if not prefix or not name.startswith(prefix):
        return False
    if len(name) == len(prefix):
        return True
    return not name[len(prefix)].isalnum()


def endpoint_name_matches(expected: str, candidate: str) -> bool:
    """Conservative match for two endpoint display names."""
    if not expected or not candidate:
        return False
    return (expected == candidate
            or _name_prefix_match(expected, candidate)
            or _name_prefix_match(candidate, expected))


def loopback_matches(render_name: str, loopback_name: str) -> bool:
    """True if `loopback_name` is the WASAPI loopback of render endpoint
    `render_name`. Loopback devices are named '<render endpoint> [Loopback]', so
    prefer that exact form and fall back to a conservative prefix match for odd
    drivers."""
    suffix = " [Loopback]"
    if not render_name or not loopback_name.endswith(suffix):
        return False
    return endpoint_name_matches(render_name, loopback_name[:-len(suffix)])


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
    def __init__(self, ceiling_db: float, poll_ms: int = 1000, device_name: str = ""):
        self.ceiling_db = ceiling_db
        # "" = follow the current Windows default output; otherwise the exact
        # render-endpoint name to lock onto (its calibration + volume only).
        self.device_name = (device_name or "").strip()
        self.active_device = ""               # render endpoint currently captured
        self._pa = None
        self._stream = None
        self._weigher = None
        self._pyaudio = None
        self._rate = 48000
        self._channels = 2
        self._frame = 4800
        self._buf = collections.deque()      # np.float32 blocks, shape (samples, channels)
        self._carry = np.zeros((0, self._channels), np.float32)  # leftover < one frame

        # master-volume reader (pycaw), bound to the captured device when the
        # stream opens; re-resolved on device swap / after a failed read
        self._vol = None
        self._open_stream()

    # -- device discovery ---------------------------------------------------
    @staticmethod
    def list_output_devices():
        """Names of currently-available WASAPI output (render) endpoints.

        These are the human-facing 'playback devices'; each maps 1:1 to a
        loopback capture device by name. Used to populate the device picker.
        """
        names = []
        try:
            import pyaudiowpatch as pyaudio
            pa = pyaudio.PyAudio()
            try:
                wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
                for i in range(pa.get_device_count()):
                    d = pa.get_device_info_by_index(i)
                    if (d.get("hostApi") == wasapi["index"]
                            and int(d.get("maxOutputChannels", 0)) > 0
                            and not d.get("isLoopbackDevice", False)):
                        nm = d.get("name", "")
                        if nm and nm not in names:
                            names.append(nm)
            finally:
                pa.terminate()
        except Exception:
            pass
        return names

    # -- pycaw master volume ------------------------------------------------
    def _open_volume(self, render_name):
        """Bind the master-volume interface to a specific render endpoint (by
        name). Blank name (default-follow, if the name can't be matched) falls
        back to the Windows default endpoint."""
        try:
            from pycaw.utils import AudioUtilities
            vol = self._find_endpoint_volume(render_name) if render_name else None
            if vol is None and not self.device_name:
                vol = AudioUtilities.GetSpeakers().EndpointVolume
            self._vol = vol
        except Exception:
            self._vol = None

    def _find_endpoint_volume(self, render_name):
        """IAudioEndpointVolume for the active render endpoint named
        `render_name`, or None if it isn't a currently-active endpoint."""
        import warnings
        from pycaw.utils import AudioUtilities
        from pycaw.constants import EDataFlow, DEVICE_STATE
        try:
            with warnings.catch_warnings():
                # GetAllDevices probes every property store; disconnected
                # endpoints raise per-property COMErrors we don't care about
                warnings.simplefilter("ignore")
                devs = AudioUtilities.GetAllDevices(
                    EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
        except Exception:
            return None
        exact = loose = None
        for d in devs:
            try:
                fn = d.FriendlyName
            except Exception:
                continue
            if fn == render_name:
                exact = d
                break
            if loose is None and endpoint_name_matches(render_name, fn):
                loose = d
        dev = exact or loose
        if dev is None:
            return None
        try:
            return dev.EndpointVolume
        except Exception:
            return None

    def _read_volume(self):
        try:
            if self._vol is None:
                self._open_volume(self.active_device)
            if self._vol is None:
                raise RuntimeError("volume endpoint unavailable")
            return float(self._vol.GetMasterVolumeLevel()), bool(self._vol.GetMute()), True
        except Exception:
            self._vol = None
            return 0.0, False, False

    # -- loopback stream ----------------------------------------------------
    def _find_loopback(self, render_name):
        """The loopback capture device for a render endpoint name, or None.
        Prefers the exact '<render> [Loopback]' device over a substring hit."""
        match = None
        for d in self._pa.get_loopback_device_info_generator():
            if d["name"] == render_name + " [Loopback]":
                return d
            if match is None and loopback_matches(render_name, d["name"]):
                match = d
        return match

    def _open_stream(self):
        try:
            import pyaudiowpatch as pyaudio
            self._pyaudio = pyaudio
            self._pa = pyaudio.PyAudio()
            wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)

            # Capture the locked device if one is set, else the current default.
            target = self.device_name
            if not target:
                spk = self._pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
                target = spk["name"]

            lb = self._find_loopback(target)
            if lb is None:
                # A locked device that's unplugged / disabled lands here. Do NOT
                # fall back to another device: its calibration wouldn't apply and
                # we'd count audio the user never meant to measure. Leave the
                # stream dead -> UI shows it unavailable and no dose accrues.
                raise RuntimeError("output device unavailable: {!r}".format(target))

            self._rate = int(lb["defaultSampleRate"])
            self._channels = max(1, int(lb["maxInputChannels"]))
            self._buf.clear()
            self._carry = np.zeros((0, self._channels), np.float32)
            frame = max(256, int(self._rate * 0.1))   # 100 ms analysis frame
            self._weigher = AWeighter(self._rate, frame)
            self._frame = frame
            self.active_device = target
            self._device_name = lb["name"]
            # read THIS device's volume slider (not the default's) so the
            # calibration matches the audio we're actually capturing
            self._open_volume(target)
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
        self._vol = None            # volume interface belongs to the dead device
        self.active_device = ""
        self._buf.clear()
        self._carry = np.zeros((0, max(1, self._channels)), np.float32)

    # -- the per-tick read --------------------------------------------------
    def poll(self) -> LevelResult:
        # (Re)open a dead stream first (device swap, sleep/wake, unplug) so the
        # volume read below binds to the right device on the same tick. Opening
        # the stream also resolves that device's volume interface.
        try:
            if self._stream is None or not self._stream.is_active():
                self._teardown_stream()
                self._open_stream()
            stream_ok = self._stream is not None and self._stream.is_active()
        except Exception:
            self._teardown_stream()
            stream_ok = False

        master_db, muted, vol_ok = self._read_volume()
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
