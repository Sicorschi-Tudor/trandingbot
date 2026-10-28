"""disc-h16: intraday home-currency seasonality (H1, discovery research; Breedon & Ranaldo 2013).

Fixed schedule, one trade per pair per weekday (Mon-Fri, by the UTC date of the entry):
- 'eur'   : SELL EURUSD, EURJPY at the 07:00 UTC open, exit at the 12:00 UTC close (5 h).
- 'us'    : BUY EURUSD, GBPUSD, AUDUSD; SELL USDJPY, USDCAD at the 13:00 UTC open, exit at the 20:00 UTC close (7 h).
- 'tokyo' : BUY USDJPY, AUDJPY at the 00:00 UTC open, exit at the 06:00 UTC close (6 h).
The decision is taken at the close of the bar before the window (next open = window start). The correlation limit of the
backtest (1 per currency + direction) keeps one leg per currency + direction. Optional filter (h16_filter 1): trade only
when the mean log return of the same window over the prior 20 weekdays has the expected sign (needs ~30 calendar days of
H1 bars: the research history hook extends the 400-bar window backwards; with fewer than 20 days it holds).
SL = 1.0×ATR14 D1 (htf D1); no TP (far placeholder), no BE. Exit: default_exit time stop = window length."""
from __future__ import annotations

import math

from .. import _common as C
from .. import _htf as X
from . import _disc3 as D

NAME = "disc-h16"
VERSION = "1.0"
DESCRIPTION_RO = ("Cercetare: sezonalitatea intraday a monedei locale (H1). Vinde moneda în timpul orelor ei de "
                  "tranzacționare (EUR 07-12 UTC, USD 13-20 UTC, JPY 00-06 UTC), cu ieșire pe timp și SL 1×ATR D1; "
                  "filtru opțional pe semnul mediei ultimelor 20 de zile.")
PARAMS = {"h16_variant": "eur", "h16_filter": 0, "h16_filter_days": 20, "h16_atr_n": 14, "h16_sl_atr": 1.0,
          "h16_tp_atr": 50.0}
VARIANTS = {
    "eur": (7, 12, {"EURUSD": "SELL", "EURJPY": "SELL"}),
    "us": (13, 20, {"EURUSD": "BUY", "GBPUSD": "BUY", "AUDUSD": "BUY", "USDJPY": "SELL", "USDCAD": "SELL"}),
    "tokyo": (0, 6, {"USDJPY": "BUY", "AUDJPY": "BUY"}),
}
RUN_DEFAULTS = {**D.RUN_DEFAULTS_H1, "htf_timeframe": "D1"}


def _variant(cfg: dict):
    v = str(C.p(cfg, PARAMS, "h16_variant")).lower()
    if v not in VARIANTS:
        raise ValueError(f"h16_variant necunoscut: {v} (unul din {', '.join(VARIANTS)})")
    return VARIANTS[v]


def default_exit(cfg: dict) -> dict:
    s, e, _ = _variant(cfg)
    return {"mode": "base", "params": {"max_hold_hours": e - s}}


def window_returns(bars: list[dict], start_h: int, end_h: int, before_day: int) -> list[float]:
    """Log return open(start_h bar) -> close(bar ending at end_h) per UTC weekday strictly before `before_day`
    (day index = t // 86400), oldest first; days missing either bar are skipped."""
    by_t = {b["t"]: b for b in bars}
    days = sorted({b["t"] // D.DAY for b in bars if b["t"] // D.DAY < before_day})
    out = []
    for d in days:
        if D.utc(d * D.DAY).weekday() >= 5:
            continue
        o = by_t.get(d * D.DAY + start_h * D.H)
        c = by_t.get(d * D.DAY + (end_h - 1) * D.H)
        if o and c and o["o"] > 0 and c["c"] > 0:
            out.append(math.log(c["c"] / o["o"]))
    return out


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    start_h, end_h, legs = _variant(cfg)
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, 30)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    a["indicators"] = X.indicators(digits, None, "FLAT")
    side = legs.get(symbol.upper())
    if side is None:
        return C.hold(a, "Simbolul nu face parte din varianta aleasă.")
    T = bars[-1]["t"] + D.H  # next open
    if T % D.DAY != start_h * D.H or D.utc(T).weekday() >= 5:
        return C.hold(a, f"În afara orei de intrare ({start_h:02d}:00 UTC, luni-vineri).")
    atr_d = D.atr_last(list(htf_bars or [])[-60:], int(g("h16_atr_n")))
    a["indicators"]["atr"] = C.r_(atr_d, digits + 1)
    if not atr_d:
        return C.hold(a, "ATR D1 indisponibil.")
    if int(g("h16_filter")):
        n = int(g("h16_filter_days"))
        hist = D.extend_back(symbol, sig_bars, 24 * (n + 14))
        rets = window_returns(hist, start_h, end_h, T // D.DAY)[-n:]
        if len(rets) < n:
            return C.hold(a, f"Filtru: doar {len(rets)}/{n} zile de istoric.")
        mean = math.fsum(rets) / n
        a["indicators"].update(window_mean_ret=C.r_(mean, 7))
        if (mean > 0) != (side == "BUY") or mean == 0:
            return C.hold(a, f"Filtru: media ferestrei pe {n} zile ({mean:+.5f}) nu are semnul așteptat.")
    comps = [f"Fereastra {start_h:02d}:00-{end_h:02d}:00 UTC: {side} {symbol} (moneda locală se depreciază)",
             "Ieșire pe timp la sfârșitul ferestrei"]
    return X.finish_atr(a, symbol, side, quote, cfg, risk, ctx, comps, atr_d, g("h16_sl_atr"), g("h16_tp_atr"),
                        note=f"ieșire la {end_h:02d}:00 UTC")
