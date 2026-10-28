"""disc-H11: consecutive-close exhaustion fade (H4, research).

A run = at least N consecutive closes in the same direction at the last closed bar; cumulative move over the last
N bars |close(t-N) - close(t)| >= k × ATR14. Down run -> BUY, up run -> SELL at the next open. SL = 1.5×ATR14,
TP = 1.0×ATR14. Exit: SL/TP, the close of the first bar that closes against the run (signal_flip on
bias = -sign(close - previous close): a BUY closes at the first up close, a SELL at the first down close), or the
time stop after max_bars H4 bars (max_hold_hours = 4 × max_bars; after a weekend gap it counts wall-clock hours)."""
from __future__ import annotations

from .. import _common as C
from .. import _htf as X

NAME = "disc-H11"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare: după N închideri consecutive în aceeași direcție cu mișcare cumulată ≥ k×ATR, "
                  "pariu pe revenire; SL 1.5×ATR, TP 1×ATR, ieșire la prima închidere opusă sau după 6 bare H4.")
PARAMS = {"h11_n": 4, "h11_k": 1.5, "h11_sl_atr": 1.5, "h11_tp_atr": 1.0, "h11_max_bars": 6}
RUN_DEFAULTS = {**X.RUN_DEFAULTS, "signal_timeframe": "H4"}


def default_exit(cfg: dict) -> dict:
    hours = 4.0 * float(C.p(cfg, PARAMS, "h11_max_bars"))
    return {"mode": "signal_flip", "params": {"flip_on_flat": 0, "max_hold_hours": hours}}


def _g(cfg, k):
    return C.p(cfg, PARAMS, k)


def run_signal(closes: list[float], atr_v: float, n: int, k: float) -> int:
    """+1 (BUY after a down run) / -1 (SELL after an up run) / 0, at the last close."""
    if len(closes) < n + 1 or not atr_v:
        return 0
    tail = closes[-n - 1:]
    diffs = [b - a for a, b in zip(tail, tail[1:])]
    if all(x < 0 for x in diffs):
        d = -1
    elif all(x > 0 for x in diffs):
        d = 1
    else:
        return 0
    if abs(tail[-1] - tail[0]) < k * atr_v:
        return 0
    return -d


def bias(symbol, sig_bars, htf_bars, cfg) -> int:
    if len(sig_bars) < 2:
        return 0
    d = sig_bars[-1]["c"] - sig_bars[-2]["c"]
    return 0 if d == 0 else (-1 if d > 0 else 1)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    n = int(_g(cfg, "h11_n"))
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, max(n, 14) + 3)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    atr_v = X.atr_series(bars, 14)[-1]
    a["indicators"] = X.indicators(digits, atr_v, "FLAT")
    if not atr_v:
        return C.hold(a, "ATR indisponibil.")
    closes = [b["c"] for b in bars]
    s = run_signal(closes, atr_v, n, float(_g(cfg, "h11_k")))
    if not s:
        return C.hold(a, f"Fără serie de {n} închideri consecutive cu mișcare ≥ {float(_g(cfg, 'h11_k')):g}×ATR.")
    side = "BUY" if s > 0 else "SELL"
    move = abs(closes[-1] - closes[-1 - n]) / atr_v
    comps = [f"{n} închideri consecutive {'în scădere' if s > 0 else 'în creștere'}", f"Mișcare {move:.2f}×ATR14"]
    return X.finish_atr(a, symbol, side, quote, cfg, risk, ctx, comps, atr_v, _g(cfg, "h11_sl_atr"),
                        _g(cfg, "h11_tp_atr"), note="ieșire la prima închidere opusă seriei")
