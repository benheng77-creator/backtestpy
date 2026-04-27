def to_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default

def to_ts(x):
    try:
        n = int(float(x))
        if n > 10**17:
            return n // 1_000_000
        if n > 10**14:
            return n // 1_000
        if n > 10**11:
            return n
        if n > 10**8:
            return n * 1000
        return n
    except Exception:
        return None

def normalize_candles(rows):
    out = []

    for r in rows:
        if isinstance(r, list) and len(r) >= 6:
            ts = to_ts(r[0])
            o = to_float(r[1])
            h = to_float(r[2])
            l = to_float(r[3])
            c = to_float(r[4])
            v = to_float(r[5], 0.0)
        elif isinstance(r, dict):
            ts = to_ts(r.get("ts") or r.get("timestamp") or r.get("time") or r.get("t") or r.get("open_time") or r.get("openTime"))
            o = to_float(r.get("open") or r.get("o"))
            h = to_float(r.get("high") or r.get("h"))
            l = to_float(r.get("low") or r.get("l"))
            c = to_float(r.get("close") or r.get("c"))
            v = to_float(r.get("volume") or r.get("vol") or r.get("v") or 0, 0.0)
        else:
            continue

        if ts is not None and o is not None and h is not None and l is not None and c is not None:
            out.append({"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v})

    out.sort(key=lambda x: x["ts"])

    dedup = {}
    for x in out:
        dedup[x["ts"]] = x

    return list(dedup.values())
