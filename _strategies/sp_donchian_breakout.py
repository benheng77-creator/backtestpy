"""SP Donchian breakout — long when close > rolling-N high.

BACKTEST READY
This strategy can be tested on market data now.
3-layer Recipe Spread is unavailable because the strategy does not expose optimization knobs.
Missing Recipe Spread knobs: w_sp01..w_sp08


Entry rule (decided here, not in helpers):
  fire long iff  close[-1] > donchian_upper[-2]   (yesterday's channel,
  to avoid the lookahead of breaking your own bar)
  AND atr[-1] is finite and > 0.

Bracket on fire:
    entry  = close + eps_atr_mult * ATR
    stop   = entry - k_stop      * ATR
    tp     = entry + k_target    * ATR
    pts    = position_time_stop_bars
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from common.contract import to_arrays, make_get, build_bracket
from common.indicators import atr, donchian


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAME = "30m"
LOOKBACK_BARS = 240

DIRECTION = "long"
SIGNAL_FAMILY = "breakout"
FREQUENCY = "intraday"

DEFAULTS = {
    # Mandatory bracket knobs
    "eps_atr_mult":            0.10,
    "k_stop":                  1.00,
    "k_target":                2.50,
    "position_time_stop_bars": 24,
    # Recipe Spread weights (mandatory contract; this strategy is single-detector,
    # so weights act as confidence/sizing scalars only — see panel wrapper.)
    "w_sp01": 1.0, "w_sp02": 1.0, "w_sp03": 1.0, "w_sp04": 1.0,
    "w_sp05": 1.0, "w_sp06": 1.0, "w_sp07": 1.0, "w_sp08": 1.0,
    # Strategy-internal
    "donch_n":   55,
    "atr_n":     14,
}

_get, _get_int = make_get(DEFAULTS)


def _base_generate_signal(window) -> Optional[dict]:
    if not isinstance(window, list) or len(window) < LOOKBACK_BARS:
        return None
    o, h, l, c, v = to_arrays(window)
    n = _get_int("donch_n")
    upper, _lower, _mid = donchian(h, l, n)
    a = atr(h, l, c, _get_int("atr_n"))
    if a.size < 2 or not np.isfinite(a[-1]) or a[-1] <= 0:
        return None
    if not np.isfinite(upper[-2]):
        return None
    if not (c[-1] > upper[-2]):
        return None
    bracket = build_bracket(float(c[-1]), float(a[-1]), _get)
    if bracket is None:
        return None
    # confidence: distance above prior channel, scaled by ATR
    excess = (c[-1] - upper[-2]) / a[-1]
    confidence = float(min(1.0, max(0.0, excess / 2.0)))  # 2 ATRs above => 1.0
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
    _base_generate_signal, "sp_donchian_breakout", detectors=["DONCHIAN_BREAK"])
