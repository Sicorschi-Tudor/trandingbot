"""disc-f2-tsmom2: ensemble time-series momentum on D1/H4 (Stage F, family F2; research only).

Mirror of tsmom_v1 with an ensemble of lookbacks instead of one. For every lookback L in `lookbacks` (default
[21, 63, 126, 252] closed bars) the log return r_L = ln(c[-1] / c[-1-L]) and its z-score z_L = r_L / (sigma_1 * sqrt(L)),
where sigma_1 is the standard deviation of the last `vol_n` one-bar log returns (same volatility filter as tsmom-v1).
A lookback votes sign(r_L) when |z_L| >= min_z, otherwise 0. Direction = sign of the sum of the votes, and at least
`k` of the lookbacks must vote that direction (k = 4 is unanimity). Re-evaluated on every closed bar: a candidate on
every bar while the ensemble agrees (the context blockers stop duplicates while a position is open).
SL = sl_atr × ATR(atr_n) (catastrophic stop), TP = tp_atr × ATR (far placeholder).
Exit: default_exit() = signal_flip on bias() = sign of the sum of the RAW signs of the lookback returns (no volatility
filter, no k requirement): the position is held while the ensemble sign persists and closed when it inverts (a 2–2 tie
gives bias 0, which signal_flip treats as flat = no close). HTF not used (reported only).
Strictly on closed bars: analyze() and bias() read only sig_bars[-TAIL:]; the quote is used only for the entry price
and the spread filter, never for the direction."""
from __future__ import annotations

import math

from .. import _common as C
from .. import _htf as X

NAME = "disc-f2-tsmom2"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare F2: momentum pe serie de timp în ansamblu (D1/H4): direcția = semnul sumei semnelor "
                  "randamentelor pe 21/63/126/252 bare, cu acordul a cel puțin k din 4 lookback-uri și filtru de "
                  "volatilitate (min_z deviații standard pe fiecare lookback); SL de avarie sl_atr×ATR; ieșire la "
                  "inversarea semnului ansamblului.")
PARAMS = {"lookbacks": [21, 63, 126, 252], "k": 3, "vol_n": 60, "min_z": 0.5, "atr_n": 20, "sl_atr": 3.0,
          "tp_atr": 20.0}
RUN_DEFAULTS = dict(X.RUN_DEFAULTS)


def default_exit(cfg: dict) -> dict:
    return {"mode": "signal_flip", "params": {}}


def _lookbacks(cfg: dict) -> list[int]:
    raw = C.p(cfg, PARAMS, "lookbacks")
    if isinstance(raw, str):
        raw = [x for x in raw.replace(";", ",").split(",") if x.strip()]
    out = sorted({int(x) for x in raw if int(x) > 0})
    return out or list(PARAMS["lookbacks"])


def _ret(bars, L: int) -> float | None:
    if len(bars) < L + 1:
        return None
    return X.log_ret(bars[-1 - L]["c"], bars[-1]["c"])


def _sign(x) -> int:
    return 0 if not x else (1 if x > 0 else -1)


def _raw_votes(bars, lbs: list[int]) -> list[int]:
    return [_sign(_ret(bars, L)) for L in lbs]


def bias(symbol, sig_bars, htf_bars, cfg) -> int:
    """Sign of the sum of the raw signs of the lookback returns at the last closed bar (0 on a tie / undefined)."""
    bars = sig_bars[-X.TAIL:]
    lbs = _lookbacks(cfg)
    if len(bars) < max(lbs) + 1:
        return 0
    return _sign(sum(_raw_votes(bars, lbs)))


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k_: C.p(cfg, PARAMS, k_)  # noqa: E731
    lbs = _lookbacks(cfg)
    k = max(1, min(len(lbs), int(g("k"))))
    vn, atr_n, min_z = max(2, int(g("vol_n"))), int(g("atr_n")), float(g("min_z"))
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, max(max(lbs), vn, atr_n) + 2)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    atr_s = X.atr_series(bars, atr_n)
    atr_v = atr_s[-1]
    rets1 = [X.log_ret(bars[j - 1]["c"], bars[j]["c"]) for j in range(len(bars) - vn, len(bars))]
    mean = math.fsum(rets1) / vn
    sd = math.sqrt(max(0.0, math.fsum((x - mean) ** 2 for x in rets1) / (vn - 1)))
    rets = [_ret(bars, L) for L in lbs]
    zs = [(r / (sd * math.sqrt(L)) if (sd > 0 and r is not None) else 0.0) for r, L in zip(rets, lbs)]
    raw = [_sign(r) for r in rets]
    votes = [s if abs(z) >= min_z else 0 for s, z in zip(raw, zs)]
    total = sum(votes)
    side_sign = _sign(total)
    agree = sum(1 for v in votes if v == side_sign) if side_sign else 0
    raw_sign = _sign(sum(raw))
    a["regime"] = X.regime(atr_s, side_sign)
    a["indicators"] = X.indicators(digits, atr_v, X.htf_trend(htf_bars), lookbacks=list(lbs),
                                   mom_rets=[C.r_(r, 6) for r in rets], mom_z=[C.r_(z, 3) for z in zs],
                                   votes=list(votes), ensemble_sign=raw_sign, agree=agree, vol_daily=C.r_(sd, 6))
    if not atr_v:
        return C.hold(a, "Indicatori incompleți (ATR indisponibil).")
    if side_sign == 0:
        return C.hold(a, f"Ansamblul nu are direcție (voturi {votes}; filtru volatilitate min_z={min_z:g}).")
    if agree < k:
        return C.hold(a, f"Acord insuficient: {agree}/{len(lbs)} lookback-uri < k={k} (voturi {votes}).")
    side = "BUY" if side_sign > 0 else "SELL"
    comps = [f"{agree}/{len(lbs)} lookback-uri ({', '.join(str(L) for L in lbs)} bare) {'pozitive' if side_sign > 0 else 'negative'} "
             f"peste {min_z:g} deviații standard",
             f"Poziția se ține cât timp semnul ansamblului rămâne {'pozitiv' if side_sign > 0 else 'negativ'}"]
    return X.finish_atr(a, symbol, side, quote, cfg, risk, ctx, comps, atr_v, g("sl_atr"), g("tp_atr"),
                        note=f"SL de avarie; TP {float(g('tp_atr')):g}×ATR doar ca plasă")
