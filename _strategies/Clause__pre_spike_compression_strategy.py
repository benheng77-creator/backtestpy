"""
pre_spike_compression_strategy.py
================================================================================
BACKTEST READY
This strategy can be tested on market data now.
3-layer Recipe Spread READY.
Optimization knobs exposed: eps_atr_mult, k_stop, k_target, position_time_stop_bars, w_sp01..w_sp08
================================================================================
GROUP 8 — VOLATILITY COMPRESSION / PRE-SPIKE OPPORTUNITY
Direction-agnostic compression detector → OCO bracket signal.

Single-file BotXLLM Panel-compatible strategy. All 8 detectors, voting stack,
regime filter, hard vetoes, and corrected bracket geometry (entry-relative
stop with band-floor safeguard) inlined. No external strategy modules, no
shared imports, no network, no API calls.

Detectors:
    SP01  TTM Squeeze              (Bollinger inside Keltner)
    SP02  VCR                      (ATR-short / ATR-long ratio compression)
    SP03  Coiled Spring            (NR7 + Volume compression composite)
    SP04  BBW Percentile Rank      (Bollinger width in bottom 15%)
    SP05  ATR Percentile Rank      (ATR in bottom 20%)
    SP06  Donchian Width Contract  (DC width tight AND shrinking)
    SP07  RV Percentile Collapse   (Realized vol in bottom 10%)
    SP08  Inside Bar Cluster       (3 consecutive inside bars, mother bar)

Voting weights:
    SP01:0.18  SP02:0.12  SP03:0.08  SP04:0.14
    SP05:0.12  SP06:0.12  SP07:0.16  SP08:0.08
    Score gate default 0.40

Hard vetoes:
    σ_ann > 1.0   (already in expansion regime)
    ADX_14 > 25   (market already trending — not in "space")

Bracket geometry (corrected):
    Entry  = (R_high + ε) for long, (R_low - ε) for short
    Stop   = min(entry-relative, band-relative) for long
             max(entry-relative, band-relative) for short
    Target = entry + k_target·ATR (long) / entry - k_target·ATR (short)
    R:R gate: reject if target_dist / stop_dist < 1.8
================================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ============================================================================
# Required top-level contract
# ============================================================================

SYMBOLS = ["BTCUSDT"]
TIMEFRAME = "1m"
LOOKBACK_BARS = 220   # need >= 200 for SP07 percentile window + warmup buffer

DEFAULTS = {
    # Voting
    "score_gate": 0.40,
    "rr_min": 1.80,

    # Hard vetoes
    "sigma_ann_cap": 1.00,
    "adx_cap": 25.0,

    # Bracket geometry
    "atr_period": 14,
    "k_stop": 0.90,
    "k_target": 2.50,
    "eps_atr_mult": 0.25,
    "n_range": 10,
    "position_time_stop_bars": 80,

    # SP01 — TTM Squeeze
    "sp01_n": 20,
    "sp01_kb": 2.00,
    "sp01_kk": 1.50,
    "sp01_min_dur": 6,

    # SP02 — VCR
    "sp02_n_short": 5,
    "sp02_n_long": 20,
    "sp02_theta_low": 0.60,
    "sp02_d_min": 8,

    # SP03 — Coiled Spring
    "sp03_nr": 14,
    "sp03_nv": 20,
    "sp03_rr_thresh": 0.60,
    "sp03_vr_thresh": 0.70,
    "sp03_theta_cs": 0.42,
    "sp03_k_consec": 3,

    # SP04 — BBW Percentile Rank
    "sp04_n": 20,
    "sp04_k": 2.00,
    "sp04_w": 120,
    "sp04_p_low": 0.15,
    "sp04_d_min": 4,

    # SP05 — ATR Percentile Rank
    "sp05_n": 14,
    "sp05_w": 100,
    "sp05_p_low": 0.20,
    "sp05_d_min": 5,

    # SP06 — Donchian Contraction
    "sp06_n": 20,
    "sp06_theta_dcw": 0.012,
    "sp06_k_shrink": 3,

    # SP07 — RV Percentile Collapse
    "sp07_n": 30,
    "sp07_w": 200,
    "sp07_p_extreme": 0.10,
    "sp07_d_min": 4,
    "sp07_periods_per_year": 525600.0,  # 1m bars × 365 days

    # SP08 — Inside Bar Cluster
    "sp08_k_required": 3,

    # ADX
    "adx_period": 14,

    # Voting weights
    "w_sp01": 0.18,
    "w_sp02": 0.12,
    "w_sp03": 0.08,
    "w_sp04": 0.14,
    "w_sp05": 0.12,
    "w_sp06": 0.12,
    "w_sp07": 0.16,
    "w_sp08": 0.08,

    # Sizing
    "base_confidence": 0.55,
    "sp07_confidence_boost": 0.15,
    "sp01_sp04_confidence_boost": 0.10,
    "max_confidence": 0.95,
    "base_sizing": 1.00,
    "sp07_sizing_mult": 1.30,
    "sp01_sp04_sizing_mult": 1.20,
    "max_sizing": 1.50,
}

# ============================================================================
# Internal state (per-process, cleared on reset_state)
# ============================================================================

_state: dict = {}


def reset_state() -> None:
    """Clear all rolling state. Called by panel between runs."""
    _state.clear()


# ============================================================================
# Indicator helpers (pure functions, no side effects)
# ============================================================================

def _atr(df: pd.DataFrame, n: int) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def _std(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).std(ddof=0)


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _rolling_percentile_rank(s: pd.Series, w: int) -> pd.Series:
    """Percentile rank of last value in rolling window of width w. Range [0,1]."""
    def rank_last(x: np.ndarray) -> float:
        if len(x) == 0 or not np.isfinite(x[-1]):
            return np.nan
        last = x[-1]
        n = len(x)
        # rank = (count of values <= last) / n
        return float(np.sum(x <= last)) / float(n)
    return s.rolling(w, min_periods=w).apply(rank_last, raw=True)


def _adx(df: pd.DataFrame, n: int) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
                        index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
                         index=df.index)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr_n = tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    plus_di = 100.0 * (plus_dm.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean() / atr_n)
    minus_di = 100.0 * (minus_dm.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean() / atr_n)

    denom = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    adx = dx.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    return adx


def _realized_vol_annualized(close: pd.Series, n: int, periods_per_year: float) -> pd.Series:
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(n, min_periods=n).std(ddof=0) * np.sqrt(periods_per_year)


# ============================================================================
# Detectors — all return (fired: bool, value: float)
# ============================================================================

def _detect_sp01(df: pd.DataFrame, atr_series: pd.Series, p: dict) -> tuple[bool, float]:
    """TTM Squeeze: Bollinger inside Keltner for >= min_duration bars."""
    n, kb, kk, min_dur = p["sp01_n"], p["sp01_kb"], p["sp01_kk"], p["sp01_min_dur"]
    close = df["close"].astype(float)
    if len(close) < n + min_dur:
        return False, 0.0
    mid = _sma(close, n)
    sd = _std(close, n)
    bb_up = mid + kb * sd
    bb_lo = mid - kb * sd
    ema_n = _ema(close, n)
    kc_up = ema_n + kk * atr_series
    kc_lo = ema_n - kk * atr_series
    inside = (bb_up < kc_up) & (bb_lo > kc_lo)
    tail = inside.iloc[-min_dur:]
    if tail.isna().any():
        return False, 0.0
    fired = bool(tail.all())
    width_ratio = float((bb_up.iloc[-1] - bb_lo.iloc[-1]) /
                        max(kc_up.iloc[-1] - kc_lo.iloc[-1], 1e-12))
    return fired, width_ratio


def _detect_sp02(df: pd.DataFrame, p: dict) -> tuple[bool, float]:
    """VCR: ATR_short / ATR_long <= theta_low for >= d_min bars."""
    n_s, n_l, theta, d_min = p["sp02_n_short"], p["sp02_n_long"], p["sp02_theta_low"], p["sp02_d_min"]
    if len(df) < n_l + d_min:
        return False, 0.0
    atr_s = _atr(df, n_s)
    atr_l = _atr(df, n_l)
    vcr = atr_s / atr_l.replace(0.0, np.nan)
    tail = vcr.iloc[-d_min:]
    if tail.isna().any():
        return False, 0.0
    fired = bool((tail <= theta).all())
    return fired, float(vcr.iloc[-1]) if np.isfinite(vcr.iloc[-1]) else 0.0


def _detect_sp03(df: pd.DataFrame, p: dict) -> tuple[bool, float]:
    """Coiled Spring: Range×Volume composite below threshold for k_consec bars."""
    nr, nv = p["sp03_nr"], p["sp03_nv"]
    rr_t, vr_t, theta_cs, k_consec = p["sp03_rr_thresh"], p["sp03_vr_thresh"], p["sp03_theta_cs"], p["sp03_k_consec"]
    need = max(nr, nv) + k_consec
    if len(df) < need:
        return False, 0.0
    if "volume" not in df.columns:
        return False, 0.0
    rng = (df["high"].astype(float) - df["low"].astype(float))
    vol = df["volume"].astype(float)
    rng_avg = _sma(rng, nr)
    vol_avg = _sma(vol, nv)
    rr = rng / rng_avg.replace(0.0, np.nan)
    vr = vol / vol_avg.replace(0.0, np.nan)
    cs = rr * vr
    tail = cs.iloc[-k_consec:]
    if tail.isna().any():
        return False, 0.0
    rr_tail = rr.iloc[-k_consec:]
    vr_tail = vr.iloc[-k_consec:]
    fired = bool((cs <= theta_cs).iloc[-k_consec:].all() and
                 (rr_tail <= rr_t).all() and
                 (vr_tail <= vr_t).all())
    return fired, float(cs.iloc[-1]) if np.isfinite(cs.iloc[-1]) else 0.0


def _detect_sp04(df: pd.DataFrame, p: dict) -> tuple[bool, float]:
    """BBW percentile rank in bottom p_low for >= d_min bars."""
    n, k, w, p_low, d_min = p["sp04_n"], p["sp04_k"], p["sp04_w"], p["sp04_p_low"], p["sp04_d_min"]
    if len(df) < n + w + d_min:
        return False, 0.0
    close = df["close"].astype(float)
    mid = _sma(close, n)
    sd = _std(close, n)
    bbw = (2.0 * k * sd) / mid.replace(0.0, np.nan)
    rank = _rolling_percentile_rank(bbw, w)
    tail = rank.iloc[-d_min:]
    if tail.isna().any():
        return False, 0.0
    fired = bool((tail <= p_low).all())
    return fired, float(rank.iloc[-1]) if np.isfinite(rank.iloc[-1]) else 0.0


def _detect_sp05(df: pd.DataFrame, atr_series: pd.Series, p: dict) -> tuple[bool, float]:
    """ATR percentile rank in bottom p_low for >= d_min bars."""
    w, p_low, d_min = p["sp05_w"], p["sp05_p_low"], p["sp05_d_min"]
    if len(atr_series.dropna()) < w + d_min:
        return False, 0.0
    rank = _rolling_percentile_rank(atr_series, w)
    tail = rank.iloc[-d_min:]
    if tail.isna().any():
        return False, 0.0
    fired = bool((tail <= p_low).all())
    return fired, float(rank.iloc[-1]) if np.isfinite(rank.iloc[-1]) else 0.0


def _detect_sp06(df: pd.DataFrame, p: dict) -> tuple[bool, float]:
    """Donchian width: <= theta_dcw AND shrinking for k_shrink consecutive bars."""
    n, theta, k_shrink = p["sp06_n"], p["sp06_theta_dcw"], p["sp06_k_shrink"]
    if len(df) < n + k_shrink + 1:
        return False, 0.0
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    dc_h = high.rolling(n, min_periods=n).max()
    dc_l = low.rolling(n, min_periods=n).min()
    dc_mid = (dc_h + dc_l) / 2.0
    dcw = (dc_h - dc_l) / dc_mid.replace(0.0, np.nan)
    tail = dcw.iloc[-(k_shrink + 1):]
    if tail.isna().any():
        return False, 0.0
    # Last k_shrink bars must be strictly non-increasing AND last value <= theta
    diffs = tail.diff().iloc[1:]
    fired = bool((diffs <= 0).all() and dcw.iloc[-1] <= theta)
    return fired, float(dcw.iloc[-1]) if np.isfinite(dcw.iloc[-1]) else 0.0


def _detect_sp07(df: pd.DataFrame, p: dict) -> tuple[bool, float]:
    """Realized vol percentile in bottom p_extreme for >= d_min bars."""
    n, w, p_ex, d_min, ppy = p["sp07_n"], p["sp07_w"], p["sp07_p_extreme"], p["sp07_d_min"], p["sp07_periods_per_year"]
    if len(df) < n + w + d_min:
        return False, 0.0
    close = df["close"].astype(float)
    rv = _realized_vol_annualized(close, n, ppy)
    rank = _rolling_percentile_rank(rv, w)
    tail = rank.iloc[-d_min:]
    if tail.isna().any():
        return False, 0.0
    fired = bool((tail <= p_ex).all())
    return fired, float(rank.iloc[-1]) if np.isfinite(rank.iloc[-1]) else 0.0


def _detect_sp08(df: pd.DataFrame, p: dict) -> tuple[bool, dict]:
    """Inside-bar cluster: k_required consecutive inside bars. Returns mother bar info."""
    k_req = p["sp08_k_required"]
    if len(df) < k_req + 2:
        return False, {}
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    # Mother bar = bar at index -(k_req + 1). Then k_req bars after must each be inside the previous.
    mother_idx = len(df) - (k_req + 1)
    if mother_idx < 0:
        return False, {}
    inside_count = 0
    for i in range(1, k_req + 1):
        idx = mother_idx + i
        if high[idx] < high[idx - 1] and low[idx] > low[idx - 1]:
            inside_count += 1
        else:
            break
    if inside_count < k_req:
        return False, {}
    return True, {"mother_high": float(high[mother_idx]), "mother_low": float(low[mother_idx])}


# ============================================================================
# Regime / veto
# ============================================================================

def _hard_veto(df: pd.DataFrame, p: dict) -> tuple[bool, str]:
    """Returns (vetoed, reason)."""
    close = df["close"].astype(float)
    if len(close) < max(p["sp07_n"], p["adx_period"]) + 5:
        return True, "insufficient_history"

    # σ_ann veto
    rv = _realized_vol_annualized(close, p["sp07_n"], p["sp07_periods_per_year"])
    rv_now = rv.iloc[-1]
    if not np.isfinite(rv_now):
        return True, "rv_nan"
    if rv_now > p["sigma_ann_cap"]:
        return True, f"sigma_ann={rv_now:.3f}_gt_cap"

    # ADX veto
    adx_series = _adx(df, p["adx_period"])
    adx_now = adx_series.iloc[-1]
    if not np.isfinite(adx_now):
        return True, "adx_nan"
    if adx_now > p["adx_cap"]:
        return True, f"adx={adx_now:.2f}_gt_cap"

    return False, ""


# ============================================================================
# Main signal generator
# ============================================================================

def generate_signal(window):
    """
    Evaluate Group 8 voting stack on the latest bar of `window`.
    Returns a signal dict if the bracket fires and passes R:R gate, else None.
    """
    p = DEFAULTS

    # Normalize input → DataFrame
    df = pd.DataFrame(window)
    if len(df) < LOOKBACK_BARS:
        return None
    required_cols = {"high", "low", "close"}
    if not required_cols.issubset(df.columns):
        return None

    # --- Hard veto first (cheap exit if regime invalid) ---
    vetoed, _veto_reason = _hard_veto(df, p)
    if vetoed:
        return None

    # --- ATR (used by multiple detectors and bracket math) ---
    atr_series = _atr(df, p["atr_period"])
    atr_now = atr_series.iloc[-1]
    if not np.isfinite(atr_now) or atr_now <= 0.0:
        return None

    # --- Run all 8 detectors ---
    f01, _ = _detect_sp01(df, atr_series, p)
    f02, _ = _detect_sp02(df, p)
    f03, _ = _detect_sp03(df, p)
    f04, _ = _detect_sp04(df, p)
    f05, _ = _detect_sp05(df, atr_series, p)
    f06, _ = _detect_sp06(df, p)
    f07, _ = _detect_sp07(df, p)
    f08, mother = _detect_sp08(df, p)

    fired_map = {
        "SP01": f01, "SP02": f02, "SP03": f03, "SP04": f04,
        "SP05": f05, "SP06": f06, "SP07": f07, "SP08": f08,
    }

    # --- Voting score ---
    score = (
        p["w_sp01"] * float(f01) +
        p["w_sp02"] * float(f02) +
        p["w_sp03"] * float(f03) +
        p["w_sp04"] * float(f04) +
        p["w_sp05"] * float(f05) +
        p["w_sp06"] * float(f06) +
        p["w_sp07"] * float(f07) +
        p["w_sp08"] * float(f08)
    )

    if score < p["score_gate"]:
        return None

    detectors_fired = [k for k, v in fired_map.items() if v]
    if len(detectors_fired) == 0:
        return None  # defensive: should be impossible if score >= gate

    # --- Bracket geometry (corrected: entry-relative stop with band-floor safeguard) ---
    n_range = p["n_range"]
    if len(df) < n_range:
        return None
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    r_high = float(high.iloc[-n_range:].max())
    r_low = float(low.iloc[-n_range:].min())
    if not (np.isfinite(r_high) and np.isfinite(r_low)) or r_high <= r_low:
        return None

    eps = p["eps_atr_mult"] * atr_now
    k_stop_atr = p["k_stop"] * atr_now
    k_target_atr = p["k_target"] * atr_now

    last_close = float(df["close"].astype(float).iloc[-1])

    # SP08 mother-bar override for entry levels (when SP08 alone or alongside others
    # supplies a tighter, cleaner geometry, we still use the broader compression range —
    # this preserves consistent semantics across detector combinations).
    # Mother bar info is captured for telemetry only.

    # Determine bias: prefer the side closer to the breakout edge.
    dist_to_high = (r_high + eps) - last_close
    dist_to_low = last_close - (r_low - eps)

    # Default direction = long if upper edge is closer; short if lower edge is closer.
    # Both stops use min/max of entry-relative and band-relative.
    if dist_to_high <= dist_to_low:
        direction = 1
        entry = r_high + eps
        # Long stop: tighter of (entry - k_stop·ATR) vs (r_low - k_stop·ATR)
        stop_entry_rel = entry - k_stop_atr
        stop_band_rel = r_low - k_stop_atr
        stop = min(stop_entry_rel, stop_band_rel)
        target = entry + k_target_atr
    else:
        direction = -1
        entry = r_low - eps
        # Short stop: tighter of (entry + k_stop·ATR) vs (r_high + k_stop·ATR)
        stop_entry_rel = entry + k_stop_atr
        stop_band_rel = r_high + k_stop_atr
        stop = max(stop_entry_rel, stop_band_rel)
        target = entry - k_target_atr

    # --- Sanity + R:R gate ---
    if not (np.isfinite(entry) and np.isfinite(stop) and np.isfinite(target)):
        return None

    stop_dist = abs(entry - stop)
    target_dist = abs(target - entry)
    if stop_dist <= 0.0 or target_dist <= 0.0:
        return None

    rr = target_dist / stop_dist
    if rr < p["rr_min"]:
        return None  # defensive invariant — never emit inverted-R:R intents

    # Verify direction consistency
    if direction == 1 and not (stop < entry < target):
        return None
    if direction == -1 and not (target < entry < stop):
        return None

    # --- Confidence + sizing (size-up rules from spec) ---
    confidence = p["base_confidence"]
    sizing = p["base_sizing"]

    sp07_fires = bool(f07)
    sp01_and_sp04 = bool(f01 and f04)

    if sp07_fires and (f01 or f04):
        # Strongest combo (1.50× cap)
        confidence = min(confidence + p["sp07_confidence_boost"] + p["sp01_sp04_confidence_boost"],
                         p["max_confidence"])
        sizing = p["max_sizing"]
    elif sp07_fires:
        confidence = min(confidence + p["sp07_confidence_boost"], p["max_confidence"])
        sizing = p["sp07_sizing_mult"]
    elif sp01_and_sp04:
        confidence = min(confidence + p["sp01_sp04_confidence_boost"], p["max_confidence"])
        sizing = p["sp01_sp04_sizing_mult"]

    # Boost confidence proportionally to how far above gate the score is
    score_excess = max(0.0, score - p["score_gate"])
    confidence = min(confidence + 0.5 * score_excess, p["max_confidence"])

    reason_parts = [
        f"score={score:.3f}",
        f"detectors={'+'.join(detectors_fired)}",
        f"rr={rr:.2f}",
        f"atr={atr_now:.4f}",
        f"comp_range={(r_high - r_low):.4f}",
    ]
    if mother:
        reason_parts.append(f"mother_bar=[{mother['mother_low']:.2f},{mother['mother_high']:.2f}]")

    reason = "pre_spike_compression | " + " | ".join(reason_parts)

    return {
        "direction": int(direction),
        "entry": float(entry),
        "stop": float(stop),
        "tp": float(target),
        "confidence": float(confidence),
        "sizing_multiplier": float(sizing),
        "reason": reason,
        "detectors_fired": detectors_fired,
        "score": float(score),
    }
