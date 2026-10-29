"""filt-S3_OB: the live trend-v1 with a Order block retest filter (pattern builder #2, research only).

trend-v1 output unchanged except that a candidate is blocked (extra blocker, direction HOLD) when the filter
disagrees: cere un retest de order block în aceeași direcție (ultimele K bare) și interzice intrarea într-un order block opus. No RUN_DEFAULTS / default_exit: the backtest uses the live exits (incl. RESCUE).
Filter definition: app.patterns.p2.FILTERS['S3_OB']; parameters are cfg keys 'pat_*'."""
from __future__ import annotations

from . import _pat2 as P

NAME = "filt-S3_OB"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare (filtru pe trend-v1): cere un retest de order block în aceeași direcție (ultimele K bare) și interzice intrarea într-un order block opus."
PARAMS = {'pat_m': 1.5, 'pat_K': 4}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.filter_analyze("S3_OB", PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
