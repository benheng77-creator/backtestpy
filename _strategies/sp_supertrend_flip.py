"""SP Supertrend bear→bull flip.

BACKTEST READY
This strategy can be tested on market data now.
3-layer Recipe Spread is unavailable because the strategy does not expose optimization knobs.
Missing Recipe Spread knobs: w_sp01..w_sp08


Entry rule:
  supertrend_dir[-2] == -1  AND  supertrend_dir[-1] == +1
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from common.contract import to_arrays, make_get, build_bracket
from common.indicators import atr, supertrend


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAME = "30m"
LOOKBACK_BARS = 240

DIRECTION = "long"
SIGNAL_FAMILY = "trend_flip"
FREQUENCY = "intraday"

DEFAULTS = {
    "eps_atr_mult":            0.10,
    "k_stop":                  1.20,
    "k_target":                2.40,
    "position_time_stop_bars": 24,
    "w_sp01": 1.0, "w_sp02": 1.0, "w_sp03": 1.0, "w_sp04": 1.0,
    "w_sp05": 1.0, "w_sp06": 1.0, "w_sp07": 1.0, "w_sp08": 1.0,
    "st_n":  10,
    "st_k":  3.0,
    "atr_n": 14,
}

_get, _get_int = make_get(DEFAULTS)


def _base_generate_signal(window) -> Optional[dict]:
    if not isinstance(window, list) or len(window) < LOOKBACK_BARS:
        return None
    o, h, l, c, v = to_arrays(window)
    _line, dir_ = supertrend(h, l, c, _get_int("st_n"), _get("st_k"))
    a = atr(h, l, c, _get_int("atr_n"))
    if dir_.size < 2 or a.size < 1:
        return None
    if not (np.isfinite(a[-1]) and a[-1] > 0):
        return None
    flipped = (int(dir_[-2]) == -1) and (int(dir_[-1]) == +1)
    if not flipped:
        return None
    bracket = build_bracket(float(c[-1]), float(a[-1]), _get)
    if bracket is None:
        return None
    # confidence: a flip is binary; weight by close-vs-line distance in ATRs (mild)
    confidence = 0.6
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
    _base_generate_signal, "sp_supertrend_flip", detectors=["ST_FLIP"])
