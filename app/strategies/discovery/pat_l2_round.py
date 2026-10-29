"""pat-L2_ROUND: Round numbers 00/50 as a standalone strategy (pattern builder #0, research only).

Detector app.patterns.p0.detect_l2_round; levels and exits in _pat0.standalone (entry next open, structural SL,
R-multiple TP, time stop via max_hold_minutes)."""
from __future__ import annotations

from . import _pat0 as P

PID = "L2_ROUND"
NAME = "pat-L2_ROUND"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: tiparul Round numbers 00/50 folosit ca strategie de sine stătătoare."
PARAMS = {'pat_step': 50}
RUN_DEFAULTS = dict(P.RUN_DEFAULTS)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
