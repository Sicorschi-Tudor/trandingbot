"""disc-H13: cross-sectional short-term reversal across the FX pairs (D1; research only).

At every D1 close, for every pair of the universe: z = ln(c_t / c_{t-L}) / (ATR20/c_t * sqrt(L)), using only bars
whose open time is <= the decision bar's open time (closed bars). Pairs without a bar at the decision time are left
out. If this symbol has the maximum z and z >= z_min -> SELL; if it has the minimum z and z <= -z_min -> BUY.
Deviation from the hypothesis text (documented): the plugin decides per symbol, so "skip to the next-ranked pair when
the correlation limit blocks the top one" is approximated by max_rank (default 1 = only the extreme pair; with
max_rank 2 the second-ranked pair also signals and the backtest's correlation/one-per-symbol blockers decide).
SL = 2*ATR14, no TP (far placeholder 50*ATR). Exit after `hold` D1 bars (time_exit hook, bars counted, so weekends do
not shorten the hold; without the hook armed set strategy max_hold_minutes yourself) or SL. Swap applies.
Universe: _disc0.set_universe({symbol: D1 bars}) or cfg 'xs_db' (read-only path of a bot.db copy)."""
from __future__ import annotations

import bisect
import math

from .. import _common as C
from .. import _htf as X
from . import _disc0 as D

NAME = "disc-H13"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare: inversare pe termen scurt între perechi — perechea cu cea mai mare mișcare normalizată "
                  "pe ultimele L zile se vinde (cea mai slabă se cumpără) și se ține `hold` zile; SL 2×ATR.")
UNIVERSE = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURJPY", "GBPJPY", "NZDUSD", "EURGBP", "EURCHF",
            "AUDJPY"]
PARAMS = {"lookback": 1, "hold": 2, "z_min": 1.5, "atr_n": 20, "sl_atr": 2.0, "sl_atr_n": 14, "max_rank": 1,
          "xs_tail": 120}
RUN_DEFAULTS = {**X.RUN_DEFAULTS, "signal_timeframe": "D1", "htf_timeframe": "NONE", "max_hold_minutes": 0}
_MEMO: dict[tuple, float | None] = {}


def time_exit(opened_t, bar_t, bar_sec, n_bars, cfg):
    return "close" if n_bars >= int(C.p(cfg, PARAMS, "hold")) else None


def zscore(bars: list[dict], L: int, atr_n: int) -> float | None:
    if len(bars) < max(L, atr_n) + 2:
        return None
    atr = D.atr_last(bars, atr_n)
    c, c0 = bars[-1]["c"], bars[-1 - L]["c"]
    if not atr or c <= 0 or c0 <= 0:
        return None
    return math.log(c / c0) / (atr / c * math.sqrt(L))


def cross_section(cfg: dict, t0: int) -> dict[str, float]:
    """{symbol: z} for the universe pairs that have a bar opening exactly at t0 (bars up to t0 only)."""
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    syms = [str(s).upper() for s in (cfg.get("xs_symbols") or UNIVERSE)]
    uni = D.universe(cfg, syms)
    L, an, tail = int(g("lookback")), int(g("atr_n")), int(g("xs_tail"))
    out: dict[str, float] = {}
    for s in syms:
        bars = uni.get(s) or []
        key = (id(bars), len(bars), s, t0, L, an, tail)
        if key not in _MEMO:
            ts = [b["t"] for b in bars] if bars else []
            k = bisect.bisect_right(ts, t0)
            _MEMO[key] = zscore(bars[max(0, k - tail):k], L, an) if k and bars[k - 1]["t"] == t0 else None
            if len(_MEMO) > 500_000:
                _MEMO.clear()
        z = _MEMO.get(key)
        if z is not None:
            out[s] = z
    return out


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, 30)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    atr_sl = D.atr_last(bars[-120:], int(g("sl_atr_n")))
    a["indicators"] = X.indicators(digits, atr_sl, "FLAT")
    if not atr_sl:
        return C.hold(a, "ATR indisponibil.")
    zs = cross_section(cfg, bars[-1]["t"])
    sym = symbol.upper()
    if sym not in zs or len(zs) < 3:
        return C.hold(a, "Secțiune transversală indisponibilă (universul de perechi lipsește sau e incomplet).")
    ranked = sorted(zs, key=lambda s: zs[s], reverse=True)
    zmin, rank = float(g("z_min")), int(g("max_rank"))
    z = zs[sym]
    a["indicators"].update(xs_z=C.r_(z, 3), xs_rank=ranked.index(sym) + 1, xs_n=len(ranked))
    side = None
    if sym in ranked[:rank] and z >= zmin:
        side = "SELL"
    elif sym in ranked[-rank:] and z <= -zmin:
        side = "BUY"
    if side is None:
        return C.hold(a, f"z {z:+.2f} (loc {ranked.index(sym) + 1}/{len(ranked)}): nu este extrema secțiunii peste ±{zmin:g}.")
    a["regime"] = "RANGE"
    L = int(g("lookback"))
    comps = [f"Mișcare normalizată pe {L} zile {z:+.2f} — {'cea mai mare' if side == 'SELL' else 'cea mai mică'} "
             f"din {len(ranked)} perechi", f"Inversare așteptată; ținere {int(g('hold'))} zile"]
    return X.finish_atr(a, symbol, side, quote, cfg, risk, ctx, comps, atr_sl, g("sl_atr"), 50.0,
                        note=f"ieșire după {int(g('hold'))} bare D1")
