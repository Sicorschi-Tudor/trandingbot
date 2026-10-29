"""filt-P2_HS: the live trend-v1 with a Head & shoulders filter (pattern builder #2, research only).

trend-v1 output unchanged except that a candidate is blocked (extra blocker, direction HOLD) when the filter
disagrees: veto la un cap-umeri opus în ultimele K bare (sau, mod keep, cere unul în aceeași direcție). No RUN_DEFAULTS / default_exit: the backtest uses the live exits (incl. RESCUE).
Filter definition: app.patterns.p2.FILTERS['P2_HS']; parameters are cfg keys 'pat_*'."""
from __future__ import annotations

from . import _pat2 as P

NAME = "filt-P2_HS"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare (filtru pe trend-v1): veto la un cap-umeri opus în ultimele K bare (sau, mod keep, cere unul în aceeași direcție)."
PARAMS = {'pat_L': 3, 'pat_tol': 1.0, 'pat_K': 12, 'pat_mode': 'veto'}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.filter_analyze("P2_HS", PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
