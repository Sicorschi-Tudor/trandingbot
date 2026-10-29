"""Stage F, family F2: smoke tests for the ensemble time-series momentum plugin disc-f2-tsmom2
(app/strategies/discovery/disc_f2_tsmom2.py).

Registry resolution, trend-v1 output shape, closed-bars-only decisions (no lookahead, tail invariance), crafted
direction / k-agreement / volatility-filter cases, and a runner-level sanity run through app.backtest.run."""
import math
import random

import pytest

from app import backtest, strategy
from app import strategies as S
from app.exits import ExitPolicy
from app.strategies.discovery import discovery_names, get_discovery
from app.strategies.discovery import disc_f2_tsmom2 as M
from tests.test_stage_b_backtest import T0, db, store  # noqa: F401
from tests.test_stage_c2_strategies import CFG, DAY, RISK, cfg_of, ctx, d1_params, d1_walk, daily, q_at, seed_d1

NAME = "disc-f2-tsmom2"


def run_p(sig, **over):
    return M.analyze("EURUSD", sig, [], q_at(sig[-1]["c"]), cfg_of(NAME, **over), RISK, ctx(now_ts=sig[-1]["t"] + DAY))


def test_registry_resolves_and_defaults():
    assert NAME in discovery_names() and S.get(NAME) is get_discovery(NAME)
    assert NAME not in S.names(include_research=True)  # never offered to the live engine / UI
    assert M.PARAMS["lookbacks"] == [21, 63, 126, 252] and M.PARAMS["k"] == 3
    assert M.RUN_DEFAULTS["signal_timeframe"] == "D1" and M.RUN_DEFAULTS["htf_timeframe"] == "NONE"
    assert M.RUN_DEFAULTS["swap_mode"] == "table"
    pol = ExitPolicy.from_spec(M.default_exit(cfg_of(NAME)))
    assert "signal_flip" in pol.modes and callable(M.bias)
    assert S.merged_cfg(NAME, CFG, {"k": 4, "sl_atr": 2.0})["k"] == 4
    assert M._lookbacks({"lookbacks": "63,21,126"}) == [21, 63, 126]  # string form from --strategy-json


def test_output_shape_same_as_trend_v1():
    bars = d1_walk(800, 3)
    live = strategy.analyze("EURUSD", bars[-400:], [], q_at(bars[-1]["c"]), CFG, RISK, ctx())
    seen = set()
    for i in range(420, 800):
        a = run_p(bars[i - 399: i + 1])
        assert set(a) == set(live)
        assert set(live["indicators"]) <= set(a["indicators"])
        seen.add(a["direction"])
        if a["direction"] != "HOLD":
            sg = 1 if a["direction"] == "BUY" else -1
            assert (a["entry"] - a["stop_loss"]) * sg > 0 and (a["take_profit"] - a["entry"]) * sg > 0
            assert a["candidate"]["side"] == a["direction"] and not a["candidate"]["blockers"]
            assert a["indicators"]["agree"] >= 3 and a["indicators"]["ensemble_sign"] in (-1, 0, 1)
    assert "HOLD" in seen and len(seen) >= 2


def test_no_lookahead_and_window_independence():
    a_bars = d1_walk(1600, 5)
    b_bars = d1_walk(1600, 77, prefix=a_bars[:1200])  # same past, different future
    cfg = cfg_of(NAME)
    n_cand = 0
    for i in range(400, 1199):
        sa, sb = a_bars[max(0, i - 399): i + 1], b_bars[max(0, i - 399): i + 1]
        q, c = q_at(a_bars[i]["c"]), ctx(now_ts=a_bars[i]["t"] + DAY)
        ra = M.analyze("EURUSD", sa, [], q, cfg, RISK, c)
        assert ra == M.analyze("EURUSD", sb, [], q, cfg, RISK, c)
        assert ra == M.analyze("EURUSD", a_bars[: i + 1], [], q, cfg, RISK, c)  # longer window, same decision
        assert M.bias("EURUSD", sa, [], cfg) == M.bias("EURUSD", sb, [], cfg) == M.bias("EURUSD", a_bars[: i + 1], [], cfg)
        n_cand += ra["candidate"] is not None
    assert n_cand > 3


def test_quote_never_sets_the_direction():
    bars = d1_walk(700, 8)
    cfg = cfg_of(NAME)
    for i in range(400, 700):
        sig = bars[i - 399: i + 1]
        c = ctx(now_ts=bars[i]["t"] + DAY)
        hi = M.analyze("EURUSD", sig, [], q_at(round(bars[i]["c"] * 1.02, 5)), cfg, RISK, c)
        lo = M.analyze("EURUSD", sig, [], q_at(round(bars[i]["c"] * 0.98, 5)), cfg, RISK, c)
        assert hi["direction"] == lo["direction"] and hi["indicators"]["votes"] == lo["indicators"]["votes"]


@pytest.mark.parametrize("sgn,side", [(1, "BUY"), (-1, "SELL")])
def test_direction_k_and_volatility_filter(sgn, side):
    rng = random.Random(9)
    closes = [round(1.10 * math.exp(sgn * 0.0015 * i + rng.gauss(0, 0.002)), 5) for i in range(300)]
    bars = daily(closes)
    a = run_p(bars)
    assert a["direction"] == side, a["reasons"]
    assert a["indicators"]["votes"] == [sgn] * 4 and a["indicators"]["agree"] == 4
    assert M.bias("EURUSD", bars, [], cfg_of(NAME)) == sgn
    assert abs(a["entry"] - a["stop_loss"]) == pytest.approx(3.0 * a["indicators"]["atr"], abs=2e-5)
    assert abs(a["entry"] - a["stop_loss"]) != pytest.approx(abs(run_p(bars, sl_atr=2.0)["entry"] - run_p(bars, sl_atr=2.0)["stop_loss"]))
    # unanimity still passes k=4; lookbacks given as a string also work
    assert run_p(bars, k=4)["direction"] == side
    assert run_p(bars, lookbacks="21,63,126")["direction"] == side
    # same drift sign but huge periodic noise (period 21 divides every lookback, so the lookback returns keep the
    # drift sign) -> every lookback below min_z -> HOLD, raw bias still the drift sign
    noisy = [round(1.10 * math.exp(sgn * 0.00005 * i + (0.03 if i % 21 < 10 else -0.03)), 5) for i in range(300)]
    b = run_p(daily(noisy))
    assert b["direction"] == "HOLD" and b["indicators"]["votes"] == [0, 0, 0, 0]
    assert M.bias("EURUSD", daily(noisy), [], cfg_of(NAME)) == sgn
    assert run_p(daily(noisy), min_z=0.0)["direction"] == side  # filter off -> trades


def test_k_agreement_blocks_mixed_ensembles():
    # 270 bars of a strong down trend then 30 bars of a strong up move: 21 and 63 vote up, 126 and 252 vote down
    closes = [round(1.30 * math.exp(-0.002 * i), 5) for i in range(270)]
    closes += [round(closes[-1] * math.exp(0.004 * j), 5) for j in range(1, 31)]
    bars = daily(closes)
    a = run_p(bars, k=3)
    assert a["indicators"]["votes"] == [1, 1, -1, -1], a["indicators"]
    assert a["direction"] == "HOLD" and "nu are direcție" in a["reasons"][-1]
    assert M.bias("EURUSD", bars, [], cfg_of(NAME)) == 0  # 2-2 tie -> flat (signal_flip does not close on flat)
    assert run_p(bars, k=2)["direction"] == "HOLD"  # tie: no direction even with k=2
    # 250 down then 50 up: 21, 63 and 126 vote up, 252 votes down -> 3/4 agreement: BUY with k<=3, HOLD with k=4
    closes = [round(1.30 * math.exp(-0.002 * i), 5) for i in range(250)]
    closes += [round(closes[-1] * math.exp(0.004 * j), 5) for j in range(1, 51)]
    bars = daily(closes)
    a = run_p(bars, k=3)
    assert a["indicators"]["votes"] == [1, 1, 1, -1] and a["direction"] == "BUY", a["indicators"]
    assert M.bias("EURUSD", bars, [], cfg_of(NAME)) == 1
    b = run_p(bars, k=4)
    assert b["direction"] == "HOLD" and "Acord insuficient" in b["reasons"][-1]
    assert b["candidate"] is None and b["indicators"]["agree"] == 3


def test_insufficient_data_and_no_quote():
    a = run_p(d1_walk(60, 1))
    assert a["direction"] == "HOLD" and "Date insuficiente" in a["reasons"][0]
    bars = d1_walk(400, 1)
    a = M.analyze("EURUSD", bars, [], None, cfg_of(NAME), RISK, ctx())
    assert a["direction"] == "HOLD" and a["candidate"] is None
    assert M.bias("EURUSD", bars[:100], [], cfg_of(NAME)) == 0


def test_runner_level_backtest(db):  # noqa: F811
    seed_d1(db)
    r = backtest.run(db, d1_params(NAME, strategy={"k": 2, "min_z": 0.0}, htf_tf="NONE", signal_tf="D1",
                                   cost={"mode": "fixed", "fixed_spread_pts": 10, "swap_mode": "table"}))
    s = r["summary"]
    assert s["trades"] > 5 and isinstance(s["avg_r"], float)
    assert all(abs(t["r"]) < 50 for t in r["trades"])
    outcomes = set(r["by_outcome"])
    assert "FLIP" in outcomes, outcomes  # the ensemble sign flip really closes positions
    assert "TIME" not in outcomes and "BE" not in outcomes  # no time stop / break-even (RUN_DEFAULTS)
    assert r["params"]["cost"]["swap_mode"] == "table" and s["swap_r"] < 0
    assert any(t["exit_t"] - t["entry_t"] > 10 * DAY for t in r["trades"])  # long holds really happen
