"""trend-v1: thin wrapper over the live engine app.strategy.analyze (bit-identical output)."""
from __future__ import annotations

from .. import strategy

NAME = "trend-v1"
VERSION = "1.0"
DESCRIPTION_RO = ("Strategia live: EMA20/EMA50, RSI, MACD și trendul EMA50 pe H1; intrare la scor ≥ min_score, "
                  "SL = atr_sl_mult×ATR (minim min_sl_points), TP = atr_tp_mult×ATR.")
# same defaults as Settings (live values override these in merged_cfg)
PARAMS = {"min_score": 4, "atr_sl_mult": 1.5, "atr_tp_mult": 2.5, "min_sl_points": 60}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return strategy.analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
