"""pat-P1_DOUBLE: Double top / double bottom cu spargerea neckline-ului as a standalone strategy (pattern builder #1, research only).

Detector/levels app.patterns.p1 (setup_p1_double); entries and exits in _pat1.standalone (market entry at the next open,
structural SL, pattern TP / R multiple, time stop via max_hold_minutes)."""
from __future__ import annotations

from . import _pat1 as P

PID = "P1_DOUBLE"
NAME = "pat-P1_DOUBLE"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: tiparul Double top / double bottom cu spargerea neckline-ului folosit ca strategie de sine stătătoare."
PARAMS = {'pat_L': 3, 'pat_tol': 0.6}
RUN_DEFAULTS = dict(P.RUN_DEFAULTS)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
