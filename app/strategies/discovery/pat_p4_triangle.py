"""pat-P4_TRIANGLE: Triunghi ascendent / descendent (breakout) as a standalone strategy (pattern builder #1, research only).

Detector/levels app.patterns.p1 (setup_p4_triangle); entries and exits in _pat1.standalone (market entry at the next open,
structural SL, pattern TP / R multiple, time stop via max_hold_minutes)."""
from __future__ import annotations

from . import _pat1 as P

PID = "P4_TRIANGLE"
NAME = "pat-P4_TRIANGLE"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: tiparul Triunghi ascendent / descendent (breakout) folosit ca strategie de sine stătătoare."
PARAMS = {'pat_W': 60, 'pat_tol': 0.5}
RUN_DEFAULTS = dict(P.RUN_DEFAULTS)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
