# -*- coding: utf-8 -*-
"""
Hearing Dose Meter - launcher
=============================
Realtime hearing-damage dosimeter. Reads the PC's actual audio output via
WASAPI loopback, estimates dBA at the ear, and tracks a running daily noise
dose with front-loaded log-time recovery.

Run (any of):
    * double-click the Startup shortcut (created by setup), or this file
    * pythonw HearingDose.pyw
    * python  HearingDose.pyw --selftest     (one reading to stdout, then exit)

Settings live in HearingDose.ini; dose state in HearingDose.state.json - both
created next to this file. State survives close / crash / restart: on launch it
resumes where it left off, treating the gap as quiet (recovery).
"""
import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- single-instance guard -------------------------------------------------
# A named mutex: if it already exists, another copy is already running (e.g.
# Startup launched it and you double-clicked too) so just exit quietly. The OS
# releases it on process death, so a crash never blocks a restart. (This used
# to bind a loopback port, but that port sat in Windows' ephemeral range, so
# any process's random outbound connection could take it first and the app
# would silently refuse to start.)
# use_last_error=True: the plain ctypes.windll.GetLastError() can be polluted
# by ctypes' own intervening API calls; this captures it at call time.
_ERROR_ALREADY_EXISTS = 183
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_GUARD = _kernel32.CreateMutexW(None, False, "HearingDoseMeter.single-instance")
if _GUARD and ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
    if "--selftest" not in sys.argv:
        sys.exit(0)

if __name__ == "__main__":
    # Under pythonw a failed import (missing/broken dependency, e.g. after a
    # Python upgrade) has no console to print to -> the app would silently
    # never appear. Surface it in a message box instead. main() itself already
    # reports its own startup failures via tkinter.
    try:
        from hearingdose.app import main
    except Exception:
        import traceback
        ctypes.windll.user32.MessageBoxW(
            None, traceback.format_exc(),
            "Hearing Dose Meter failed to start", 0x10)  # MB_ICONERROR
        raise
    main()
