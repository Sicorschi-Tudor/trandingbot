"""tsmom-v1: time-series momentum on D1 (Stage C2).

Direction = sign of the log return over `lookback` bars (126). Volatility filter: the return must be at least
min_z standard deviations of a `lookback`-bar return (sigma of the last vol_n one-bar log returns × sqrt(lookback)),
so weak drifts in noisy markets are skipped. Re-evaluated on every closed bar: a candidate on every bar while the
filter passes (the context blockers stop duplicates while a position is open). SL = sl_atr × ATR (3×ATR20,
catastrophic stop), TP = tp_atr × ATR (far placeholder). Exit: default_exit() = signal_flip on bias() = raw sign of
the lookback return (no filter): the position is held while the sign persists. HTF not used."""
from __future__ import annotations

import math

from . import _common as C
from . import _htf as X

NAME = "tsmom-v1"
VERSION = "1.0"
DESCRIPTION_RO = ("Momentum pe serie de timp (D1): direcția = semnul randamentului pe ultimele 126 de bare, doar dacă "
                  "este de cel puțin 0,5 deviații standard; poziția se ține cât timp semnul se păstrează și se "
                  "închide când se inversează. SL de avarie 3×ATR.")
PARAMS = {"lookback": 126, "vol_n": 60, "min_z": 0.5, "atr_n": 20, "sl_atr": 3.0, "tp_atr": 20.0}
RUN_DEFAULTS = dict(X.RUN_DEFAULTS)


def default_exit(cfg: dict) -> dict:
    return {"mode": "signal_flip", "params": {}}


def _ret(bars, L: int) -> float | None:
    if len(bars) < L + 1:
        return None
    return X.log_ret(bars[-1 - L]["c"], bars[-1]["c"])


def bias(symbol, sig_bars, htf_bars, cfg) -> int:
    """Raw sign of the lookback return at the last closed bar (0 when undefined)."""
    r = _ret(sig_bars[-X.TAIL:], int(C.p(cfg, PARAMS, "lookback")))
    return 0 if not r else (1 if r > 0 else -1)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    L, vn, atr_n = int(g("lookback")), max(2, int(g("vol_n"))), int(g("atr_n"))
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, max(L, vn, atr_n) + 2)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    atr_s = X.atr_series(bars, atr_n)
    atr_v = atr_s[-1]
    ret = _ret(bars, L)
    rets = [X.log_ret(bars[j - 1]["c"], bars[j]["c"]) for j in range(len(bars) - vn, len(bars))]
    mean = math.fsum(rets) / vn
    sd = math.sqrt(max(0.0, math.fsum((x - mean) ** 2 for x in rets) / (vn - 1)))
    z = ret / (sd * math.sqrt(L)) if sd > 0 else 0.0
    sign = 0 if not ret else (1 if ret > 0 else -1)
    a["regime"] = X.regime(atr_s, sign)
    a["indicators"] = X.indicators(digits, atr_v, X.htf_trend(htf_bars), mom_ret=C.r_(ret, 6), mom_z=C.r_(z, 3),
                                   vol_daily=C.r_(sd, 6), lookback=L)
    if not atr_v:
        return C.hold(a, "Indicatori incompleți (ATR indisponibil).")
    if sign == 0:
        return C.hold(a, f"Randament zero pe ultimele {L} bare.")
    if abs(z) < float(g("min_z")):
        return C.hold(a, f"Momentum slab: {abs(z):.2f} deviații standard < {float(g('min_z')):g} (filtru volatilitate).")
    side = "BUY" if sign > 0 else "SELL"
    comps = [f"Randament pe {L} bare {ret * 100:+.2f}% ({z:+.2f} deviații standard)",
             f"Poziția se ține cât timp semnul momentumului rămâne {'pozitiv' if sign > 0 else 'negativ'}"]
    return X.finish_atr(a, symbol, side, quote, cfg, risk, ctx, comps, atr_v, g("sl_atr"), g("tp_atr"),
                        note=f"SL de avarie; TP {float(g('tp_atr')):g}×ATR doar ca plasă")
