"""SP OBV bullish divergence — price makes lower low, OBV makes higher low.

BACKTEST READY
This strategy can be tested on market data now.
3-layer Recipe Spread is unavailable because the strategy does not expose optimization knobs.
Missing Recipe Spread knobs: w_sp01..w_sp08


Entry rule:
  Look at last `lookback` bars. Find two recent troughs (price local minima
  separated by >= min_separation bars).
  price_trough_now < price_trough_prev   (lower low in price)
  obv_at_now       > obv_at_prev         (higher low in OBV — divergence)
  AND last bar is bullish (close > open).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from common.contract import to_arrays, make_get, build_bracket
from common.indicators import atr, obv


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAME = "30m"
LOOKBACK_BARS = 240

DIRECTION = "long"
SIGNAL_FAMILY = "divergence"
FREQUENCY = "intraday"

DEFAULTS = {
    "eps_atr_mult":            0.10,
    "k_stop":                  1.30,
    "k_target":                2.60,
    "position_time_stop_bars": 24,
    "w_sp01": 1.0, "w_sp02": 1.0, "w_sp03": 1.0, "w_sp04": 1.0,
    "w_sp05": 1.0, "w_sp06": 1.0, "w_sp07": 1.0, "w_sp08": 1.0,
    "div_lookback":      80,
    "div_min_separation": 8,
    "atr_n":             14,
}

_get, _get_int = make_get(DEFAULTS)


def _find_local_min_idx(arr: np.ndarray, start: int, end: int) -> int:
    """Return absolute index of min in arr[start:end] (inclusive of both ends)."""
    s = max(0, start); e = min(arr.size, end + 1)
    if e <= s:
        return -1
    return int(s + np.argmin(arr[s:e]))


def _base_generate_signal(window) -> Optional[dict]:
    if not isinstance(window, list) or len(window) < LOOKBACK_BARS:
        return None
    o, h, l, c, v = to_arrays(window)
    a = atr(h, l, c, _get_int("atr_n"))
    if a.size < 1 or not np.isfinite(a[-1]) or a[-1] <= 0:
        return None
    obv_arr = obv(c, v)
    lb = _get_int("div_lookback")
    sep = _get_int("div_min_separation")
    if c.size < lb + 1:
        return None
    end = c.size - 1
    start = end - lb
    mid = end - lb // 2
    # "Now" trough: min in second half (mid..end)
    now_idx = _find_local_min_idx(l, mid, end)
    # "Prev" trough: min in first half (start..mid - sep)
    prev_idx = _find_local_min_idx(l, start, mid - sep)
    if now_idx < 0 or prev_idx < 0 or (now_idx - prev_idx) < sep:
        return None
    lower_low_price = l[now_idx] < l[prev_idx]
    higher_low_obv = obv_arr[now_idx] > obv_arr[prev_idx]
    bullish_bar = c[-1] > o[-1]
    if not (lower_low_price and higher_low_obv and bullish_bar):
        return None
    bracket = build_bracket(float(c[-1]), float(a[-1]), _get)
    if bracket is None:
        return None
    # confidence: OBV separation as fraction of recent OBV range
    obv_range = float(np.nanmax(obv_arr[start:end + 1]) - np.nanmin(obv_arr[start:end + 1]))
    if obv_range > 0:
        sep_strength = (obv_arr[now_idx] - obv_arr[prev_idx]) / obv_range
    else:
        sep_strength = 0.0
    confidence = float(min(1.0, max(0.0, sep_strength)))
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
    _base_generate_signal, "sp_obv_divergence_long", detectors=["OBV_DIVERGENCE"])
