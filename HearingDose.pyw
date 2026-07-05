#!/usr/bin/env pythonw
# -*- coding: utf-8 -*-
"""
Hearing Dose Meter - launcher
=============================
Realtime hearing-damage dosimeter. Reads the PC's actual audio output via
WASAPI loopback, estimates dBA at the ear, and tracks a running daily noise
dose with front-loaded log-time recovery.

Run: double-click, or  `pythonw HearingDose.pyw`
Self-test (prints one reading and exits):  `python HearingDose.pyw --selftest`

Settings live in HearingDose.ini (created next to this file on first run).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hearingdose.app import main

if __name__ == "__main__":
    main()
