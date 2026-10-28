"""disc-h08: prior-day extreme sweep and reclaim (H1 signals, D1 levels; discovery research).

PDH/PDL = high/low of the prior broker day (last closed D1 bar of the HTF series, htf_tf D1); ATRd = ATR14 on D1.
Short: today's high (broker day so far) went above PDH by >= x×ATRd (sweep, strictly above PDH) and the last closed H1
bar closes back below PDH; long is the mirror at PDL. Entries only when the next open is within 07:00-17:00 UTC.
SL = extreme since the sweep ± 0.1×ATRd; TP = prior-day midpoint (tp_mode 'mid') or 2R ('2R').
At most one trade per side per day (stateless: only the FIRST bar of the day meeting the condition inside the window
signals). Exit: fixed SL/TP, time exit at 20:00 UTC the same day (exit_deadline hook)."""
from __future__ import annotations

from datetime import datetime, timezone

from .. import _common as C
from .. import _htf as X
from . import _disc3 as D

NAME = "disc-h08"
VERSION = "1.0"
DESCRIPTION_RO = ("Cercetare: sweep peste maximul/minimul zilei precedente urmat de revenire (H1, niveluri D1). "
                  "După ce prețul depășește maximul de ieri și o bară H1 închide înapoi sub el, vinde spre mijlocul "
                  "zilei precedente (simetric la minim); doar 07-17 UTC, ieșire la 20:00 UTC.")
PARAMS = {"h08_x": 0.0, "h08_tp_mode": "mid", "h08_atr_n": 14, "h08_sl_buf": 0.1, "h08_start_h": 7, "h08_end_h": 17,
          "h08_exit_utc_h": 20}
RUN_DEFAULTS = {**D.RUN_DEFAULTS_H1, "htf_timeframe": "D1"}


def default_exit(cfg: dict) -> dict:
    return {"mode": "base", "params": {}}


def exit_deadline(opened_t: float, cfg: dict) -> float:
    d = datetime.fromtimestamp(opened_t, timezone.utc)
    h = int(C.p(cfg, PARAMS, "h08_exit_utc_h"))
    return max(opened_t, datetime(d.year, d.month, d.day, h, tzinfo=timezone.utc).timestamp())


def _in_window(t_next_open: float, cfg: dict) -> bool:
    s = t_next_open % D.DAY
    return int(C.p(cfg, PARAMS, "h08_start_h")) * D.H <= s <= int(C.p(cfg, PARAMS, "h08_end_h")) * D.H


def signal(bars: list[dict], htf: list[dict], cfg: dict) -> dict | None:
    """{side, sl, pdh, pdl, atr} when the last closed bar is the first qualifying bar of the day, else None."""
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    atr_n = int(g("h08_atr_n"))
    if len(htf) < atr_n + 1 or not bars:
        return None
    atr_d = D.atr_last(htf[-(atr_n + 60):], atr_n)
    if not atr_d:
        return None
    today = D.h1_day(bars[-1]["t"])
    prev_day = D.d1_day(htf[-1]["t"])
    if not (0 < (today - prev_day).days <= 3):
        return None  # the prior day's D1 bar is missing / not closed yet
    pdh, pdl = htf[-1]["h"], htf[-1]["l"]
    k = len(bars)
    while k > 0 and D.h1_day(bars[k - 1]["t"]) == today:
        k -= 1
    day = bars[k:]
    x = float(g("h08_x")) * atr_d
    buf = float(g("h08_sl_buf")) * atr_d
    sweep_hi = sweep_lo = None  # index (in day) of the first sweep bar
    first = {"SELL": None, "BUY": None}
    for j, b in enumerate(day):
        if sweep_hi is None and b["h"] > pdh and b["h"] - pdh >= x:
            sweep_hi = j
        if sweep_lo is None and b["l"] < pdl and pdl - b["l"] >= x:
            sweep_lo = j
        if not _in_window(b["t"] + D.H, cfg):
            continue
        if first["SELL"] is None and sweep_hi is not None and b["c"] < pdh:
            first["SELL"] = j
        if first["BUY"] is None and sweep_lo is not None and b["c"] > pdl:
            first["BUY"] = j
    last = len(day) - 1
    sides = [s for s, j in first.items() if j == last]
    if len(sides) != 1:
        return None
    side = sides[0]
    if side == "SELL":
        sl = max(b["h"] for b in day[sweep_hi:]) + buf
    else:
        sl = min(b["l"] for b in day[sweep_lo:]) - buf
    return {"side": side, "sl": sl, "pdh": pdh, "pdl": pdl, "atr": atr_d}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, 30)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    s = signal(bars, htf_bars or [], cfg)
    a["indicators"] = X.indicators(digits, s["atr"] if s else None, "FLAT")
    if s is None:
        return C.hold(a, "Fără sweep + revenire peste/sub extremul zilei precedente (prima bară validă a zilei).")
    side = s["side"]
    mode = str(g("h08_tp_mode")).lower()
    mid = (s["pdh"] + s["pdl"]) / 2
    a["indicators"].update(pdh=s["pdh"], pdl=s["pdl"], pd_mid=C.r_(mid, digits + 1))
    comps = [f"Sweep {'peste maximul' if side == 'SELL' else 'sub minimul'} zilei precedente "
             f"({s['pdh'] if side == 'SELL' else s['pdl']}) și închidere înapoi în interval",
             f"TP {'mijlocul zilei precedente' if mode == 'mid' else '2R'}"]
    if mode == "2r":
        return C.finish(a, symbol, side, s["sl"], None, quote, cfg, risk, ctx, comps, len(comps), tp_r=2.0,
                        note="ieșire la 20:00 UTC")
    return C.finish(a, symbol, side, s["sl"], mid, quote, cfg, risk, ctx, comps, len(comps), note="ieșire la 20:00 UTC")
