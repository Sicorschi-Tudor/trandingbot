"""pat-L3_PIVOTS: Pivoți zilnici P / R1 / S1 as a standalone strategy (pattern builder #1, research only).

Detector/levels app.patterns.p1 (setup_l3_pivots); entries and exits in _pat1.standalone (market entry at the next open,
structural SL, pattern TP / R multiple, time stop via max_hold_minutes)."""
from __future__ import annotations

from . import _pat1 as P

PID = "L3_PIVOTS"
NAME = "pat-L3_PIVOTS"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: tiparul Pivoți zilnici P / R1 / S1 folosit ca strategie de sine stătătoare."
PARAMS = {'pat_mode': 'rev'}
RUN_DEFAULTS = dict(P.RUN_DEFAULTS)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
