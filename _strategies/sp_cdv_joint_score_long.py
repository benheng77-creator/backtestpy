"""SP CDV joint-score long — fade-strength admission.

BACKTEST READY
This strategy can be tested on market data now.
3-layer Recipe Spread is unavailable because the strategy does not expose optimization knobs.
Missing Recipe Spread knobs: w_sp01..w_sp08


Replaces the killed _admit_contrarian / _admit_deep_value rules.

Hypothesis (validated by panel IC test 2026-04-25 on 12-symbol x ~129K
1m-bar OKX tape, n=1.42M, hold=45m):

  Combined sign-normalized z-score of four features has Spearman IC
  +0.0490 (t=+58.6) against forward 45m log-return, broad across all
  11 measured symbols (per-symbol IC range +0.035..+0.074).

  Component features (each z-scored on a per-symbol rolling window so
  cross-symbol scale doesn't dominate):

    z_neg_range_pos_60   high range_pos_60 -> negative forward (fade highs)
    z_neg_vwap_dev_60    far above VWAP -> negative forward (fade extension)
    z_neg_ret_15m        recent 15m up-move -> negative forward (mean-revert)
    z_pos_accel_5_15     5m bending up vs 15m -> positive forward (catch turn)

  score = w_range_pos * z_neg_range_pos
        + w_vwap_dev  * z_neg_vwap_dev
        + w_ret_15m   * z_neg_ret_15m
        + w_accel     * z_pos_accel

LONG admit iff score >= per-symbol score_pctile threshold (default 0.80
= top 20% of recent scores).

LOCKED_INVARIANTS:
  direction = 1   -- the IC sign was measured for the long side; the
                     short side is NOT validated and is rejected by
                     panel_adapter.enforce_locked_invariants.

This file emits a contract-compliant signal only. It does NOT execute
trades, place orders, or interact with portfolio state. The 7-sprint
risk wrapper (fractal regime, liquidity inference, exploration wallet)
sits OUTSIDE this file in the existing simulator infrastructure.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from common.contract import to_arrays, make_get, build_bracket
from common.indicators import atr


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "INJUSDT", "SEIUSDT",
           "DOGEUSDT", "WIFUSDT", "PEPEUSDT", "ARBUSDT", "DOTUSDT", "ADAUSDT"]
TIMEFRAME = "1m"
LOOKBACK_BARS = 2880   # 48h on 1m for stable z-score base; >= z_lookback default

DIRECTION = "long"
SIGNAL_FAMILY = "mean_revert"
FREQUENCY = "intraday"
LOCKED_INVARIANTS = {"direction": 1}

DEFAULTS = {
    # Mandatory bracket knobs
    "eps_atr_mult":            0.05,
    "k_stop":                  1.20,
    "k_target":                1.60,
    "position_time_stop_bars": 45,
    # Mandatory Recipe Spread weights (this strategy uses w_range_pos/w_vwap_dev/
    # w_ret_15m/w_accel internally; w_sp01..w_sp08 act as confidence scalars.)
    "w_sp01": 1.0, "w_sp02": 1.0, "w_sp03": 1.0, "w_sp04": 1.0,
    "w_sp05": 1.0, "w_sp06": 1.0, "w_sp07": 1.0, "w_sp08": 1.0,
    # Joint-score weights (default = equal, sign-normalized)
    "w_range_pos": 1.0,
    "w_vwap_dev":  1.0,
    "w_ret_15m":   1.0,
    "w_accel":     1.0,
    # Admission threshold: percentile of recent scores in [0,1]
    "score_pctile": 0.80,
    # Z-score lookback
    "z_lookback":  2880,
    # ATR window for bracket
    "atr_n":       60,
    # Internal feature lookbacks (match the IC test specification)
    "vwap_n":      60,
    "range_n":     60,
    "ret_window":  15,
    "ret_short":   5,
}

_get, _get_int = make_get(DEFAULTS)


def _zclip(x: np.ndarray, lookback: int) -> float:
    """Z-score the latest value of x against its trailing `lookback`
    distribution. Clipped to [-6, 6]. Returns NaN if insufficient data."""
    if x.size < lookback:
        return float("nan")
    tail = x[-lookback:]
    tail = tail[np.isfinite(tail)]
    if tail.size < max(60, lookback // 10):
        return float("nan")
    mu = float(tail.mean())
    sd = float(tail.std(ddof=0))
    if sd <= 0 or not np.isfinite(sd):
        return float("nan")
    z = (float(x[-1]) - mu) / sd
    return float(max(-6.0, min(6.0, z)))


def _compute_features(o, h, l, c, v) -> dict:
    """Compute the 4 features at the latest bar + their z-scores' history."""
    range_n = _get_int("range_n")
    vwap_n  = _get_int("vwap_n")
    ret_w   = _get_int("ret_window")
    ret_s   = _get_int("ret_short")
    z_lb    = _get_int("z_lookback")
    if c.size < z_lb + max(range_n, vwap_n, ret_w):
        return {}
    s = pd.Series(c)
    h_s = pd.Series(h)
    l_s = pd.Series(l)
    v_s = pd.Series(v)
    # range_pos_60
    rh = h_s.rolling(range_n, min_periods=range_n).max()
    rl = l_s.rolling(range_n, min_periods=range_n).min()
    range_pos = (s - rl) / (rh - rl)
    # vwap_dev_60 in ATR units
    tp = (h_s + l_s + s) / 3.0
    vwap = (tp * v_s).rolling(vwap_n, min_periods=vwap_n).sum() / \
           v_s.rolling(vwap_n, min_periods=vwap_n).sum()
    a_arr = atr(h, l, c, vwap_n)
    a_s = pd.Series(a_arr)
    vwap_dev = (s - vwap) / a_s
    # ret_15m (log)
    ret_15 = np.log(s).diff(ret_w)
    # accel: ret_short - ret_window
    ret_5 = np.log(s).diff(ret_s)
    accel = ret_5 - ret_15
    return {
        "range_pos": range_pos.to_numpy(),
        "vwap_dev":  vwap_dev.to_numpy(),
        "ret_15m":   ret_15.to_numpy(),
        "accel":     accel.to_numpy(),
        "atr":       a_arr,
    }


def _score(window) -> Optional[tuple[float, float, float]]:
    """Return (score, atr_value, score_pctile_threshold) or None."""
    o, h, l, c, v = to_arrays(window)
    feats = _compute_features(o, h, l, c, v)
    if not feats:
        return None
    z_lb = _get_int("z_lookback")
    z_neg_range = _zclip(-feats["range_pos"], z_lb)
    z_neg_vwap  = _zclip(-feats["vwap_dev"],  z_lb)
    z_neg_ret15 = _zclip(-feats["ret_15m"],   z_lb)
    z_pos_accel = _zclip( feats["accel"],     z_lb)
    if any(not np.isfinite(z) for z in (z_neg_range, z_neg_vwap, z_neg_ret15, z_pos_accel)):
        return None
    score = (
        _get("w_range_pos") * z_neg_range
        + _get("w_vwap_dev") * z_neg_vwap
        + _get("w_ret_15m")  * z_neg_ret15
        + _get("w_accel")    * z_pos_accel
    )
    # Build score history over the trailing window to derive the
    # per-symbol percentile threshold honestly (online, not lookahead).
    n_bars = c.size
    start = max(0, n_bars - z_lb)
    # Rebuild z-series on the trailing window for percentile calculation.
    rp = feats["range_pos"][start:]
    vd = feats["vwap_dev"][start:]
    r15 = feats["ret_15m"][start:]
    ac = feats["accel"][start:]
    # Component-wise z within this same window (same mu/sigma as point z).
    def _zcol(arr_full, sign):
        tail = arr_full[start:]
        finite = tail[np.isfinite(tail)]
        if finite.size < 60:
            return None
        mu = float(finite.mean()); sd = float(finite.std(ddof=0))
        if sd <= 0:
            return None
        z = sign * (tail - mu) / sd
        return np.clip(z, -6.0, 6.0)
    zr = _zcol(feats["range_pos"], -1.0)
    zv = _zcol(feats["vwap_dev"],  -1.0)
    zt = _zcol(feats["ret_15m"],   -1.0)
    za = _zcol(feats["accel"],     +1.0)
    if any(z is None for z in (zr, zv, zt, za)):
        return None
    score_hist = (
        _get("w_range_pos") * zr
        + _get("w_vwap_dev") * zv
        + _get("w_ret_15m")  * zt
        + _get("w_accel")    * za
    )
    score_hist = score_hist[np.isfinite(score_hist)]
    if score_hist.size < 100:
        return None
    pctile = _get("score_pctile")
    threshold = float(np.quantile(score_hist, pctile))
    a_arr = feats["atr"]
    if a_arr.size == 0 or not np.isfinite(a_arr[-1]) or a_arr[-1] <= 0:
        return None
    return (float(score), float(a_arr[-1]), threshold)


def _base_generate_signal(window) -> Optional[dict]:
    if not isinstance(window, list) or len(window) < LOOKBACK_BARS:
        return None
    res = _score(window)
    if res is None:
        return None
    score, atr_value, threshold = res
    if score < threshold:
        return None
    o, h, l, c, v = to_arrays(window)
    bracket = build_bracket(float(c[-1]), float(atr_value), _get)
    if bracket is None:
        return None
    # Confidence: how far above threshold, capped. Score range is roughly
    # [-12, +12] (4 components clipped to +/-6 each), threshold typically
    # in [0, 6]. Rescale (score - threshold) by ~3 sigma.
    excess = score - threshold
    confidence = float(min(1.0, max(0.0, excess / 3.0)))
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
    _base_generate_signal, "sp_cdv_joint_score_long", detectors=["CDV_JOINT"])
