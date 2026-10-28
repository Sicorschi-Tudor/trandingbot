"""disc-h04: Monday gap fill (H1, discovery research).

At the close of the first H1 bar of the week (previous bar >= 36 h earlier): gap = open(that bar) - close(last
Friday bar); ATRh = ATR24 on the bars up to Friday's last bar (before the gap). If |gap| >= g×ATRh and the bar has
not already closed at or beyond Friday's close, fade the gap toward Friday's close: TP = Friday close,
SL = entry ± max(sl_mult×|gap|, 1.0×ATRh) in the gap direction. Fill at the next bar open (backtest).
Exit: fixed SL/TP, time exit at the close of the Monday 12:00 UTC bar (exit_deadline hook -> 13:00 UTC Monday)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import _common as C
from .. import _htf as X
from . import _disc3 as D

NAME = "disc-h04"
VERSION = "1.0"
DESCRIPTION_RO = ("Cercetare: închiderea gap-ului de luni (H1). Dacă prima bară a săptămânii deschide cu un gap de cel "
                  "puțin g×ATR24 față de închiderea de vineri și nu l-a închis deja, intră spre închiderea de vineri; "
                  "TP = închiderea de vineri, ieșire la ora 13:00 UTC luni.")
PARAMS = {"h04_g": 0.5, "h04_sl_mult": 1.0, "h04_atr_n": 24, "h04_min_sl_atr": 1.0, "h04_gap_hours": 36,
          "h04_exit_utc_h": 13}
RUN_DEFAULTS = dict(D.RUN_DEFAULTS_H1)


def default_exit(cfg: dict) -> dict:
    return {"mode": "base", "params": {}}


def exit_deadline(opened_t: float, cfg: dict) -> float:
    """Close of the Monday 12:00 UTC bar (13:00 UTC Monday) of the week the trade was opened in."""
    d = datetime.fromtimestamp(opened_t, timezone.utc)
    days = (0 - d.weekday()) % 7  # Sunday -> next day; Monday -> same day
    day = (d + timedelta(days=days)).date()
    h = int(C.p(cfg, PARAMS, "h04_exit_utc_h"))
    return datetime(day.year, day.month, day.day, h, tzinfo=timezone.utc).timestamp()


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    atr_n = int(g("h04_atr_n"))
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, atr_n + 3)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    cur, prev = bars[-1], bars[-2]
    a["indicators"] = X.indicators(digits, None, "FLAT")
    if cur["t"] - prev["t"] < float(g("h04_gap_hours")) * D.H:
        return C.hold(a, "Nu este prima bară a săptămânii.")
    if D.utc(cur["t"]).weekday() not in (6, 0):
        return C.hold(a, "Pauză de date care nu este un weekend.")
    atr_h = D.atr_last(bars[-(atr_n + 60):-1], atr_n)
    a["indicators"]["atr"] = C.r_(atr_h, digits + 1)
    if not atr_h:
        return C.hold(a, "ATR indisponibil.")
    fri = prev["c"]
    gap = cur["o"] - fri
    a["indicators"].update(gap=C.r_(gap, digits + 1), gap_atr=C.r_(gap / atr_h, 3), friday_close=fri)
    if abs(gap) < float(g("h04_g")) * atr_h:
        return C.hold(a, f"Gap {abs(gap) / atr_h:.2f}×ATR < {float(g('h04_g')):g}×ATR.")
    d = 1 if gap > 0 else -1
    if (cur["c"] - fri) * d <= 0:
        return C.hold(a, "Gap-ul s-a închis deja în prima bară.")
    side = "SELL" if d > 0 else "BUY"
    entry = float(quote["bid"]) if side == "SELL" else float(quote["ask"])
    sl_dist = max(float(g("h04_sl_mult")) * abs(gap), float(g("h04_min_sl_atr")) * atr_h)
    comps = [f"Gap de weekend {gap / atr_h:+.2f}×ATR24 față de închiderea de vineri {fri}",
             "Intrare spre închiderea de vineri (umplerea gap-ului)"]
    return C.finish(a, symbol, side, entry + d * sl_dist, fri, quote, cfg, risk, ctx, comps, len(comps),
                    note="ieșire la 13:00 UTC luni")
