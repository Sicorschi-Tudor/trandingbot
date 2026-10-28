"""disc-H19: USD-index breakout confirmed by a USD pair (H4, research).

USDX level = cumulative sum of r_U (mean USD-signed log return of the six USD pairs, panel, see _b2). A USDX
breakout = level above the max (below the min) of the previous N levels. At that bar, trade the USD side of the
USD pair whose own close is also at an N-bar extreme (vs the previous N closes) in the same USD direction, picking
the largest USD-signed move (close - close N bars ago) / ATR14; none qualifies -> no trade. SL = 2×ATR14, TP far
placeholder (20×ATR). Exit variant (h19_exit): 'trailing' = 2×ATR trailing stop armed at +1R, 'time' = close
after 12 H4 bars (max_hold_hours 48) or SL."""
from __future__ import annotations

from .. import _common as C
from .. import _htf as X
from . import _b2 as B

NAME = "disc-H19"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare: breakout al indicelui USD (din 6 perechi) confirmat de o pereche USD la extrema ei de N "
                  "bare; SL 2×ATR, ieșire trailing 2×ATR sau după 12 bare H4.")
PARAMS = {"h19_n": 30, "h19_exit": "trailing", "h19_sl_atr": 2.0, "h19_tp_atr": 20.0, "h19_time_bars": 12}
RUN_DEFAULTS = {**X.RUN_DEFAULTS, "signal_timeframe": "H4"}
SYMBOLS = list(B.USD_PAIRS)


def _g(cfg, k):
    return C.p(cfg, PARAMS, k)


def default_exit(cfg: dict) -> dict:
    if str(_g(cfg, "h19_exit")) == "time":
        return {"mode": "base", "params": {"max_hold_hours": 4.0 * float(_g(cfg, "h19_time_bars"))}}
    return {"mode": "trailing", "params": {"trail_start_r": 1.0, "trail_atr_mult": 2.0, "trail_min_r": 0.0}}


def decision(panel: dict, t: int, n: int):
    """(symbol, side_sign, usd_dir, score) for the bar opening at t, or None (panel bars with time <= t only)."""
    return B._memo(("h19", id(panel), int(t), int(n)), lambda: _decision(panel, t, n) or False, panel) or None


def _decision(panel, t, n):
    ts, _, lv = B.usdx(panel)
    i = B.idx_at(ts, t)
    if i is None or i < n:
        return None
    prev = lv[i - n:i]
    if lv[i] > max(prev):
        usd_dir = 1
    elif lv[i] < min(prev):
        usd_dir = -1
    else:
        return None
    best = None
    for sym, sg in B.USD_PAIRS.items():
        pts, bars, atr_s = B.pair_view(panel, sym)
        j = B.idx_at(pts, t)
        if j is None or j < n or atr_s[j] is None or atr_s[j] <= 0:
            continue
        d = sg * usd_dir  # +1: the pair's price rises when USD moves in usd_dir
        s = [d * b["c"] for b in bars[j - n:j + 1]]
        if s[-1] <= max(s[:-1]):
            continue
        score = (s[-1] - s[0]) / atr_s[j]
        if best is None or score > best[1]:
            best = (sym, score, d)
    if best is None:
        return None
    return best[0], best[2], usd_dir, best[1]


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, 30)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    atr_v = X.atr_series(bars, 14)[-1]
    a["indicators"] = X.indicators(digits, atr_v, "FLAT")
    if symbol not in B.USD_PAIRS:
        return C.hold(a, "disc-H19 tranzacționează doar perechile USD.")
    panel = B.get_panel(cfg, cfg.get("signal_timeframe", "H4"))
    if panel is None:
        return C.hold(a, "Panel USD indisponibil (cfg.panel_db lipsă).")
    n = int(_g(cfg, "h19_n"))
    dec = decision(panel, sig_bars[-1]["t"], n)
    if not dec:
        return C.hold(a, f"Fără breakout USD confirmat pe {n} bare.")
    sym, sign, usd_dir, score = dec
    a["indicators"].update(pick=sym, usd_dir=usd_dir, pick_score=C.r_(score, 3))
    if sym != symbol or not atr_v:
        return C.hold(a, f"Perechea aleasă este {sym}.")
    side = "BUY" if sign > 0 else "SELL"
    comps = [f"Indicele USD la extrema de {n} bare ({'sus' if usd_dir > 0 else 'jos'})",
             f"{symbol} confirmă (mișcare {score:.2f}×ATR pe {n} bare)"]
    return X.finish_atr(a, symbol, side, quote, cfg, risk, ctx, comps, atr_v, _g(cfg, "h19_sl_atr"),
                        _g(cfg, "h19_tp_atr"), note=f"ieșire {_g(cfg, 'h19_exit')}")
