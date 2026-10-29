"""pat-S1_SWEEP: Liquidity sweep of a confirmed swing as a standalone strategy (pattern builder #0, research only).

Detector app.patterns.p0.detect_s1_sweep; levels and exits in _pat0.standalone (entry next open, structural SL,
R-multiple TP, time stop via max_hold_minutes)."""
from __future__ import annotations

from . import _pat0 as P

PID = "S1_SWEEP"
NAME = "pat-S1_SWEEP"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: tiparul Liquidity sweep of a confirmed swing folosit ca strategie de sine stătătoare."
PARAMS = {'pat_L': 3, 'pat_min_pierce': 0.1}
RUN_DEFAULTS = dict(P.RUN_DEFAULTS)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
