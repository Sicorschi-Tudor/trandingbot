"""pat-C4_3SOLDIERS: Three white soldiers / three black crows as a standalone strategy (pattern builder #0, research only).

Detector app.patterns.p0.detect_c4_3soldiers; levels and exits in _pat0.standalone (entry next open, structural SL,
R-multiple TP, time stop via max_hold_minutes)."""
from __future__ import annotations

from . import _pat0 as P

PID = "C4_3SOLDIERS"
NAME = "pat-C4_3SOLDIERS"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: tiparul Three white soldiers / three black crows folosit ca strategie de sine stătătoare."
PARAMS = {'pat_b': 0.3, 'pat_R': 2.0}
RUN_DEFAULTS = dict(P.RUN_DEFAULTS)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
