"""BotXLLM panel-contract wrapper helpers.

Strategies expose a `_base_generate_signal(window) -> dict | None` that emits
at minimum {direction, entry, stop, tp, confidence, position_time_stop_bars}.
This module fills in the remaining required fields (sizing_multiplier,
reason, detectors_fired, score) and provides a no-op reset_state when the
strategy has no per-run state.
"""
from __future__ import annotations

from typing import Callable, Optional


def normalize_signal(sig: Optional[dict], strategy_name: str,
                     detectors: Optional[list] = None) -> Optional[dict]:
    if sig is None:
        return None
    confidence = float(sig.get("confidence", 0.5))
    sig.setdefault("sizing_multiplier", 1.0)
    sig.setdefault("score", confidence)
    sig.setdefault("detectors_fired", list(detectors) if detectors else [strategy_name])
    sig.setdefault("reason", strategy_name)
    return sig


def make_panel_pair(base_generate_signal: Callable, strategy_name: str,
                    detectors: Optional[list] = None,
                    base_reset_state: Optional[Callable] = None):
    """Return (reset_state, generate_signal) bound to the strategy's base fn."""
    def reset_state() -> None:
        if base_reset_state is not None:
            base_reset_state()

    def generate_signal(window):
        return normalize_signal(base_generate_signal(window),
                                strategy_name, detectors)

    return reset_state, generate_signal
