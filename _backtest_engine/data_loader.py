import json, csv
from pathlib import Path

def load_rows(path):
    path = Path(path)

    if path.suffix.lower() == ".csv":
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            return list(csv.DictReader(f))

    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        obj = json.load(f)

    if isinstance(obj, list):
        return obj

    if isinstance(obj, dict):
        for key in ["data","rows","candles","ohlcv","klines","history","result","list"]:
            v = obj.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                for vv in v.values():
                    if isinstance(vv, list):
                        return vv

        for v in obj.values():
            if isinstance(v, list):
                return v

    return []
