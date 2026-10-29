"""pat-P2_HS: Head & shoulders as a standalone strategy (pattern builder #2, research only).

SELL la spargerea liniei gâtului unui cap-umeri (BUY pentru inversul lui); SL = umărul drept ± 0,2×ATR, TP = proiecția înălțimii (max 4R).
Definition and causality: app.patterns.p2 (scan_p2_hs); parameters are cfg keys 'pat_*'."""
from __future__ import annotations

from . import _pat2 as P

NAME = "pat-P2_HS"
VERSION = "0.1"
DESCRIPTION_RO = "Cercetare (pattern): SELL la spargerea liniei gâtului unui cap-umeri (BUY pentru inversul lui); SL = umărul drept ± 0,2×ATR, TP = proiecția înălțimii (max 4R)."
PARAMS = {'pat_L': 3, 'pat_tol': 0.5}
RUN_DEFAULTS = {"break_even_at_r": 0, "close_at_session_end": False, "session": None, "htf_timeframe": "NONE"}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    return P.standalone_analyze("P2_HS", PARAMS, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
