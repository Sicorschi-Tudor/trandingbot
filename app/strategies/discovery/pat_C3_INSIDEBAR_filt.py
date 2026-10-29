"""filt-C3_INSIDEBAR: the live trend-v1 with a Inside-bar breakout filter (pattern builder #2, research only).

trend-v1 output unchanged except that a candidate is blocked (extra blocker, direction HOLD) when the filter
disagrees: păstrează doar candidații cu o spargere inside-bar în aceeași direcție în ultimele K bare. No RUN_DEFAULTS / default_exit: the backtest uses the live exits (incl. RESCUE).
Filter definition: app.patterns.p2.FILTERS['C3_INSIDEBAR']; parameters are cfg keys 'pat_*'."""
from __future__ import annotations

from . import _pat2 as P

NAME = "filt-C3_INSIDEBAR"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare (filtru pe trend-v1): păstrează doar candidații cu o spargere inside-bar în aceeași direcție în ultimele K bare."
PARAMS = {'pat_min_inside': 1, 'pat_K': 3, 'pat_strict': 0}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.filter_analyze("C3_INSIDEBAR", PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
