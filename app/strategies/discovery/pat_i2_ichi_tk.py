"""pat-I2_ICHI_TK: Ichimoku: cross Tenkan/Kijun as a standalone strategy (pattern builder #1, research only).

Detector/levels app.patterns.p1 (setup_i2_ichi_tk); entries and exits in _pat1.standalone (market entry at the next open,
structural SL, pattern TP / R multiple, time stop via max_hold_minutes)."""
from __future__ import annotations

from . import _pat1 as P

PID = "I2_ICHI_TK"
NAME = "pat-I2_ICHI_TK"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: tiparul Ichimoku: cross Tenkan/Kijun folosit ca strategie de sine stătătoare."
PARAMS = {'pat_min_strength': 'strong', 'pat_R': 2.0}
RUN_DEFAULTS = dict(P.RUN_DEFAULTS)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
