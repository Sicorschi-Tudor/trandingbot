"""filt-L1_SR: the live trend-v1 with a Support/resistance bounce filter (pattern builder #2, research only).

trend-v1 output unchanged except that a candidate is blocked (extra blocker, direction HOLD) when the filter
disagrees: cere spațiu ≥ D×ATR până la cel mai apropiat nivel S/R din fața intrării. No RUN_DEFAULTS / default_exit: the backtest uses the live exits (incl. RESCUE).
Filter definition: app.patterns.p2.FILTERS['L1_SR']; parameters are cfg keys 'pat_*'."""
from __future__ import annotations

from . import _pat2 as P

NAME = "filt-L1_SR"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare (filtru pe trend-v1): cere spațiu ≥ D×ATR până la cel mai apropiat nivel S/R din fața intrării."
PARAMS = {'pat_N': 2, 'pat_D': 1.0, 'pat_strict': 0}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.filter_analyze("L1_SR", PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
