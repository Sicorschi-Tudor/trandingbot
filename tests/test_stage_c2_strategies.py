"""Stage C2: higher-timeframe trend plugins (donchian-v1, ma-trend-v1, tsmom-v1), exits 'channel' / 'signal_flip',
long-hold time stops, htf 'NONE' and swap/financing accounting in the backtest."""
import math
import random
import time

import pytest

from app import backtest, exits, labeler, strategy
from app import strategies as S
from app.config import Settings
from app.exits import ExitPolicy
from app.strategies import tsmom_v1
from app.strategy import AnalysisContext
from tests.conftest import EURUSD_INFO
from tests.test_stage_b_backtest import T0, db, sim_case, store, walk  # noqa: F401

SET = Settings(_env_file=None)
CFG = SET.default_strategy()
RISK = SET.default_risk()
C2 = ["donchian-v1", "ma-trend-v1", "tsmom-v1"]
DAY = 86400
H = 3600
PT = 0.00001


def ctx(**kw):
    base = dict(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.0, now_ts=T0)
    base.update(kw)
    return AnalysisContext(**base)


def q_at(c, sp=3, **info):
    d = dict(EURUSD_INFO)
    d.update(info)
    pt = d["point"]
    d.update(bid=c, ask=round(c + sp * pt, d["digits"]), spread_points=sp)
    return d


def cfg_of(name, **over):
    return S.merged_cfg(name, {**CFG, "signal_timeframe": "D1", "htf_timeframe": "NONE"}, over)


def run_p(name, sig, htf=None, q=None, c=None, **over):
    return S.get(name).analyze("EURUSD", sig, htf or [], q or q_at(sig[-1]["c"]), cfg_of(name, **over), RISK,
                               c or ctx(now_ts=sig[-1]["t"] + DAY))


def daily(closes, t0=T0 - 2000 * DAY, wick=0.0008):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append({"t": t0 + i * DAY, "o": o, "h": max(o, c) + wick, "l": min(o, c) - wick, "c": c, "sp": 3})
        prev = c
    return out


def d1_walk(n, seed, prefix=None, vol=0.006):
    """Weekday D1 random walk with persistent drift regimes; `prefix` keeps given bars and continues from them."""
    rng = random.Random(seed)
    bars = list(prefix or [])
    p = bars[-1]["c"] if bars else 1.10
    t = bars[-1]["t"] + DAY if bars else T0 - 1700 * DAY
    drift = 0.0
    while len(bars) < n:
        if (t // DAY + 3) % 7 < 5:
            if rng.random() < 0.03:
                drift = rng.gauss(0, vol * 0.3)
            o = p
            p = o + drift + rng.gauss(0, vol)
            bars.append({"t": t, "o": round(o, 5), "h": round(max(o, p) + abs(rng.gauss(0, vol / 2)), 5),
                         "l": round(min(o, p) - abs(rng.gauss(0, vol / 2)), 5), "c": round(p, 5), "sp": 3})
        t += DAY
    return bars


# ---- registry ----------------------------------------------------------------------------------

def test_registry_research_plugins():
    assert S.names() == ["trend-v1", "meanrev-v1", "breakout-v1", "pullback-v1"]  # live/UI list unchanged
    assert S.names(include_research=True)[-3:] == C2 and S.research_names() == C2
    names = [d["name"] for d in S.describe()]
    for n in C2:
        m = S.get(n)
        assert m.NAME == n and n in names and isinstance(m.DESCRIPTION_RO, str) and callable(m.analyze)
        assert callable(m.default_exit) and m.RUN_DEFAULTS["htf_timeframe"] == "NONE"
        ExitPolicy.from_spec(m.default_exit(cfg_of(n)))  # valid policy
    assert callable(S.get("tsmom-v1").bias) and callable(S.get("ma-trend-v1").bias)
    assert S.merged_cfg("donchian-v1", CFG, {"exit_n": 10})["exit_n"] == 10
    with pytest.raises(KeyError):
        S.get("nope-v1")


# ---- no lookahead / determinism -------------------------------------------------------------------

@pytest.mark.parametrize("name", C2)
def test_no_lookahead_and_window_independence(name):
    a_bars = d1_walk(2000, 5)
    b_bars = d1_walk(2000, 77, prefix=a_bars[:1500])  # same past, different future
    m = S.get(name)
    cfg = cfg_of(name)
    n_cand = 0
    for i in range(400, 1499):
        sa, sb = a_bars[max(0, i - 399): i + 1], b_bars[max(0, i - 399): i + 1]
        q, c = q_at(a_bars[i]["c"]), ctx(now_ts=a_bars[i]["t"] + DAY)
        ra = m.analyze("EURUSD", sa, [], q, cfg, RISK, c)
        assert ra == m.analyze("EURUSD", sb, [], q, cfg, RISK, c)
        assert ra == m.analyze("EURUSD", a_bars[: i + 1], [], q, cfg, RISK, c)  # longer window, same decision
        n_cand += ra["candidate"] is not None
        if hasattr(m, "bias"):
            assert m.bias("EURUSD", sa, [], cfg) == m.bias("EURUSD", sb, [], cfg) == \
                m.bias("EURUSD", a_bars[: i + 1], [], cfg)
    assert n_cand > 3  # the sample exercised setups


@pytest.mark.parametrize("name", C2)
def test_output_shape_same_as_live_engine(name):
    bars = d1_walk(600, 3)
    live = strategy.analyze("EURUSD", bars[-400:], [], q_at(bars[-1]["c"]), CFG, RISK, ctx())
    seen_dir = set()
    for i in range(420, 600):
        a = run_p(name, bars[i - 399: i + 1])
        assert set(a) == set(live)
        assert set(live["indicators"]) <= set(a["indicators"])
        seen_dir.add(a["direction"])
        if a["direction"] != "HOLD":
            sg = 1 if a["direction"] == "BUY" else -1
            assert (a["entry"] - a["stop_loss"]) * sg > 0 and (a["take_profit"] - a["entry"]) * sg > 0
            assert a["candidate"]["side"] == a["direction"] and not a["candidate"]["blockers"]
    assert "HOLD" in seen_dir and len(seen_dir) >= 2


def test_insufficient_data_and_no_quote():
    for n in C2:
        a = run_p(n, d1_walk(60, 1))
        assert a["direction"] == "HOLD" and "Date insuficiente" in a["reasons"][0]
        bars = d1_walk(400, 1)
        a = S.get(n).analyze("EURUSD", bars, [], None, cfg_of(n), RISK, ctx())
        assert a["direction"] == "HOLD" and a["candidate"] is None


# ---- crafted setups ------------------------------------------------------------------------------

def chop(n, base=1.10, amp=0.002):
    return [round(base + (amp if i % 2 else -amp), 5) for i in range(n)]


@pytest.mark.parametrize("sgn,side", [(1, "BUY"), (-1, "SELL")])
def test_donchian_breakout_levels(sgn, side):
    closes = chop(200) + [round(1.10 + sgn * 0.0060, 5)]
    a = run_p("donchian-v1", daily(closes))
    assert a["direction"] == side, a["reasons"]
    ind = a["indicators"]
    assert ind["entry_n"] == 55 and ind["exit_n"] == 20
    atr = ind["atr"]
    assert abs(a["entry"] - a["stop_loss"]) == pytest.approx(2.0 * atr, abs=2 * PT)
    assert abs(a["take_profit"] - a["entry"]) == pytest.approx(20.0 * atr, abs=2 * PT)
    # inside the channel -> HOLD; H4 uses a 100-bar channel
    assert run_p("donchian-v1", daily(chop(201)))["direction"] == "HOLD"
    assert run_p("donchian-v1", daily(closes), signal_timeframe="H4")["indicators"]["entry_n"] == 100
    assert run_p("donchian-v1", daily(closes), entry_n=30)["indicators"]["entry_n"] == 30


def test_plugins_honour_context_blockers():
    closes = chop(200) + [1.1060]
    bars = daily(closes)
    a = run_p("donchian-v1", bars, c=ctx(has_bot_position=True))
    assert a["direction"] == "HOLD" and a["candidate"]["side"] == "BUY"
    assert any("poziție a botului" in b for b in a["candidate"]["blockers"])
    a = run_p("donchian-v1", bars, c=ctx(open_exposures=[("EURGBP", "BUY")], corr_limit=1))
    assert a["direction"] == "HOLD" and any("Expunere corelată" in b for b in a["candidate"]["blockers"])
    a = run_p("donchian-v1", bars, q=q_at(1.1060, sp=500))
    assert a["direction"] == "HOLD" and any("Spread" in b for b in a["candidate"]["blockers"])


def ma_series(sgn):
    """Steady trend, one close just against EMA50, then a close back across it in the trend direction."""
    closes = [1.10 + sgn * 0.001 * i for i in range(300)]
    k = 2 / 51

    def ema50(cs):
        e = sum(cs[:50]) / 50
        for v in cs[50:]:
            e = v * k + e * (1 - k)
        return e
    closes.append(ema50(closes) - sgn * 0.002)
    e_prev = ema50(closes)
    closes.append(e_prev + sgn * 0.004)
    return [round(c, 5) for c in closes]


@pytest.mark.parametrize("sgn,side", [(1, "BUY"), (-1, "SELL")])
def test_ma_trend_cross_entry_and_bias(sgn, side):
    closes = ma_series(sgn)
    bars = daily(closes, wick=0.0003)
    a = run_p("ma-trend-v1", bars)
    assert a["direction"] == side, a["reasons"]
    assert abs(a["entry"] - a["stop_loss"]) == pytest.approx(3.0 * a["indicators"]["atr"], abs=2 * PT)
    assert S.get("ma-trend-v1").bias("EURUSD", bars, [], cfg_of("ma-trend-v1")) == sgn
    # the bar before (close against EMA50, no cross in the trend direction) -> HOLD, bias opposite
    prev = bars[:-1]
    assert run_p("ma-trend-v1", prev)["direction"] == "HOLD"
    assert S.get("ma-trend-v1").bias("EURUSD", prev, [], cfg_of("ma-trend-v1")) == -sgn
    # deep in the trend without a cross -> HOLD
    assert run_p("ma-trend-v1", bars[:-2])["direction"] == "HOLD"


@pytest.mark.parametrize("sgn,side", [(1, "BUY"), (-1, "SELL")])
def test_tsmom_sign_and_volatility_filter(sgn, side):
    rng = random.Random(9)
    closes = [round(1.10 * math.exp(sgn * 0.0015 * i + rng.gauss(0, 0.002)), 5) for i in range(300)]
    a = run_p("tsmom-v1", daily(closes))
    assert a["direction"] == side, a["reasons"]
    assert a["indicators"]["mom_z"] * sgn > 0.5 and tsmom_v1.bias("EURUSD", daily(closes), [], cfg_of("tsmom-v1")) == sgn
    # same sign but huge noise -> filtered (bias still reports the raw sign)
    noisy = [round(1.10 * math.exp(sgn * 0.00002 * i + (0.03 if i % 2 else -0.03)), 5) for i in range(300)]
    b = run_p("tsmom-v1", daily(noisy))
    assert b["direction"] == "HOLD" and "Momentum slab" in b["reasons"][-1]
    assert tsmom_v1.bias("EURUSD", daily(noisy), [], cfg_of("tsmom-v1")) == sgn
    assert run_p("tsmom-v1", daily(noisy), min_z=0.0)["direction"] == side


# ---- exit modes --------------------------------------------------------------------------------------

E, SL, TP = 1.10000, 1.09000, 1.13000


def bar(t, o, h, l, c, sp=0):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "sp": sp}


def new_pos(mode, params=None, side="BUY", history=(), bias_fn=None, sl=None, tp=None):
    if side == "BUY":
        sl, tp = sl or SL, tp or TP
    else:
        sl, tp = sl or 1.11000, tp or 1.07000
    p = labeler.SimPosition(side, E, sl, tp, PT, T0, be_at_r=0)
    exits.attach(p, ExitPolicy(mode, params or {}), history=list(history), bar_sec=DAY, bias_fn=bias_fn)
    return p


def test_channel_exit_excludes_current_bar_and_needs_full_channel():
    hist = [bar(T0 - (3 - k) * DAY, E, 1.1010, low, E) for k, low in enumerate((1.0995, 1.0990, 1.0992))]
    p = new_pos("channel", {"channel_n": 3}, history=hist)
    # low below the channel but close above the prior 3-bar low (1.0990) -> stays
    assert p.step(bar(T0, E, 1.1005, 1.0980, 1.0991), 0) is None
    # prior lows now 1.0990, 1.0992, 1.0980: close 1.0985 is above 1.0980 -> stays (its own low 1.0970 ignored)
    assert p.step(bar(T0 + DAY, 1.0991, 1.0995, 1.0970, 1.0985), 0) is None
    # prior lows 1.0992, 1.0980, 1.0970 -> close 1.0965 < 1.0970 -> CHANNEL at the close
    assert p.step(bar(T0 + 2 * DAY, 1.0985, 1.0990, 1.0960, 1.0965), 0) == ("CHANNEL", 1.0965)
    # without history the channel must fill first
    p2 = new_pos("channel", {"channel_n": 3})
    for k in range(3):
        assert p2.step(bar(T0 + k * DAY, E, 1.1001, 1.0999 - k * 0.0001, 1.0999 - k * 0.0002), 0) is None
    assert p2.step(bar(T0 + 3 * DAY, E, E, 1.0990, 1.0990), 0) == ("CHANNEL", 1.0990)


def test_channel_exit_sell_closes_at_ask():
    hist = [bar(T0 - (3 - k) * DAY, E, high, 1.0990, E) for k, high in enumerate((1.1005, 1.1010, 1.1008))]
    p = new_pos("channel", {"channel_n": 3}, side="SELL", history=hist)
    assert p.step(bar(T0, E, 1.1009, 1.0995, 1.1009), 0.0002) is None  # close 1.1009 < prior high 1.1010
    hit = p.step(bar(T0 + DAY, 1.1009, 1.1020, 1.1005, 1.1015), 0.0002)
    assert hit[0] == "CHANNEL" and hit[1] == pytest.approx(1.1017)  # SELL closes at ask = bid close + spread
    assert "CHANNEL" in exits.AT_CLOSE_REASONS and "FLIP" in exits.AT_CLOSE_REASONS


def test_signal_flip_exit_uses_only_the_current_closed_bar():
    seen = []
    plan = {T0: 1, T0 + DAY: 0, T0 + 2 * DAY: 1, T0 + 3 * DAY: -1, T0 + 4 * DAY: -1}

    def bias_fn(b):
        seen.append(b["t"])
        return plan[b["t"]]
    p = new_pos("signal_flip", bias_fn=bias_fn)
    for k in range(3):
        assert p.step(bar(T0 + k * DAY, E, 1.1010, 1.0995, 1.1005), 0) is None
    assert p.step(bar(T0 + 3 * DAY, E, 1.1010, 1.0995, 1.1003), 0) == ("FLIP", 1.1003)
    assert seen == [T0 + k * DAY for k in range(4)]  # never asked about a future bar
    # flip_on_flat closes on a 0 too
    p2 = new_pos("signal_flip", {"flip_on_flat": 1}, bias_fn=bias_fn)
    assert p2.step(bar(T0, E, 1.1010, 1.0995, 1.1005), 0) is None
    assert p2.step(bar(T0 + DAY, E, 1.1010, 1.0995, 1.1004), 0) == ("FLIP", 1.1004)
    # SELL: flips on +1; SL is still checked first inside the bar
    p3 = new_pos("signal_flip", side="SELL", bias_fn=lambda b: 1)
    assert p3.step(bar(T0, E, 1.1200, 1.0990, 1.1000), 0) == ("SL", 1.11)
    # no bias_fn (e.g. shadow labels): nothing happens
    p4 = new_pos("signal_flip")
    assert p4.step(bar(T0, E, 1.1010, 1.0995, 1.1005), 0) is None


def test_combo_trailing_and_flip_policy_and_hold_params():
    pol = ExitPolicy.from_spec(S.get("ma-trend-v1").default_exit(cfg_of("ma-trend-v1")))
    assert pol.modes == ("trailing", "signal_flip") and pol.p["trail_atr_mult"] == 3.0 and pol.p["trail_start_r"] == 0
    assert ExitPolicy("base", {"max_hold_days": 30}).hold_minutes == 30 * 1440
    assert ExitPolicy("channel", {"max_hold_hours": 36}).hold_minutes == 36 * 60
    assert ExitPolicy("base", {"max_hold_minutes": 0}).hold_minutes == 0
    assert ExitPolicy("base").hold_minutes is None and ExitPolicy("base", {"max_hold_days": 30}).is_base
    for bad in ({"max_hold_days": 61}, {"max_hold_minutes": -1}, {"max_hold_days": 1, "max_hold_hours": 2}):
        with pytest.raises(ValueError):
            ExitPolicy("base", bad)
    with pytest.raises(ValueError):
        ExitPolicy("channel", {"channel_n": 0})
    assert set(exits.DEFAULT_PARAMS) == set(exits.MODES)


# ---- swap accounting -------------------------------------------------------------------------------

def test_rollovers_wednesday_triple_and_weekend():
    mon = T0  # Monday 00:00 UTC (summer: broker midnight = 17:00 New York = 21:00 UTC)
    assert backtest.rollovers(mon, mon + 21 * H - 1) == 0
    assert backtest.rollovers(mon, mon + 21 * H) == 0  # closed at the rollover itself (D1 bar close)
    assert backtest.rollovers(mon, mon + 21 * H + 1) == 1  # held through the 21:00 UTC rollover
    assert backtest.rollovers(mon + 21 * H, mon + 23 * H) == 0  # opened at the rollover itself
    assert backtest.rollovers(mon + 2 * DAY + 20 * H, mon + 3 * DAY + H) == 3  # Wednesday night
    assert backtest.rollovers(mon + 4 * DAY + 20 * H, mon + 7 * DAY + H) == 1  # Friday + weekend
    assert backtest.rollovers(mon, mon + 7 * DAY) == 7  # a full week = 7 swap days
    assert backtest.rollovers(mon, mon + 28 * DAY) == 28


def h1_flat(n, px=1.10000, t0=T0):
    return [{"t": t0 + i * H, "o": px, "h": px + 0.00005, "l": px - 0.00005, "c": px, "sp": 0} for i in range(n)]


NOCOST = {"slippage_pts": 0, "commission_per_lot_usd": 0, "fixed_spread_pts": 0}


def swap_case(db, side, bars, sym="EURUSD", hold_h=74, swap=None, **kw):
    px = bars[0]["c"]
    pt = 0.001 if "JPY" in sym else PT
    sg = 1 if side == "BUY" else -1
    dec = {sym: {0: (side, px, round(px - sg * 100 * pt, 5), round(px + sg * 2000 * pt, 5), 0.8, ())}}
    cost = dict(NOCOST, **(swap if swap is not None else {"swap_mode": "table"}))
    return sim_case(db, dec, {sym: bars}, signal_tf="H1", end_ts=T0 + 20 * DAY, cost=cost,
                    exit={"mode": "base", "params": {"max_hold_hours": hold_h}}, strategy={"break_even_at_r": 0},
                    **kw)


def test_swap_long_short_sign_and_r_conversion(db):
    table = {"swap_mode": "table", "swap_pts_per_day": {"EURUSD": [-6, 2]}}
    res, _ = swap_case(db, "BUY", h1_flat(100), swap=table)
    (t,) = res["trades"]
    assert t["outcome"] == "TIME" and t["exit_t"] == T0 + 75 * H  # Thursday 03:00 UTC
    assert t["rollovers"] == 5  # Mon + Tue + Wed×3
    assert t["volume"] == pytest.approx(1.0)  # 100 USD risk over 100 pts at 1 USD/pt/lot
    assert t["swap_usd"] == pytest.approx(-6 * 5 * 1.0)
    assert t["swap_r"] == pytest.approx(-0.30) and t["gross_r"] == pytest.approx(0.0)
    assert t["r"] == pytest.approx(-0.30) and t["usd"] == pytest.approx(-30.0)
    assert res["equity"] == pytest.approx(10000 - 30.0)
    res, _ = swap_case(db, "SELL", h1_flat(100), swap=table)
    (t,) = res["trades"]
    assert t["swap_usd"] == pytest.approx(10.0) and t["swap_r"] == pytest.approx(0.10)  # positive carry
    # zero mode: reported, no effect
    res, _ = swap_case(db, "BUY", h1_flat(100), swap={"swap_mode": "zero", "swap_pts_per_day": {"EURUSD": [-6, 2]}})
    (t,) = res["trades"]
    assert t["swap_usd"] == 0 and t["rollovers"] == 5 and t["r"] == pytest.approx(0.0)


def test_swap_default_table_jpy_points(db):
    res, _ = swap_case(db, "BUY", h1_flat(60, px=150.000), sym="USDJPY", hold_h=30)
    (t,) = res["trades"]
    assert t["rollovers"] == 1
    # default -0.5 pip = -5 points of 0.001; R = -5 × 0.001 / 0.100
    assert t["swap_r"] == pytest.approx(-0.05, abs=1e-4)
    tv = 0.001 * 100000 / 150.0  # USD per point per lot (USD base)
    assert t["swap_usd"] == pytest.approx(-5 * tv * t["volume"], abs=0.01)
    assert t["r"] == pytest.approx(t["usd"] / (100 * tv * t["volume"]), abs=1e-3)


def test_swap_intrabar_exit_is_charged_conservatively(db):
    for sl_hour, n in ((20, 0), (21, 1)):  # T0 is in summer: rollover at 21:00 UTC, inside the 21:00 bar
        bars = h1_flat(60)
        bars[sl_hour] = dict(bars[sl_hour], l=1.0980)
        res, _ = swap_case(db, "BUY", bars, swap={"swap_mode": "table", "swap_pts_per_day": {"EURUSD": [-6, -6]}})
        (t,) = res["trades"]
        assert t["outcome"] == "SL" and t["exit_t"] == T0 + sl_hour * H and t["rollovers"] == n


def test_swap_off_adds_nothing(db):
    res_a, _ = swap_case(db, "BUY", h1_flat(100), swap={})
    res_b, _ = swap_case(db, "BUY", h1_flat(100), swap={"swap_mode": "off", "swap_pts_per_day": {"EURUSD": [-6, 2]}})
    (ta,) = res_a["trades"]
    assert "swap_usd" not in ta and "rollovers" not in ta and res_a["trades"] == res_b["trades"]
    cfg = backtest.resolve(db, {"symbols": ["EURUSD"], "start_ts": T0, "end_ts": T0 + DAY, "now_ts": T0 + DAY})
    assert not any(k.startswith("swap") for k in cfg["cost"]) and cfg["max_hold_s"] is None
    assert cfg["exit"].is_base and cfg["strategy"]["signal_timeframe"] == CFG["signal_timeframe"]
    for bad in ({"swap_mode": "x"}, {"swap_pts_per_day": {"EURUSD": [1]}}):
        with pytest.raises(ValueError):
            backtest.resolve(db, {"symbols": ["EURUSD"], "start_ts": T0, "end_ts": T0 + DAY, "cost": bad})


# ---- long holds ------------------------------------------------------------------------------------

def d1_flat(n, px=1.10000):
    return [{"t": T0 + i * DAY, "o": px, "h": px + 0.0002, "l": px - 0.0002, "c": px, "sp": 0} for i in range(n)]


def test_long_hold_time_stop_in_days(db):
    dec = {"EURUSD": {0: ("BUY", 1.1, 1.0990, 1.12, 0.8, ())}}
    base = dict(signal_tf="D1", htf_tf="NONE", end_ts=T0 + 200 * DAY, cost=NOCOST, strategy={"break_even_at_r": 0})
    res, _ = sim_case(db, dec, {"EURUSD": d1_flat(100)}, exit={"mode": "base", "params": {"max_hold_days": 30}}, **base)
    (t,) = res["trades"]
    assert t["outcome"] == "TIME" and t["exit_t"] - t["entry_t"] == 30 * DAY
    res, _ = sim_case(db, dec, {"EURUSD": d1_flat(100)}, exit={"mode": "base", "params": {"max_hold_days": 0}}, **base)
    assert res["trades"][0]["outcome"] == "END" and res["trades"][0]["exit_t"] == T0 + 100 * DAY
    # the strategy's own max_hold_minutes still works (and is bounded to 60 days)
    res, _ = sim_case(db, dec, {"EURUSD": d1_flat(100)}, **{**base, "strategy": {"break_even_at_r": 0,
                                                                             "max_hold_minutes": 10 * 1440}})
    assert res["trades"][0]["exit_t"] - res["trades"][0]["entry_t"] == 10 * DAY
    with pytest.raises(ValueError):
        backtest.resolve(db, {"symbols": ["EURUSD"], "start_ts": T0, "end_ts": T0 + DAY,
                              "strategy": {"max_hold_minutes": 61 * 1440}})


def test_resolve_htf_none_and_plugin_defaults(db):
    p = {"symbols": ["EURUSD"], "start_ts": T0, "end_ts": T0 + DAY, "now_ts": T0 + DAY}
    cfg = backtest.resolve(db, {**p, "strategy_name": "tsmom-v1"})
    st = cfg["strategy"]
    assert (st["signal_timeframe"], st["htf_timeframe"]) == ("D1", "NONE")
    assert st["max_hold_minutes"] == 0 and st["break_even_at_r"] == 0 and cfg["schedule"] is None
    assert cfg["exit"].modes == ("signal_flip",) and cfg["cost"]["swap_mode"] == "table"
    # run params win over plugin defaults
    cfg = backtest.resolve(db, {**p, "strategy_name": "donchian-v1", "signal_tf": "H4", "htf_tf": "d1",
                                "strategy": {"break_even_at_r": 1.0}, "cost": {"swap_mode": "zero"},
                                "session": {"start": "08:00", "end": "20:00", "tz": "UTC"},
                                "exit": {"mode": "trailing", "params": {}}})
    st = cfg["strategy"]
    assert (st["signal_timeframe"], st["htf_timeframe"], st["break_even_at_r"]) == ("H4", "D1", 1.0)
    assert cfg["schedule"]["start"] == "08:00" and cfg["exit"].modes == ("trailing",)
    assert cfg["cost"]["swap_mode"] == "zero"
    assert backtest.resolve(db, {**p, "strategy_name": "donchian-v1"})["exit"].to_dict() == \
        {"mode": "channel", "params": {"channel_n": 20.0}}
    with pytest.raises(ValueError, match="bias"):
        backtest.resolve(db, {**p, "exit": {"mode": "signal_flip"}})  # trend-v1 has no bias()
    with pytest.raises(ValueError, match="timeframe"):
        backtest.resolve(db, {**p, "htf_tf": "W1"})
    with pytest.raises(ValueError, match="timeframe"):
        backtest.resolve(db, {**p, "signal_tf": "NONE"})


# ---- full runs on D1 history ------------------------------------------------------------------------

def seed_d1(db, n=1100):
    out = {}
    for sym, seed, p0 in (("EURUSD", 21, 1.10), ("GBPUSD", 22, 1.30)):
        bars = d1_walk(n, seed)
        if p0 != 1.10:
            bars = [dict(b, o=round(b["o"] + 0.2, 5), h=round(b["h"] + 0.2, 5), l=round(b["l"] + 0.2, 5),
                         c=round(b["c"] + 0.2, 5)) for b in bars]
        store(db, sym, "D1", bars)
        out[sym] = bars
    return out


def d1_params(name, **kw):
    p = {"symbols": ["EURUSD", "GBPUSD"], "strategy_name": name, "end_ts": T0 - 150 * DAY,
         "start_ts": T0 - 1150 * DAY, "now_ts": T0 + 30 * DAY, "initial_equity": 10000, "risk_pct": 1.0,
         "workers": 1, "specs": {"EURUSD": {"point": PT, "digits": 5, "tick_value": 1.0},
                                 "GBPUSD": {"point": PT, "digits": 5, "tick_value": 1.0}},
         "cost": {"mode": "fixed", "fixed_spread_pts": 10},
         "risk": {"max_signals_per_symbol_per_session": 50, "max_trades_per_day": 50, "max_consecutive_losses": 99,
                  "max_daily_loss_pct": 50, "max_open_positions": 5, "max_total_open_risk_pct": 50,
                  "max_same_currency_direction": 2}}
    p.update(kw)
    return p


EXPECTED_EXITS = {"donchian-v1": "CHANNEL", "ma-trend-v1": None, "tsmom-v1": "FLIP"}
SPEED: dict[str, float] = {}


@pytest.mark.parametrize("name", C2)
def test_full_d1_run_per_plugin(db, name):
    seed_d1(db)
    t0 = time.perf_counter()
    r = backtest.run(db, d1_params(name))
    SPEED[name] = time.perf_counter() - t0
    r2 = backtest.run(db, d1_params(name))
    assert r2["trades"] == r["trades"] and r2["summary"] == r["summary"]  # deterministic
    pr = r["params"]
    assert pr["signal_tf"] == "D1" and pr["htf_tf"] == "NONE" and pr["session"] is None
    assert pr["cost"]["swap_mode"] == "table"
    s = r["summary"]
    assert s["trades"] > 3, r["counts"]
    assert s["swap_r"] == pytest.approx(sum(t["swap_r"] for t in r["trades"]), abs=1e-2) and s["swap_r"] < 0
    assert s["swap_usd"] < 0 and s["swap_days"] > s["trades"]
    for t in r["trades"]:
        assert t["rollovers"] == backtest.rollovers(t["entry_t"], t["exit_t"] + (DAY if t["outcome"] in
                                                    backtest.INTRABAR_REASONS else 0))
        assert t["swap_usd"] == pytest.approx(-5 * t["rollovers"] * t["volume"], abs=0.02)
    outcomes = set(r["by_outcome"])
    assert "TIME" not in outcomes and "BE" not in outcomes  # no time stop / break-even by default
    if EXPECTED_EXITS[name]:
        assert EXPECTED_EXITS[name] in outcomes, outcomes
    else:
        assert outcomes & {"TRAIL", "FLIP"}, outcomes
    assert any(t["exit_t"] - t["entry_t"] > 10 * DAY for t in r["trades"])  # long holds really happen
    # swap sensitivity: same trades, only swap differs
    z = backtest.run(db, d1_params(name, cost={"mode": "fixed", "fixed_spread_pts": 10, "swap_mode": "zero"}))
    assert [t["entry_t"] for t in z["trades"]] == [t["entry_t"] for t in r["trades"]]
    assert z["summary"]["swap_r"] == 0 and z["summary"]["avg_r"] >= s["avg_r"]


def test_full_run_no_lookahead_with_flip_exit(tmp_path):
    from app.db import DB
    from tests.test_stage_b_backtest import EXTRA_SCHEMA
    from tests.conftest import make_settings
    make_settings(tmp_path)
    runs = []
    cut = T0 - 600 * DAY
    for k, seed in enumerate((31, 32)):
        d = DB(tmp_path / f"c2_{k}.db")
        d.conn.executescript(EXTRA_SCHEMA)
        a = d1_walk(1100, 21)
        past = [b for b in a if b["t"] + DAY <= cut]
        bars = d1_walk(1100, seed, prefix=past)
        store(d, "EURUSD", "D1", bars)
        runs.append(backtest.run(d, d1_params("tsmom-v1", symbols=["EURUSD"])))
        d.close()
    before = [t for t in runs[0]["trades"] if t["exit_t"] <= cut]
    assert before and before == [t for t in runs[1]["trades"] if t["exit_t"] <= cut]
    assert runs[0]["trades"] != runs[1]["trades"]


def test_run_cached_memoises_bias(db, monkeypatch):
    seed_d1(db)
    calls = {"n": 0}
    orig = tsmom_v1.bias

    def counting(*a, **kw):
        calls["n"] += 1
        return orig(*a, **kw)
    monkeypatch.setattr(tsmom_v1, "bias", counting)
    cache = {}
    r1 = backtest.run_cached(db, d1_params("tsmom-v1"), cache)
    n1 = calls["n"]
    assert n1 > 0
    r2 = backtest.run_cached(db, d1_params("tsmom-v1", exit={"mode": "combo", "params": {
        "modes": ["signal_flip", "trailing"], "trail_start_r": 2.0}}), cache)
    assert r2["timing"]["phase1_cached"] and calls["n"] - n1 < n1 // 4  # mostly served from the memo
    r3 = backtest.run_cached(db, d1_params("tsmom-v1"), cache)
    assert r3["trades"] == r1["trades"]


def test_trend_v1_unchanged_with_new_modes_present(db):
    """Existing strategies: no swap, no plugin defaults, live session/time stop/BE untouched."""
    from tests.test_stage_b_backtest import run_params, seed_market
    seed_market(db)
    ref = backtest.run(db, run_params())
    assert "swap_r" not in ref["summary"] and not any("swap_usd" in t for t in ref["trades"])
    assert not any(k.startswith("swap") for k in ref["params"]["cost"])
    off = backtest.run(db, run_params(cost={"mode": "fixed", "fixed_spread_pts": 3, "swap_mode": "off"}))
    assert off["trades"] == ref["trades"] and off["summary"] == ref["summary"]
    on = backtest.run(db, run_params(cost={"mode": "fixed", "fixed_spread_pts": 3, "swap_mode": "zero"}))
    assert [t["r"] for t in on["trades"]] == [t["r"] for t in ref["trades"]]


# ---- speed -------------------------------------------------------------------------------------

def test_speed_per_call():
    bars = d1_walk(800, 4)
    out = []
    for n in C2:
        m, cfg = S.get(n), cfg_of(n)
        t0 = time.perf_counter()
        k = 0
        for i in range(400, 800):
            m.analyze("EURUSD", bars[i - 399: i + 1], [], q_at(bars[i]["c"]), cfg, RISK, ctx())
            k += 1
        ms = (time.perf_counter() - t0) / k * 1000
        msg = f"{n} analyze {ms:.3f}"
        if hasattr(m, "bias"):
            t0 = time.perf_counter()
            for i in range(400, 800):
                m.bias("EURUSD", bars[i - 399: i + 1], [], cfg)
            msg += f", bias {(time.perf_counter() - t0) / 400 * 1000:.3f}"
        out.append(msg)
        assert ms < 20
    print("\nms/call (400 D1 bars): " + "; ".join(out))
    if SPEED:
        print("full D1 run (2 symbols × 1000 days, s): " + ", ".join(f"{k} {v:.2f}" for k, v in SPEED.items()))
