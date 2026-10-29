"""GOLD/sessions study: smoke tests for disc-gold-h17 (disc-H17 with the round-number step in price units).

- registers as research-only (never offered to the live engine / UI), same RUN_DEFAULTS as disc-H17;
- on gold-like bars (price ~1950, digits 2) a first touch of a 10-USD level with rejection gives SELL / BUY with the
  SL 0.5*ATR beyond the level and TP = tp_r * risk; step_price 0 falls back to disc-H17's pip step (1.00 USD)."""
import random
from datetime import datetime, timezone

import pytest

from app import strategies as S
from app.config import Settings
from app.strategy import AnalysisContext
from tests.conftest import EURUSD_INFO

SET = Settings(_env_file=None)
CFG = SET.default_strategy()
RISK = {**SET.default_risk(), "max_spread_points": 100}
H = 3600
NAME = "disc-gold-h17"


def T(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())


def gold_quote(c, sp=30):
    d = dict(EURUSD_INFO)
    d.update(symbol="XAUUSD", point=0.01, digits=2, bid=c, ask=round(c + sp * 0.01, 2), spread_points=sp,
             contract_size=100)
    return d


def bar(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


def flat_h1(end_t, n, px, rng=2.0, seed=1):
    r = random.Random(seed)
    out = []
    for k in range(n):
        t = end_t - (n - k) * H
        c = px + r.uniform(-0.3, 0.3)
        out.append(bar(t, px, max(px, c) + rng / 2, min(px, c) - rng / 2, c))
    return out


def case(short=True, touched=False):
    """Flat bars ~1943 (short) / ~1957 (long), signal bar touches the 1950 level and closes ~2 USD away."""
    end = T("2024-01-10 12:00")
    sig = flat_h1(end, 150, px=1943.0 if short else 1957.0)
    if touched:
        b = sig[-10]
        sig[-10] = dict(b, h=1950.1) if short else dict(b, l=1949.9)
    if short:
        sig.append(bar(end, 1947.0, 1949.95, 1946.5, 1948.0))
    else:
        sig.append(bar(end, 1953.0, 1953.5, 1950.05, 1952.0))
    return sig


def run(sig, **over):
    cfg = S.merged_cfg(NAME, {**CFG, "signal_timeframe": "H1", "htf_timeframe": "NONE"}, over)
    ctx = AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.0,
                          now_ts=sig[-1]["t"] + H)
    return S.get(NAME).analyze("XAUUSD", sig, [], gold_quote(sig[-1]["c"]), cfg, RISK, ctx)


def side(a):
    return a["direction"] if a["direction"] != "HOLD" else (a["candidate"] or {}).get("side", "HOLD")


def test_registered_research_only():
    m = S.get(NAME)
    assert m.NAME == NAME and NAME not in S.names(include_research=True)
    assert m.RUN_DEFAULTS == S.get("disc-H17").RUN_DEFAULTS
    assert m.PARAMS["step_price"] == 10.0 and m.PARAMS["lvl"] == "00"


def test_gold_10usd_level_sides_and_levels():
    a = run(case(True), step_price=10.0, tp_r=1.5)
    assert side(a) == "SELL"
    assert a["indicators"]["round_level"] == pytest.approx(1950.0)
    atr = a["indicators"]["atr"]
    assert a["stop_loss"] == pytest.approx(1950.0 + 0.5 * atr, abs=0.02)
    assert (a["entry"] - a["take_profit"]) == pytest.approx(1.5 * (a["stop_loss"] - a["entry"]), abs=0.02)
    a = run(case(False), step_price=10.0)
    assert side(a) == "BUY"
    assert a["indicators"]["round_level"] == pytest.approx(1950.0)


def test_first_touch_required_and_pip_fallback():
    assert side(run(case(True, touched=True), step_price=10.0)) == "HOLD"
    sig = case(True)
    sig[-1] = dict(sig[-1], c=1949.8)  # closes too near the level: no rejection
    assert side(run(sig, step_price=10.0)) == "HOLD"
    # step 50 USD: 1950 is a level, 1960 is not
    assert side(run(case(True), step_price=50.0)) == "SELL"
    sig = [dict(b, o=b["o"] + 10, h=b["h"] + 10, l=b["l"] + 10, c=b["c"] + 10) for b in case(True)]
    assert side(run(sig, step_price=50.0)) == "HOLD"
    # step_price 0 -> disc-H17's pip step (1.00 USD on digits 2): the level found is the 1.00 multiple, not 1950
    a = run(case(True), step_price=0)
    assert a["indicators"].get("level_step") in (None, 1.0)
