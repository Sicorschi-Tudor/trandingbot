"""pat-L4_FIB618: Fibonacci 50-61.8% pullback as a standalone strategy (pattern builder #2, research only).

BUY la revenirea din zona 50–61,8% a ultimului picior ascendent (EMA50 > EMA200), SELL invers; SL sub 78,6%, TP = extrema piciorului.
Definition and causality: app.patterns.p2 (scan_l4_fib618); parameters are cfg keys 'pat_*'."""
from __future__ import annotations

from . import _pat2 as P

NAME = "pat-L4_FIB618"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare (pattern): BUY la revenirea din zona 50–61,8% a ultimului picior ascendent (EMA50 > EMA200), SELL invers; SL sub 78,6%, TP = extrema piciorului."
PARAMS = {'pat_z': 0.0, 'pat_leg_min': 3.0}
RUN_DEFAULTS = {"break_even_at_r": 0, "close_at_session_end": False, "session": None, "htf_timeframe": "NONE"}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone_analyze("L4_FIB618", PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
