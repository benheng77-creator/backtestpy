"""SP MACD bullish-cross long, in EMA200 uptrend.

BACKTEST READY
This strategy can be tested on market data now.
3-layer Recipe Spread is unavailable because the strategy does not expose optimization knobs.
Missing Recipe Spread knobs: w_sp01..w_sp08


Entry rule:
  macd_line[-2] <= signal_line[-2]
  macd_line[-1] >  signal_line[-1]                (bullish cross this bar)
  close[-1] > ema200[-1]                          (price above slow EMA)
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from common.contract import to_arrays, make_get, build_bracket
from common.indicators import atr, ema, macd


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAME = "30m"
LOOKBACK_BARS = 240

DIRECTION = "long"
SIGNAL_FAMILY = "momentum"
FREQUENCY = "intraday"

DEFAULTS = {
    "eps_atr_mult":            0.10,
    "k_stop":                  1.20,
    "k_target":                2.40,
    "position_time_stop_bars": 24,
    "w_sp01": 1.0, "w_sp02": 1.0, "w_sp03": 1.0, "w_sp04": 1.0,
    "w_sp05": 1.0, "w_sp06": 1.0, "w_sp07": 1.0, "w_sp08": 1.0,
    "macd_fast":   12,
    "macd_slow":   26,
    "macd_signal": 9,
    "ema_slow":    200,
    "atr_n":       14,
}

_get, _get_int = make_get(DEFAULTS)


def _base_generate_signal(window) -> Optional[dict]:
    if not isinstance(window, list) or len(window) < LOOKBACK_BARS:
        return None
    o, h, l, c, v = to_arrays(window)
    line, sig, _hist = macd(c, _get_int("macd_fast"), _get_int("macd_slow"), _get_int("macd_signal"))
    e_slow = ema(c, _get_int("ema_slow"))
    a = atr(h, l, c, _get_int("atr_n"))
    if line.size < 2 or a.size < 1:
        return None
    if not (np.isfinite(line[-1]) and np.isfinite(line[-2])
            and np.isfinite(sig[-1]) and np.isfinite(sig[-2])
            and np.isfinite(e_slow[-1]) and np.isfinite(a[-1]) and a[-1] > 0):
        return None
    crossed_up = (line[-2] <= sig[-2]) and (line[-1] > sig[-1])
    above_slow = c[-1] > e_slow[-1]
    if not (crossed_up and above_slow):
        return None
    bracket = build_bracket(float(c[-1]), float(a[-1]), _get)
    if bracket is None:
        return None
    # confidence: histogram width in ATR units
    width = (line[-1] - sig[-1]) / a[-1]
    confidence = float(min(1.0, max(0.0, width / 0.5)))
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
    _base_generate_signal, "sp_macd_cross_long", detectors=["MACD_CROSS"])
