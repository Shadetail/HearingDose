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
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- single-instance guard -------------------------------------------------
# Bind a private loopback port; if it's taken, another copy is already running
# (e.g. Startup launched it and you double-clicked too) so just exit quietly.
# A crash releases the port automatically, so restart is never blocked.
_GUARD = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    _GUARD.bind(("127.0.0.1", 49677))
    _GUARD.listen(1)
except OSError:
    if "--selftest" not in sys.argv:
        sys.exit(0)

from hearingdose.app import main

if __name__ == "__main__":
    main()
