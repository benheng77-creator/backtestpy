"""SP-style compression voting-stack strategy for BotXLLM_Panel.

BACKTEST READY
This strategy can be tested on market data now.
3-layer Recipe Spread READY.
Optimization knobs exposed: eps_atr_mult, k_stop, k_target, position_time_stop_bars, w_sp01..w_sp08


Eight independent boolean detectors (SP01..SP08) each look for a different
flavour of pre-spike volatility compression. They are weighted by
w_sp01..w_sp08, summed into a normalized score, and fire long when the
score crosses score_gate (default 0.40). On a fire, an ATR-bracket entry
is emitted:

    entry  = close + eps_atr_mult * ATR
    stop   = entry  - k_stop      * ATR
    target = entry  + k_target    * ATR
    time   = exit after position_time_stop_bars (handled by harness)

The 12 recipe-spread knobs are read via _get(name) so the recipe engine
can vary every one of them between variants:

    eps_atr_mult, k_stop, k_target, position_time_stop_bars,
    w_sp01..w_sp08

Detectors:
  SP01 TTM_SQUEEZE      Bollinger band inside Keltner band on latest bar
  SP02 VCR              ATR_short / ATR_long <= 0.60, sustained
  SP03 COILED_SPRING    rolling stdev of close in bottom percentile
  SP04 BBW_PERCENTILE   Bollinger Band Width in bottom 15th percentile
  SP05 ATR_PERCENTILE   ATR in bottom 20th percentile
  SP06 DONCHIAN_CONTRA  Donchian channel width < 1.2% of price, sustained
  SP07 RV_COLLAPSE      annualized realized vol in bottom 10th percentile
  SP08 INSIDE_BAR_CLUST >= 3 consecutive inside bars

Direction is long-only (compression-breakout family). The harness wraps
the bracket and applies its own cost model; this file emits the signal
and bracket only — no execution, no portfolio state, no I/O.
"""
from __future__ import annotations

import os
from typing import List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------- #
# Layer 1 — required module-level constants                              #
# ---------------------------------------------------------------------- #

SYMBOLS: List[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAME: str = "30m"
LOOKBACK_BARS: int = 240   # 240 30m bars = 5 days; covers all percentile lookbacks

# Optional display / metadata
DIRECTION = "long"
SIGNAL_FAMILY = "breakout"
FREQUENCY = "intraday"
TAKE_PROFIT_PCT = 1.5
STOP_LOSS_PCT = 0.5
RR = 3.0
LOCKED_INVARIANTS = {"score_gate": 0.40, "min_active_detectors": 2}


# ---------------------------------------------------------------------- #
# Layer 2 — recipe-spread knobs (env-var overridable)                    #
# ---------------------------------------------------------------------- #

DEFAULTS = {
    # Bracket geometry
    "eps_atr_mult":            0.25,
    "k_stop":                  0.85,
    "k_target":                2.50,
    "position_time_stop_bars": 24,
    # Detector weights — sum to 1.00 by design at defaults
    "w_sp01": 0.18,
    "w_sp02": 0.12,
    "w_sp03": 0.08,
    "w_sp04": 0.14,
    "w_sp05": 0.12,
    "w_sp06": 0.12,
    "w_sp07": 0.16,
    "w_sp08": 0.08,
    # Strategy-internal tunables (not in the 12 mandatory list — safe to add)
    "score_gate":              0.40,
    "atr_n":                   14,
    "bb_n":                    20,
    "bb_k":                    2.0,
    "kc_n":                    20,
    "kc_k":                    1.5,
    "vcr_atr_short":           5,
    "vcr_atr_long":            20,
    "vcr_threshold":           0.60,
    "vcr_min_consec":          8,
    "coiled_n":                14,
    "coiled_pct":              0.20,
    "bbw_lookback":            120,
    "bbw_pct":                 0.15,
    "atrp_lookback":           100,
    "atrp_pct":                0.20,
    "donch_n":                 20,
    "donch_max_width_pct":     0.012,
    "donch_min_consec":        3,
    "rv_n":                    30,
    "rv_lookback":             200,
    "rv_pct":                  0.10,
    "inside_min_consec":       3,
}


def _get(name: str) -> float:
    """Read a knob with optional env override. Caller passes a string;
    function returns float (caller casts to int where it expects an int)."""
    env = os.environ.get(f"BOTX_RECIPE_{name.upper()}")
    if env is not None:
        try:
            return float(env)
        except ValueError:
            pass
    return float(DEFAULTS[name])


def _get_int(name: str) -> int:
    return int(_get(name))


# ---------------------------------------------------------------------- #
# Indicator primitives — all numpy-vectorized                            #
# ---------------------------------------------------------------------- #

def _to_arrays(window):
    """Convert list-of-dicts to numpy float arrays. Single allocation."""
    n = len(window)
    o = np.empty(n, dtype=np.float64)
    h = np.empty(n, dtype=np.float64)
    l = np.empty(n, dtype=np.float64)
    c = np.empty(n, dtype=np.float64)
    v = np.empty(n, dtype=np.float64)
    for i, b in enumerate(window):
        o[i] = b["open"]; h[i] = b["high"]; l[i] = b["low"]
        c[i] = b["close"]; v[i] = b["volume"]
    return o, h, l, c, v


def _true_range(h, l, c):
    """ATR true range: max(h-l, |h-prev_c|, |l-prev_c|), rolling."""
    pc = np.empty_like(c)
    pc[0] = c[0]
    pc[1:] = c[:-1]
    return np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])


def _atr(h, l, c, n: int) -> np.ndarray:
    tr = _true_range(h, l, c)
    return pd.Series(tr).rolling(n, min_periods=n).mean().to_numpy()


def _sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).mean().to_numpy()


def _stdev(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).std(ddof=0).to_numpy()


# ---------------------------------------------------------------------- #
# The 8 detectors                                                        #
# Each returns bool for the most recent bar.                             #
# ---------------------------------------------------------------------- #

def _sp01_ttm_squeeze(h, l, c) -> bool:
    """BB inside KC: BB_upper < KC_upper AND BB_lower > KC_lower."""
    n = _get_int("bb_n")
    k_b = _get("bb_k")
    k_k = _get("kc_k")
    if len(c) < n + 1:
        return False
    sma = _sma(c, n)
    sd  = _stdev(c, n)
    bb_u = sma + k_b * sd
    bb_l = sma - k_b * sd
    atr_n = _atr(h, l, c, n)
    kc_u = sma + k_k * atr_n
    kc_l = sma - k_k * atr_n
    if not np.isfinite(bb_u[-1]) or not np.isfinite(kc_u[-1]):
        return False
    return bool(bb_u[-1] < kc_u[-1] and bb_l[-1] > kc_l[-1])


def _sp02_vcr(h, l, c) -> bool:
    """ATR_short / ATR_long <= threshold for at least min_consec bars."""
    s = _get_int("vcr_atr_short")
    long_n = _get_int("vcr_atr_long")
    thr = _get("vcr_threshold")
    consec = _get_int("vcr_min_consec")
    if len(c) < long_n + consec + 1:
        return False
    atr_s = _atr(h, l, c, s)
    atr_l = _atr(h, l, c, long_n)
    ratio = atr_s / np.where(atr_l > 0, atr_l, np.nan)
    tail = ratio[-consec:]
    if np.any(~np.isfinite(tail)):
        return False
    return bool(np.all(tail <= thr))


def _sp03_coiled_spring(c) -> bool:
    """Rolling stdev of close in bottom percentile of its own history."""
    n = _get_int("coiled_n")
    pct = _get("coiled_pct")
    if len(c) < n * 4:
        return False
    sd = _stdev(c, n)
    sd = sd[~np.isnan(sd)]
    if sd.size < 20:
        return False
    threshold = np.quantile(sd, pct)
    return bool(sd[-1] <= threshold)


def _sp04_bbw_percentile(c) -> bool:
    """Bollinger Band Width in bottom percentile."""
    n = _get_int("bb_n")
    k = _get("bb_k")
    lookback = _get_int("bbw_lookback")
    pct = _get("bbw_pct")
    if len(c) < lookback + n + 1:
        return False
    sma = _sma(c, n)
    sd  = _stdev(c, n)
    bbw = (4.0 * k * sd) / np.where(sma > 0, sma, np.nan)
    bbw = bbw[~np.isnan(bbw)]
    if bbw.size < lookback:
        return False
    recent = bbw[-lookback:]
    threshold = np.quantile(recent, pct)
    return bool(bbw[-1] <= threshold)


def _sp05_atr_percentile(h, l, c) -> bool:
    """ATR in bottom percentile."""
    n = _get_int("atr_n")
    lookback = _get_int("atrp_lookback")
    pct = _get("atrp_pct")
    if len(c) < lookback + n + 1:
        return False
    a = _atr(h, l, c, n)
    a = a[~np.isnan(a)]
    if a.size < lookback:
        return False
    recent = a[-lookback:]
    threshold = np.quantile(recent, pct)
    return bool(a[-1] <= threshold)


def _sp06_donchian_contraction(h, l, c) -> bool:
    """Donchian channel width / price < threshold for min_consec bars."""
    n = _get_int("donch_n")
    max_w = _get("donch_max_width_pct")
    consec = _get_int("donch_min_consec")
    if len(c) < n + consec + 1:
        return False
    hi = pd.Series(h).rolling(n, min_periods=n).max().to_numpy()
    lo = pd.Series(l).rolling(n, min_periods=n).min().to_numpy()
    width = (hi - lo) / np.where(c > 0, c, np.nan)
    tail = width[-consec:]
    if np.any(~np.isfinite(tail)):
        return False
    return bool(np.all(tail < max_w))


def _sp07_rv_collapse(c) -> bool:
    """Annualized realized vol of log returns in bottom percentile."""
    n = _get_int("rv_n")
    lookback = _get_int("rv_lookback")
    pct = _get("rv_pct")
    if len(c) < lookback + n + 1:
        return False
    logret = np.diff(np.log(np.where(c > 0, c, 1e-12)))
    rv = pd.Series(logret).rolling(n, min_periods=n).std(ddof=0).to_numpy()
    # Annualize (assume bars are 30m -> 17520 bars/yr; harness can override
    # this if needed — we just use a constant since we only need percentile rank)
    rv = rv * np.sqrt(17520.0)
    rv = rv[~np.isnan(rv)]
    if rv.size < lookback:
        return False
    recent = rv[-lookback:]
    threshold = np.quantile(recent, pct)
    return bool(rv[-1] <= threshold)


def _sp08_inside_bar_cluster(h, l) -> bool:
    """At least min_consec consecutive inside bars at the end of window."""
    consec = _get_int("inside_min_consec")
    if len(h) < consec + 1:
        return False
    for i in range(1, consec + 1):
        idx = len(h) - i
        prev = idx - 1
        if prev < 0:
            return False
        if not (h[idx] <= h[prev] and l[idx] >= l[prev]):
            return False
    return True


# ---------------------------------------------------------------------- #
# Voting stack                                                           #
# ---------------------------------------------------------------------- #

_DETECTOR_ORDER = (
    ("w_sp01", "_sp01_ttm_squeeze"),
    ("w_sp02", "_sp02_vcr"),
    ("w_sp03", "_sp03_coiled_spring"),
    ("w_sp04", "_sp04_bbw_percentile"),
    ("w_sp05", "_sp05_atr_percentile"),
    ("w_sp06", "_sp06_donchian_contraction"),
    ("w_sp07", "_sp07_rv_collapse"),
    ("w_sp08", "_sp08_inside_bar_cluster"),
)


def _evaluate_detectors(o, h, l, c, v):
    """Return list of (weight_name, fired_bool, weight_value) for the
    8 detectors at the most recent bar."""
    results = []
    sp01 = _sp01_ttm_squeeze(h, l, c)
    sp02 = _sp02_vcr(h, l, c)
    sp03 = _sp03_coiled_spring(c)
    sp04 = _sp04_bbw_percentile(c)
    sp05 = _sp05_atr_percentile(h, l, c)
    sp06 = _sp06_donchian_contraction(h, l, c)
    sp07 = _sp07_rv_collapse(c)
    sp08 = _sp08_inside_bar_cluster(h, l)
    fires = (sp01, sp02, sp03, sp04, sp05, sp06, sp07, sp08)
    for (wname, _), fired in zip(_DETECTOR_ORDER, fires):
        results.append((wname, bool(fired), _get(wname)))
    return results


# ---------------------------------------------------------------------- #
# Public entry: generate_signal                                          #
# ---------------------------------------------------------------------- #

def _base_generate_signal(window: list) -> Optional[dict]:
    if not isinstance(window, list) or len(window) < LOOKBACK_BARS:
        return None

    o, h, l, c, v = _to_arrays(window)

    detectors = _evaluate_detectors(o, h, l, c, v)
    total_weight = sum(w for _, _, w in detectors) or 1.0
    fired_weight = sum(w for _, fired, w in detectors if fired)
    n_fired      = sum(1 for _, fired, _ in detectors if fired)
    score = fired_weight / total_weight

    score_gate = _get("score_gate")
    min_active = int(LOCKED_INVARIANTS.get("min_active_detectors", 2))
    if score < score_gate or n_fired < min_active:
        return None

    # Bracket geometry — uses 4 of the 12 recipe knobs directly
    atr_n = _get_int("atr_n")
    a = _atr(h, l, c, atr_n)
    if a.size == 0 or not np.isfinite(a[-1]) or a[-1] <= 0:
        return None
    atr_value = float(a[-1])
    close = float(c[-1])

    eps = _get("eps_atr_mult") * atr_value
    entry = close + eps
    stop = entry - _get("k_stop") * atr_value
    tp = entry + _get("k_target") * atr_value

    if not (np.isfinite(entry) and np.isfinite(stop) and np.isfinite(tp)):
        return None
    if stop >= entry or tp <= entry:
        return None  # degenerate bracket; skip

    # Confidence: clip the score into [0, 1]; gate already guaranteed >= score_gate
    confidence = float(min(1.0, max(0.0, score)))

    return {
        "direction":              +1,
        "entry":                  float(entry),
        "stop":                   float(stop),
        "tp":                     float(tp),
        "confidence":             confidence,
        "position_time_stop_bars": _get_int("position_time_stop_bars"),
    }


# === BotXLLM panel-contract wrapper ===
from common.panel import make_panel_pair as _make_panel_pair
reset_state, generate_signal = _make_panel_pair(
    _base_generate_signal, "sp_compression_voting_stack",
    detectors=["SP01","SP02","SP03","SP04","SP05","SP06","SP07","SP08"])
