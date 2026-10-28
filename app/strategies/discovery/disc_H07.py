"""disc-H07: range-expansion day continuation (D1, research).

Signal bar = last closed D1 bar: range >= m × ATR20 computed on the bars BEFORE it, close location
(c-l)/(h-l) >= 0.8 -> BUY, <= 0.2 -> SELL. Entry at the next D1 open, SL = midpoint of the signal bar, TP far
placeholder (20×ATR). Exit: close of the hold_days-th D1 bar of the trade (signal_flip with flip_on_flat=1: bias()
replays the signals of the last bars with one virtual position, so it knows when the planned hold ends) or SL.
Approximation: if the trade stopped out and a new signal fired while the replay still saw the old trade, the new
trade inherits the old schedule (rare: needs two expansion days within hold_days)."""
from __future__ import annotations

from .. import _common as C
from .. import _htf as X

NAME = "disc-H07"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare: după o zi cu interval foarte mare (≥ m×ATR20) închisă lângă extremă, continuare 1–3 zile; "
                  "SL la mijlocul barei semnal.")
PARAMS = {"h07_m": 1.8, "h07_hold_days": 1, "h07_atr_n": 20, "h07_clv": 0.8, "h07_tp_atr": 20.0}
RUN_DEFAULTS = dict(X.RUN_DEFAULTS)


def default_exit(cfg: dict) -> dict:
    return {"mode": "signal_flip", "params": {"flip_on_flat": 1}}


def _g(cfg, k):
    return C.p(cfg, PARAMS, k)


def signal_at(bars: list[dict], atr_s: list, i: int, m: float, clv_min: float) -> int:
    """+1/-1/0 for bar i (needs ATR at i-1, i.e. computed before the bar)."""
    if i < 1 or atr_s[i - 1] is None or atr_s[i - 1] <= 0:
        return 0
    b = bars[i]
    rng = b["h"] - b["l"]
    if rng <= 0 or rng < m * atr_s[i - 1]:
        return 0
    clv = (b["c"] - b["l"]) / rng
    if clv >= clv_min:
        return 1
    if clv <= 1 - clv_min:
        return -1
    return 0


def bias(symbol, sig_bars, htf_bars, cfg) -> int:
    hold = max(1, int(_g(cfg, "h07_hold_days")))
    bars = sig_bars[-X.TAIL:]
    j = len(bars) - 1
    if j < 2:
        return 0
    atr_s = X.atr_series(bars, int(_g(cfg, "h07_atr_n")))
    m, clv = float(_g(cfg, "h07_m")), float(_g(cfg, "h07_clv"))
    active = None  # (signal index, side)
    for k in range(max(1, j - 4 * hold - 2), j):
        if active is not None and k >= active[0] + hold:
            active = None
        if active is None:
            s = signal_at(bars, atr_s, k, m, clv)
            if s:
                active = (k, s)
    if active is not None and j < active[0] + hold:
        return active[1]
    return 0


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    atr_n = int(_g(cfg, "h07_atr_n"))
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, atr_n + 3)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    atr_s = X.atr_series(bars, atr_n)
    i = len(bars) - 1
    atr_prev = atr_s[i - 1]
    a["indicators"] = X.indicators(digits, atr_prev, "FLAT")
    if not atr_prev:
        return C.hold(a, "ATR indisponibil.")
    s = signal_at(bars, atr_s, i, float(_g(cfg, "h07_m")), float(_g(cfg, "h07_clv")))
    b = bars[i]
    rng = b["h"] - b["l"]
    a["indicators"].update(range_atr=C.r_(rng / atr_prev, 3), clv=C.r_((b["c"] - b["l"]) / rng, 3) if rng > 0 else None)
    if not s:
        return C.hold(a, "Fără zi de expansiune a intervalului închisă lângă extremă.")
    side = "BUY" if s > 0 else "SELL"
    mid = (b["h"] + b["l"]) / 2
    entry = float(quote["ask"]) if s > 0 else float(quote["bid"])
    comps = [f"Interval {rng / atr_prev:.2f}×ATR{atr_n}", f"Închidere la {(b['c'] - b['l']) / rng:.2f} din interval",
             f"Ținere {int(_g(cfg, 'h07_hold_days'))} bare D1"]
    return C.finish(a, symbol, side, mid, entry + s * float(_g(cfg, "h07_tp_atr")) * atr_prev, quote, cfg, risk,
                    ctx, comps, len(comps), note="SL la mijlocul barei semnal")
