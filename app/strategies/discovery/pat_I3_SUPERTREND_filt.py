"""filt-I3_SUPERTREND: the live trend-v1 with a Supertrend flip filter (pattern builder #2, research only).

trend-v1 output unchanged except that a candidate is blocked (extra blocker, direction HOLD) when the filter
disagrees: cere ca direcția Supertrend să fie aceeași (și, cu K>0, un flip în ultimele K bare). No RUN_DEFAULTS / default_exit: the backtest uses the live exits (incl. RESCUE).
Filter definition: app.patterns.p2.FILTERS['I3_SUPERTREND']; parameters are cfg keys 'pat_*'."""
from __future__ import annotations

from . import _pat2 as P

NAME = "filt-I3_SUPERTREND"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare (filtru pe trend-v1): cere ca direcția Supertrend să fie aceeași (și, cu K>0, un flip în ultimele K bare)."
PARAMS = {'pat_n': 10, 'pat_mult': 3.0, 'pat_K': 0}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.filter_analyze("I3_SUPERTREND", PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
