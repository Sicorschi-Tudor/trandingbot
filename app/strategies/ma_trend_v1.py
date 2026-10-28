"""ma-trend-v1: moving-average trend following on higher timeframes (Stage C2).

Trend UP: close above EMA(ema_slow_n=200) and EMA(ema_fast_n=50) rising over slope_n bars (mirror for DOWN).
Entry on the bar whose close crosses EMA50 in the trend direction (previous close on the other side of the
previous EMA50). SL = sl_atr × ATR(atr_n) (3×ATR20), TP = tp_atr × ATR (far placeholder).
Exit: default_exit() = combo trailing (trail_atr × ATR14 of the exit engine, from the first bar in profit) +
signal_flip, where bias() = side of the close vs EMA50 (a close back across EMA50 = opposite cross). HTF not used."""
from __future__ import annotations

from .. import indicators as ind
from . import _common as C
from . import _htf as X

NAME = "ma-trend-v1"
VERSION = "1.0"
DESCRIPTION_RO = ("Trend pe medii mobile pe H4/D1: preț peste/sub EMA200 și EMA50 în pantă în aceeași direcție; "
                  "intrare când închiderea traversează EMA50 în sensul trendului. SL 3×ATR, ieșire cu trailing "
                  "3×ATR sau la traversarea inversă a EMA50.")
PARAMS = {"ema_fast_n": 50, "ema_slow_n": 200, "slope_n": 5, "atr_n": 20, "sl_atr": 3.0, "tp_atr": 20.0,
          "trail_atr": 3.0}
RUN_DEFAULTS = dict(X.RUN_DEFAULTS)


def default_exit(cfg: dict) -> dict:
    return {"mode": "combo", "params": {"modes": ["trailing", "signal_flip"], "trail_start_r": 0.0,
                                        "trail_atr_mult": float(C.p(cfg, PARAMS, "trail_atr")), "trail_min_r": 0.0}}


def bias(symbol, sig_bars, htf_bars, cfg) -> int:
    """+1 close above EMA50, -1 below, 0 equal / not enough bars (closed bars only)."""
    n = int(C.p(cfg, PARAMS, "ema_fast_n"))
    bars = sig_bars[-X.TAIL:]
    closes = [b["c"] for b in bars]
    e = ind.last(ind.ema(closes, n))
    if e is None:
        return 0
    return 1 if closes[-1] > e else -1 if closes[-1] < e else 0


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    fn, sn, k, atr_n = int(g("ema_fast_n")), int(g("ema_slow_n")), max(1, int(g("slope_n"))), int(g("atr_n"))
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, max(sn, fn + k, atr_n) + 2)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    closes = [b["c"] for b in bars]
    ef, es = ind.ema(closes, fn), ind.ema(closes, sn)
    atr_s = X.atr_series(bars, atr_n)
    atr_v = atr_s[-1]
    f_now, f_prev, f_k, s_now = ef[-1], ind.last(ef, 1), ind.last(ef, k), es[-1]
    a["indicators"] = X.indicators(digits, atr_v, X.htf_trend(htf_bars), ema_fast=C.r_(f_now, digits + 1),
                                   ema_slow=C.r_(s_now, digits + 1),
                                   ema_fast_slope=C.r_(None if f_k is None or f_now is None else f_now - f_k, digits + 2))
    if None in (f_now, f_prev, f_k, s_now) or not atr_v:
        return C.hold(a, "Indicatori incompleți (date insuficiente).")
    c, pc = closes[-1], closes[-2]
    slope = f_now - f_k
    trend = 1 if c > s_now and slope > 0 else -1 if c < s_now and slope < 0 else 0
    a["regime"] = X.regime(atr_s, trend)
    if trend == 0:
        return C.hold(a, f"Fără trend: prețul față de EMA{sn} și panta EMA{fn} nu sunt de acord.")
    side = "BUY" if trend > 0 else "SELL"
    crossed = (pc <= f_prev and c > f_now) if trend > 0 else (pc >= f_prev and c < f_now)
    if not crossed:
        return C.hold(a, f"Trend {'UP' if trend > 0 else 'DOWN'}, dar închiderea nu a traversat EMA{fn} "
                         f"în sensul trendului pe această bară.")
    comps = [f"Preț {'peste' if trend > 0 else 'sub'} EMA{sn}",
             f"EMA{fn} în {'creștere' if trend > 0 else 'scădere'} pe {k} bare",
             f"Închiderea traversează EMA{fn} în sensul trendului"]
    return X.finish_atr(a, symbol, side, quote, cfg, risk, ctx, comps, atr_v, g("sl_atr"), g("tp_atr"),
                        note=f"TP {float(g('tp_atr')):g}×ATR doar ca plasă")
