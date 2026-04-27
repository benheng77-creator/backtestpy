"""Shared indicator primitives. numpy + pandas only."""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


def _true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    pc = np.empty_like(c)
    pc[0] = c[0]
    pc[1:] = c[:-1]
    return np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])


def atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int) -> np.ndarray:
    tr = _true_range(h, l, c)
    return pd.Series(tr).rolling(int(n), min_periods=int(n)).mean().to_numpy()


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(span=int(n), adjust=False, min_periods=int(n)).mean().to_numpy()


def rsi(c: np.ndarray, n: int) -> np.ndarray:
    n = int(n)
    delta = np.diff(c, prepend=c[0])
    up = np.where(delta > 0, delta, 0.0)
    dn = np.where(delta < 0, -delta, 0.0)
    roll_up = pd.Series(up).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().to_numpy()
    roll_dn = pd.Series(dn).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().to_numpy()
    rs = np.where(roll_dn > 0, roll_up / roll_dn, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def macd(c: np.ndarray, fast: int, slow: int, signal: int
         ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    ef = ema(c, fast)
    es = ema(c, slow)
    line = ef - es
    sig = pd.Series(line).ewm(span=int(signal), adjust=False,
                              min_periods=int(signal)).mean().to_numpy()
    hist = line - sig
    return line, sig, hist


def bollinger(c: np.ndarray, n: int, k: float
              ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = int(n)
    s = pd.Series(c)
    mid = s.rolling(n, min_periods=n).mean().to_numpy()
    sd = s.rolling(n, min_periods=n).std(ddof=0).to_numpy()
    upper = mid + float(k) * sd
    lower = mid - float(k) * sd
    return mid, upper, lower


def keltner(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int, k: float
            ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = int(n)
    mid = ema(c, n)
    a = atr(h, l, c, n)
    upper = mid + float(k) * a
    lower = mid - float(k) * a
    return mid, upper, lower


def donchian(h: np.ndarray, l: np.ndarray, n: int
             ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = int(n)
    upper = pd.Series(h).rolling(n, min_periods=n).max().to_numpy()
    lower = pd.Series(l).rolling(n, min_periods=n).min().to_numpy()
    mid = (upper + lower) / 2.0
    return upper, lower, mid


def supertrend(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int, k: float
               ) -> Tuple[np.ndarray, np.ndarray]:
    n = int(n); k = float(k)
    a = atr(h, l, c, n)
    hl2 = (h + l) / 2.0
    upper = hl2 + k * a
    lower = hl2 - k * a
    sz = c.size
    line = np.full(sz, np.nan)
    direction = np.zeros(sz, dtype=np.int64)
    fu = np.copy(upper)
    fl = np.copy(lower)
    for i in range(1, sz):
        if not np.isfinite(a[i]):
            continue
        fu[i] = upper[i] if (upper[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lower[i] if (lower[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        prev_dir = direction[i - 1] if direction[i - 1] != 0 else 1
        if prev_dir == 1:
            direction[i] = -1 if c[i] < fl[i] else 1
        else:
            direction[i] = 1 if c[i] > fu[i] else -1
        line[i] = fl[i] if direction[i] == 1 else fu[i]
    return line, direction


def obv(c: np.ndarray, v: np.ndarray) -> np.ndarray:
    sign = np.sign(np.diff(c, prepend=c[0]))
    return np.cumsum(sign * v)


def pct_rank(arr: np.ndarray, lookback: int) -> float:
    """Percentile rank in [0,1] of last finite value within trailing lookback window."""
    lookback = int(lookback)
    a = arr[np.isfinite(arr)]
    if a.size < lookback:
        return float("nan")
    tail = a[-lookback:]
    last = tail[-1]
    return float(np.sum(tail <= last)) / float(tail.size)
