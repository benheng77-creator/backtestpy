"""SP Bollinger mean-reversion long.

BACKTEST READY
This strategy can be tested on market data now.
3-layer Recipe Spread is unavailable because the strategy does not expose optimization knobs.
Missing Recipe Spread knobs: w_sp01..w_sp08


Entry rule:
  low[-2]   <= bb_lower[-2]                       (touched/pierced lower band)
  close[-1] >  bb_lower[-1]                       (closed back inside)
  close[-1] >  open[-1]                           (bullish bar)
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from common.contract import to_arrays, make_get, build_bracket
from common.indicators import atr, bollinger


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAME = "30m"
LOOKBACK_BARS = 240

DIRECTION = "long"
SIGNAL_FAMILY = "mean_revert"
FREQUENCY = "intraday"

DEFAULTS = {
    "eps_atr_mult":            0.05,
    "k_stop":                  1.00,
    "k_target":                1.80,
    "position_time_stop_bars": 16,
    "w_sp01": 1.0, "w_sp02": 1.0, "w_sp03": 1.0, "w_sp04": 1.0,
    "w_sp05": 1.0, "w_sp06": 1.0, "w_sp07": 1.0, "w_sp08": 1.0,
    "bb_n": 20,
    "bb_k": 2.0,
    "atr_n": 14,
}

_get, _get_int = make_get(DEFAULTS)


def _base_generate_signal(window) -> Optional[dict]:
    if not isinstance(window, list) or len(window) < LOOKBACK_BARS:
        return None
    o, h, l, c, v = to_arrays(window)
    _mid, _bb_u, bb_l = bollinger(c, _get_int("bb_n"), _get("bb_k"))
    a = atr(h, l, c, _get_int("atr_n"))
    if bb_l.size < 2 or a.size < 2:
        return None
    if not (np.isfinite(bb_l[-1]) and np.isfinite(bb_l[-2]) and np.isfinite(a[-1]) and a[-1] > 0):
        return None
    pierced = l[-2] <= bb_l[-2]
    reclaim = (c[-1] > bb_l[-1]) and (c[-1] > o[-1])
    if not (pierced and reclaim):
        return None
    bracket = build_bracket(float(c[-1]), float(a[-1]), _get)
    if bracket is None:
        return None
    # confidence: how deep the prior bar pierced (in ATR units), capped
    depth = max(0.0, (bb_l[-2] - l[-2]) / a[-1])
    confidence = float(min(1.0, max(0.0, depth / 2.0)))
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
    _base_generate_signal, "sp_bollinger_meanrevert", detectors=["BB_MEANREV"])
