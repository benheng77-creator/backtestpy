"""SP Keltner squeeze break — sustained KC contraction then close > KC upper.

BACKTEST READY
This strategy can be tested on market data now.
3-layer Recipe Spread is unavailable because the strategy does not expose optimization knobs.
Missing Recipe Spread knobs: w_sp01..w_sp08


Entry rule:
  ATR%(atr/close) <= squeeze_atr_pct sustained for at least squeeze_min_bars
  AND close[-1] > kc_upper[-1]
  AND close[-1] > open[-1]
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from common.contract import to_arrays, make_get, build_bracket
from common.indicators import atr, keltner


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAME = "30m"
LOOKBACK_BARS = 240

DIRECTION = "long"
SIGNAL_FAMILY = "breakout"
FREQUENCY = "intraday"

DEFAULTS = {
    "eps_atr_mult":            0.10,
    "k_stop":                  1.20,
    "k_target":                2.40,
    "position_time_stop_bars": 24,
    "w_sp01": 1.0, "w_sp02": 1.0, "w_sp03": 1.0, "w_sp04": 1.0,
    "w_sp05": 1.0, "w_sp06": 1.0, "w_sp07": 1.0, "w_sp08": 1.0,
    "kc_n":             20,
    "kc_k":             1.5,
    "atr_n":            14,
    "squeeze_atr_pct":  0.012,
    "squeeze_min_bars": 8,
}

_get, _get_int = make_get(DEFAULTS)


def _base_generate_signal(window) -> Optional[dict]:
    if not isinstance(window, list) or len(window) < LOOKBACK_BARS:
        return None
    o, h, l, c, v = to_arrays(window)
    _mid, kc_u, _kc_l = keltner(h, l, c, _get_int("kc_n"), _get("kc_k"))
    a = atr(h, l, c, _get_int("atr_n"))
    if a.size < 1 or kc_u.size < 1:
        return None
    if not (np.isfinite(kc_u[-1]) and np.isfinite(a[-1]) and a[-1] > 0):
        return None
    consec = _get_int("squeeze_min_bars")
    thr = _get("squeeze_atr_pct")
    # Use bars [-(consec+1):-1] (i.e. excluding the latest breakout bar) for sustained-squeeze test.
    if a.size < consec + 1:
        return None
    atr_pct_tail = a[-(consec + 1):-1] / np.where(c[-(consec + 1):-1] > 0, c[-(consec + 1):-1], np.nan)
    if np.any(~np.isfinite(atr_pct_tail)):
        return None
    sustained = bool(np.all(atr_pct_tail <= thr))
    breakout = (c[-1] > kc_u[-1]) and (c[-1] > o[-1])
    if not (sustained and breakout):
        return None
    bracket = build_bracket(float(c[-1]), float(a[-1]), _get)
    if bracket is None:
        return None
    excess = (c[-1] - kc_u[-1]) / a[-1]
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
    _base_generate_signal, "sp_keltner_squeeze_break", detectors=["KC_SQUEEZE_BREAK"])
