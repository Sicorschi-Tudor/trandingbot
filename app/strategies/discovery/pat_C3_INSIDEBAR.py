"""pat-C3_INSIDEBAR: Inside-bar breakout as a standalone strategy (pattern builder #2, research only).

BUY/SELL la deschiderea barei următoare după o închidere dincolo de bara-mamă (≥1 inside bar, range ≥ 0,6×ATR); SL la extrema opusă a barei-mamă, TP = R×risc.
Definition and causality: app.patterns.p2 (scan_c3_insidebar); parameters are cfg keys 'pat_*'."""
from __future__ import annotations

from . import _pat2 as P

NAME = "pat-C3_INSIDEBAR"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare (pattern): BUY/SELL la deschiderea barei următoare după o închidere dincolo de bara-mamă (≥1 inside bar, range ≥ 0,6×ATR); SL la extrema opusă a barei-mamă, TP = R×risc."
PARAMS = {'pat_min_inside': 1, 'pat_R': 2.0, 'pat_sl_mode': 'extreme'}
RUN_DEFAULTS = {"break_even_at_r": 0, "close_at_session_end": False, "session": None, "htf_timeframe": "NONE"}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone_analyze("C3_INSIDEBAR", PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
