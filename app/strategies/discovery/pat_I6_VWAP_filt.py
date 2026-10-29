"""filt-I6_VWAP: the live trend-v1 with a Session VWAP reversion filter (pattern builder #2, research only).

trend-v1 output unchanged except that a candidate is blocked (extra blocker, direction HOLD) when the filter
disagrees: cere poziția corectă față de VWAP-ul de sesiune (mod side) sau o intrare nesupraextinsă (mod not_overextended). No RUN_DEFAULTS / default_exit: the backtest uses the live exits (incl. RESCUE).
Filter definition: app.patterns.p2.FILTERS['I6_VWAP']; parameters are cfg keys 'pat_*'."""
from __future__ import annotations

from . import _pat2 as P

NAME = "filt-I6_VWAP"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare (filtru pe trend-v1): cere poziția corectă față de VWAP-ul de sesiune (mod side) sau o intrare nesupraextinsă (mod not_overextended)."
PARAMS = {'pat_anchor': 'day', 'pat_k': 1.5, 'pat_mode': 'side', 'pat_min_bars': 12}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.filter_analyze("I6_VWAP", PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
