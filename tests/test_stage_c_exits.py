"""Stage C: exit policies (app/exits.py), strategy selection, per-symbol spreads, trade paths and run_cached."""
import json
import random

import pytest

from app import backtest, exits, labeler, strategy
from app import indicators as ind
from app.exits import ExitPolicy, IncIndicators
from tests.test_stage_b_backtest import T0, db, flat_bars, run_params, seed_market, sim_case  # noqa: F401

E, SL, TP = 1.10000, 1.09900, 1.10300  # BUY: R = 0.001 (100 pts), TP = +3R
PT = 0.00001


def bar(t, o, h, l, c, sp=0):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "sp": sp}


def flat_hist(n=60, px=E, t_end=T0):
    return [bar(t_end - (n - k) * 60, px, px + 0.0001, px - 0.0001, px) for k in range(n)]


def pos(mode, params=None, be=1.0, side="BUY", sl=SL, tp=TP, history=None, bar_sec=60):
    p = labeler.SimPosition(side, E, sl, tp, PT, T0, be_at_r=be)
    exits.attach(p, ExitPolicy(mode, params or {}), history=history if history is not None else flat_hist(),
                 bar_sec=bar_sec)
    return p


# ---- indicators / policy object --------------------------------------------------------------

def test_incremental_indicators_match_batch():
    rng = random.Random(4)
    px, bars = 1.1, []
    for k in range(300):
        o = px
        px += rng.gauss(0, 0.0004)
        bars.append(bar(k * 60, o, max(o, px) + abs(rng.gauss(0, 0.0002)), min(o, px) - abs(rng.gauss(0, 0.0002)), px))
    c = [b["c"] for b in bars]
    e20 = ind.ema(c, 20)
    _, _, hist = ind.macd(c, 12, 26, 9)
    atr = ind.atr([b["h"] for b in bars], [b["l"] for b in bars], c, 14)
    it = IncIndicators()
    for k, b in enumerate(bars):
        it.update(b)
        assert it.ema20 == e20[k] and it.hist == hist[k] and it.atr == atr[k]


def test_policy_parsing_and_validation():
    assert ExitPolicy().is_base and ExitPolicy.from_spec(None).is_base
    assert ExitPolicy("combo", {"modes": ["base"]}).is_base
    c = ExitPolicy("combo", {"modes": ["trailing", "giveback"], "gb_frac": 0.4})
    assert c.modes == ("trailing", "giveback") and c.p["gb_frac"] == 0.4 and c.p["trail_start_r"] == 1.0
    assert c.to_dict()["params"]["modes"] == ["trailing", "giveback"]
    for bad in (("nope", {}), ("combo", {}), ("combo", {"modes": ["combo"]}), ("partial", {"partial_frac": 1.5}),
                ("stall", {"stall_minutes": "x"})):
        with pytest.raises(ValueError):
            ExitPolicy(*bad)
    assert set(exits.DEFAULT_PARAMS) == set(exits.MODES)


def test_base_policy_keeps_labeler_code_path():
    p = labeler.SimPosition("BUY", E, SL, TP, PT, T0, be_at_r=1.0)
    exits.attach(p, ExitPolicy("base"))
    assert p.exit is None and p.ind is None
    assert p.step(bar(T0, E, 1.1010, 1.0999, 1.1008), 0.0) is None and p.be_done


# ---- modes on crafted bars -------------------------------------------------------------------

def test_trailing_never_loosens_and_exits_as_trail():
    p = pos("trailing", {"trail_start_r": 1.0, "trail_atr_mult": 0.0, "trail_min_r": 0.5}, be=0)
    closes = [1.1005, 1.1012, 1.1018, 1.1016, 1.1015, 1.1017, 1.1022, 1.1019]
    sls, t = [p.sl], T0
    for c in closes:
        assert p.step(bar(t, c - 0.0001, c + 0.0001, c - 0.0001, c), 0.0) is None
        sls.append(p.sl)
        t += 60
    assert all(b >= a for a, b in zip(sls, sls[1:]))  # only tightens
    assert p.sl == pytest.approx(1.1022 - 0.0005)  # best close 1.1022 - 0.5R
    assert sls[2] == pytest.approx(1.1012 - 0.0005)  # starts once the best close is >= +1R
    reason, px = p.step(bar(t, 1.1019, 1.1019, 1.1010, 1.1012), 0.0)
    assert reason == "TRAIL" and p.r_of(px) == pytest.approx(1.7)


def test_trailing_uses_atr_and_needs_room():
    p = pos("trailing", {"trail_start_r": 1.0, "trail_atr_mult": 10.0, "trail_min_r": 0.1}, be=0)
    atr = p.ind.atr  # flat history: ATR 0.0002 -> 10*ATR = 2R behind the best close: below the SL, no move
    assert atr == pytest.approx(0.0002)
    p.step(bar(T0, E, 1.1016, 1.0999, 1.1015), 0.0)
    assert p.sl == SL
    # room rule: a new SL closer to the price than spread + 1 point is not applied
    q = pos("trailing", {"trail_start_r": 1.0, "trail_atr_mult": 0.0, "trail_min_r": 0.01}, be=0)
    q.step(bar(T0, E, 1.1012, 1.0999, 1.1012), 0.0001)
    assert q.sl == SL


def test_partial_accounting_in_r():
    # +1R: half closed at 1R, rest to BE (next bar), then TP for the rest: 0.5*1 + 0.5*3 = 2R
    p = pos("partial", {"partial_frac": 0.5, "partial_r": 1.0}, be=0)
    assert p.step(bar(T0, E, 1.1011, 1.0999, 1.1008), 0.0) is None
    assert p.closed_frac == 0.5 and p.realized_r == pytest.approx(0.5) and p.sl == pytest.approx(E + 2 * PT)
    reason, px = p.step(bar(T0 + 60, 1.1008, 1.1031, 1.1005, 1.103), 0.0)
    assert reason == "TP" and p.total_r(px) == pytest.approx(2.0)
    # back to break-even: 0.5*1 + 0.5*0.02
    p = pos("partial", {"partial_frac": 0.5, "partial_r": 1.0}, be=0)
    p.step(bar(T0, E, 1.1011, 1.0999, 1.1008), 0.0)
    reason, px = p.step(bar(T0 + 60, 1.1008, 1.1009, 1.0990, 1.0995), 0.0)
    assert reason == "BE" and p.total_r(px) == pytest.approx(0.51)
    # partial and TP in the same bar: the partial level is passed first
    p = pos("partial", {"partial_frac": 0.5, "partial_r": 1.0}, be=0)
    reason, px = p.step(bar(T0, E, 1.1035, 1.0999, 1.103), 0.0)
    assert reason == "TP" and p.total_r(px) == pytest.approx(2.0)
    # SL first when the bar touches both the SL and the partial level
    p = pos("partial", {"partial_frac": 0.5, "partial_r": 1.0}, be=0)
    reason, px = p.step(bar(T0, E, 1.1015, 1.0985, 1.1), 0.0)
    assert reason == "SL" and p.closed_frac == 0 and p.total_r(px) == pytest.approx(-1.0)


def test_labeler_simulate_with_partial_policy():
    after = [bar(T0 + 300, E, 1.1011, 1.0999, 1.1008), bar(T0 + 360, 1.1008, 1.1031, 1.1005, 1.103)]
    cfg = {"break_even_at_r": 0, "max_hold_minutes": 60, "_bar_sec": 60}
    r = labeler.simulate("BUY", SL, TP, flat_hist(t_end=T0 + 300) + after, PT, T0 + 300, cfg, T0 + 86400,
                         exit_policy={"mode": "partial", "params": {}})
    assert r["outcome"] == "TP" and r["outcome_r"] == pytest.approx(2.0)
    base = labeler.simulate("BUY", SL, TP, after, PT, T0 + 300, cfg, T0 + 86400)
    assert base["outcome"] == "TP" and base["outcome_r"] == pytest.approx(3.0)


def test_invalidation_only_on_closed_bars_and_after_min_bars():
    p = pos("invalidation", {"min_bars": 2}, be=0, sl=1.0980)
    # bar 1 closes below EMA20 but min_bars not reached
    assert p.step(bar(T0, E, 1.1001, 1.0996, 1.0999), 0.0) is None
    p = pos("invalidation", {"min_bars": 1}, be=0, sl=1.0980)
    # intrabar dip far below EMA20, close above it and MACD hist rising -> no exit
    assert p.step(bar(T0, E, 1.1004, 1.0990, 1.1003), 0.0) is None
    assert p.step(bar(T0 + 60, 1.1003, 1.1006, 1.0992, 1.1005), 0.0) is None
    # close crosses EMA20 downwards -> exit at that close (bid for BUY)
    reason, px = p.step(bar(T0 + 120, 1.1005, 1.1005, 1.0995, 1.0997), 0.0)
    assert reason == "INVALIDATION" and px == 1.0997 and p.ind.close < p.ind.ema20
    # SELL: exit at ask close, cross upwards
    s = pos("invalidation", {"min_bars": 1}, be=0, side="SELL", sl=1.1020, tp=1.0970)
    assert s.step(bar(T0, E, 1.1001, 1.0996, 1.0997), 0.0) is None
    reason, px = s.step(bar(T0 + 60, 1.0997, 1.1006, 1.0996, 1.1004), 0.0001)
    assert reason == "INVALIDATION" and px == pytest.approx(1.1005)


def test_macd_flip_invalidation():
    rising = [bar(T0 - (60 - k) * 60, 1.09 + k * 0.0002, 1.09 + k * 0.0002 + 0.0001, 1.09 + k * 0.0002 - 0.0001,
                  1.09 + k * 0.0002) for k in range(60)]
    p = labeler.SimPosition("BUY", rising[-1]["c"], 1.0900, 1.1200, PT, T0, be_at_r=0)
    exits.attach(p, ExitPolicy("invalidation", {"min_bars": 1}), history=rising)
    assert p.ind.hist > 0 or p.ind.hist == pytest.approx(0, abs=1e-12)
    c, out = rising[-1]["c"], None
    for k in range(10):  # flat closes above EMA20: histogram decays and turns negative
        out = p.step(bar(T0 + k * 60, c, c + 0.00005, c - 0.00005, c), 0.0)
        if out:
            break
    assert out and out[0] == "INVALIDATION" and p.ind.hist < 0 <= p.ind.prev_hist
    assert p.ind.close > p.ind.ema20  # it was the MACD flip, not the EMA cross


def test_stall_math_one_shot():
    p = pos("stall", {"stall_minutes": 10, "stall_band_r": 0.3}, be=0)
    for k in range(9):
        assert p.step(bar(T0 + k * 60, E, 1.1003, 1.0998, 1.1002), 0.0) is None
    reason, px = p.step(bar(T0 + 540, E, 1.1003, 1.0998, 1.1002), 0.0)  # bar end = 10 min after the entry
    assert reason == "STALL" and p.r_of(px) == pytest.approx(0.2)
    q = pos("stall", {"stall_minutes": 10, "stall_band_r": 0.3}, be=0)
    for k in range(10):
        assert q.step(bar(T0 + k * 60, E, 1.1006, 1.0998, 1.1005), 0.0) is None  # +0.5R at the check: keep
    assert q.step(bar(T0 + 600, E, 1.1003, 1.0998, 1.1001), 0.0) is None  # checked once only
    # SELL band uses the ask close
    s = pos("stall", {"stall_minutes": 1, "stall_band_r": 0.3}, be=0, side="SELL", sl=1.1010, tp=1.0970)
    reason, px = s.step(bar(T0, E, 1.1001, 1.0995, 1.0998), 0.0001)
    assert reason == "STALL" and s.r_of(px) == pytest.approx(0.1)


def test_giveback_math():
    p = pos("giveback", {"gb_arm_r": 1.0, "gb_frac": 0.5}, be=0)
    assert p.step(bar(T0, E, 1.1016, 1.0999, 1.1012), 0.0) is None  # MFE 1.6R, R 1.2 > 0.8
    assert p.step(bar(T0 + 60, 1.1012, 1.1013, 1.1009, 1.1009), 0.0) is None  # 0.9 > 0.8
    reason, px = p.step(bar(T0 + 120, 1.1009, 1.1010, 1.1006, 1.1007), 0.0)
    assert reason == "GIVEBACK" and p.r_of(px) == pytest.approx(0.7)
    q = pos("giveback", {"gb_arm_r": 1.0, "gb_frac": 0.5}, be=0)
    assert q.step(bar(T0, E, 1.1009, 1.0999, 1.1001), 0.0) is None  # MFE 0.9 < arm: never
    assert q.step(bar(T0 + 60, 1.1001, 1.1002, 1.0995, 1.0996), 0.0) is None


def test_combo_applies_all_modes():
    p = pos("combo", {"modes": ["partial", "giveback"], "gb_arm_r": 1.0, "gb_frac": 0.5}, be=0)
    assert p.step(bar(T0, E, 1.1016, 1.0999, 1.1012), 0.0) is None
    assert p.closed_frac == 0.5
    reason, px = p.step(bar(T0 + 60, 1.1012, 1.1012, 1.1006, 1.1007), 0.0)
    assert reason == "GIVEBACK" and p.total_r(px) == pytest.approx(0.5 * 1 + 0.5 * 0.7)


# ---- backtest phase 2 with policies ----------------------------------------------------------

DEC = {"EURUSD": {0: ("BUY", 1.10000, 1.09900, 1.10300, 0.8, ())}}
NOCOST = {"slippage_pts": 0, "commission_per_lot_usd": 0, "fixed_spread_pts": 0}


def test_backtest_partial_usd_and_min_lot(db):
    bars = flat_bars(8)
    bars[1] = {"t": T0 + 300, "o": 1.1, "h": 1.1011, "l": 1.0999, "c": 1.1008, "sp": 0}
    bars[2] = {"t": T0 + 600, "o": 1.1008, "h": 1.1031, "l": 1.1005, "c": 1.103, "sp": 0}
    ex = {"mode": "partial", "params": {"partial_frac": 0.5, "partial_r": 1.0}}
    res, _ = sim_case(db, DEC, {"EURUSD": bars}, cost=NOCOST, exit=ex, strategy={"break_even_at_r": 0})
    (t,) = res["trades"]
    assert t["outcome"] == "TP" and t["gross_r"] == pytest.approx(2.0) and t["partial_frac"] == 0.5
    assert t["usd"] == pytest.approx(2.0 * t["volume"] * 100, abs=0.01)  # R × risk (100 pts × 1 USD × lots)
    # 0.01 lot cannot be split in live sizing -> partial disabled, full TP
    res2, _ = sim_case(db, DEC, {"EURUSD": bars}, cost=NOCOST, exit=ex, strategy={"break_even_at_r": 0},
                       initial_equity=100)
    (t2,) = res2["trades"]
    assert t2["volume"] == pytest.approx(0.01) and t2["gross_r"] == pytest.approx(3.0) and "partial_frac" not in t2
    assert res2["counts"]["partial_disabled_min_lot"] == 1
    # R mode: always simulated
    res3, _ = sim_case(db, DEC, {"EURUSD": bars}, cost=NOCOST, exit=ex, strategy={"break_even_at_r": 0},
                       initial_equity=100, size_mode="r_only")
    assert res3["trades"][0]["gross_r"] == pytest.approx(2.0)


def test_backtest_close_exit_time_is_bar_end(db):
    bars = flat_bars(30)
    res, _ = sim_case(db, DEC, {"EURUSD": bars}, cost=NOCOST,
                      exit={"mode": "stall", "params": {"stall_minutes": 30, "stall_band_r": 0.3}})
    (t,) = res["trades"]
    assert t["outcome"] == "STALL" and t["exit_t"] - t["entry_t"] == 1800


# ---- full runs ---------------------------------------------------------------------------------

def test_base_exit_and_trend_v1_bit_identical(db):
    seed_market(db)
    ref = backtest.run(db, run_params())
    for extra in ({"exit": {"mode": "base", "params": {}}}, {"strategy_name": "trend-v1"},
                  {"exit": {"mode": "combo", "params": {"modes": ["base"]}}}):
        r = backtest.run(db, run_params(**extra))
        assert r["trades"] == ref["trades"] and r["summary"] == ref["summary"] and r["counts"] == ref["counts"]
    assert ref["params"]["strategy_name"] == "trend-v1" and ref["params"]["exit"]["mode"] == "base"


def test_unknown_strategy_is_a_clear_error(db):
    seed_market(db)
    with pytest.raises(ValueError, match="Strategie necunoscută"):
        backtest.run(db, run_params(strategy_name="nu-exista"))


def test_run_cached_reuses_phase1(db, monkeypatch):
    seed_market(db)
    calls = {"n": 0}

    def counting(name):
        fn = strategy.analyze

        def wrapped(*a, **kw):
            calls["n"] += 1
            return fn(*a, **kw)
        return wrapped

    monkeypatch.setattr(backtest, "get_analyzer", counting)
    cache: dict = {}
    modes = [{"mode": "base"}, {"mode": "trailing"}, {"mode": "partial"}, {"mode": "invalidation"},
             {"mode": "stall"}, {"mode": "giveback"}, {"mode": "combo", "params": {"modes": ["trailing", "stall"]}}]
    out = []
    for m in modes:
        out.append(backtest.run_cached(db, run_params(exit=m, size_mode="r_only"), cache))
        if len(out) == 1:
            first = calls["n"]
            assert first > 500 and not out[0]["timing"]["phase1_cached"]
    assert calls["n"] == first  # the 6 other exit policies re-ran phase 2 only
    assert all(r["timing"]["phase1_cached"] for r in out[1:])
    assert all(r["counts"]["analyses"] == out[0]["counts"]["analyses"] for r in out)
    plain = backtest.run(db, run_params(size_mode="r_only"))
    assert plain["trades"] == out[0]["trades"] and plain["summary"] == out[0]["summary"]
    assert calls["n"] == 2 * first
    assert len({json.dumps(r["summary"], sort_keys=True) for r in out}) > 1  # exits change the results
    # a different strategy setting is a new phase-1 key
    backtest.run_cached(db, run_params(size_mode="r_only", strategy={"min_score": 3}), cache)
    assert calls["n"] == 3 * first


def test_per_symbol_spread_applied(db):
    seed_market(db)
    seed_market(db, "GBPUSD", seed=12, p0=1.27)
    p = run_params(symbols=["EURUSD", "GBPUSD"], cost={"mode": "fixed", "fixed_spread_pts": 3,
                                                       "per_symbol_spread_pts": {"gbpusd": 12}},
                   specs={**run_params()["specs"], "GBPUSD": run_params()["specs"]["EURUSD"]},
                   risk={**run_params()["risk"], "max_same_currency_direction": 2})
    r = backtest.run(db, p)
    sp = {t["symbol"]: set() for t in r["trades"]}
    for t in r["trades"]:
        sp[t["symbol"]].add(t["spread_pts"])
    assert sp.get("GBPUSD") == {12.0} and sp.get("EURUSD") == {3.0}
    assert r["params"]["cost"]["per_symbol_spread_pts"] == {"GBPUSD": 12.0}
    # fixed mode without fixed_spread_pts is fine when every symbol has its own spread
    q = run_params(cost={"mode": "fixed", "per_symbol_spread_pts": {"EURUSD": 7}})
    assert all(t["spread_pts"] == 7.0 for t in backtest.run(db, q)["trades"])
    with pytest.raises(ValueError):
        backtest.run(db, run_params(cost={"mode": "fixed", "fixed_spread_pts": None}))


def test_record_paths_jsonl(db, tmp_path):
    seed_market(db)
    out = tmp_path / "paths.jsonl"
    r = backtest.run(db, run_params(record_paths=True, paths_out=str(out), exit={"mode": "trailing"}))
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert len(rows) == r["summary"]["trades"] > 0
    row = rows[0]
    assert row["source"] == "backtest" and row["path"]
    pt = row["path"][0]
    assert set(pt) == {"ts", "minutes", "r_now", "mfe_r", "mae_r", "price", "atr", "ema_dist_atr", "spread_pts"}
    assert pt["minutes"] == 5.0 and pt["atr"] > 0
    assert all(a["ts"] < b["ts"] for a, b in zip(row["path"], row["path"][1:]))
    # recording never changes the simulation
    plain = backtest.run(db, run_params(exit={"mode": "trailing"}))
    assert plain["trades"] == r["trades"]
    with pytest.raises(ValueError):
        backtest.run(db, run_params(record_paths=True))
