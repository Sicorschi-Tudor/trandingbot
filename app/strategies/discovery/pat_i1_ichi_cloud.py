"""pat-I1_ICHI_CLOUD: Ichimoku cloud breakout as a standalone strategy (pattern builder #0, research only).

Detector app.patterns.p0.detect_i1_ichi_cloud; levels and exits in _pat0.standalone (entry next open, structural SL,
R-multiple TP, time stop via max_hold_minutes, exit on signal_flip)."""
from __future__ import annotations

from . import _pat0 as P

PID = "I1_ICHI_CLOUD"
NAME = "pat-I1_ICHI_CLOUD"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: tiparul Ichimoku cloud breakout folosit ca strategie de sine stătătoare."
PARAMS = {'pat_conf': 'none', 'pat_scale': 1}
RUN_DEFAULTS = dict(P.RUN_DEFAULTS)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)


def default_exit(cfg):
    return {"mode": "signal_flip", "params": {}}


def bias(symbol, sig_bars, htf_bars, cfg) -> int:
    return P.bias(PID, PARAMS, sig_bars, cfg)
