"""Stage C: strategy plugins (app/strategies) — registry, trend-v1 identity, no lookahead, crafted setups, speed."""
import bisect
import random
import time

import pytest

from app import strategies as S
from app import strategy
from app.config import Settings
from app.strategy import AnalysisContext
from tests.conftest import EURUSD_INFO, make_bars, quote, zigzag

SET = Settings(_env_file=None)
CFG = SET.default_strategy()
RISK = SET.default_risk()
NEW = ["meanrev-v1", "breakout-v1", "pullback-v1"]
T0 = 1767571200  # 2026-01-05 00:00 UTC (Monday)
DAY0 = T0 + 86400


def ctx(**kw):
    base = dict(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.0, now_ts=T0)
    base.update(kw)
    return AnalysisContext(**base)


def walk(n, seed, start=1.08, vol=0.0003, prefix=None):
    """M5 random walk with regime drift; `prefix` keeps given bars and continues from them."""
    rnd = random.Random(seed)
    bars = list(prefix or [])
    c = bars[-1]["c"] if bars else start
    drift = 0.0
    for i in range(len(bars), n):
        if i % 200 == 0 or not bars:
            drift = rnd.choice([-1, 0, 1]) * vol * 0.15
        o = c
        c = round(o + drift + rnd.gauss(0, vol), 5)
        bars.append({"t": T0 + i * 300, "o": o, "h": round(max(o, c) + abs(rnd.gauss(0, vol / 2)), 5),
                     "l": round(min(o, c) - abs(rnd.gauss(0, vol / 2)), 5), "c": c})
    return bars


def htf_of(bars, sec=3600):
    out = []
    for b in bars:
        t = b["t"] - b["t"] % sec
        if out and out[-1]["t"] == t:
            x = out[-1]
            x["h"], x["l"], x["c"] = max(x["h"], b["h"]), min(x["l"], b["l"]), b["c"]
        else:
            out.append({"t": t, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]})
    return out


def q_at(c, sp=10):
    d = dict(EURUSD_INFO)
    d.update(bid=c, ask=round(c + sp * 1e-5, 5), spread_points=sp)
    return d


def window(bars, H, i, sig_len=400):
    """Closed bars at the close of bar i, like backtest.scan_symbol."""
    T = bars[i]["t"] + 300
    k = bisect.bisect_right([h["t"] + 3600 for h in H], T)
    return bars[max(0, i - sig_len + 1): i + 1], H[max(0, k - 200): k], T


def run(name, sig, htf, q=None, c=None, **over):
    return S.get(name).analyze("EURUSD", sig, htf, q or quote(bid=sig[-1]["c"]), S.merged_cfg(name, CFG, over),
                               RISK, c or ctx())


def steps(start, seq):
    out = [start]
    for s in seq:
        out.append(round(out[-1] + s, 5))
    return out


# ---- registry ---------------------------------------------------------------

def test_registry_interface():
    assert S.names() == ["trend-v1", "meanrev-v1", "breakout-v1", "pullback-v1"]
    assert S.get(None) is S.get("trend-v1")
    with pytest.raises(KeyError):
        S.get("nope")
    for d in S.describe():
        m = S.get(d["name"])
        assert d == {"name": m.NAME, "version": m.VERSION, "description": m.DESCRIPTION_RO, "params": m.PARAMS}
        assert isinstance(m.DESCRIPTION_RO, str) and m.DESCRIPTION_RO and callable(m.analyze)
    # live settings win over plugin defaults, per-run overrides win over both
    assert S.merged_cfg("trend-v1", CFG) == CFG
    m = S.merged_cfg("meanrev-v1", {**CFG, "bb_k": 2.5}, {"sl_atr_k": 0.5})
    assert m["bb_k"] == 2.5 and m["sl_atr_k"] == 0.5 and m["rsi_low"] == 30.0 and m["atr_sl_mult"] == CFG["atr_sl_mult"]


# ---- trend-v1 wrapper identity ----------------------------------------------

def test_trend_v1_wrapper_is_identical():
    rnd = random.Random(7)
    bars = walk(3000, 11)
    H = htf_of(bars)
    n_sig = 0
    for _ in range(250):
        i = rnd.randrange(150, len(bars))
        sig, htf, T = window(bars, H, i, rnd.randrange(90, 401))
        q = q_at(bars[i]["c"], rnd.choice([5, 10, 25, 40]))
        c = ctx(now_ts=T, has_bot_position=rnd.random() < 0.2, signals_this_session=rnd.randrange(0, 7),
                tick_age=rnd.choice([0.0, 3.0, 30.0]))
        cfg = dict(CFG, min_score=rnd.choice([3, 4, 5]))
        a = S.get("trend-v1").analyze("EURUSD", sig, htf, q, S.merged_cfg("trend-v1", cfg), RISK, c)
        b = strategy.analyze("EURUSD", sig, htf, q, cfg, RISK, c)
        assert a == b
        n_sig += a["direction"] != "HOLD"
    assert n_sig > 10  # the sample really exercised signal paths


# ---- shape / levels on random data ------------------------------------------

def _check_levels(name, a, q, cfg):
    point = q["point"]
    for k in strategy.analyze("EURUSD", [], [], q, CFG, RISK, ctx()):
        assert k in a
    for k in ("ema_fast", "ema_slow", "rsi", "atr", "macd_hist", "htf_trend"):
        assert k in a["indicators"]
    cand = a["candidate"]
    if cand:
        assert set(cand) == {"side", "entry", "stop_loss", "take_profit", "score", "blockers"}
        sign = 1 if cand["side"] == "BUY" else -1
        assert sign * (cand["entry"] - cand["stop_loss"]) >= float(cfg["min_sl_points"]) * point - 1e-9
        assert sign * (cand["take_profit"] - cand["entry"]) > 0
        assert cand["entry"] == (q["ask"] if sign > 0 else q["bid"])
        assert (sign * cand["score"]) > 0
        assert (a["direction"] == "HOLD") == bool(cand["blockers"])
    if a["direction"] != "HOLD":
        sl_d, tp_d = abs(a["entry"] - a["stop_loss"]), abs(a["take_profit"] - a["entry"])
        tol = 0.006 + 2 * point * (1 + tp_d / sl_d) / sl_d  # 2-decimals rr + price rounding to digits
        assert a["rr"] == pytest.approx(tp_d / sl_d, abs=tol)
        if name == "pullback-v1":
            assert a["rr"] == pytest.approx(cfg["tp_r"], abs=0.006)
        if name == "breakout-v1":
            assert tp_d == pytest.approx(cfg["tp_range_mult"] * a["indicators"]["range_width"], abs=2 * point)
        if name == "meanrev-v1":
            assert a["take_profit"] == pytest.approx(a["indicators"]["bb_mid"], abs=point)
            assert a["rr"] >= cfg["min_rr"] - 0.01
    else:
        assert a["entry"] is None and a["rr"] is None


@pytest.mark.parametrize("name", NEW)
def test_plugin_shape_and_levels_on_random_walk(name):
    found = 0
    for seed in (1, 2, 3):
        bars = walk(4000, seed)
        H = htf_of(bars)
        cfg = S.merged_cfg(name, CFG)
        for i in range(800, len(bars), 3):
            sig, htf, T = window(bars, H, i)
            q = q_at(bars[i]["c"])
            a = S.get(name).analyze("EURUSD", sig, htf, q, cfg, RISK, ctx(now_ts=T))
            _check_levels(name, a, q, cfg)
            found += a["candidate"] is not None
    assert found > 0


@pytest.mark.parametrize("name", S.names())
def test_insufficient_data_and_no_quote(name):
    sig = make_bars(zigzag(50, 0.0003, 0.0002))
    htf = make_bars(zigzag(80, 0.003, 0.002), 3600)
    a = run(name, sig, htf)
    assert a["direction"] == "HOLD" and a["candidate"] is None and "insuficiente" in a["reasons"][0]
    a = S.get(name).analyze("EURUSD", make_bars(zigzag(150, 0.0003, 0.0002)), htf, None, CFG, RISK, ctx())
    assert a["direction"] == "HOLD" and "cotație" in a["reasons"][0]


# ---- no lookahead -------------------------------------------------------------

@pytest.mark.parametrize("name", S.names())
def test_no_lookahead(name):
    """Past decisions are identical whatever bars come after them, and (new plugins) whatever window length."""
    a_bars = walk(3000, 5)
    b_bars = walk(3000, 99, prefix=a_bars[:2000])  # same past, different future
    HA, HB = htf_of(a_bars), htf_of(b_bars)
    cfg = S.merged_cfg(name, CFG)
    m = S.get(name)
    for i in range(400, 1999, 7):
        sa, ha, T = window(a_bars, HA, i)
        sb, hb, _ = window(b_bars, HB, i)
        q, c = q_at(a_bars[i]["c"]), ctx(now_ts=T)
        ra = m.analyze("EURUSD", sa, ha, q, cfg, RISK, c)
        assert ra == m.analyze("EURUSD", sb, hb, q, cfg, RISK, c)
        if name != "trend-v1":
            long_sig = a_bars[: i + 1]
            assert ra == m.analyze("EURUSD", long_sig, ha, q, cfg, RISK, c)


# ---- crafted scenarios --------------------------------------------------------

FLAT_H = make_bars(zigzag(80, 0.0010, 0.0010), 3600, wick=0.0003)
UP_H = make_bars(zigzag(80, 0.0030, 0.0010), 3600, wick=0.0005)
DOWN_H = make_bars(steps(1.10, [-0.0030 if i % 2 == 0 else 0.0010 for i in range(79)]), 3600, wick=0.0005)


def meanrev_bars(sgn):
    return make_bars(steps(1.08, [0.0003 if i % 2 == 0 else -0.0003 for i in range(150)] + [sgn * 0.0006] * 5))


@pytest.mark.parametrize("sgn,side", [(-1, "BUY"), (1, "SELL")])
def test_meanrev_fades_extremes_in_flat_htf(sgn, side):
    sig = meanrev_bars(sgn)
    a = run("meanrev-v1", sig, FLAT_H)
    assert a["direction"] == side, a["reasons"]
    ind = a["indicators"]
    assert ind["htf_trend"] == "FLAT"
    if side == "BUY":
        assert sig[-1]["c"] < ind["bb_lo"] and ind["rsi"] < 30
        assert a["stop_loss"] < min(ind["bb_lo"], sig[-1]["l"]) < a["entry"] < a["take_profit"]
    else:
        assert sig[-1]["c"] > ind["bb_up"] and ind["rsi"] > 70
        assert a["take_profit"] < a["entry"] < max(ind["bb_up"], sig[-1]["h"]) < a["stop_loss"]
    assert a["take_profit"] == pytest.approx(ind["bb_mid"], abs=1e-5)


def test_meanrev_ignored_in_trending_htf_and_blocked_candidate():
    a = run("meanrev-v1", meanrev_bars(-1), UP_H)
    assert a["direction"] == "HOLD" and a["candidate"] is None and "nu este plat" in a["reasons"][0]
    a = run("meanrev-v1", meanrev_bars(-1), FLAT_H, c=ctx(has_bot_position=True))
    assert a["direction"] == "HOLD" and a["candidate"]["side"] == "BUY"
    assert any("poziție a botului" in b for b in a["candidate"]["blockers"])
    a = run("meanrev-v1", meanrev_bars(-1), FLAT_H, min_rr=10)
    assert a["direction"] == "HOLD" and any("RR" in b for b in a["candidate"]["blockers"])


def breakout_bars(last_close, hour=7, broken_before=False):
    """Day DAY0: bars oscillate inside 1.0795..1.0805 from the previous day to the signal bar at `hour`:00 UTC."""
    end_t = DAY0 + hour * 3600
    n = 160
    closes = [1.0797 if i % 2 == 0 else 1.0803 for i in range(n - 1)] + [last_close]
    if broken_before:
        closes[-4] = 1.0812 if last_close > 1.08 else 1.0788
    return make_bars(closes, 300, end_t=end_t, wick=0.0002)


@pytest.mark.parametrize("close,htf,side", [(1.0815, UP_H, "BUY"), (1.0815, FLAT_H, "BUY"),
                                            (1.0785, DOWN_H, "SELL"), (1.0785, FLAT_H, "SELL")])
def test_breakout_side(close, htf, side):
    sig = breakout_bars(close)
    a = run("breakout-v1", sig, htf)
    assert a["direction"] == side, a["reasons"]
    ind = a["indicators"]
    hi, lo = ind["range_high"], ind["range_low"]
    assert hi == pytest.approx(1.0805) and lo == pytest.approx(1.0795)
    mid = (hi + lo) / 2
    assert a["stop_loss"] == pytest.approx(mid, abs=1e-5)  # SL at midpoint (within the ATR cap here)
    sign = 1 if side == "BUY" else -1
    assert sign * (a["take_profit"] - a["entry"]) == pytest.approx(1.5 * (hi - lo), abs=2e-5)


def test_breakout_filters():
    # against the HTF trend: the day is skipped
    a = run("breakout-v1", breakout_bars(1.0815), DOWN_H)
    assert a["direction"] == "HOLD" and a["candidate"] is None and "contra trendului" in a["reasons"][0]
    # already broken earlier today: one entry per day
    a = run("breakout-v1", breakout_bars(1.0815, broken_before=True), UP_H)
    assert a["direction"] == "HOLD" and "deja spart" in a["reasons"][0]
    # outside the 06:00-10:00 UTC trade window
    a = run("breakout-v1", breakout_bars(1.0815, hour=11), UP_H)
    assert a["direction"] == "HOLD" and "ferestrei" in a["reasons"][0]
    # inside the range
    a = run("breakout-v1", breakout_bars(1.0801), UP_H)
    assert a["direction"] == "HOLD" and "în interval" in a["reasons"][0]
    # SL capped by ATR and the 'opposite' mode
    a = run("breakout-v1", breakout_bars(1.0815), UP_H, sl_max_atr=0.5)
    assert a["candidate"] and a["entry"] - a["stop_loss"] < a["entry"] - 1.08
    a = run("breakout-v1", breakout_bars(1.0815), UP_H, sl_mode="opposite", sl_max_atr=0)
    assert a["stop_loss"] == pytest.approx(1.0795, abs=1e-5)
    # context blocker keeps the candidate
    a = run("breakout-v1", breakout_bars(1.0815), UP_H, c=ctx(session_open=False))
    assert a["direction"] == "HOLD" and a["candidate"]["side"] == "BUY"
    assert any("Sesiunea" in b for b in a["candidate"]["blockers"])


def pullback_bars(sgn):
    seq = [sgn * (0.0003 if i % 2 == 0 else -0.0001) for i in range(150)] + [-sgn * 0.0003] * 4 + [sgn * 0.0007]
    return make_bars(steps(1.08, seq))


@pytest.mark.parametrize("sgn,htf,side", [(1, UP_H, "BUY"), (-1, DOWN_H, "SELL")])
def test_pullback_side(sgn, htf, side):
    sig = pullback_bars(sgn)
    a = run("pullback-v1", sig, htf)
    assert a["direction"] == side, a["reasons"]
    assert a["rr"] == 2.0
    if side == "BUY":
        assert a["stop_loss"] < min(b["l"] for b in sig[-10:]) < a["entry"] < a["take_profit"]
    else:
        assert a["take_profit"] < a["entry"] < max(b["h"] for b in sig[-10:]) < a["stop_loss"]


def test_pullback_filters():
    # wrong HTF (flat / opposite): no setup
    assert run("pullback-v1", pullback_bars(1), FLAT_H)["direction"] == "HOLD"
    assert run("pullback-v1", pullback_bars(1), DOWN_H)["candidate"] is None
    # trend without pullback: steady uptrend never crosses RSI 50 on the last bar
    plain = make_bars(steps(1.08, [0.0003 if i % 2 == 0 else -0.0001 for i in range(155)]))
    a = run("pullback-v1", plain, UP_H)
    assert a["direction"] == "HOLD" and "pullback incomplet" in a["reasons"][0]
    # stop too wide vs ATR: candidate kept, blocked
    a = run("pullback-v1", pullback_bars(1), UP_H, sl_max_atr=0.5)
    assert a["direction"] == "HOLD" and any("prea larg" in b for b in a["candidate"]["blockers"])
    # spread / stale tick filters as in trend-v1
    sig = pullback_bars(1)
    a = run("pullback-v1", sig, UP_H, q=quote(bid=sig[-1]["c"], spread_pts=45))
    assert a["direction"] == "HOLD" and any("Spread 45" in b for b in a["candidate"]["blockers"])
    a = run("pullback-v1", sig, UP_H, c=ctx(tick_age=60.0))
    assert any("Cotație veche" in b for b in a["candidate"]["blockers"])


def test_min_sl_floor_applies_to_structural_stops():
    sig = pullback_bars(1)
    a = run("pullback-v1", sig, UP_H, min_sl_points=500)
    assert a["entry"] - a["stop_loss"] == pytest.approx(500 * 1e-5, abs=1e-5)
    assert a["rr"] == 2.0 and "minim impus" in a["reasons"][-1]


# ---- speed --------------------------------------------------------------------

def test_speed_per_call(capsys):
    bars = walk(2000, 3)
    H = htf_of(bars)
    wins = [window(bars, H, i) for i in range(400, 2000, 4)]
    res = {}
    for name in S.names():
        m, cfg = S.get(name), S.merged_cfg(name, CFG)
        t = time.perf_counter()
        for sig, htf, T in wins:
            m.analyze("EURUSD", sig, htf, q_at(sig[-1]["c"]), cfg, RISK, ctx(now_ts=T))
        res[name] = (time.perf_counter() - t) / len(wins) * 1000
    with capsys.disabled():
        print("\nms/call (400 sig + 200 htf bars): " + ", ".join(f"{k} {v:.3f}" for k, v in res.items()))
    for name in NEW:
        assert res[name] < 3.0  # target < 1 ms; generous bound for a loaded shared machine
