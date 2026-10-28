"""Pure-python indicators. Each returns a list aligned with input; None where undefined."""
from __future__ import annotations

import math
from typing import Optional

Series = list[Optional[float]]


def ema(values: list[float], period: int) -> Series:
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def sma(values: list[float], period: int) -> Series:
    out: Series = [None] * len(values)
    if period <= 0:
        return out
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        if i >= period - 1:
            out[i] = s / period
    return out


def rsi(closes: list[float], period: int = 14) -> Series:
    out: Series = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / period, losses / period

    def _val(g: float, l: float) -> float:
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[period] = _val(avg_g, avg_l)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
        out[i] = _val(avg_g, avg_l)
    return out


def true_ranges(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    tr = []
    for i in range(len(closes)):
        if i == 0:
            tr.append(highs[i] - lows[i])
        else:
            pc = closes[i - 1]
            tr.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    return tr


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> Series:
    n = len(closes)
    out: Series = [None] * n
    if n < period:
        return out
    tr = true_ranges(highs, lows, closes)
    prev = sum(tr[:period]) / period
    out[period - 1] = prev
    for i in range(period, n):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Series, Series, Series]:
    ef, es = ema(closes, fast), ema(closes, slow)
    line: Series = [None if (a is None or b is None) else a - b for a, b in zip(ef, es)]
    start = next((i for i, v in enumerate(line) if v is not None), None)
    sig: Series = [None] * len(closes)
    hist: Series = [None] * len(closes)
    if start is None:
        return line, sig, hist
    sub = ema([v for v in line[start:]], signal)  # type: ignore[misc]
    for j, v in enumerate(sub):
        sig[start + j] = v
        if v is not None:
            hist[start + j] = line[start + j] - v  # type: ignore[operator]
    return line, sig, hist


def bollinger(closes: list[float], period: int = 20, k: float = 2.0) -> tuple[Series, Series, Series]:
    mid = sma(closes, period)
    up: Series = [None] * len(closes)
    lo: Series = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        m = mid[i]
        sd = math.sqrt(sum((x - m) ** 2 for x in window) / period)  # type: ignore[operator]
        up[i] = m + k * sd  # type: ignore[operator]
        lo[i] = m - k * sd  # type: ignore[operator]
    return mid, up, lo


def median(values: list[float]) -> float | None:
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def last(series: Series, offset: int = 0) -> float | None:
    idx = len(series) - 1 - offset
    return series[idx] if 0 <= idx < len(series) else None
