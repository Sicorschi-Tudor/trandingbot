"""pat-I6_VWAP: Session VWAP reversion as a standalone strategy (pattern builder #2, research only).

Revenire la VWAP-ul de sesiune după o întindere de k×σ, doar cu ADX < 25; SL = extrema sesiunii sau 1×ATR (max 2×ATR), TP = VWAP, închidere la sfârșitul sesiunii.
Definition and causality: app.patterns.p2 (scan_i6_vwap); parameters are cfg keys 'pat_*'."""
from __future__ import annotations

from ...patterns import p2
from . import _pat2 as P

NAME = "pat-I6_VWAP"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare (pattern): Revenire la VWAP-ul de sesiune după o întindere de k×σ, doar cu ADX < 25; SL = extrema sesiunii sau 1×ATR (max 2×ATR), TP = VWAP, închidere la sfârșitul sesiunii."
PARAMS = {'pat_anchor': 'day', 'pat_k': 1.5, 'pat_min_bars': 12, 'pat_adx_max': 25}
RUN_DEFAULTS = {"break_even_at_r": 0, "close_at_session_end": False, "session": None, "htf_timeframe": "NONE"}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone_analyze("I6_VWAP", PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)


def time_exit(opened_t, bar_t, bar_sec, n_bars, cfg):
    """Flat at the end of the VWAP session the trade was opened in (research hook, see _disc0.time_exits)."""
    from ._disc0 import deadline_rule
    deadline = p2.session_end_ts(int(opened_t), str(P.pattern_params(cfg, PARAMS).get("anchor", "day")))
    return deadline_rule(deadline, bar_t, bar_sec)
