"""filt-P4_TRIANGLE: live trend-v1 + Triunghi ascendent / descendent (breakout) filter (pattern builder #1, research only).

The trend-v1 decision is bit-identical (app.strategy.analyze); a candidate the pattern does not confirm gets one
extra market blocker (_pat1.filtered), so the backtest runs the live exits (incl. RESCUE) on the filtered stream."""
from __future__ import annotations

from . import _pat1 as P

PID = "P4_TRIANGLE"
NAME = "filt-P4_TRIANGLE"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: trend-v1 filtrat cu tiparul Triunghi ascendent / descendent (breakout)."
PARAMS = {"min_score": 4, "atr_sl_mult": 1.5, "atr_tp_mult": 2.5, "min_sl_points": 60, **{'pat_W': 60, 'pat_tol': 0.5, 'pat_K': 6}}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.filtered(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
