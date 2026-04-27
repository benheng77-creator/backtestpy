from pathlib import Path
import json, re

ROOT = Path(r"C:\Users\jvben\Desktop\EXHANGE BACKTET DATAS")
OUT = ROOT / "_registry" / "data_index.json"

SKIP_TOP = {
    "_logs","_duplicates","_checkpoints","_registry","_backtest_engine",
    "_strategies","_results","_reports","_ui","_ui_runs",
    "_browse","browse","exchange_data","unknown_exchange"
}

TIMEFRAMES = {"1m","3m","5m","15m","30m","1h","4h","1d","1w","1mo","funding"}
QUOTE_SUFFIXES = ("USDT","USDC","USD","BTC","ETH","BUSD","FDUSD","EUR","GBP","JPY","SGD")

def detect_tf(name):
    m = re.search(r"(?:^|[_\-.])(1m|3m|5m|15m|30m|1h|4h|1d|1w|1mo|funding)(?:__\d+)?(?:\.|$)", name.lower())
    return m.group(1) if m else None

def clean_symbol(name):
    s = Path(name).stem.upper()
    s = re.sub(r"^(BINANCE|OKX|BYBIT|CDC|CRYPTOCOM|COINBASE|KRAKEN|KUCOIN|BITGET|MEXC|GATEIO|HTX)_", "", s)
    s = re.sub(r"_(1M|3M|5M|15M|30M|1H|4H|1D|1W|1MO|FUNDING)(?:__\d+)?$", "", s)
    s = re.sub(r"[^A-Z0-9.-]+", "_", s).strip("_")
    return s

def valid_symbol(sym):
    if not sym or "UNKNOWN" in sym: return False
    if sym.lower() in TIMEFRAMES: return False
    if re.fullmatch(r"\d+(M|H|D|W)(?:__\d+)?", sym): return False
    return any(sym.endswith(q) and len(sym)>len(q) for q in QUOTE_SUFFIXES)

items = []

for ex_dir in ROOT.iterdir():
    if not ex_dir.is_dir(): continue
    ex = ex_dir.name
    if ex.startswith("_") or ex in SKIP_TOP: continue

    for f in ex_dir.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in [".json",".csv"]: continue
        tf = detect_tf(f.name)
        sym = clean_symbol(f.name)
        if not tf or not valid_symbol(sym): continue
        items.append({
            "exchange": ex,
            "symbol": sym,
            "timeframe": tf,
            "path": str(f),
            "file": f.name,
            "source_top_folder": ex,
            "size_bytes": f.stat().st_size,
            "size_mb": round(f.stat().st_size/1024/1024, 4),
        })

items.sort(key=lambda x:(x["exchange"],x["symbol"],x["timeframe"],x["file"]))
OUT.parent.mkdir(exist_ok=True)
OUT.write_text(json.dumps(items, indent=2), encoding="utf-8")

summary = {
    "files": len(items),
    "size_gb": round(sum(x["size_bytes"] for x in items)/1024/1024/1024, 3),
    "size_mb": round(sum(x["size_bytes"] for x in items)/1024/1024, 2),
    "exchanges": sorted(set(x["exchange"] for x in items)),
    "timeframes": sorted(set(x["timeframe"] for x in items)),
}
print(json.dumps(summary, indent=2))
