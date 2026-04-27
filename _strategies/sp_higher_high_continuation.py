"""SP Higher-high continuation in uptrend.

BACKTEST READY
This strategy can be tested on market data now.
3-layer Recipe Spread is unavailable because the strategy does not expose optimization knobs.
Missing Recipe Spread knobs: w_sp01..w_sp08


Entry rule (long):
  EMA50[-1] > EMA200[-1]                                      (uptrend)
  Find last two pivot highs (highest in look-left/right window of `pivot_n`)
  pivot_high_recent > pivot_high_prev                         (HH structure)
  Find last pivot low between them; require pivot_low_recent > pivot_low_prev (HL)
  close[-1] > pivot_high_recent                               (continuation break)
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from common.contract import to_arrays, make_get, build_bracket
from common.indicators import atr, ema


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAME = "30m"
LOOKBACK_BARS = 240

DIRECTION = "long"
SIGNAL_FAMILY = "structure"
FREQUENCY = "intraday"

DEFAULTS = {
    "eps_atr_mult":            0.10,
    "k_stop":                  1.20,
    "k_target":                2.40,
    "position_time_stop_bars": 24,
    "w_sp01": 1.0, "w_sp02": 1.0, "w_sp03": 1.0, "w_sp04": 1.0,
    "w_sp05": 1.0, "w_sp06": 1.0, "w_sp07": 1.0, "w_sp08": 1.0,
    "pivot_n":   3,
    "ema_mid":   50,
    "ema_slow":  200,
    "atr_n":     14,
    "scan_back": 80,
}

_get, _get_int = make_get(DEFAULTS)


def _find_pivots(arr: np.ndarray, pivot_n: int, kind: str, scan_back: int
                 ) -> list[Tuple[int, float]]:
    """Return list of (idx, value) pivots within last `scan_back` bars
    (excluding the very last bar itself, which can't be confirmed)."""
    out: list[Tuple[int, float]] = []
    end = arr.size - 1
    start = max(pivot_n, end - scan_back)
    for i in range(start, end - pivot_n):
        left = arr[i - pivot_n:i]
        right = arr[i + 1:i + 1 + pivot_n]
        if left.size < pivot_n or right.size < pivot_n:
            continue
        if kind == "high":
            if arr[i] > left.max() and arr[i] > right.max():
                out.append((i, float(arr[i])))
        elif kind == "low":
            if arr[i] < left.min() and arr[i] < right.min():
                out.append((i, float(arr[i])))
    return out


def _base_generate_signal(window) -> Optional[dict]:
    if not isinstance(window, list) or len(window) < LOOKBACK_BARS:
        return None
    o, h, l, c, v = to_arrays(window)
    e_mid = ema(c, _get_int("ema_mid"))
    e_slow = ema(c, _get_int("ema_slow"))
    a = atr(h, l, c, _get_int("atr_n"))
    if a.size < 1 or not (np.isfinite(a[-1]) and a[-1] > 0):
        return None
    if not (np.isfinite(e_mid[-1]) and np.isfinite(e_slow[-1])):
        return None
    if not (e_mid[-1] > e_slow[-1]):
        return None
    pivot_n = _get_int("pivot_n")
    scan = _get_int("scan_back")
    highs = _find_pivots(h, pivot_n, "high", scan)
    lows = _find_pivots(l, pivot_n, "low", scan)
    if len(highs) < 2 or len(lows) < 2:
        return None
    ph_prev_idx, ph_prev_val = highs[-2]
    ph_recent_idx, ph_recent_val = highs[-1]
    if ph_recent_val <= ph_prev_val:
        return None
    # Find pivot lows around the recent and previous highs
    lows_before_prev = [pl for pl in lows if pl[0] < ph_prev_idx]
    lows_between = [pl for pl in lows if ph_prev_idx < pl[0] < ph_recent_idx]
    if not lows_before_prev or not lows_between:
        return None
    pl_prev = lows_before_prev[-1][1]
    pl_recent = lows_between[-1][1]
    if pl_recent <= pl_prev:
        return None
    if not (c[-1] > ph_recent_val):
        return None
    bracket = build_bracket(float(c[-1]), float(a[-1]), _get)
    if bracket is None:
        return None
    excess = (c[-1] - ph_recent_val) / a[-1]
    confidence = float(min(1.0, max(0.0, excess / 1.5)))
    return {
        "direction": +1,
        "entry": bracket["entry"],
        "stop": bracket["stop"],
        "tp": bracket["tp"],
        "confidence": confidence,
        "position_time_stop_bars": bracket["position_time_stop_bars"],
    }


# === BotXLLM panel-contract wrapper ===
from common.panel import make_panel_pair as _make_panel_pair
reset_state, generate_signal = _make_panel_pair(
    _base_generate_signal, "sp_higher_high_continuation", detectors=["HH_CONTINUATION"])
