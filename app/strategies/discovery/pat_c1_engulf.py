"""pat-C1_ENGULF: Engulfing bullish/bearish as a standalone strategy (pattern builder #0, research only).

Detector app.patterns.p0.detect_c1_engulf; levels and exits in _pat0.standalone (entry next open, structural SL,
R-multiple TP, time stop via max_hold_minutes)."""
from __future__ import annotations

from . import _pat0 as P

PID = "C1_ENGULF"
NAME = "pat-C1_ENGULF"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: tiparul Engulfing bullish/bearish folosit ca strategie de sine stătătoare."
PARAMS = {'pat_m': 0.5, 'pat_R': 2.0, 'pat_local': True}
RUN_DEFAULTS = dict(P.RUN_DEFAULTS)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
