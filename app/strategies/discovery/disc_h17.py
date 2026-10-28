"""disc-H17: first-touch rejection at round numbers (Osler 2000/2003). H1; research only.

Levels: multiples of 100 pips (lvl '00') or 50 pips (lvl '50'; includes the 00 levels); pip = 10 points
(0.0001, JPY pairs 0.01 -> 1.00 / 0.50 yen). ATRh = ATR(24) of the H1 bars, b = 0.1*ATRh.
Short: a level L above the previous bar's close; the last closed bar's high is within [L-b, L+b], the bar closes
at least 0.3*ATRh below L, and no bar of the previous 24 H1 bars (before the signal bar) reached the touch band
[L-b, L+b] (first touch; the same band as the signal touch, so a near-miss counts as a touch).
SELL at the next open, SL = L + 0.5*ATRh, TP = tp_r x initial risk. Long = mirror below the previous close.
Both at once -> no trade.
Exit: fixed SL/TP + time exit after 8 H1 bars (time_exit hook, bars counted; fallback max_hold 8 h)."""
from __future__ import annotations

import math

from .. import _common as C
from .. import _htf as X
from . import _disc0 as D

NAME = "disc-H17"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare: prima atingere a unui nivel rotund (xx00 / xx50) după 24 de ore fără atingere, cu "
                  "respingere de cel puțin 0,3×ATR — intrare inversă; SL 0,5×ATR dincolo de nivel, TP tp_r×risc, "
                  "ieșire după 8 bare H1.")
PARAMS = {"lvl": "00", "tp_r": 1.5, "atrh_n": 24, "band_atr": 0.1, "reject_atr": 0.3, "sl_atr": 0.5,
          "fresh_bars": 24, "hold_bars": 8}
RUN_DEFAULTS = {**X.RUN_DEFAULTS, "signal_timeframe": "H1", "htf_timeframe": "NONE", "max_hold_minutes": 480,
                "signal_expiry_minutes": 4 * 1440}


def time_exit(opened_t, bar_t, bar_sec, n_bars, cfg):
    return "close" if n_bars >= int(C.p(cfg, PARAMS, "hold_bars")) else None


def level_step(lvl, point: float, digits: int) -> float:
    pip = point * 10 if digits in (3, 5) else point
    return (100 if str(lvl) in ("00", "100") else 50) * pip


def touched(bars: list[dict], level: float, band: float = 0.0) -> bool:
    """Some bar reached the touch band [level - band, level + band] (the same band that defines the signal touch)."""
    return any(b["h"] >= level - band and b["l"] <= level + band for b in bars)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    fresh = int(g("fresh_bars"))
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, fresh + 30)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    point = float(quote.get("point") or 10 ** -digits)
    atr = D.atr_last(bars[-200:], int(g("atrh_n")))
    a["indicators"] = X.indicators(digits, atr, "FLAT")
    if not atr:
        return C.hold(a, "ATR indisponibil.")
    step = level_step(g("lvl"), point, digits)
    last, prev_c = bars[-1], bars[-2]["c"]
    hist = bars[-1 - fresh:-1]
    band, rej = float(g("band_atr")) * atr, float(g("reject_atr")) * atr
    hits = []
    # short: level above the previous close, high near it, close well below it
    L = round(math.floor((last["h"] + band) / step) * step, digits)
    if L > prev_c and last["h"] >= L - band and last["c"] <= L - rej and not touched(hist, L, band):
        hits.append(("SELL", L, L + float(g("sl_atr")) * atr))
    L = round(math.ceil((last["l"] - band) / step) * step, digits)
    if L < prev_c and last["l"] <= L + band and last["c"] >= L + rej and not touched(hist, L, band):
        hits.append(("BUY", L, L - float(g("sl_atr")) * atr))
    if len(hits) != 1:
        return C.hold(a, "Nicio respingere la prima atingere a unui nivel rotund." if not hits
                      else "Respingere la două niveluri simultan — ambiguu.")
    side, lvl, sl = hits[0]
    a["regime"] = "RANGE"
    a["indicators"].update(round_level=lvl)
    comps = [f"Prima atingere a nivelului rotund {lvl} după {fresh} bare",
             f"Respingere: închidere la {abs(last['c'] - lvl) / atr:.2f}×ATR de nivel"]
    return C.finish(a, symbol, side, sl, None, quote, cfg, risk, ctx, comps, len(comps), tp_r=float(g("tp_r")),
                    note=f"ieșire după {int(g('hold_bars'))} bare")
