"""disc-h18: inside-bar false break ("fakey") on H4 (Stage D discovery, hypothesis H18).

Rationale: a breakout of an inside bar that immediately fails traps breakout traders; their stop-outs fuel a move
the other way (the mirror of a successful inside-bar breakout).

Rules (closed bars only): mother bar M, inside bar I (I.h <= M.h and I.l >= M.l, not both equal). The breaking bar B is
the last closed bar and comes 1 or 2 bars after I (when 2, the bar in between stayed inside I's range, so B is the
first break). B breaks exactly one side of I intrabar and closes back inside the range named by
require_close_inside ('I': I.l <= close <= I.h; 'M': M.l <= close <= M.h). Up-break failed -> SELL at the next open; down-break failed -> BUY.
SL = B's extreme (high for SELL, low for BUY) + sl_buf_atr(0.1)×ATR14. TP = opposite extreme of M (tp_mode
'mother_extreme': M.l for SELL, M.h for BUY) or tp_mode '2R'.
Exit: default_exit() = fixed SL/TP + time stop hold_hours = 40 h (10 H4 bars of wall-clock time)."""
from __future__ import annotations

from .. import _common as C
from .. import _htf as X

NAME = "disc-h18"
VERSION = "1.0"
DESCRIPTION_RO = ("Spargere falsă a unei bare interioare (fakey, H4): bara care sparge range-ul barei interioare și se "
                  "închide înapoi în interior dă semnal în sens opus; SL dincolo de extrema barei, TP la extrema opusă a "
                  "barei mamă sau 2R, ieșire după 10 bare.")
PARAMS = {"tp_mode": "mother_extreme", "require_close_inside": "M", "max_after": 2, "atr_n": 14, "sl_buf_atr": 0.1,
          "tp_r": 2.0, "hold_hours": 40}
RUN_DEFAULTS = {**X.RUN_DEFAULTS, "signal_timeframe": "H4", "signal_expiry_minutes": 4 * 1440}


def default_exit(cfg: dict) -> dict:
    return {"mode": "base", "params": {"max_hold_hours": float(C.p(cfg, PARAMS, "hold_hours"))}}


def _inside(i: dict, m: dict) -> bool:
    return i["h"] <= m["h"] and i["l"] >= m["l"] and (i["h"] < m["h"] or i["l"] > m["l"])


def pattern(bars: list[dict], cfg: dict):
    """(side, M, I, B) of a failed inside-bar break on the last closed bar B, else None."""
    mode = str(C.p(cfg, PARAMS, "require_close_inside")).upper()
    n = len(bars)
    B = bars[-1] if bars else None
    for gap in range(1, int(C.p(cfg, PARAMS, "max_after")) + 1):
        ii = n - 1 - gap
        if ii < 1:
            break
        I, M = bars[ii], bars[ii - 1]
        if not _inside(I, M):
            continue
        between = bars[ii + 1:n - 1]
        if any(x["h"] > I["h"] or x["l"] < I["l"] for x in between):
            continue  # an earlier bar already broke I: B is not the (first) break
        up, dn = B["h"] > I["h"], B["l"] < I["l"]
        if up == dn:
            continue  # no break, or both sides broken (outside bar): ambiguous
        lo, hi = (I["l"], I["h"]) if mode == "I" else (M["l"], M["h"])
        if not lo <= B["c"] <= hi:
            continue
        return ("SELL" if up else "BUY"), M, I, B
    return None


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, int(g("atr_n")) + 5)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    atr_s = X.atr_series(bars, int(g("atr_n")))
    atr_v = atr_s[-1]
    a["indicators"] = X.indicators(digits, atr_v, "FLAT")
    a["regime"] = X.regime(atr_s, 0)
    if not atr_v:
        return C.hold(a, "ATR indisponibil.")
    pat = pattern(bars, cfg)
    if pat is None:
        return C.hold(a, "Fără spargere falsă a unei bare interioare pe ultima bară închisă.")
    side, M, I, B = pat
    sign = 1 if side == "BUY" else -1
    buf = float(g("sl_buf_atr")) * atr_v
    sl = B["l"] - buf if sign > 0 else B["h"] + buf
    comps = [f"Bară interioară în bara mamă ({M['l']}–{M['h']})",
             f"Spargere {'în sus' if sign < 0 else 'în jos'} a barei interioare eșuată (închidere {B['c']} înapoi în "
             f"{str(g('require_close_inside')).upper()})"]
    if str(g("tp_mode")) == "2R":
        return C.finish(a, symbol, side, sl, None, quote, cfg, risk, ctx, comps, len(comps), tp_r=float(g("tp_r")),
                        note=f"TP {float(g('tp_r')):g}R")
    tp = M["h"] if sign > 0 else M["l"]
    return C.finish(a, symbol, side, sl, tp, quote, cfg, risk, ctx, comps, len(comps), note="TP la extrema barei mamă")
