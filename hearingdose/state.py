# -*- coding: utf-8 -*-
"""Persist the dose across restarts; recover for the time the app was closed."""

from __future__ import annotations

import json
import os
import time


def load_state(path: str, model) -> float:
    """
    Load dose state into `model` and apply recovery for the downtime since it
    was last saved (downtime is assumed quiet). Returns the downtime seconds.
    """
    if not os.path.exists(path):
        return 0.0
    try:
        with open(path, "r", encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return 0.0
    model.load_state(st)
    downtime = max(0.0, time.time() - float(st.get("saved_epoch", time.time())))
    model.apply_downtime(downtime)
    return downtime


def save_state(path: str, model) -> None:
    st = model.to_state()
    st["saved_epoch"] = time.time()
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, path)
    except Exception:
        pass
