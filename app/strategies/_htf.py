"""Shared plumbing for the Stage C2 higher-timeframe trend plugins (donchian-v1, ma-trend-v1, tsmom-v1).

Same output shape, SL floor, market filters and context blockers as the other plugins (_common.finish).
Indicators are computed on a fixed tail of the closed bars passed in (TAIL), so a decision depends only on the
last TAIL bars, never on how long a window the caller passes (live 400 = backtest 400 = TAIL).
The HTF series is optional (htf_tf 'NONE' in the backtest): it is only reported (htf_trend), never required.

RUN_DEFAULTS: backtest-only defaults for these plugins (app.backtest.resolve applies them unless the run sets the
key itself): D1 without HTF, no session filter, no time stop, no break-even, fills allowed after a weekend gap,
swap/financing charged with the default table. They never touch the live settings."""
from __future__ import annotations

import math

from .. import indicators as ind
from ..strategy import MIN_HTF_BARS, MIN_SIG_BARS
from . import _common as C

TAIL = 400
RUN_DEFAULTS = {"signal_timeframe": "D1", "htf_timeframe": "NONE", "max_hold_minutes": 0, "break_even_at_r": 0,
                "signal_expiry_minutes": 4 * 1440, "close_at_session_end": False, "session": None,
                "swap_mode": "table"}


def start(symbol, sig_bars, htf_bars, quote, cfg, ctx, need: int):
    """(analysis skeleton, tail of closed bars) or (analysis with the HOLD reason, None)."""
    a = C.skeleton(symbol, sig_bars, quote, cfg, ctx)
    if not quote or not quote.get("bid") or not quote.get("ask"):
        a["reasons"].append("Nu există cotație bid/ask pentru simbol.")
        return a, None
    need = max(MIN_SIG_BARS, int(need))
    if len(sig_bars) < need:
        a["reasons"].append(f"Date insuficiente: {len(sig_bars)}/{need} bare închise pe {a['timeframe']}.")
        return a, None
    return a, sig_bars[-TAIL:]


def htf_trend(htf_bars) -> str:
    if htf_bars and len(htf_bars) >= MIN_HTF_BARS:
        return C.htf_state(htf_bars)["trend"]
    return "FLAT"


def atr_series(bars, n: int):
    return ind.atr([b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars], int(n))


def regime(atr_s, side_hint: int) -> str:
    atr_v = ind.last(atr_s)
    med = ind.median([x for x in atr_s[-100:] if x is not None]) or atr_v
    if atr_v and med and atr_v > 2 * med:
        return "HIGH_VOL"
    return "TREND_UP" if side_hint > 0 else "TREND_DOWN" if side_hint < 0 else "RANGE"


def indicators(digits: int, atr_v, htf: str, **extra) -> dict:
    out = {"ema_fast": None, "ema_slow": None, "rsi": None, "atr": C.r_(atr_v, digits + 1), "macd_hist": None,
           "htf_trend": htf}
    out.update(extra)
    return out


def finish_atr(a, symbol, side, quote, cfg, risk, ctx, comps, atr_v, sl_atr, tp_atr, note=""):
    """SL = sl_atr × ATR, TP = tp_atr × ATR (far placeholder: the exit policy closes the trade)."""
    sign = 1 if side == "BUY" else -1
    entry = float(quote["ask"]) if side == "BUY" else float(quote["bid"])
    return C.finish(a, symbol, side, entry - sign * float(sl_atr) * atr_v, entry + sign * float(tp_atr) * atr_v,
                    quote, cfg, risk, ctx, comps, len(comps), note=note)


def log_ret(a: float, b: float) -> float:
    return math.log(b / a) if a > 0 and b > 0 else 0.0
