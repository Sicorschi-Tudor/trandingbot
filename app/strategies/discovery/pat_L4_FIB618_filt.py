"""filt-L4_FIB618: the live trend-v1 with a Fibonacci 50-61.8% pullback filter (pattern builder #2, research only).

trend-v1 output unchanged except that a candidate is blocked (extra blocker, direction HOLD) when the filter
disagrees: cere o retragere 50–61,8% în aceeași direcție în ultimele K bare, cu intrarea sub nivelul de 38,2%. No RUN_DEFAULTS / default_exit: the backtest uses the live exits (incl. RESCUE).
Filter definition: app.patterns.p2.FILTERS['L4_FIB618']; parameters are cfg keys 'pat_*'."""
from __future__ import annotations

from . import _pat2 as P

NAME = "filt-L4_FIB618"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare (filtru pe trend-v1): cere o retragere 50–61,8% în aceeași direcție în ultimele K bare, cu intrarea sub nivelul de 38,2%."
PARAMS = {'pat_z': 0.0, 'pat_leg_min': 3.0, 'pat_K': 6}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.filter_analyze("L4_FIB618", PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
