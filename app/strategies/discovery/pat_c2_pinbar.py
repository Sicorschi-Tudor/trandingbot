"""pat-C2_PINBAR: Pin bar / hammer / shooting star as a standalone strategy (pattern builder #1, research only).

Detector/levels app.patterns.p1 (setup_c2_pinbar); entries and exits in _pat1.standalone (market entry at the next open,
structural SL, pattern TP / R multiple, time stop via max_hold_minutes)."""
from __future__ import annotations

from . import _pat1 as P

PID = "C2_PINBAR"
NAME = "pat-C2_PINBAR"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: tiparul Pin bar / hammer / shooting star folosit ca strategie de sine stătătoare."
PARAMS = {'pat_w': 0.66, 'pat_R': 2.0}
RUN_DEFAULTS = dict(P.RUN_DEFAULTS)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
