"""BotXLLM Gate-1 + Gate-2 validator. Run from repo root.

Checks for each target .py:
  - imports cleanly (no relative-import errors, no missing common.* symbols)
  - exposes SYMBOLS, TIMEFRAME, LOOKBACK_BARS, DEFAULTS, reset_state, generate_signal
  - DEFAULTS contains all 12 mandatory Recipe knobs
  - generate_signal(window) on synthetic data returns either None or full-shape dict
"""
from __future__ import annotations

import importlib.util
import math
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

TARGETS = [
    "sp_compression_voting_stack.py",
    "sp_donchian_breakout.py",
    "sp_ema_pullback.py",
    "sp_bollinger_meanrevert.py",
    "sp_rsi_extreme_revert.py",
    "sp_macd_cross_long.py",
    "sp_keltner_squeeze_break.py",
    "sp_atr_expansion_break.py",
    "sp_supertrend_flip.py",
    "sp_obv_divergence_long.py",
    "sp_higher_high_continuation.py",
    "sp_cdv_joint_score_long.py",
    "Clause/pre_spike_compression_strategy.py",
    "Clause/sp_supergod_v1.py",
]

MANDATORY_KNOBS = [
    "eps_atr_mult", "k_stop", "k_target", "position_time_stop_bars",
    "w_sp01", "w_sp02", "w_sp03", "w_sp04",
    "w_sp05", "w_sp06", "w_sp07", "w_sp08",
]

REQUIRED_ATTRS = ["SYMBOLS", "TIMEFRAME", "LOOKBACK_BARS", "DEFAULTS",
                  "reset_state", "generate_signal"]

REQUIRED_SHAPE = ["direction", "entry", "stop", "tp", "confidence",
                  "sizing_multiplier", "reason", "detectors_fired", "score"]


def synthetic_window(n: int):
    out = []
    price = 100.0
    for i in range(n):
        price += 0.05 + math.sin(i / 11.0) * 0.02
        out.append({
            "open": price - 0.10,
            "high": price + 0.35,
            "low":  price - 0.35,
            "close": price,
            "volume": 1000.0 + i,
            "timestamp": 1_700_000_000_000 + i * 60_000,
        })
    return out


def validate(rel: str) -> tuple[bool, str]:
    path = ROOT / rel
    if not path.exists():
        return False, "MISSING_FILE"
    name = f"botxllm_v_{path.stem}_{int(time.time()*1000)}"
    try:
        spec = importlib.util.spec_from_file_location(name, str(path))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    except Exception as exc:
        return False, f"IMPORT_FAIL: {type(exc).__name__}: {exc}"

    for a in REQUIRED_ATTRS:
        if not hasattr(mod, a):
            return False, f"MISSING_ATTR: {a}"

    for k in MANDATORY_KNOBS:
        if k not in mod.DEFAULTS:
            return False, f"MISSING_KNOB: {k}"

    try:
        mod.reset_state()
    except Exception as exc:
        return False, f"RESET_STATE_FAIL: {type(exc).__name__}: {exc}"

    n = max(int(getattr(mod, "LOOKBACK_BARS", 200)) + 50, 300)
    try:
        sig = mod.generate_signal(synthetic_window(n))
    except Exception as exc:
        return False, f"GENERATE_SIGNAL_FAIL: {type(exc).__name__}: {exc}"

    if sig is None:
        return True, "OK (no fire on synthetic)"
    if not isinstance(sig, dict):
        return False, f"SIGNAL_NOT_DICT: {type(sig).__name__}"
    missing = [k for k in REQUIRED_SHAPE if k not in sig]
    if missing:
        return False, f"SIGNAL_MISSING_FIELDS: {missing}"
    return True, "OK (full signal shape)"


def main():
    print("=== BotXLLM Gate-1 + Gate-2 Validation ===\n")
    failures = []
    for rel in TARGETS:
        ok, msg = validate(rel)
        tag = "PASS" if ok else "FAIL"
        print(f"{tag}: {rel}\n      {msg}")
        if not ok:
            failures.append(rel)
    print(f"\n=== {len(TARGETS) - len(failures)}/{len(TARGETS)} passed ===")
    if failures:
        print("Failures:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL TARGET STRATEGIES ARE BOTXLLM BACKTEST + RECIPE SPREAD READY.")


if __name__ == "__main__":
    main()
