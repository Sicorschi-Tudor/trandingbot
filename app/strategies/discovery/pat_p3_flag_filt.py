"""filt-P3_FLAG: live trend-v1 + Bull / bear flag breakout filter (pattern builder #0, research only).

The trend-v1 decision is bit-identical (app.strategy.analyze); a candidate the pattern does not confirm gets one
extra market blocker (_pat0.filtered), so the backtest runs the live exits (incl. RESCUE) on the filtered stream."""
from __future__ import annotations

from . import _pat0 as P

PID = "P3_FLAG"
NAME = "filt-P3_FLAG"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare: trend-v1 filtrat cu tiparul Bull / bear flag breakout."
PARAMS = {"min_score": 4, "atr_sl_mult": 1.5, "atr_tp_mult": 2.5, "min_sl_points": 60, **{'pat_p': 2.0, 'pat_max_flag_bars': 12, 'pat_K': 4, 'pat_fmode': 'confirm'}}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.filtered(PID, PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
