import argparse, csv, json, sys, time, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / "_analysis_engine"
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ROOT / "_strategies"))

from data_loader import load_rows
from normalizer import normalize_candles
from strategy_adapter import load_strategy, get_signal_fn, call_strategy
from simulator import simulate
from metrics import metrics

STATE_PATH = None
STATE = {}

def save_state(**kwargs):
    global STATE
    STATE.update(kwargs)
    STATE["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if STATE_PATH:
        Path(STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(STATE_PATH).write_text(json.dumps(STATE, indent=2), encoding="utf-8")

def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

def main():
    global STATE_PATH

    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--exchange", default="all")
    ap.add_argument("--symbols", default="all")
    ap.add_argument("--timeframes", default="1m,5m,15m,30m,1h,4h,1d")
    ap.add_argument("--starting-capital", type=float, default=1000.0)
    ap.add_argument("--maker-fee", type=float, default=0.001)
    ap.add_argument("--taker-fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--max-hold-bars", type=int, default=96)
    ap.add_argument("--default-tp", type=float, default=0.03)
    ap.add_argument("--default-sl", type=float, default=0.015)
    ap.add_argument("--max-bars", type=int, default=50000)
    ap.add_argument("--max-files", type=int, default=999999)
    ap.add_argument("--strategy-lookback-bars", type=int, default=500)
    ap.add_argument("--signal-step", type=int, default=1)
    ap.add_argument("--run-state", default="")
    args = ap.parse_args()

    STATE_PATH = args.run_state or None
    started = time.time()
    strat_name = Path(args.strategy).stem

    save_state(status="STARTING", strategy_name=strat_name, current_index=0, total_files=0, percent=0, error="")

    try:
        registry = json.loads((ROOT / "_registry" / "data_index.json").read_text(encoding="utf-8"))

        wanted_ex = None if args.exchange == "all" else set(x.strip().lower() for x in args.exchange.split(",") if x.strip())
        wanted_sym = None if args.symbols == "all" else set(x.strip().upper() for x in args.symbols.split(",") if x.strip())
        wanted_tf = None if args.timeframes == "all" else set(x.strip().lower() for x in args.timeframes.split(",") if x.strip())

        selected = []
        for item in registry:
            if wanted_ex and item["exchange"].lower() not in wanted_ex: continue
            if wanted_sym and item["symbol"].upper() not in wanted_sym: continue
            if wanted_tf and item["timeframe"].lower() not in wanted_tf: continue
            if item["timeframe"].lower() == "funding": continue
            selected.append(item)

        if args.max_files > 0:
            selected = selected[:args.max_files]

        selected_size_mb = round(sum(float(x.get("size_mb", 0)) for x in selected), 2)
        exchanges = sorted(set(x["exchange"] for x in selected))
        symbols = sorted(set(x["symbol"] for x in selected))
        tfs = sorted(set(x["timeframe"] for x in selected))

        save_state(
            status="LOADING_STRATEGY",
            selected_files=len(selected),
            total_files=len(selected),
            selected_size_mb=selected_size_mb,
            selected_size_gb=round(selected_size_mb/1024, 3),
            exchanges=exchanges,
            symbols_sample=symbols[:50],
            timeframes=tfs,
            exchange_filter=args.exchange,
            symbol_filter=args.symbols,
            timeframe_filter=args.timeframes,
            max_bars=args.max_bars,
            max_files=args.max_files,
        )

        print("=== RUN SUMMARY ===", flush=True)
        print(f"strategy={strat_name}", flush=True)
        print(f"selected_files={len(selected)}", flush=True)
        print(f"selected_size_mb={selected_size_mb}", flush=True)
        print(f"selected_size_gb={round(selected_size_mb/1024,3)}", flush=True)
        print(f"exchange_filter={args.exchange}", flush=True)
        print(f"symbol_filter={args.symbols}", flush=True)
        print(f"timeframe_filter={args.timeframes}", flush=True)
        print(f"exchanges={','.join(exchanges)}", flush=True)
        print(f"symbols_count={len(symbols)}", flush=True)
        print(f"timeframes={','.join(tfs)}", flush=True)
        print("=== START ===", flush=True)

        mod = load_strategy(args.strategy)
        raw_fn = get_signal_fn(mod)
        def fn(candles, state=None, params=None):
            return call_strategy(raw_fn, candles, state, params)

        out_root = ROOT / "_results" / strat_name
        all_metrics = []
        failures = []

        for idx, item in enumerate(selected, 1):
            label = f'{item["exchange"]}/{item["symbol"]}/{item["timeframe"]}'
            percent = round((idx-1) / len(selected) * 100, 1) if selected else 0

            save_state(
                status="RUNNING",
                current_index=idx,
                total_files=len(selected),
                percent=percent,
                current_exchange=item["exchange"],
                current_symbol=item["symbol"],
                current_timeframe=item["timeframe"],
                current_file=item.get("file",""),
                current_path=item.get("path",""),
                analyzed=len(all_metrics),
                failures=len(failures),
            )

            print(f"[{idx}/{len(selected)}] START {label} size_mb={item.get('size_mb',0)}", flush=True)

            try:
                rows = load_rows(item["path"])
                candles = normalize_candles(rows)

                if args.max_bars > 0 and len(candles) > args.max_bars:
                    candles = candles[-args.max_bars:]

                if len(candles) < 300:
                    failures.append({**item, "error": f"not enough candles: {len(candles)}"})
                    print(f"[{idx}/{len(selected)}] SKIP {label} candles={len(candles)} reason=not_enough_candles", flush=True)
                    continue

                trades = simulate(
                    candles,
                    fn,
                    starting_capital=args.starting_capital,
                    maker_fee=args.maker_fee,
                    taker_fee=args.taker_fee,
                    slippage=args.slippage,
                    max_hold_bars=args.max_hold_bars,
                    default_tp_pct=args.default_tp,
                    default_sl_pct=args.default_sl,
                    strategy_window_bars=args.strategy_lookback_bars,
                    signal_step=args.signal_step,
                )

                m = metrics(trades, starting_capital=args.starting_capital)
                tag = f'{item["exchange"]}_{item["symbol"]}_{item["timeframe"]}_{idx}'
                write_csv(out_root / f"{tag}_trades.csv", trades)

                all_metrics.append({"strategy": strat_name, **item, **m})

                print(f"[{idx}/{len(selected)}] DONE {label} candles={len(candles)} trades={m.get('trades')} pnl={m.get('net_pnl_pct')} pf={m.get('profit_factor')} verdict={m.get('verdict')}", flush=True)

            except Exception as e:
                failures.append({**item, "error": repr(e)})
                print(f"[{idx}/{len(selected)}] FAIL {label} error={repr(e)}", flush=True)

        write_csv(out_root / "metrics.csv", all_metrics)
        write_csv(out_root / "failures.csv", failures)

        leaderboard = sorted(
            all_metrics,
            key=lambda x: (float(x.get("net_pnl_pct", 0) or 0), float(x.get("profit_factor", 0) or 0), int(x.get("trades", 0) or 0)),
            reverse=True
        )

        report = ROOT / "_reports" / f"{strat_name}_leaderboard.csv"
        write_csv(report, leaderboard)

        save_state(
            status="FINISHED",
            current_index=len(selected),
            total_files=len(selected),
            percent=100,
            analyzed=len(all_metrics),
            failures=len(failures),
            seconds=round(time.time()-started, 2),
            leaderboard=str(report),
            result_folder=str(out_root),
        )

        print("=== RUN COMPLETE ===", flush=True)
        print(f"analyzed={len(all_metrics)}", flush=True)
        print(f"failures={len(failures)}", flush=True)
        print(f"seconds={round(time.time()-started,2)}", flush=True)
        print(f"leaderboard={report}", flush=True)

    except Exception:
        err = traceback.format_exc()
        save_state(status="CRASHED", error=err)
        print(err, flush=True)
        raise

if __name__ == "__main__":
    main()
