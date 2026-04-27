"""Shared BotXLLM strategy contract helpers.

to_arrays(window)         -> (o, h, l, c, v) float64 ndarrays
make_get(DEFAULTS)        -> (_get, _get_int) closures, env-var overridable
build_bracket(close, atr, _get) -> dict(entry, stop, tp, position_time_stop_bars) | None
"""
from __future__ import annotations

import os
from typing import Callable, Optional, Tuple

import numpy as np


def to_arrays(window) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(window)
    o = np.empty(n, dtype=np.float64)
    h = np.empty(n, dtype=np.float64)
    l = np.empty(n, dtype=np.float64)
    c = np.empty(n, dtype=np.float64)
    v = np.empty(n, dtype=np.float64)
    for i, b in enumerate(window):
        o[i] = b["open"]; h[i] = b["high"]; l[i] = b["low"]
        c[i] = b["close"]; v[i] = b.get("volume", 0.0)
    return o, h, l, c, v


def make_get(defaults: dict) -> Tuple[Callable[[str], float], Callable[[str], int]]:
    def _get(name: str) -> float:
        env = os.environ.get(f"BOTX_RECIPE_{name.upper()}")
        if env is not None:
            try:
                return float(env)
            except ValueError:
                pass
        return float(defaults[name])

    def _get_int(name: str) -> int:
        return int(_get(name))

    return _get, _get_int


def build_bracket(close: float, atr_value: float, _get: Callable[[str], float]) -> Optional[dict]:
    if not (np.isfinite(close) and np.isfinite(atr_value) and atr_value > 0):
        return None
    eps = _get("eps_atr_mult") * atr_value
    entry = close + eps
    stop = entry - _get("k_stop") * atr_value
    tp = entry + _get("k_target") * atr_value
    if not (np.isfinite(entry) and np.isfinite(stop) and np.isfinite(tp)):
        return None
    if stop >= entry or tp <= entry:
        return None
    return {
        "entry": float(entry),
        "stop": float(stop),
        "tp": float(tp),
        "position_time_stop_bars": int(_get("position_time_stop_bars")),
    }
