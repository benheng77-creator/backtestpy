"""SP ATR expansion + new high break.

BACKTEST READY
This strategy can be tested on market data now.
3-layer Recipe Spread is unavailable because the strategy does not expose optimization knobs.
Missing Recipe Spread knobs: w_sp01..w_sp08


Entry rule:
  pct_rank(ATR, atrp_lookback) >= atrp_high   (volatility expanding)
  AND close[-1] > rolling-N high[-2]          (new lookback-N high vs prior bar)
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from common.contract import to_arrays, make_get, build_bracket
from common.indicators import atr, pct_rank


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAME = "30m"
LOOKBACK_BARS = 240

DIRECTION = "long"
SIGNAL_FAMILY = "volatility_break"
FREQUENCY = "intraday"

DEFAULTS = {
    "eps_atr_mult":            0.15,
    "k_stop":                  1.20,
    "k_target":                2.50,
    "position_time_stop_bars": 24,
    "w_sp01": 1.0, "w_sp02": 1.0, "w_sp03": 1.0, "w_sp04": 1.0,
    "w_sp05": 1.0, "w_sp06": 1.0, "w_sp07": 1.0, "w_sp08": 1.0,
    "atr_n":         14,
    "atrp_lookback": 100,
    "atrp_high":     0.80,
    "hh_n":          50,
}

_get, _get_int = make_get(DEFAULTS)


def _base_generate_signal(window) -> Optional[dict]:
    if not isinstance(window, list) or len(window) < LOOKBACK_BARS:
        return None
    o, h, l, c, v = to_arrays(window)
    a = atr(h, l, c, _get_int("atr_n"))
    if a.size < 2 or not np.isfinite(a[-1]) or a[-1] <= 0:
        return None
    pr = pct_rank(a, _get_int("atrp_lookback"))
    if not np.isfinite(pr):
        return None
    if pr < _get("atrp_high"):
        return None
    hh_n = _get_int("hh_n")
    if h.size < hh_n + 1:
        return None
    prior_hh = float(pd.Series(h[:-1]).rolling(hh_n, min_periods=hh_n).max().iloc[-1])
    if not np.isfinite(prior_hh):
        return None
    if not (c[-1] > prior_hh):
        return None
    bracket = build_bracket(float(c[-1]), float(a[-1]), _get)
    if bracket is None:
        return None
    confidence = float(min(1.0, max(0.0, pr)))
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
    _base_generate_signal, "sp_atr_expansion_break", detectors=["ATR_EXPANSION"])
