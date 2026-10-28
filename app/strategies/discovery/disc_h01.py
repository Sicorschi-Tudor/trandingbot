"""disc-H01: London-open fade of an outsized Asian move (H1 signals, D1 ATR context; research only).

All times UTC (feed timestamps are UTC). Asian window = H1 bars 00:00..06:00 (7 bars, all required).
A = close(06:00 bar) - open(00:00 bar); ATRd = ATR14 of the closed D1 bars (htf series, htf_timeframe D1).
Signal at the close of the 07:00 H1 bar when |A| >= k*ATRd and the 07:00 bar did not extend the Asian extreme in the
direction of A by more than 0.25*ATRd: trade AGAINST A at the next open (Asia up -> SELL).
SL = Asian extreme in the direction of A + 0.15*ATRd; TP = Asian range midpoint (tp_mode 'mid') or the 00:00 open
('open'). One decision per symbol per day (only the 07:00 bar can signal).
Exit: fixed SL/TP + hard time exit at the close of the 15:00 UTC bar (time_exit hook; max_hold 8 H1 bars = the
fallback when the hook is not armed)."""
from __future__ import annotations

from .. import _common as C
from .. import _htf as X
from . import _disc0 as D

NAME = "disc-H01"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare: la deschiderea Londrei (07:00 UTC) se joacă invers o mișcare asiatică mare "
                  "(>= k×ATR zilnic); SL peste extrema asiatică, TP la mijlocul intervalului sau la deschiderea 00:00; "
                  "ieșire forțată la 16:00 UTC.")
PARAMS = {"k_atr": 0.5, "tp_mode": "mid", "ext_atr": 0.25, "sl_buf_atr": 0.15, "atrd_n": 14, "signal_hour": 7,
          "exit_hour": 16}
RUN_DEFAULTS = {**X.RUN_DEFAULTS, "signal_timeframe": "H1", "htf_timeframe": "D1", "max_hold_minutes": 480,
                "signal_expiry_minutes": 120}


def time_exit(opened_t, bar_t, bar_sec, n_bars, cfg):
    d = D.utc(opened_t)
    deadline = int(d.replace(hour=int(C.p(cfg, PARAMS, "exit_hour")), minute=0, second=0).timestamp())
    if deadline <= opened_t:
        deadline += D.DAY
    return D.deadline_rule(deadline, bar_t, bar_sec)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, 24)
    if bars is None:
        return a
    last = bars[-1]
    sh = int(g("signal_hour"))
    if D.utc(last["t"]).hour != sh:
        return C.hold(a, f"Semnal doar la închiderea barei H1 de {sh:02d}:00 UTC.")
    digits = int(quote.get("digits") or 5)
    atr_d = D.atr_last(list(htf_bars or [])[-60:], int(g("atrd_n")))
    a["indicators"] = X.indicators(digits, atr_d, "FLAT")
    if not atr_d:
        return C.hold(a, "ATR zilnic indisponibil (sunt necesare bare D1 închise).")
    day0 = last["t"] - sh * D.H
    asia = [b for b in bars[-(sh + 6):-1] if day0 <= b["t"] < day0 + sh * D.H]
    if len(asia) != sh or asia[0]["t"] != day0 or asia[-1]["t"] != day0 + (sh - 1) * D.H:
        return C.hold(a, "Sesiunea asiatică incompletă (lipsesc bare H1 00:00–06:00).")
    A = asia[-1]["c"] - asia[0]["o"]
    hi, lo = max(b["h"] for b in asia), min(b["l"] for b in asia)
    a["indicators"].update(asia_move=C.r_(A, digits + 1), asia_hi=hi, asia_lo=lo)
    k = float(g("k_atr"))
    if abs(A) < k * atr_d:
        return C.hold(a, f"Mișcarea asiatică {abs(A):.5f} < {k:g}×ATR zilnic.")
    ext, buf = float(g("ext_atr")) * atr_d, float(g("sl_buf_atr")) * atr_d
    tp = (hi + lo) / 2 if str(g("tp_mode")) == "mid" else asia[0]["o"]
    if A > 0:
        if last["h"] > hi + ext:
            return C.hold(a, "Bara de 07:00 a extins maximul asiatic peste 0,25×ATR — continuare, nu inversare.")
        side, sl = "SELL", hi + buf
        if last["c"] >= sl:
            return C.hold(a, "Prețul este deja peste SL-ul structural.")
    else:
        if last["l"] < lo - ext:
            return C.hold(a, "Bara de 07:00 a extins minimul asiatic peste 0,25×ATR — continuare, nu inversare.")
        side, sl = "BUY", lo - buf
        if last["c"] <= sl:
            return C.hold(a, "Prețul este deja sub SL-ul structural.")
    a["regime"] = "RANGE"
    comps = [f"Mișcare asiatică {'+' if A > 0 else '-'}{abs(A) / atr_d:.2f}×ATR zilnic",
             f"Deschiderea Londrei: se joacă revenirea spre {'mijlocul intervalului' if g('tp_mode') == 'mid' else 'deschiderea 00:00'}"]
    return C.finish(a, symbol, side, sl, tp, quote, cfg, risk, ctx, comps, len(comps), note="ieșire forțată 16:00 UTC")
