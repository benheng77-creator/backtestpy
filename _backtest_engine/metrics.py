def metrics(trades, starting_capital=1000.0):
    if not trades:
        return {
            "trades": 0,
            "win_rate": 0,
            "net_pnl_cash": 0,
            "net_pnl_pct": 0,
            "avg_pnl_pct": 0,
            "profit_factor": 0,
            "max_drawdown_pct": 0,
            "tp_exits": 0,
            "sl_exits": 0,
            "reverse_exits": 0,
            "timeout_exits": 0,
            "verdict": "NO_TRADES",
        }

    pnls = [float(t["net_pct"]) for t in trades]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]

    equity = float(starting_capital)
    peak = equity
    maxdd = 0.0

    for t in trades:
        equity = float(t["equity_after"])
        peak = max(peak, equity)
        maxdd = min(maxdd, equity / peak - 1)

    pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 999 if wins else 0
    wr = len(wins) / len(pnls)
    net_cash = equity - starting_capital
    net_pct = equity / starting_capital - 1

    verdict = "PROMOTE" if net_pct > 0 and len(trades) >= 30 and pf >= 1.2 and maxdd > -0.25 else "WATCHLIST" if net_pct > 0 else "REJECT"

    return {
        "trades": len(trades),
        "win_rate": round(wr, 4),
        "net_pnl_cash": round(net_cash, 4),
        "net_pnl_pct": round(net_pct, 6),
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 6),
        "profit_factor": round(pf, 4),
        "max_drawdown_pct": round(maxdd, 6),
        "tp_exits": sum(1 for t in trades if t["exit_reason"] == "TP"),
        "sl_exits": sum(1 for t in trades if t["exit_reason"] == "SL"),
        "reverse_exits": sum(1 for t in trades if t["exit_reason"] == "REVERSE"),
        "timeout_exits": sum(1 for t in trades if t["exit_reason"] == "TIMEOUT"),
        "verdict": verdict,
    }

