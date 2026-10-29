"""pat-L1_SR: Support/resistance bounce as a standalone strategy (pattern builder #2, research only).

BUY la respingerea unui suport (pivoți grupați, ≥N atingeri), SELL la respingerea unei rezistențe; SL = nivel ∓ 0,5×ATR, TP = nivelul următor sau 2R.
Definition and causality: app.patterns.p2 (scan_l1_sr); parameters are cfg keys 'pat_*'."""
from __future__ import annotations

from . import _pat2 as P

NAME = "pat-L1_SR"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare (pattern): BUY la respingerea unui suport (pivoți grupați, ≥N atingeri), SELL la respingerea unei rezistențe; SL = nivel ∓ 0,5×ATR, TP = nivelul următor sau 2R."
PARAMS = {'pat_N': 2}
RUN_DEFAULTS = {"break_even_at_r": 0, "close_at_session_end": False, "session": None, "htf_timeframe": "NONE"}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone_analyze("L1_SR", PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
