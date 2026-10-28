"""disc-H15: Friday-afternoon week squaring (H1, research).

Signal bar = the Friday H1 bar opening at (entry_hour - 1):00 UTC (12:00 for the base entry at 13:00).
WTD = close(signal bar) - open(first H1 bar of the trading week). ATRd = ATR14 of the closed D1 bars (HTF D1).
If |WTD| >= k × ATRd: enter against WTD at the next open (entry_hour:00 UTC). SL = 0.6×ATRd, TP = 0.25×|WTD|.
Hard time exit at the close of the Friday 19:00 UTC bar (signal_flip with flip_on_flat=1: bias() returns the trade
direction on Friday bars from entry_hour to 18:00 and 0 from 19:00), never held over the weekend.
Variant entry_hour=15 measures WTD at the close of the 14:00 bar."""
from __future__ import annotations

from .. import _common as C
from .. import _htf as X
from . import _b2 as B

NAME = "disc-H15"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare: vineri după-amiază, contra mișcării săptămânii (închiderea pozițiilor înainte de "
                  "weekend); SL 0.6×ATR zilnic, TP 25% din mișcarea săptămânii, ieșire forțată la 20:00 UTC.")
PARAMS = {"h15_k": 1.0, "h15_entry_hour": 13, "h15_exit_hour": 19, "h15_sl_atrd": 0.6, "h15_tp_frac": 0.25}
RUN_DEFAULTS = {**X.RUN_DEFAULTS, "signal_timeframe": "H1", "htf_timeframe": "D1"}


def default_exit(cfg: dict) -> dict:
    return {"mode": "signal_flip", "params": {"flip_on_flat": 1}}


def _g(cfg, k):
    return C.p(cfg, PARAMS, k)


def wtd_signal(bars: list[dict], i: int, atr_d: float, k: float, entry_hour: int):
    """(side_sign, wtd) when bar i is the Friday signal bar and |WTD| >= k×ATRd, else None."""
    t = bars[i]["t"]
    u = B.utc(t)
    if u.weekday() != 4 or u.hour != entry_hour - 1 or not atr_d or atr_d <= 0:
        return None
    wk = B.week_id(t)
    k0 = i
    while k0 - 1 >= 0 and B.week_id(bars[k0 - 1]["t"]) == wk:
        k0 -= 1
    if k0 == 0 or B.utc(bars[k0]["t"] + 3 * 3600).weekday() != 0:
        return None  # week start not in the window (or the week opened late): undefined WTD
    wtd = bars[i]["c"] - bars[k0]["o"]
    if wtd == 0 or abs(wtd) < k * atr_d:
        return None
    return (-1 if wtd > 0 else 1), wtd


def _atr_d(htf_bars) -> float | None:
    if not htf_bars or len(htf_bars) < 16:
        return None
    return X.atr_series(htf_bars[-X.TAIL:], 14)[-1]


def bias(symbol, sig_bars, htf_bars, cfg) -> int:
    if not sig_bars:
        return 0
    u = B.utc(sig_bars[-1]["t"])
    eh, xh = int(_g(cfg, "h15_entry_hour")), int(_g(cfg, "h15_exit_hour"))
    if u.weekday() != 4 or not eh <= u.hour < xh:
        return 0
    bars = sig_bars[-X.TAIL:]
    for i in range(len(bars) - 1, max(-1, len(bars) - 30), -1):
        ui = B.utc(bars[i]["t"])
        if ui.date() != u.date():
            break
        if ui.hour == eh - 1:
            s = wtd_signal(bars, i, _atr_d(htf_bars), float(_g(cfg, "h15_k")), eh)
            return s[0] if s else 0
    return 0


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, 130)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    atr_d = _atr_d(htf_bars)
    a["indicators"] = X.indicators(digits, atr_d, "FLAT")
    eh = int(_g(cfg, "h15_entry_hour"))
    if not atr_d:
        return C.hold(a, "ATR zilnic indisponibil (HTF D1).")
    s = wtd_signal(bars, len(bars) - 1, atr_d, float(_g(cfg, "h15_k")), eh)
    if not s:
        return C.hold(a, "Nu este bara semnal de vineri sau mișcarea săptămânii este sub prag.")
    sign, wtd = s
    side = "BUY" if sign > 0 else "SELL"
    entry = float(quote["ask"]) if sign > 0 else float(quote["bid"])
    sl = entry - sign * float(_g(cfg, "h15_sl_atrd")) * atr_d
    tp = entry + sign * float(_g(cfg, "h15_tp_frac")) * abs(wtd)
    comps = [f"Mișcarea săptămânii {wtd / atr_d:+.2f}×ATR zilnic", "Vineri după-amiază: închiderea pozițiilor"]
    a["indicators"].update(wtd_atr=C.r_(wtd / atr_d, 3))
    return C.finish(a, symbol, side, sl, tp, quote, cfg, risk, ctx, comps, len(comps),
                    note=f"ieșire forțată la închiderea barei de {int(_g(cfg, 'h15_exit_hour'))}:00 UTC")
