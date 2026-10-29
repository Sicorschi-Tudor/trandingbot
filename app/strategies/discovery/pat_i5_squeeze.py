"""pat-I5_SQUEEZE: Squeeze Bollinger în Keltner (breakout) as a standalone strategy (pattern builder #1, research only).

Detector/levels app.patterns.p1 (setup_i5_squeeze); entries and exits in _pat1.standalone (market entry at the next open,
structural SL, pattern TP / R multiple, time stop via max_hold_minutes)."""
from __future__ import annotations

from . import _pat1 as P

PID = "I5_SQUEEZE"
NAME = "pat-I5_SQUEEZE"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: tiparul Squeeze Bollinger în Keltner (breakout) folosit ca strategie de sine stătătoare."
PARAMS = {'pat_S': 4, 'pat_kc_mult': 1.5, 'pat_R': 2.5}
RUN_DEFAULTS = dict(P.RUN_DEFAULTS)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
