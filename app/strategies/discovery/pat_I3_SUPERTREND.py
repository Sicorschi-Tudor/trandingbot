"""pat-I3_SUPERTREND: Supertrend flip as a standalone strategy (pattern builder #2, research only).

Stop-and-reverse pe Supertrend: BUY la flip în sus, SELL la flip în jos; SL = linia Supertrend, ieșire la flip-ul opus.
Definition and causality: app.patterns.p2 (scan_i3_supertrend); parameters are cfg keys 'pat_*'."""
from __future__ import annotations

from ...patterns import p2
from . import _pat2 as P

NAME = "pat-I3_SUPERTREND"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare (pattern): Stop-and-reverse pe Supertrend: BUY la flip în sus, SELL la flip în jos; SL = linia Supertrend, ieșire la flip-ul opus."
PARAMS = {'pat_n': 10, 'pat_mult': 3.0, 'pat_tp_r': 10.0}
RUN_DEFAULTS = {"break_even_at_r": 0, "close_at_session_end": False, "session": None, "htf_timeframe": "NONE"}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone_analyze("I3_SUPERTREND", PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)


def default_exit(cfg):
    """Stop-and-reverse: close at the bar close where the Supertrend flips against the trade."""
    return {"mode": "signal_flip", "params": {}}


def bias(symbol, sig_bars, htf_bars, cfg) -> int:
    pp = P.pattern_params(cfg, PARAMS)
    dirs, _, _ = p2.supertrend(list(sig_bars[-300:]), pp)
    return dirs[-1] if dirs else 0
