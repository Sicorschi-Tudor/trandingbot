"""filt-L2_ROUND: live trend-v1 + Round numbers 00/50 filter (pattern builder #0, research only).

The trend-v1 decision is bit-identical (app.strategy.analyze); a candidate the pattern does not confirm gets one
extra market blocker (_pat0.filtered), so the backtest runs the live exits (incl. RESCUE) on the filtered stream."""
from __future__ import annotations

from . import _pat0 as P

PID = "L2_ROUND"
NAME = "filt-L2_ROUND"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: trend-v1 filtrat cu tiparul Round numbers 00/50."
PARAMS = {"min_score": 4, "atr_sl_mult": 1.5, "atr_tp_mult": 2.5, "min_sl_points": 60, **{'pat_step': 50, 'pat_D': 0.5}}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.filtered(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
