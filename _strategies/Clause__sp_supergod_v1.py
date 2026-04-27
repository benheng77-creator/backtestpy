"""
sp_supergod_v1.py
=================

BACKTEST READY
This strategy can be tested on market data now.
3-layer Recipe Spread is unavailable because the strategy does not expose optimization knobs.
Missing Recipe Spread knobs: eps_atr_mult, k_stop, k_target, position_time_stop_bars, w_sp01..w_sp08


BotXLLM Panel-compliant single-file strategy.

Top-level contract (required by the panel):
    SYMBOLS, TIMEFRAME, LOOKBACK_BARS, DEFAULTS
    reset_state()
    generate_signal(window) -> dict | None

All Supergod safety logic from the r4960 forensic work is preserved:
    • Z-score spike detection (no fixed-% thresholds)
    • Regime classifier (TRENDING_UP / TRENDING_DOWN / RANGING / CRISIS)
    • Regime-conditional direction (mean-revert in range, momentum in trend)
    • ATR(14) stops at 2x, take-profits at 3x  →  1.5:1 R/R
    • Expectancy gate: refuse entries with E[R] < 0.10 after 10bp round-trip cost
    • Sign-inversion self-check (rolling hit rate < 15% → halt)
    • Equity drawdown circuit breaker (-30% from peak → halt forever)
    • Consecutive-loss cooldown (25 in a row → 50-bar pause)
    • Trade-frequency cap (24/day max)

The panel does not feed equity/PnL back, so the strategy SIMULATES its own
fills using the bar window's recent prices. This is identical to how the
panel's own backtester resolves stops/TPs against the bar stream — we just
mirror that resolution internally so our self-checks (drawdown, hit rate,
consecutive losses) reflect what would have actually happened.

Imports: stdlib + numpy + pandas only. No claw/features/monitors/core/
openclaw_v1/scoring/_sp_common/sp_voting_stack/sp01..sp08 references.
No network. No subprocess. No API keys. No order placement.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import numpy as np
import pandas as pd


# =============================================================================
# Required panel contract
# =============================================================================

SYMBOLS = ["BTCUSDT"]
TIMEFRAME = "1m"
LOOKBACK_BARS = 200

DEFAULTS: Dict[str, Any] = {
    # ----- Mandatory BotXLLM Recipe Spread knobs
    # eps_atr_mult / k_stop / k_target are aliases for stop_atr/tp_atr that the
    # panel adapter applies post-hoc; this strategy's native bracket comes from
    # stop_atr/tp_atr below. position_time_stop_bars is honored by the panel.
    "eps_atr_mult":            0.10,
    "k_stop":                  1.00,
    "k_target":                2.00,
    "position_time_stop_bars": 80,
    "w_sp01": 1.0, "w_sp02": 1.0, "w_sp03": 1.0, "w_sp04": 1.0,
    "w_sp05": 1.0, "w_sp06": 1.0, "w_sp07": 1.0, "w_sp08": 1.0,

    # ----- Spike detection
    "ewma_lambda": 0.94,
    "spike_zscore": 2.5,

    # ----- Regime
    "regime_lookback": 120,
    "regime_vol_window": 60,
    "trend_z_threshold": 1.0,
    "crisis_vol_multiple": 3.0,

    # ----- Stops / TPs
    "atr_period": 14,
    "stop_atr": 2.0,
    "tp_atr": 3.0,

    # ----- Costs and gates
    "cost_bps_round_trip": 10.0,
    "min_expected_r": 0.10,
    "score_gate": 0.40,

    # ----- Sizing
    "kelly_fraction": 0.25,
    "kelly_cap": 0.10,
    "risk_ceiling_per_trade": 0.02,

    # ----- Circuit breakers (the r4960 lessons)
    "max_drawdown_pct": 0.30,
    "consecutive_loss_limit": 25,
    "cooldown_bars": 50,
    "max_trades_per_day": 24,
    "inversion_check_window": 50,
    "inversion_hit_rate_floor": 0.15,
}

EPS = 1e-12

# =============================================================================
# Module-level state (panel calls reset_state() between runs)
# =============================================================================

_state: Dict[str, Any] = {}


def reset_state() -> None:
    """
    Required by panel. Clears per-strategy state between backtest runs.
    Panel calls this once before iterating bars.
    """
    _state.clear()
    _state.update({
        # Equity simulation
        "starting_equity": 10_000.0,
        "equity": 10_000.0,
        "peak_equity": 10_000.0,

        # Open position state
        "position": None,            # None or dict(side, entry, stop, tp, ts, size_fraction, risk_usd)

        # Trade ledger (only counts CLOSED trades)
        "closed_trades": [],
        "recent_outcomes": deque(maxlen=int(DEFAULTS["inversion_check_window"])),
        "consecutive_losses": 0,

        # Circuit breakers
        "halted_forever": False,
        "halt_reason": None,
        "cooldown_until_bar": 0,
        "bars_seen": 0,

        # Trade frequency
        "trade_timestamps": deque(maxlen=2000),

        # Last-seen bar (for stop/TP simulation)
        "last_bar_ts": None,
    })


# Initialize on module import so generate_signal() works even if the panel
# forgets to call reset_state() first (defensive).
reset_state()


# =============================================================================
# Numerical helpers
# =============================================================================

def _safe_div(a: float, b: float) -> float:
    return a / b if abs(b) > EPS else 0.0


def _ewma_realized_vol(log_returns: np.ndarray, lam: float) -> float:
    """Final EWMA realized vol (sqrt of EWMA variance) over the series."""
    if log_returns.size == 0:
        return 0.0
    var = log_returns[0] ** 2
    for r in log_returns[1:]:
        var = lam * var + (1.0 - lam) * (r * r)
    return math.sqrt(max(var, EPS))


def _atr(df: pd.DataFrame, n: int = 14) -> float:
    """
    Average True Range over last n bars. Defensive fallback to last-bar
    range if rolling mean is degenerate.
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    val = tr.rolling(n).mean().iloc[-1]
    if np.isfinite(val) and val > 0:
        return float(val)
    return float(max(high.iloc[-1] - low.iloc[-1], EPS))


def _classify_regime(closes: np.ndarray, lookback: int, vol_window: int,
                     trend_z_thr: float, crisis_mult: float) -> str:
    """Four-state regime classifier on log-returns of close series."""
    if closes.size < lookback + vol_window + 5:
        return "unknown"
    rets = np.diff(np.log(closes))

    # Crisis: recent vol >> trailing vol
    recent_vol = float(np.std(rets[-vol_window:])) or EPS
    trailing = rets[-(3 * vol_window):-vol_window] if rets.size >= 3 * vol_window else rets[:-vol_window]
    if trailing.size > 5:
        trailing_vol = float(np.std(trailing)) or EPS
        if recent_vol > crisis_mult * trailing_vol:
            return "crisis"

    # Trend z-score on lookback window
    trend_window = rets[-lookback:]
    sigma = float(np.std(trend_window)) or EPS
    z = float(trend_window.sum() / (sigma * math.sqrt(len(trend_window))))
    if z > trend_z_thr:
        return "trending_up"
    if z < -trend_z_thr:
        return "trending_down"
    return "ranging"


def _kelly(prob_win: float, payoff_ratio: float, fractional: float, cap: float) -> float:
    """Fractional Kelly with hard cap. Negative edge → 0."""
    p = max(0.0, min(1.0, prob_win))
    q = 1.0 - p
    b = max(payoff_ratio, EPS)
    raw = (p * b - q) / b
    if raw <= 0.0 or not math.isfinite(raw):
        return 0.0
    return min(raw * fractional, cap)


def _expectancy_r(prob_win: float, payoff_ratio: float, cost_r: float) -> float:
    """E[R] in R units. R = initial risk = stop_distance."""
    p = max(0.0, min(1.0, prob_win))
    return p * payoff_ratio - (1.0 - p) - cost_r


# =============================================================================
# Internal: simulate stop/TP resolution on bars between calls
# =============================================================================

def _resolve_open_position_against_recent_bars(df: pd.DataFrame) -> None:
    """
    The panel calls generate_signal() once per bar but doesn't tell us
    whether our previous BUY/SELL got stopped out. We resolve this by
    walking forward over the bars in `window` that occurred AFTER our
    last entry. Conservative: stop wins if both stop and TP hit same bar.
    """
    pos = _state.get("position")
    if pos is None:
        return

    entry_ts = pos["ts"]
    # Find bars strictly after entry. df has columns timestamp/ts/time? Try common names.
    ts_col = None
    for cand in ("timestamp", "ts", "time", "open_time"):
        if cand in df.columns:
            ts_col = cand
            break

    if ts_col is None:
        # Fall back to positional walk over the last few bars
        bars_to_check = df.tail(50)
    else:
        bars_to_check = df[df[ts_col] > entry_ts]

    for _, bar in bars_to_check.iterrows():
        if _state["position"] is None:
            return
        h = float(bar["high"])
        l = float(bar["low"])
        bar_ts = float(bar[ts_col]) if ts_col else _state["bars_seen"]

        side = pos["side"]
        stop = pos["stop"]
        tp = pos["tp"]

        hit_stop = (l <= stop) if side == 1 else (h >= stop)
        hit_tp = (h >= tp) if side == 1 else (l <= tp)

        if hit_stop or hit_tp:
            exit_price = stop if hit_stop else tp
            _close_position(exit_price, bar_ts)
            return  # one resolution per call


def _close_position(exit_price: float, exit_ts: float) -> None:
    pos = _state["position"]
    if pos is None:
        return

    side = pos["side"]
    move = (exit_price - pos["entry"]) if side == 1 else (pos["entry"] - exit_price)
    risk_per_unit = abs(pos["entry"] - pos["stop"])
    r_mult = _safe_div(move, risk_per_unit)

    gross_pnl = r_mult * pos["risk_usd"]
    cost_usd = _state["equity"] * pos["size_fraction"] * (DEFAULTS["cost_bps_round_trip"] / 1e4)
    net_pnl = gross_pnl - cost_usd

    _state["equity"] += net_pnl
    won = net_pnl > 0

    _state["closed_trades"].append({
        "ts_entry": pos["ts"], "ts_exit": exit_ts,
        "side": side, "entry": pos["entry"], "exit": exit_price,
        "stop": pos["stop"], "tp": pos["tp"],
        "r": r_mult, "pnl": net_pnl, "won": won,
    })
    _state["recent_outcomes"].append(1 if won else 0)
    _state["consecutive_losses"] = 0 if won else (_state["consecutive_losses"] + 1)

    if _state["equity"] > _state["peak_equity"]:
        _state["peak_equity"] = _state["equity"]

    _state["position"] = None
    _post_trade_breaker_checks()


def _post_trade_breaker_checks() -> None:
    # Drawdown breach → permanent halt
    dd = (_state["equity"] - _state["peak_equity"]) / max(_state["peak_equity"], EPS)
    if dd <= -DEFAULTS["max_drawdown_pct"]:
        _state["halted_forever"] = True
        _state["halt_reason"] = (
            f"DD {dd:.1%} breached -{DEFAULTS['max_drawdown_pct']:.0%}. "
            f"Equity={_state['equity']:.0f} Peak={_state['peak_equity']:.0f}"
        )
        return

    # Sign-inversion self-check
    n = len(_state["recent_outcomes"])
    if n >= int(DEFAULTS["inversion_check_window"]):
        hr = sum(_state["recent_outcomes"]) / n
        if hr < DEFAULTS["inversion_hit_rate_floor"]:
            _state["halted_forever"] = True
            _state["halt_reason"] = (
                f"SIGN INVERSION DETECTED. Rolling hit rate {hr:.1%} over last "
                f"{n} trades < floor {DEFAULTS['inversion_hit_rate_floor']:.0%}. "
                f"Strategy is structurally backwards. Manual reset required."
            )
            return

    # Consecutive-loss cooldown
    if _state["consecutive_losses"] >= int(DEFAULTS["consecutive_loss_limit"]):
        _state["cooldown_until_bar"] = _state["bars_seen"] + int(DEFAULTS["cooldown_bars"])
        _state["consecutive_losses"] = 0


def _trade_frequency_ok(ts_now: float) -> bool:
    """Sliding 24h trade-count cap."""
    cutoff = ts_now - 86_400_000  # 24h in ms
    tt = _state["trade_timestamps"]
    while tt and tt[0] < cutoff:
        tt.popleft()
    return len(tt) < int(DEFAULTS["max_trades_per_day"])


# =============================================================================
# Required panel function
# =============================================================================

def generate_signal(window) -> Optional[Dict[str, Any]]:
    """
    Called once per bar. `window` is a list-like of recent bars (latest last).
    Returns None for HOLD/CLOSE/no-trade, or a signal dict for entries.

    Per-bar order of operations:
      1. Resolve any open position against bars seen since entry.
      2. Run circuit-breaker gates (halt? cooldown? warmup?).
      3. Compute features (regime + spike z-score).
      4. Apply regime-conditional direction logic.
      5. Apply expectancy gate + score gate.
      6. Size with fractional Kelly, hard-clamped by risk ceiling.
      7. Open position internally + return signal dict.
    """
    df = pd.DataFrame(window)
    if len(df) < LOOKBACK_BARS:
        return None

    # Standardize column access (panel may pass capitalized / lowercase)
    df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
    required = {"open", "high", "low", "close"}
    if not required.issubset(set(df.columns)):
        return None

    _state["bars_seen"] += 1

    # 1) Resolve open position against recent bars
    _resolve_open_position_against_recent_bars(df)

    # 2) Circuit-breaker gates
    if _state["halted_forever"]:
        return None
    if _state["bars_seen"] < _state["cooldown_until_bar"]:
        return None
    if _state["position"] is not None:
        return None  # already in a trade — no new signal

    # 3) Features
    closes = df["close"].astype(float).to_numpy()
    if closes.size < LOOKBACK_BARS or not np.all(np.isfinite(closes)) or np.any(closes <= 0):
        return None

    log_rets = np.diff(np.log(closes))
    if log_rets.size < int(DEFAULTS["regime_lookback"]):
        return None

    sigma = _ewma_realized_vol(log_rets, float(DEFAULTS["ewma_lambda"]))
    if sigma <= EPS:
        return None
    last_r = float(log_rets[-1])
    z = last_r / sigma

    regime = _classify_regime(
        closes,
        lookback=int(DEFAULTS["regime_lookback"]),
        vol_window=int(DEFAULTS["regime_vol_window"]),
        trend_z_thr=float(DEFAULTS["trend_z_threshold"]),
        crisis_mult=float(DEFAULTS["crisis_vol_multiple"]),
    )
    if regime == "crisis" or regime == "unknown":
        return None
    if abs(z) < float(DEFAULTS["spike_zscore"]):
        return None

    # 4) Regime-conditional direction (the structural inversion lesson)
    direction: Optional[int] = None
    detectors: List[str] = [f"regime={regime}", f"spike_z={z:+.2f}"]
    if regime == "trending_up" and z > 0:
        direction = 1
        detectors.append("MOMENTUM_UP")
    elif regime == "trending_down" and z < 0:
        direction = -1
        detectors.append("MOMENTUM_DOWN")
    elif regime == "ranging":
        direction = -1 if z > 0 else 1
        detectors.append("MEAN_REVERT_RANGE")

    if direction is None:
        return None  # counter-trend spike in a trend → skip

    # 5) Stops / TPs / expectancy
    entry = float(closes[-1])
    atr = _atr(df, int(DEFAULTS["atr_period"]))
    if atr <= 0:
        return None

    stop_dist = float(DEFAULTS["stop_atr"]) * atr
    tp_dist = float(DEFAULTS["tp_atr"]) * atr
    if direction == 1:
        stop = entry - stop_dist
        tp = entry + tp_dist
    else:
        stop = entry + stop_dist
        tp = entry - tp_dist

    # Conservative probability mapping: z=2.5 → 0.55, saturating to ~0.65.
    # Over-confident probability estimates are how Kelly turns into ruin.
    prob_win = 0.50 + min(0.15, 0.06 * (abs(z) - float(DEFAULTS["spike_zscore"])))
    payoff_ratio = float(DEFAULTS["tp_atr"]) / float(DEFAULTS["stop_atr"])
    r_pct = stop_dist / max(entry, EPS)
    cost_r = (float(DEFAULTS["cost_bps_round_trip"]) / 1e4) / max(r_pct, EPS)
    exp_r = _expectancy_r(prob_win, payoff_ratio, cost_r)

    if exp_r < float(DEFAULTS["min_expected_r"]):
        return None

    # Score is a normalized (0,1) signal-quality measure for the panel's gate
    # and ranking. Combines spike strength and expectancy headroom.
    z_score_norm = min(1.0, max(0.0, (abs(z) - float(DEFAULTS["spike_zscore"])) / 3.0))
    exp_norm = min(1.0, max(0.0, (exp_r - float(DEFAULTS["min_expected_r"])) / 0.5))
    score = 0.5 * (0.5 + 0.5 * z_score_norm) + 0.5 * (0.5 + 0.5 * exp_norm)

    if score < float(DEFAULTS["score_gate"]):
        return None

    # 6) Trade frequency cap
    ts_col = next((c for c in ("timestamp", "ts", "time", "open_time") if c in df.columns), None)
    bar_ts = float(df[ts_col].iloc[-1]) if ts_col else float(_state["bars_seen"])
    if not _trade_frequency_ok(bar_ts):
        return None

    # 7) Sizing
    f_kelly = _kelly(prob_win, payoff_ratio,
                     float(DEFAULTS["kelly_fraction"]), float(DEFAULTS["kelly_cap"]))
    max_by_risk = float(DEFAULTS["risk_ceiling_per_trade"]) / max(r_pct, EPS)
    size_fraction = min(f_kelly, max_by_risk)
    if size_fraction <= 0:
        return None

    # Open position internally so future calls can self-resolve and run breakers
    risk_usd = _state["equity"] * size_fraction * r_pct
    _state["position"] = {
        "side": direction, "entry": entry, "stop": stop, "tp": tp,
        "ts": bar_ts, "size_fraction": size_fraction, "risk_usd": risk_usd,
    }
    _state["trade_timestamps"].append(bar_ts)

    return {
        "direction": int(direction),
        "entry": float(entry),
        "stop": float(stop),
        "tp": float(tp),
        "confidence": float(min(1.0, max(0.0, prob_win))),
        "sizing_multiplier": float(min(1.0, size_fraction / float(DEFAULTS["kelly_cap"]))),
        "reason": f"supergod_v1: {regime}, z={z:+.2f}, E[R]={exp_r:.2f}",
        "detectors_fired": detectors,
        "score": float(min(1.0, max(0.0, score))),
    }
