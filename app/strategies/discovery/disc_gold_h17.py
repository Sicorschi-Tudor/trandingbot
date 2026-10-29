"""disc-gold-h17: disc-H17 (first-touch rejection at round numbers) with the level step given in PRICE units.

Why: disc-H17 derives the round-number step from the symbol's point/digits (100 pips -> 1.00 USD on XAUUSD, digits 2),
which is noise on gold; the round numbers traders watch on gold are 10 / 50 / 100 USD. `step_price` (default 10.0)
is the level spacing in price units; 0 falls back to disc-H17's pip-based step. Everything else (touch band, first
touch, rejection, SL/TP, 8-bar time exit fallback) is disc-H17 unchanged. Research only (GOLD/sessions study)."""
from __future__ import annotations

import math

from .. import _common as C
from .. import _htf as X
from . import _disc0 as D
from . import disc_h17 as H

NAME = "disc-gold-h17"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare (aur): disc-H17 cu pasul nivelurilor rotunde în unități de preț (implicit 10 USD): "
                  "prima atingere a unui nivel rotund după 24 de bare, cu respingere ≥ 0,3×ATR — intrare inversă.")
PARAMS = {**H.PARAMS, "step_price": 10.0}
RUN_DEFAULTS = dict(H.RUN_DEFAULTS)
time_exit = H.time_exit


def level_step(cfg: dict, point: float, digits: int) -> float:
    sp = float(C.p(cfg, PARAMS, "step_price") or 0)
    return sp if sp > 0 else H.level_step(C.p(cfg, PARAMS, "lvl"), point, digits)


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
    step = level_step(cfg, point, digits)
    last, prev_c = bars[-1], bars[-2]["c"]
    hist = bars[-1 - fresh:-1]
    band, rej = float(g("band_atr")) * atr, float(g("reject_atr")) * atr
    hits = []
    L = round(math.floor((last["h"] + band) / step) * step, digits)
    if L > prev_c and last["h"] >= L - band and last["c"] <= L - rej and not H.touched(hist, L, band):
        hits.append(("SELL", L, L + float(g("sl_atr")) * atr))
    L = round(math.ceil((last["l"] - band) / step) * step, digits)
    if L < prev_c and last["l"] <= L + band and last["c"] >= L + rej and not H.touched(hist, L, band):
        hits.append(("BUY", L, L - float(g("sl_atr")) * atr))
    if len(hits) != 1:
        return C.hold(a, "Nicio respingere la prima atingere a unui nivel rotund." if not hits
                      else "Respingere la două niveluri simultan — ambiguu.")
    side, lvl, sl = hits[0]
    a["regime"] = "RANGE"
    a["indicators"].update(round_level=lvl, level_step=step)
    comps = [f"Prima atingere a nivelului rotund {lvl} (pas {step:g}) după {fresh} bare",
             f"Respingere: închidere la {abs(last['c'] - lvl) / atr:.2f}×ATR de nivel"]
    return C.finish(a, symbol, side, sl, None, quote, cfg, risk, ctx, comps, len(comps), tp_r=float(g("tp_r")),
                    note=f"ieșire după {int(g('hold_bars'))} bare")
