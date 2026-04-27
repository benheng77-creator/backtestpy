"""SP EMA pullback — uptrend regime, pullback to EMA20, reclaim bar.

BACKTEST READY
This strategy can be tested on market data now.
3-layer Recipe Spread is unavailable because the strategy does not expose optimization knobs.
Missing Recipe Spread knobs: w_sp01..w_sp08


Entry rule:
  EMA50[-1] > EMA200[-1]                          (uptrend)
  low[-2]   <= EMA20[-2]                          (pullback touched/pierced)
  close[-1] >  EMA20[-1]  AND  close[-1] > open[-1]   (reclaim bullish bar)
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from common.contract import to_arrays, make_get, build_bracket
from common.indicators import atr, ema


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAME = "30m"
LOOKBACK_BARS = 240

DIRECTION = "long"
SIGNAL_FAMILY = "trend_pullback"
FREQUENCY = "intraday"

DEFAULTS = {
    "eps_atr_mult":            0.10,
    "k_stop":                  1.20,
    "k_target":                2.40,
    "position_time_stop_bars": 24,
    "w_sp01": 1.0, "w_sp02": 1.0, "w_sp03": 1.0, "w_sp04": 1.0,
    "w_sp05": 1.0, "w_sp06": 1.0, "w_sp07": 1.0, "w_sp08": 1.0,
    "ema_fast":  20,
    "ema_mid":   50,
    "ema_slow":  200,
    "atr_n":     14,
}

_get, _get_int = make_get(DEFAULTS)


def _base_generate_signal(window) -> Optional[dict]:
    if not isinstance(window, list) or len(window) < LOOKBACK_BARS:
        return None
    o, h, l, c, v = to_arrays(window)
    e_fast = ema(c, _get_int("ema_fast"))
    e_mid  = ema(c, _get_int("ema_mid"))
    e_slow = ema(c, _get_int("ema_slow"))
    a = atr(h, l, c, _get_int("atr_n"))
    if any(x.size < 2 for x in (e_fast, e_mid, e_slow, a)):
        return None
    last_idx = -1
    prev_idx = -2
    finite = (np.isfinite(e_fast[last_idx]) and np.isfinite(e_fast[prev_idx])
              and np.isfinite(e_mid[last_idx]) and np.isfinite(e_slow[last_idx])
              and np.isfinite(a[last_idx]) and a[last_idx] > 0)
    if not finite:
        return None
    uptrend = e_mid[last_idx] > e_slow[last_idx]
    pulled  = l[prev_idx] <= e_fast[prev_idx]
    reclaim = (c[last_idx] > e_fast[last_idx]) and (c[last_idx] > o[last_idx])
    if not (uptrend and pulled and reclaim):
        return None
    bracket = build_bracket(float(c[last_idx]), float(a[last_idx]), _get)
    if bracket is None:
        return None
    # confidence: trend separation in ATR units
    sep = (e_mid[last_idx] - e_slow[last_idx]) / a[last_idx]
    confidence = float(min(1.0, max(0.0, sep / 5.0)))
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
    _base_generate_signal, "sp_ema_pullback", detectors=["EMA_PULLBACK"])
