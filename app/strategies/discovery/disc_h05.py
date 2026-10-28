"""disc-H05: NR-N inside-day breakout (Crabel). H1 entries, D1 setup (htf_timeframe D1); research only.

Setup = the last closed D1 bar S: its high-low range is the smallest of the last N D1 bars (S included, ties allowed)
and it is an inside bar of the bar before it (high <= prior high, low >= prior low).
On the broker day after S (broker day = Athens date of the bar open, see _disc0), the FIRST H1 close above
S.high + 0.05*ATRd is a BUY, the first H1 close below S.low - 0.05*ATRd a SELL (only the first break of the day
counts: one trade per setup). SL = the opposite extreme of S; TP = tp_r x initial risk.
Exit: fixed SL/TP + time exit at the end of the next broker day (2 D1 sessions after the setup) via the time_exit
hook (fallback max_hold 2 days when the hook is not armed)."""
from __future__ import annotations

from datetime import timedelta

from .. import _common as C
from .. import _htf as X
from . import _disc0 as D

NAME = "disc-H05"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare: după o zi NR-N care este și inside day, prima închidere H1 peste/sub extremele ei "
                  "(+0,05×ATR zilnic) deschide în direcția ruperii; SL la extrema opusă, TP tp_r×risc; ieșire la "
                  "sfârșitul zilei următoare.")
PARAMS = {"nr_n": 7, "tp_r": 1.5, "brk_atr": 0.05, "atrd_n": 14}
RUN_DEFAULTS = {**X.RUN_DEFAULTS, "signal_timeframe": "H1", "htf_timeframe": "D1", "max_hold_minutes": 2 * 1440,
                "signal_expiry_minutes": 120}


def time_exit(opened_t, bar_t, bar_sec, n_bars, cfg):
    # the signal bar closes at opened_t (fill at the next open): its broker day is the day of opened_t - 1 s
    day = D.broker_date(opened_t - 1)
    deadline = D.broker_midnight(D.next_trading_day(day) + timedelta(days=1))
    return D.deadline_rule(deadline, bar_t, bar_sec)


def setup(htf_bars: list[dict], n: int) -> dict | None:
    """The last closed D1 bar when it is NR-n and inside; else None."""
    if len(htf_bars) < max(n, 2):
        return None
    s, p = htf_bars[-1], htf_bars[-2]
    rng = s["h"] - s["l"]
    if any(b["h"] - b["l"] < rng for b in htf_bars[-n:-1]):
        return None
    if not (s["h"] <= p["h"] and s["l"] >= p["l"]):
        return None
    return s


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, 24)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    htf = list(htf_bars or [])[-60:]
    atr_d = D.atr_last(htf, int(g("atrd_n")))
    a["indicators"] = X.indicators(digits, atr_d, "FLAT")
    if not atr_d:
        return C.hold(a, "ATR zilnic indisponibil (sunt necesare bare D1 închise).")
    n = int(g("nr_n"))
    s = setup(htf, n)
    if s is None:
        return C.hold(a, f"Ultima zi închisă nu este NR{n} + inside day.")
    day = D.next_trading_day(D.broker_date(s["t"]))
    last = bars[-1]
    if D.broker_date(last["t"]) != day:
        return C.hold(a, "Bara curentă nu aparține zilei de după setup.")
    up, dn = s["h"] + float(g("brk_atr")) * atr_d, s["l"] - float(g("brk_atr")) * atr_d
    today = [b for b in bars[-30:] if D.broker_date(b["t"]) == day]
    first = None
    for b in today:
        if b["c"] > up or b["c"] < dn:
            first = b
            break
    if first is None:
        return C.hold(a, f"Setup NR{n}/inside activ; nicio închidere H1 în afara intervalului încă.")
    if first is not last:
        return C.hold(a, "Ruperea a avut loc deja mai devreme azi (doar prima rupere contează).")
    side = "BUY" if last["c"] > up else "SELL"
    sl = s["l"] if side == "BUY" else s["h"]
    a["regime"] = "RANGE"
    a["indicators"].update(setup_high=s["h"], setup_low=s["l"])
    comps = [f"Ziua precedentă: NR{n} + inside day (interval {(s['h'] - s['l']) / atr_d:.2f}×ATR)",
             f"Prima închidere H1 {'peste maxim' if side == 'BUY' else 'sub minim'} (+{float(g('brk_atr')):g}×ATR)"]
    return C.finish(a, symbol, side, sl, None, quote, cfg, risk, ctx, comps, len(comps), tp_r=float(g("tp_r")),
                    note="ieșire la sfârșitul zilei următoare")
