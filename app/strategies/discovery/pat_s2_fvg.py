"""pat-S2_FVG: Fair value gap (umplere) as a standalone strategy (pattern builder #1, research only).

Detector/levels app.patterns.p1 (setup_s2_fvg); entries and exits in _pat1.standalone (market entry at the next open,
structural SL, pattern TP / R multiple, time stop via max_hold_minutes)."""
from __future__ import annotations

from . import _pat1 as P

PID = "S2_FVG"
NAME = "pat-S2_FVG"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: tiparul Fair value gap (umplere) folosit ca strategie de sine stătătoare."
PARAMS = {'pat_g': 0.2, 'pat_tp_mode': '2R'}
RUN_DEFAULTS = dict(P.RUN_DEFAULTS)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
