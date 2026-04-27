"""SP RSI extreme reversion long.

BACKTEST READY
This strategy can be tested on market data now.
3-layer Recipe Spread is unavailable because the strategy does not expose optimization knobs.
Missing Recipe Spread knobs: w_sp01..w_sp08


Entry rule:
  rsi[-2] < rsi_low                               (extreme oversold prev bar)
  close[-1] > open[-1]                            (bullish bar)
  close[-1] > close[-2]                           (higher close)
  rsi[-1] > rsi[-2]                               (rsi turning up)
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from common.contract import to_arrays, make_get, build_bracket
from common.indicators import atr, rsi


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAME = "30m"
LOOKBACK_BARS = 240

DIRECTION = "long"
SIGNAL_FAMILY = "mean_revert"
FREQUENCY = "intraday"

DEFAULTS = {
    "eps_atr_mult":            0.05,
    "k_stop":                  1.10,
    "k_target":                1.80,
    "position_time_stop_bars": 16,
    "w_sp01": 1.0, "w_sp02": 1.0, "w_sp03": 1.0, "w_sp04": 1.0,
    "w_sp05": 1.0, "w_sp06": 1.0, "w_sp07": 1.0, "w_sp08": 1.0,
    "rsi_n":   14,
    "rsi_low": 25.0,
    "atr_n":   14,
}

_get, _get_int = make_get(DEFAULTS)


def _base_generate_signal(window) -> Optional[dict]:
    if not isinstance(window, list) or len(window) < LOOKBACK_BARS:
        return None
    o, h, l, c, v = to_arrays(window)
    r = rsi(c, _get_int("rsi_n"))
    a = atr(h, l, c, _get_int("atr_n"))
    if r.size < 2 or a.size < 1:
        return None
    if not (np.isfinite(r[-1]) and np.isfinite(r[-2]) and np.isfinite(a[-1]) and a[-1] > 0):
        return None
    threshold = _get("rsi_low")
    cond = (r[-2] < threshold) and (c[-1] > o[-1]) and (c[-1] > c[-2]) and (r[-1] > r[-2])
    if not cond:
        return None
    bracket = build_bracket(float(c[-1]), float(a[-1]), _get)
    if bracket is None:
        return None
    # confidence: how far below threshold, normalized 0..threshold => 0..1
    depth = max(0.0, threshold - float(r[-2]))
    confidence = float(min(1.0, depth / max(threshold, 1e-9)))
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
    _base_generate_signal, "sp_rsi_extreme_revert", detectors=["RSI_REVERT"])
