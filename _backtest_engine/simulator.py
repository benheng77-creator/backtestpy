def simulate(
    candles,
    signal_fn,
    starting_capital=1000.0,
    maker_fee=0.001,
    taker_fee=0.001,
    slippage=0.0005,
    max_hold_bars=96,
    default_tp_pct=0.03,
    default_sl_pct=0.015,
    lookback=200,
    strategy_window_bars=500,
    signal_step=1,
):
    trades = []
    position = None
    cash = float(starting_capital)
    state = {}

    n = len(candles)
    if n <= lookback + 2:
        return trades

    signal_step = max(1, int(signal_step))
    strategy_window_bars = max(50, int(strategy_window_bars))

    for i in range(lookback, n - 1):
        bar = candles[i + 1]

        run_signal = (i % signal_step == 0) or position is not None
        if run_signal:
            start = max(0, i - strategy_window_bars)
            sig = signal_fn(candles[start:i], state=state, params={})
            action = str(sig.get("action", "HOLD")).upper()
        else:
            sig = {"action": "HOLD", "reason": "signal_step_skip"}
            action = "HOLD"

        if position is None:
            if action == "BUY":
                entry = float(bar["open"]) * (1 + slippage)

                try:
                    tp_pct = float(sig.get("take_profit")) if sig.get("take_profit") is not None else default_tp_pct
                except Exception:
                    tp_pct = default_tp_pct

                try:
                    sl_pct = abs(float(sig.get("stop_loss"))) if sig.get("stop_loss") is not None else default_sl_pct
                except Exception:
                    sl_pct = default_sl_pct

                position = {
                    "entry_i": i + 1,
                    "entry_ts": bar["ts"],
                    "entry": entry,
                    "tp": entry * (1 + tp_pct),
                    "sl": entry * (1 - sl_pct),
                    "entry_reason": sig.get("reason", ""),
                }
            continue

        held = (i + 1) - position["entry_i"]
        high = float(bar["high"])
        low = float(bar["low"])

        exit_reason = None
        exit_price = None

        if low <= position["sl"]:
            exit_reason = "SL"
            exit_price = position["sl"] * (1 - slippage)
        elif high >= position["tp"]:
            exit_reason = "TP"
            exit_price = position["tp"] * (1 - slippage)
        elif action == "SELL":
            exit_reason = "REVERSE"
            exit_price = float(bar["open"]) * (1 - slippage)
        elif held >= max_hold_bars:
            exit_reason = "TIMEOUT"
            exit_price = float(bar["open"]) * (1 - slippage)

        if exit_reason:
            gross_pct = (exit_price / position["entry"]) - 1
            net_pct = gross_pct - maker_fee - taker_fee
            pnl_cash = cash * net_pct
            cash += pnl_cash

            trades.append({
                "entry_ts": position["entry_ts"],
                "exit_ts": bar["ts"],
                "entry": round(position["entry"], 10),
                "exit": round(exit_price, 10),
                "gross_pct": round(gross_pct, 8),
                "net_pct": round(net_pct, 8),
                "pnl_cash": round(pnl_cash, 6),
                "equity_after": round(cash, 6),
                "hold_bars": held,
                "exit_reason": exit_reason,
                "entry_reason": position["entry_reason"],
            })

            position = None

    return trades
