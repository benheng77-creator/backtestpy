import importlib.util
import inspect
import sys
from pathlib import Path

try:
    import pandas as pd
except Exception:
    pd = None

def load_strategy(path):
    path = Path(path).resolve()
    sys.path.insert(0, str(path.parent))
    sys.path.insert(0, str(path.parent.parent))
    sys.path.insert(0, str(path.parent / "common"))

    module_name = "real_strategy_" + path.stem.replace("-", "_").replace(" ", "_")
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load strategy: {path}")

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def get_signal_fn(mod):
    for name in [
        "generate_signal",
        "signal",
        "get_signal",
        "strategy",
        "decide",
        "generate",
        "run",
        "evaluate",
        "evaluate_signal",
        "entry_signal",
    ]:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn

    cls = getattr(mod, "Strategy", None)
    if cls:
        obj = cls()
        for name in [
            "generate_signal",
            "signal",
            "get_signal",
            "decide",
            "run",
            "evaluate",
            "evaluate_signal",
            "entry_signal",
        ]:
            fn = getattr(obj, name, None)
            if callable(fn):
                return fn

    raise AttributeError("No strategy callable found.")

def _get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, row.get(key.upper(), default))
    return default

def _to_df(candles):
    if pd is None:
        return None

    df = pd.DataFrame(candles)
    if df.empty:
        return df

    df.columns = [str(c).lower() for c in df.columns]

    aliases = {
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "vol": "volume",
        "time": "timestamp",
        "ts": "timestamp",
    }

    for old, new in aliases.items():
        if old in df.columns and new not in df.columns:
            df[new] = df[old]

    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = 0.0

    # aliases for strategies that use short names
    df["o"] = df["open"]
    df["h"] = df["high"]
    df["l"] = df["low"]
    df["c"] = df["close"]
    df["v"] = df["volume"]

    return df

def _to_arrays(candles):
    out = {
        "open": [],
        "high": [],
        "low": [],
        "close": [],
        "volume": [],
        "o": [],
        "h": [],
        "l": [],
        "c": [],
        "v": [],
    }

    for r in candles:
        o = float(_get(r, "open", 0) or 0)
        h = float(_get(r, "high", 0) or 0)
        l = float(_get(r, "low", 0) or 0)
        c = float(_get(r, "close", 0) or 0)
        v = float(_get(r, "volume", _get(r, "vol", 0)) or 0)

        out["open"].append(o); out["o"].append(o)
        out["high"].append(h); out["h"].append(h)
        out["low"].append(l); out["l"].append(l)
        out["close"].append(c); out["c"].append(c)
        out["volume"].append(v); out["v"].append(v)

    return out

def _truthy(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y", "buy", "long", "enter", "entry", "open_long", "enter_long"}
    return bool(v)

def _norm_action(x):
    if x is None:
        return "HOLD"

    if isinstance(x, bool):
        return "BUY" if x else "HOLD"

    if isinstance(x, (int, float)):
        if x > 0:
            return "BUY"
        if x < 0:
            return "SELL"
        return "HOLD"

    s = str(x).upper().strip()

    buy_words = {
        "BUY", "LONG", "ENTER", "ENTRY", "ENTER_LONG", "OPEN_LONG",
        "GO_LONG", "ALLOW_LONG", "BULL", "BULLISH", "TRIGGER_LONG",
        "FIRE", "FIRED", "TAKE", "TRADE", "1", "TRUE", "YES"
    }

    sell_words = {
        "SELL", "EXIT", "CLOSE", "CLOSE_LONG", "EXIT_LONG",
        "STOP", "STOP_LONG", "FLAT", "-1"
    }

    hold_words = {
        "HOLD", "WAIT", "NO_TRADE", "NONE", "NEUTRAL", "0", "FALSE", "NO", ""
    }

    if s in buy_words:
        return "BUY"
    if s in sell_words:
        return "SELL"
    if s in hold_words:
        return "HOLD"

    if "BUY" in s or "LONG" in s or "ENTER" in s:
        return "BUY"
    if "SELL" in s or "EXIT" in s or "CLOSE" in s:
        return "SELL"

    return "HOLD"

def _normalize_pct(v):
    if v is None:
        return None
    try:
        x = float(v)
        if x > 1:
            return x / 100.0
        return x
    except Exception:
        return None

def _normalize_output(result):
    if result is None or result is False:
        return {"action": "HOLD"}

    if isinstance(result, (bool, int, float, str)):
        return {"action": _norm_action(result)}

    if isinstance(result, (tuple, list)):
        if not result:
            return {"action": "HOLD"}

        out = {"action": _norm_action(result[0])}

        if len(result) > 1:
            out["take_profit_pct"] = _normalize_pct(result[1])
            out["tp_pct"] = out["take_profit_pct"]

        if len(result) > 2:
            out["stop_loss_pct"] = _normalize_pct(result[2])
            out["sl_pct"] = out["stop_loss_pct"]

        return out

    if isinstance(result, dict):
        out = dict(result)

        raw = None
        for key in [
            "action", "signal", "decision", "side", "direction", "position",
            "order", "order_side", "trade", "verdict", "recommendation",
            "entry", "entry_signal", "final_signal", "label"
        ]:
            if key in out:
                raw = out.get(key)
                break

        if raw is None:
            buy_keys = [
                "buy", "long", "enter", "entry_long", "enter_long",
                "open_long", "should_buy", "should_enter", "allow_long",
                "signal_long", "trigger", "fire", "fired", "take_trade",
                "trade_allowed", "entry_ok"
            ]
            sell_keys = [
                "sell", "exit", "close", "close_long", "exit_long",
                "should_exit", "stop"
            ]

            if any(_truthy(out.get(k)) for k in buy_keys if k in out):
                raw = "BUY"
            elif any(_truthy(out.get(k)) for k in sell_keys if k in out):
                raw = "SELL"

        out["action"] = _norm_action(raw)

        tp_keys = ["take_profit_pct", "tp_pct", "tp", "take_profit", "target_pct", "target", "profit_target"]
        sl_keys = ["stop_loss_pct", "sl_pct", "sl", "stop_loss", "risk_pct", "stop"]

        for k in tp_keys:
            if k in out:
                out["take_profit_pct"] = _normalize_pct(out.get(k))
                out["tp_pct"] = out["take_profit_pct"]
                break

        for k in sl_keys:
            if k in out:
                out["stop_loss_pct"] = _normalize_pct(out.get(k))
                out["sl_pct"] = out["stop_loss_pct"]
                break

        return out

    return {"action": "HOLD"}

def _call_candidates(fn, candles, state=None, params=None):
    params = params or {}
    df = _to_df(candles)
    arrays = _to_arrays(candles)

    payloads = []
    if df is not None:
        payloads.append(("df", df))
    payloads.append(("candles", candles))
    payloads.append(("arrays", arrays))
    payloads.append(("payload", {
        "df": df,
        "data": df,
        "candles": candles,
        "bars": candles,
        "ohlcv": candles,
        "arrays": arrays,
        "open": arrays["open"],
        "high": arrays["high"],
        "low": arrays["low"],
        "close": arrays["close"],
        "volume": arrays["volume"],
    }))

    calls = []

    for mode, payload in payloads:
        calls.extend([
            (mode + "(x,state,params)", lambda payload=payload: fn(payload, state, params)),
            (mode + "(x,state)", lambda payload=payload: fn(payload, state)),
            (mode + "(x,params)", lambda payload=payload: fn(payload, params)),
            (mode + "(x)", lambda payload=payload: fn(payload)),
        ])

    # keyword-call support for functions with named args
    try:
        sig = inspect.signature(fn)
        names = list(sig.parameters.keys())
        kwargs = {}
        for n in names:
            ln = n.lower()
            if ln in {"df", "data", "frame", "panel"} and df is not None:
                kwargs[n] = df
            elif ln in {"candles", "bars", "ohlcv", "rows"}:
                kwargs[n] = candles
            elif ln in {"arrays", "arr"}:
                kwargs[n] = arrays
            elif ln == "state":
                kwargs[n] = state
            elif ln in {"params", "config", "cfg"}:
                kwargs[n] = params
        if kwargs:
            calls.insert(0, ("kwargs", lambda kwargs=kwargs: fn(**kwargs)))
    except Exception:
        pass

    return calls

def call_strategy(fn, candles, state=None, params=None):
    first_hold = None
    errors = []

    for mode, call in _call_candidates(fn, candles, state, params):
        try:
            sig = _normalize_output(call())
            sig["_adapter_mode"] = mode

            if sig.get("action") != "HOLD":
                return sig

            if first_hold is None:
                first_hold = sig

        except Exception as e:
            errors.append(f"{mode}: {repr(e)}")
            continue

    if first_hold is not None:
        return first_hold

    raise RuntimeError("All strategy call modes failed: " + " | ".join(errors[-8:]))
