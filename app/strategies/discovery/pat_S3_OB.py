"""pat-S3_OB: Order block retest as a standalone strategy (pattern builder #2, research only).

BUY la retestul unui order block bullish (ultima bară bearish înaintea unui impuls ≥ m×ATR care sparge structura), SELL invers; SL dincolo de zonă, TP 2R.
Definition and causality: app.patterns.p2 (scan_s3_ob); parameters are cfg keys 'pat_*'."""
from __future__ import annotations

from . import _pat2 as P

NAME = "pat-S3_OB"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare (pattern): BUY la retestul unui order block bullish (ultima bară bearish înaintea unui impuls ≥ m×ATR care sparge structura), SELL invers; SL dincolo de zonă, TP 2R."
PARAMS = {'pat_m': 1.5, 'pat_tp_mode': '2R'}
RUN_DEFAULTS = {"break_even_at_r": 0, "close_at_session_end": False, "session": None, "htf_timeframe": "NONE"}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone_analyze("S3_OB", PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
