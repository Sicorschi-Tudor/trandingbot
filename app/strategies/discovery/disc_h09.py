"""disc-H09: prior-week extreme sweep and reclaim (H4 signals, weekly levels from D1 bars; research only).

PWH/PWL = high/low of the previous broker week (Mon-Fri, broker day = Athens date of the D1 bar open), from the
closed D1 bars (htf_timeframe D1). ATRd = ATR14 of the closed D1 bars.
Short: in the current week an H4 bar j made high >= PWH + x*ATRd (the sweep) and a later closed H4 bar i, 1..3 bars
after j (same week), closes below PWH -> SELL at the next open. Only the first such bar of the week per side.
SL = highest high from j to i + 0.2*ATRd. TP = prior-week midpoint (tp_mode 'mid') or 2R (tp_mode '2R').
Long = mirror at PWL. No signal on the last bar of the week (it would be closed by the time exit at once).
Exit: fixed SL/TP + time exit at the Friday close of the entry week (time_exit hook; fallback max_hold 5 days)."""
from __future__ import annotations

from .. import _common as C
from .. import _htf as X
from . import _disc0 as D

NAME = "disc-H09"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare: maximul/minimul săptămânii trecute este depășit (sweep) pe H4 și în maxim 3 bare "
                  "prețul închide înapoi sub/peste nivel — intrare inversă; SL peste extrema sweep-ului, TP la "
                  "mijlocul săptămânii trecute sau 2R; ieșire vineri la închidere.")
PARAMS = {"sweep_atr": 0.0, "tp_mode": "mid", "reclaim_bars": 3, "sl_buf_atr": 0.2, "atrd_n": 14}
RUN_DEFAULTS = {**X.RUN_DEFAULTS, "signal_timeframe": "H4", "htf_timeframe": "D1", "max_hold_minutes": 5 * 1440,
                "signal_expiry_minutes": 4 * 1440}


def time_exit(opened_t, bar_t, bar_sec, n_bars, cfg):
    return D.deadline_rule(D.week_end(D.broker_date(opened_t)), bar_t, bar_sec)


def prior_week(htf_bars: list[dict], wk: tuple[int, int]) -> list[dict]:
    """D1 bars of the most recent broker week before `wk`."""
    prev = None
    out: list[dict] = []
    for b in reversed(htf_bars):
        k = D.week_key(D.broker_date(b["t"]))
        if k >= wk:
            continue
        if prev is None:
            prev = k
        if k != prev:
            break
        out.append(b)
    return out[::-1]


def find_trigger(week: list[dict], level: float, sweep: float, sign: int, n_back: int):
    """First bar index i of the week that reclaims `level` within n_back bars after a sweep; (i, j_first) or None.
    sign=+1: sweep above (high >= level + sweep), reclaim = close < level (SELL). sign=-1: mirror (BUY)."""
    for i in range(1, len(week)):
        c = week[i]["c"]
        if not (c < level if sign > 0 else c > level):
            continue
        js = [j for j in range(max(0, i - n_back), i)
              if (week[j]["h"] >= level + sweep if sign > 0 else week[j]["l"] <= level - sweep)]
        if js:
            return i, js[0]
    return None


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, 40)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    htf = list(htf_bars or [])[-60:]
    atr_d = D.atr_last(htf, int(g("atrd_n")))
    a["indicators"] = X.indicators(digits, atr_d, "FLAT")
    if not atr_d:
        return C.hold(a, "ATR zilnic indisponibil (sunt necesare bare D1 închise).")
    last = bars[-1]
    sec = C.tf_seconds(cfg)
    d = D.broker_date(last["t"])
    wk = D.week_key(d)
    if last["t"] + sec >= D.week_end(d):
        return C.hold(a, "Ultima bară a săptămânii: fără intrări (ieșire vineri la închidere).")
    pw = prior_week(htf, wk)
    if len(pw) < 3:
        return C.hold(a, "Săptămâna trecută incompletă în barele D1.")
    pwh, pwl = max(b["h"] for b in pw), min(b["l"] for b in pw)
    week = [b for b in bars[-40:] if D.week_key(D.broker_date(b["t"])) == wk]
    sweep, n_back = float(g("sweep_atr")) * atr_d, int(g("reclaim_bars"))
    a["indicators"].update(pwh=pwh, pwl=pwl)
    hits = []
    for sign, lvl in ((1, pwh), (-1, pwl)):
        tr = find_trigger(week, lvl, sweep, sign, n_back)
        if tr is not None and tr[0] == len(week) - 1:
            hits.append((sign, lvl, tr[1]))
    if len(hits) != 1:
        return C.hold(a, "Niciun sweep + revenire nou la extremele săptămânii trecute." if not hits
                      else "Sweep simultan la ambele extreme — ambiguu.")
    sign, lvl, j = hits[0]
    seg = week[j:]
    buf = float(g("sl_buf_atr")) * atr_d
    if sign > 0:
        side, sl = "SELL", max(b["h"] for b in seg) + buf
    else:
        side, sl = "BUY", min(b["l"] for b in seg) - buf
    a["regime"] = "RANGE"
    comps = [f"Sweep {'peste maximul' if sign > 0 else 'sub minimul'} săptămânii trecute ({lvl})",
             f"Închidere înapoi {'sub' if sign > 0 else 'peste'} nivel în {len(week) - 1 - j} bare"]
    if str(g("tp_mode")).upper() == "2R":
        return C.finish(a, symbol, side, sl, None, quote, cfg, risk, ctx, comps, len(comps), tp_r=2.0,
                        note="TP 2R; ieșire vineri")
    return C.finish(a, symbol, side, sl, (pwh + pwl) / 2, quote, cfg, risk, ctx, comps, len(comps),
                    note="TP la mijlocul săptămânii trecute; ieșire vineri")
