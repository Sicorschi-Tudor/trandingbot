"""fade-v1: exact mirror of trend-v1 (side inverted, SL/TP distances swapped, blockers propagated, no lookahead),
registry placement (forward-only, never live) and the pre-registered exit (no BE, no trailing, 240-minute time stop)."""
import pytest

from app import backtest, exits, strategy
from app import strategies as S
from app.config import Settings
from app.strategies import fade_v1
from app.strategy import AnalysisContext
from tests.conftest import make_bars, quote, zigzag
from tests.test_stage_b_backtest import T0, db, resample, walk  # noqa: F401

SET = Settings(_env_file=None)
CFG = SET.default_strategy()
RISK = SET.default_risk()
PT = 0.00001


def ctx(**kw):
    base = dict(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.5, now_ts=T0)
    base.update(kw)
    return AnalysisContext(**base)


def zigzag_down(n, down=0.0003, up=0.0002, start=1.0800):
    closes = [start]
    for i in range(n - 1):
        closes.append(round(closes[-1] + (-down if i % 2 == 0 else up), 5))
    if closes[-1] > closes[-2]:
        closes.append(round(closes[-1] - down, 5))
    return closes


def up_data():
    sig = make_bars(zigzag(150, 0.0003, 0.0002, end_up=True), 300)
    htf = make_bars(zigzag(80, 0.0030, 0.0020, end_up=True), 3600, wick=0.0005)
    return sig, htf, quote(bid=sig[-1]["c"])


def down_data():
    sig = make_bars(zigzag_down(150), 300)
    htf = make_bars(zigzag_down(80, 0.0030, 0.0020), 3600, wick=0.0005)
    return sig, htf, quote(bid=sig[-1]["c"])


def both(sig, htf, q, c=None, risk=RISK, cfg=CFG):
    c = c or ctx()
    return (strategy.analyze("EURUSD", sig, htf, q, cfg, risk, c),
            fade_v1.analyze("EURUSD", sig, htf, q, cfg, risk, c))


def dists(a):
    return abs(a["entry"] - a["stop_loss"]), abs(a["take_profit"] - a["entry"])


# ---- exact mirroring -------------------------------------------------------------------------

@pytest.mark.parametrize("data,t_side,f_side", [(up_data, "BUY", "SELL"), (down_data, "SELL", "BUY")])
def test_mirror_side_and_levels(data, t_side, f_side):
    sig, htf, q = data()
    t, f = both(sig, htf, q)
    assert t["direction"] == t_side, t["reasons"]
    assert f["direction"] == f_side, f["reasons"]
    # same reference quote: BUY at ask, SELL at bid
    assert f["entry"] == (q["ask"] if f_side == "BUY" else q["bid"])
    t_sl, t_tp = dists(t)
    f_sl, f_tp = dists(f)
    assert f_sl == pytest.approx(t_tp, abs=PT / 2) and f_tp == pytest.approx(t_sl, abs=PT / 2)
    sign = 1 if f_side == "BUY" else -1
    assert f["stop_loss"] == round(f["entry"] - sign * t_tp, 5)
    assert f["take_profit"] == round(f["entry"] + sign * t_sl, 5)
    assert (f["stop_loss"] < f["entry"] < f["take_profit"]) == (f_side == "BUY")
    assert f["rr"] == pytest.approx(CFG["atr_sl_mult"] / CFG["atr_tp_mult"], abs=0.01)
    assert f["score"] == -t["score"] and f["probabilities"] == strategy.probabilities(-t["score"])
    fc = f["candidate"]
    assert fc == {"side": f_side, "entry": f["entry"], "stop_loss": f["stop_loss"], "take_profit": f["take_profit"],
                  "score": f["score"], "blockers": []}
    # same output shape as trend-v1, same indicators/regime/bar
    assert set(f) == set(t)
    assert (f["indicators"], f["regime"], f["bar_time"], f["timeframe"]) == (t["indicators"], t["regime"],
                                                                             t["bar_time"], t["timeframe"])
    assert any("Oglinda trend-v1" in r for r in f["reasons"])


def test_no_setup_no_mirror():
    flat = make_bars([1.08] * 150, 300)
    htf = make_bars([1.08] * 80, 3600)
    t, f = both(flat, htf, quote(bid=1.08))
    assert t["candidate"] is None and f["candidate"] is None and f["direction"] == "HOLD"
    assert f["entry"] is None and f["stop_loss"] is None and f["score"] == t["score"]
    sig, htf, q = up_data()
    t, f = both(sig[:50], htf, q)  # insufficient data -> HOLD, no candidate
    assert f["direction"] == "HOLD" and f["candidate"] is None


def test_min_sl_floor_mirrored():
    sig, htf, q = up_data()
    cfg = dict(CFG, min_sl_points=2000)  # floor dominates the ATR stop; TP keeps the RR
    t, f = both(sig, htf, q, cfg=cfg)
    assert t["direction"] == "BUY" and f["direction"] == "SELL"
    t_sl, t_tp = dists(t)
    assert t_sl == pytest.approx(2000 * PT, abs=PT)
    assert dists(f) == pytest.approx((t_tp, t_sl), abs=PT / 2)


# ---- blocked propagation -------------------------------------------------------------------

@pytest.mark.parametrize("kw,risk_kw,needle", [
    ({}, {"max_spread_points": 1}, "Spread"),
    ({"tick_age": 99.0}, {}, "Cotație veche"),
])
def test_market_blockers_propagate_with_candidate(kw, risk_kw, needle):
    sig, htf, q = up_data()
    risk = dict(RISK, **risk_kw)
    t, f = both(sig, htf, q, c=ctx(**kw), risk=risk)
    assert t["direction"] == "HOLD" and t["candidate"]["side"] == "BUY"
    assert f["direction"] == "HOLD" and f["entry"] is None and f["rr"] is None
    fc = f["candidate"]
    assert fc["side"] == "SELL" and fc["blockers"] == t["candidate"]["blockers"]
    assert any(needle in b for b in fc["blockers"])
    # levels still mirrored while blocked
    assert abs(fc["entry"] - fc["stop_loss"]) == pytest.approx(abs(t["candidate"]["take_profit"] - t["candidate"]["entry"]),
                                                               abs=PT / 2)
    assert any("blocat" in r for r in f["reasons"])


def test_stops_level_blocker_symmetric():
    sig, htf, q = up_data()
    q = dict(q, stops_level=100000)
    t, f = both(sig, htf, q)
    assert t["candidate"]["blockers"] and f["candidate"]["blockers"] == t["candidate"]["blockers"]


def test_context_blockers_use_fade_side_and_book():
    sig, htf, q = up_data()  # trend-v1 BUY EURUSD -> fade-v1 SELL EURUSD
    # long-EUR exposure blocks trend-v1 BUY, not the fade SELL
    t, f = both(sig, htf, q, c=ctx(open_exposures=[("EURGBP", "BUY")]))
    assert t["direction"] == "HOLD" and f["direction"] == "SELL" and f["candidate"]["blockers"] == []
    # short-EUR exposure blocks the fade SELL (correlation 1 per currency+direction on fade-v1's own book)
    t, f = both(sig, htf, q, c=ctx(open_exposures=[("EURGBP", "SELL")]))
    assert t["direction"] == "BUY" and f["direction"] == "HOLD"
    assert any("Expunere corelată" in b for b in f["candidate"]["blockers"])
    for kw, needle in (({"has_bot_position": True}, "poziție"), ({"session_open": False}, "Sesiunea"),
                       ({"signals_this_session": 999}, "Limita")):
        t, f = both(sig, htf, q, c=ctx(**kw))
        assert f["direction"] == "HOLD" and f["candidate"]["side"] == "SELL"
        assert any(needle in b for b in f["candidate"]["blockers"]), (kw, f["candidate"]["blockers"])
    # no news blackout in the pre-registered definition: a news ctx never blocks the fade
    t, f = both(sig, htf, q, c=ctx(news_block="Știre USD"))
    assert f["direction"] == "SELL" and f["candidate"]["blockers"] == []


def test_mirror_matches_trend_on_random_walk():
    """On every bar: fade candidate = mirror(trend candidate under a neutral ctx); blockers = market + own context."""
    m5 = walk(900, 300, T0, 5)
    h1 = resample(m5, 3600)
    checked = 0
    for i in range(300, 900, 3):
        T = m5[i]["t"] + 300
        sig = m5[max(0, i - 399): i + 1]
        htf = [b for b in h1 if b["t"] + 3600 <= T][-200:]
        q = quote(bid=sig[-1]["c"], spread_pts=3)
        real = ctx(now_ts=T, tick_age=0.0, open_exposures=[("GBPUSD", "BUY"), ("USDJPY", "SELL")])
        neutral = strategy.analyze("EURUSD", sig, htf, q, CFG, RISK, ctx(now_ts=T, tick_age=0.0))
        f = fade_v1.analyze("EURUSD", sig, htf, q, CFG, RISK, real)
        nc = neutral["candidate"]
        if nc is None:
            assert f["candidate"] is None and f["direction"] == "HOLD"
            continue
        checked += 1
        fc = f["candidate"]
        assert fc["side"] == fade_v1.OPPOSITE[nc["side"]]
        assert abs(fc["entry"] - fc["stop_loss"]) == pytest.approx(abs(nc["take_profit"] - nc["entry"]), abs=PT / 2)
        assert abs(fc["take_profit"] - fc["entry"]) == pytest.approx(abs(nc["entry"] - nc["stop_loss"]), abs=PT / 2)
        assert fc["blockers"] == nc["blockers"] + strategy.context_blockers("EURUSD", fc["side"], real, RISK)
        assert (f["direction"] != "HOLD") == (not fc["blockers"])
    assert checked > 10


# ---- no lookahead ----------------------------------------------------------------------------

def test_no_lookahead():
    """The decision at bar i depends only on bars <= i: future bars (even absurd ones) change nothing."""
    m5 = walk(1200, 300, T0, 5)
    h1 = resample(m5, 3600)
    seen = 0
    for i in range(750, 1150, 5):
        T = m5[i]["t"] + 300
        htf = [b for b in h1 if b["t"] + 3600 <= T]
        q = quote(bid=m5[i]["c"], spread_pts=3)
        c = ctx(now_ts=T, tick_age=0.0)
        a = fade_v1.analyze("EURUSD", m5[: i + 1], htf, q, CFG, RISK, c)
        future = [dict(b, h=b["h"] + 0.05, c=b["c"] + 0.04) for b in m5[i + 1:]]
        b_ = fade_v1.analyze("EURUSD", m5[: i + 1] + future[:0], htf, q, CFG, RISK, c)
        assert a == b_
        # poisoning the bars after i in the source list does not leak into the analysis of the prefix
        poisoned = m5[: i + 1] + future
        assert fade_v1.analyze("EURUSD", poisoned[: i + 1], htf, q, CFG, RISK, c) == a
        seen += a["candidate"] is not None
    assert seen > 0


def test_backtest_scan_uses_closed_bars_only(db):  # noqa: F811
    """scan_symbol on fade-v1: a decision at bar i is unchanged when every bar after i is replaced."""
    m5 = walk(1200, 300, T0, 5)
    h1 = resample(m5, 3600)
    cfg = dict(CFG, signal_timeframe="M5", htf_timeframe="H1")

    def job(bars):
        return {"symbol": "EURUSD", "strategy": cfg, "risk": RISK, "point": PT, "digits": 5, "stops_level": 0,
                "sig": [(b["t"], b["o"], b["h"], b["l"], b["c"]) for b in bars],
                "htf": [(b["t"], b["o"], b["h"], b["l"], b["c"]) for b in h1],
                "spreads": [3] * len(bars), "idx": list(range(750, 1150)), "strategy_name": "fade-v1"}

    base = backtest.scan_symbol(job(m5))["decisions"]
    assert base
    cut = 950
    poisoned = m5[: cut + 1] + [dict(b, o=b["o"] + 0.03, h=b["h"] + 0.05, l=b["l"] + 0.01, c=b["c"] + 0.04)
                                for b in m5[cut + 1:]]
    other = backtest.scan_symbol(job(poisoned))["decisions"]
    assert {i: v for i, v in base.items() if i <= cut} == {i: v for i, v in other.items() if i <= cut}
    # and every fade decision is the opposite of trend-v1's at the same bar
    trend = backtest.scan_symbol({**job(m5), "strategy_name": "trend-v1"})["decisions"]
    assert set(trend) == set(base)
    for i, (side, *_rest) in base.items():
        assert side == fade_v1.OPPOSITE[trend[i][0]]


# ---- registry and exit policy ------------------------------------------------------------------

def test_registry_forward_only():
    assert "fade-v1" not in S.names(include_research=True) and "fade-v1" not in S.research_names()
    assert S.forward_names() == ["fade-v1"] and S.get("fade-v1") is fade_v1
    assert "fade-v1" in [d["name"] for d in S.describe()]
    assert fade_v1.PARAMS["time_stop_minutes"] == 240 and fade_v1.PARAMS["break_even_at_r"] == 0
    for k in ("min_score", "atr_sl_mult", "atr_tp_mult", "min_sl_points"):
        assert k in fade_v1.PARAMS
    # live strategy params win over plugin defaults (same params as trend-v1)
    m = S.merged_cfg("fade-v1", {**CFG, "min_score": 5, "atr_sl_mult": 2.0})
    assert m["min_score"] == 5 and m["atr_sl_mult"] == 2.0
    pol = exits.ExitPolicy.from_spec(fade_v1.default_exit(m))
    assert pol.is_base and pol.hold_minutes == 240


def test_backtest_resolve_no_be_no_trailing_time_stop(db):  # noqa: F811
    db.execute("INSERT OR REPLACE INTO settings(key, value_json) VALUES('strategy', ?)",
               ('{"break_even_at_r": 1.0, "trailing_enabled": true, "max_hold_minutes": 0}',))
    r = backtest.resolve(db, {"strategy_name": "fade-v1", "symbols": ["EURUSD"], "start_ts": T0,
                              "end_ts": T0 + 86400, "now_ts": T0 + 86400})
    assert r["strategy"]["break_even_at_r"] == 0 and r["strategy"]["trailing_enabled"] is False
    assert r["strategy"]["signal_timeframe"] == "H1" and r["strategy"]["htf_timeframe"] == "H4"
    assert r["max_hold_s"] == 240 * 60 and r["exit"].is_base
