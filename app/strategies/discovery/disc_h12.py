"""disc-h12: weekly cross-sectional currency-strength momentum (D1, discovery research).

At every Friday D1 close: for each of the pairs in the universe, z = log return over L weeks (5L D1 bars) divided by
ATR20/close; a currency's strength = mean of +z (base) / -z (quote) over the pairs that contain it (USD, EUR, GBP,
JPY, AUD, CAD, CHF, NZD). The trade is the existing pair with the largest strength spread s(long ccy) - s(short ccy):
best/worst when that pair exists, otherwise the next best combination that exists (BUY when the strong currency is
the base, SELL when it is the quote). Only that pair gets a candidate. SL = 3×ATR14 D1, TP far placeholder.
Exit (default_exit): time stop 108 h = the next Friday close (5 D1 bars); the backtest then re-enters at the same
close if the pair is unchanged (close + reopen, costs paid again). Swap charged (table).

Data: the other pairs come from the research universe hook (_disc3.set_universe; only bars closed at the decision
time are used). Without it (live) only the analysed symbol is known and the plugin holds."""
from __future__ import annotations

import math

from .. import _common as C
from .. import _htf as X
from . import _disc3 as D

NAME = "disc-h12"
VERSION = "1.0"
DESCRIPTION_RO = ("Cercetare: momentum transversal pe puterea monedelor (D1, rebalansare vinerea). Ordonează cele 8 "
                  "monede după randamentul normalizat pe L săptămâni și cumpără cea mai puternică contra celei mai "
                  "slabe (perechea existentă cu cea mai mare diferență); ține o săptămână, SL 3×ATR.")
PARAMS = {"h12_weeks": 4, "h12_norm_atr_n": 20, "h12_atr_n": 14, "h12_sl_atr": 3.0, "h12_tp_atr": 20.0}
CCYS = ("USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD")
RUN_DEFAULTS = {**X.RUN_DEFAULTS}


def default_exit(cfg: dict) -> dict:
    return {"mode": "base", "params": {"max_hold_hours": 108}}


def _closed(bars: list[dict], now_ts: float) -> list[dict]:
    lo, hi = 0, len(bars)
    while lo < hi:  # first bar not closed at now_ts
        mid = (lo + hi) // 2
        if bars[mid]["t"] + D.DAY <= now_ts:
            lo = mid + 1
        else:
            hi = mid
    return bars[:lo]


def strengths(universe: dict[str, list[dict]], now_ts: float, weeks: int, norm_n: int) -> dict[str, float]:
    """Currency -> mean normalised L-week return over the pairs containing it (closed bars at now_ts only; a pair whose
    last closed bar is more than 4 days old is ignored)."""
    L = 5 * int(weeks)
    acc: dict[str, list[float]] = {}
    for sym, all_bars in universe.items():
        bars = _closed(all_bars, now_ts)
        if len(bars) < max(L + 1, norm_n + 1) or now_ts - (bars[-1]["t"] + D.DAY) > 4 * D.DAY:
            continue
        c = bars[-1]["c"]
        atr = D.atr_last(bars[-(norm_n + 60):], norm_n)
        if not atr or c <= 0 or bars[-1 - L]["c"] <= 0:
            continue
        z = math.log(c / bars[-1 - L]["c"]) / (atr / c)
        b, q = sym[:3], sym[3:6]
        acc.setdefault(b, []).append(z)
        acc.setdefault(q, []).append(-z)
    return {k: math.fsum(v) / len(v) for k, v in acc.items() if k in CCYS}


def pick(strength: dict[str, float], pairs) -> tuple[str, str] | None:
    """(pair, side) with the largest strength spread among existing pairs (ties: first in sorted order)."""
    best = None
    for sym in sorted(pairs):
        b, q = sym[:3], sym[3:6]
        if b not in strength or q not in strength:
            continue
        diff = strength[b] - strength[q]
        cand = (abs(diff), sym, "BUY" if diff > 0 else "SELL")
        if diff != 0 and (best is None or cand[0] > best[0]):
            best = cand
    return None if best is None else (best[1], best[2])


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    weeks, norm_n, atr_n = int(g("h12_weeks")), int(g("h12_norm_atr_n")), int(g("h12_atr_n"))
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, max(5 * weeks, norm_n, atr_n) + 2)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    a["indicators"] = X.indicators(digits, None, "FLAT")
    if D.d1_day(bars[-1]["t"]).weekday() != 4:
        return C.hold(a, "Rebalansarea se face doar la închiderea de vineri.")
    atr_v = D.atr_last(bars[-(atr_n + 60):], atr_n)
    a["indicators"]["atr"] = C.r_(atr_v, digits + 1)
    if not atr_v:
        return C.hold(a, "ATR indisponibil.")
    now_ts = ctx.now_ts if ctx.now_ts is not None else bars[-1]["t"] + D.DAY
    uni = dict(D.universe())
    uni[symbol.upper()] = uni.get(symbol.upper()) or list(bars)
    st = strengths(uni, now_ts, weeks, norm_n)
    choice = pick(st, uni.keys())
    a["indicators"].update(strength={k: C.r_(v, 3) for k, v in sorted(st.items(), key=lambda kv: -kv[1])})
    if choice is None:
        return C.hold(a, "Puterea monedelor nu poate fi calculată (univers insuficient).")
    pair, side = choice
    if pair != symbol.upper():
        return C.hold(a, f"Perechea aleasă săptămâna aceasta este {pair} {side}.")
    strong, weak = (pair[:3], pair[3:6]) if side == "BUY" else (pair[3:6], pair[:3])
    comps = [f"Cea mai mare diferență de putere pe {weeks} săptămâni: {strong} ({st[strong]:+.2f}) contra "
             f"{weak} ({st[weak]:+.2f})", "Rebalansare săptămânală (vinerea)"]
    return X.finish_atr(a, symbol, side, quote, cfg, risk, ctx, comps, atr_v, g("h12_sl_atr"), g("h12_tp_atr"),
                        note="ieșire la următoarea închidere de vineri")
