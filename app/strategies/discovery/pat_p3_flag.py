"""pat-P3_FLAG: Bull / bear flag breakout as a standalone strategy (pattern builder #0, research only).

Detector app.patterns.p0.detect_p3_flag; levels and exits in _pat0.standalone (entry next open, structural SL,
R-multiple TP, time stop via max_hold_minutes)."""
from __future__ import annotations

from . import _pat0 as P

PID = "P3_FLAG"
NAME = "pat-P3_FLAG"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: tiparul Bull / bear flag breakout folosit ca strategie de sine stătătoare."
PARAMS = {'pat_p': 2.0, 'pat_max_flag_bars': 12}
RUN_DEFAULTS = dict(P.RUN_DEFAULTS)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
