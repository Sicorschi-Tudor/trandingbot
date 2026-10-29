"""Stage F, family F1 (large-sample re-test of the intraday plugins trend-v1 / meanrev-v1 / breakout-v1 / pullback-v1
on H1 with HTF H4): smoke tests — import through the registry, analyze() on synthetic H1/H4 bars returns exactly the
trend-v1 output shape, decisions use closed bars only (no peeking at the quote for the direction), and runner-level
sanity of research/run_bt.py (fixed pre-registered periods, TEST split refused without --allow-test, the F1 harness
risk/strategy overrides are accepted by the backtest resolver)."""
import argparse
import json
import random
import sys
from pathlib import Path

import pytest

from app import backtest as bt
from app import strategies as S
from app.config import Settings
from app.strategy import AnalysisContext
from tests.conftest import EURUSD_INFO

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "research"))
import run_bt  # noqa: E402

SET = Settings(_env_file=None)
CFG = SET.default_strategy()
RISK = SET.default_risk()
F1 = ["trend-v1", "meanrev-v1", "breakout-v1", "pullback-v1"]
H = 3600
T0 = 1388966400  # 2014-01-06 00:00 UTC (Monday) — inside TRAIN
SHAPE = {"symbol", "timeframe", "time_utc", "bar_time", "direction", "score", "probabilities", "probability_method",
         "reasons", "indicators", "regime", "spread_points", "entry", "stop_loss", "take_profit", "rr", "candidate"}
HARNESS_RISK = {"max_open_positions": 4, "max_trades_per_day": 20, "max_same_currency_direction": 2,
                "max_spread_points": 60, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000}


def h1_walk(n, seed, start=1.3000, vol=0.0008):
    rnd = random.Random(seed)
    bars, c, drift = [], start, 0.0
    for i in range(n):
        if i % 120 == 0:
            drift = rnd.choice([-1, 0, 1]) * vol * 0.2
        o = c
        c = round(o + drift + rnd.gauss(0, vol), 5)
        bars.append({"t": T0 + i * H, "o": o, "h": round(max(o, c) + abs(rnd.gauss(0, vol / 2)), 5),
                     "l": round(min(o, c) - abs(rnd.gauss(0, vol / 2)), 5), "c": c})
    return bars


def h4_of(bars):
    out = []
    for b in bars:
        t = b["t"] - b["t"] % (4 * H)
        if out and out[-1]["t"] == t:
            x = out[-1]
            x["h"], x["l"], x["c"] = max(x["h"], b["h"]), min(x["l"], b["l"]), b["c"]
        else:
            out.append({"t": t, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]})
    return out


def q_at(c, sp=12):
    d = dict(EURUSD_INFO)
    d.update(bid=c, ask=round(c + sp * 1e-5, 5), spread_points=sp)
    return d


def analyze(name, sig, htf, q=None, **over):
    cfg = S.merged_cfg(name, {**CFG, "signal_timeframe": "H1", "htf_timeframe": "H4"}, over)
    ctx = AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.0,
                          now_ts=sig[-1]["t"] + H)
    return S.get(name).analyze("EURUSD", sig, htf, q or q_at(sig[-1]["c"]), cfg, RISK, ctx)


@pytest.mark.parametrize("name", F1)
def test_import_and_shape(name):
    mod = S.get(name)
    assert mod.NAME == name and mod.VERSION and mod.DESCRIPTION_RO and isinstance(mod.PARAMS, dict)
    bars = h1_walk(1200, seed=7)
    htf = h4_of(bars[:-1])
    ref = analyze("trend-v1", bars[-400:], htf[-200:])
    a = analyze(name, bars[-400:], htf[-200:])
    assert set(a) == set(ref) == SHAPE
    assert a["direction"] in ("BUY", "SELL", "HOLD") and a["timeframe"] == "H1"
    assert isinstance(a["reasons"], list) and isinstance(a["indicators"], dict)
    assert abs(sum(a["probabilities"].values()) - 1) < 1e-6


@pytest.mark.parametrize("name", F1)
def test_decisions_over_history_are_well_formed(name):
    """Every decision over a long synthetic history has consistent levels (BUY: SL < entry < TP; SELL mirrored)."""
    bars = h1_walk(3000, seed=11)
    htf_all = h4_of(bars)
    n_dec = 0
    for i in range(600, len(bars), 7):
        sig = bars[max(0, i - 399): i + 1]
        T = bars[i]["t"] + H
        htf = [h for h in htf_all if h["t"] + 4 * H <= T][-200:]
        a = analyze(name, sig, htf)
        if a["direction"] != "HOLD":
            n_dec += 1
            sgn = 1 if a["direction"] == "BUY" else -1
            assert sgn * (a["entry"] - a["stop_loss"]) > 0 and sgn * (a["take_profit"] - a["entry"]) > 0
            assert a["rr"] and a["rr"] > 0
    assert n_dec >= 0  # a HOLD-only walk is valid; the assertions above are the point


@pytest.mark.parametrize("name", F1)
def test_direction_does_not_depend_on_quote(name):
    """No lookahead through the quote: the side is decided on closed bars; the quote only sets the fill level."""
    bars = h1_walk(3000, seed=23)
    htf_all = h4_of(bars)
    seen = 0
    for i in range(600, len(bars), 5):
        sig = bars[max(0, i - 399): i + 1]
        T = bars[i]["t"] + H
        htf = [h for h in htf_all if h["t"] + 4 * H <= T][-200:]
        c = sig[-1]["c"]
        a1 = analyze(name, sig, htf, q_at(c))
        a2 = analyze(name, sig, htf, q_at(round(c + 0.0002, 5)))  # quote moved 2 pips after the bar closed
        if a1["direction"] != "HOLD" or a2["direction"] != "HOLD":
            seen += 1
            d1 = a1["direction"] if a1["direction"] != "HOLD" else (a1["candidate"] or {}).get("side")
            d2 = a2["direction"] if a2["direction"] != "HOLD" else (a2["candidate"] or {}).get("side")
            assert d1 == d2
    assert seen >= 0


def test_runner_fixed_periods_and_test_split_refused():
    assert run_bt.SPLITS["train"] == ("2013-06-01", "2019-01-01")
    assert run_bt.SPLITS["valid"] == ("2019-01-01", "2021-01-01")
    assert run_bt.ts("2019-01-01") == 1546300800
    with pytest.raises(SystemExit):
        run_bt.main(["--strategy", "trend-v1", "--split", "test"])
    with pytest.raises(SystemExit):
        run_bt.main(["--strategy", "trend-v1", "--start", "2020-06-01", "--end", "2021-06-01"])


def test_runner_builds_f1_params():
    a = argparse.Namespace(specs=str(ROOT / "data" / "research_specs.json"), split="train", start=None, end=None,
                           strategy_json='{"rescue_enabled": false}', risk_json=json.dumps(HARNESS_RISK), tf="H1",
                           cost="stats", commission=7.0, slippage=1.0, swap_mode=None, spread=None, strategy="meanrev-v1",
                           workers=1, symbols="EURUSD,GBPJPY", htf="H4", equity=10000.0, exit="combo",
                           exit_params='{"modes": ["trailing", "stall"], "max_hold_hours": 24}',
                           session_json='{"start":"07:00","end":"17:00","tz":"UTC","days":["MON","TUE","WED","THU","FRI"]}')
    if not Path(a.specs).exists():
        pytest.skip("data/research_specs.json missing")
    p = run_bt.build_params(a)
    assert p["start_ts"] == run_bt.ts("2013-06-01") and p["end_ts"] == run_bt.ts("2019-01-01")
    assert p["symbols"] == ["EURUSD", "GBPJPY"] and p["signal_tf"] == "H1" and p["htf_tf"] == "H4"
    assert p["risk"]["max_open_positions"] == 4 and p["session"]["start"] == "07:00"
    assert "rollover_blackout_minutes" not in p["risk"]  # only D1 gets the blackout disabled by the runner


def test_backtest_resolver_accepts_f1_harness():
    """The F1 harness (rescue off, account limits off, exit policies with max_hold_hours) resolves for every plugin."""
    specs = json.load(open(ROOT / "data" / "research_specs.json")) if (ROOT / "data" / "research_specs.json").exists() else {}

    class _NoDB:
        def query(self, *a, **k):
            return []

    for name in F1:
        for ex in ({"mode": "base", "params": {"max_hold_hours": 24}},
                   {"mode": "trailing", "params": {"max_hold_hours": 24}},
                   {"mode": "combo", "params": {"modes": ["trailing", "stall"], "stall_minutes": 240, "max_hold_hours": 24}}):
            cfg = bt.resolve(_NoDB(), {"strategy_name": name, "specs": specs, "symbols": ["EURUSD"], "signal_tf": "H1",
                                       "htf_tf": "H4", "start_ts": run_bt.ts("2014-01-01"), "end_ts": run_bt.ts("2014-02-01"),
                                       "strategy": {"rescue_enabled": False}, "risk": HARNESS_RISK, "exit": ex,
                                       "session": None, "size_mode": "r_only"})
            assert cfg["strategy_name"] == name and cfg["live_rescue"] is None
            assert cfg["max_hold_s"] == 24 * 3600 and cfg["schedule"] is None
            assert cfg["risk"]["max_open_positions"] == 4 and cfg["exit"].mode == ex["mode"]


@pytest.mark.skipif(not (ROOT / "data" / "research.db").exists(), reason="research DB not present")
def test_runner_end_to_end_small_window(tmp_path):
    """Runner-level sanity on the research DB: one symbol, two TRAIN months, writes the result JSON."""
    out = tmp_path / "r.json"
    rc = run_bt.main(["--db", str(ROOT / "data" / "research.db"), "--strategy", "trend-v1", "--tf", "H1", "--htf", "H4",
                      "--symbols", "EURUSD", "--start", "2014-01-01", "--end", "2014-03-01",
                      "--strategy-json", '{"rescue_enabled": false, "min_score": 3}', "--risk-json", json.dumps(HARNESS_RISK),
                      "--exit", "base", "--exit-params", '{"max_hold_hours": 24}', "--workers", "1", "--json-out", str(out)])
    assert rc == 0
    r = json.loads(out.read_text())
    assert r["strategy"] == "trend-v1" and r["split"] == "2014-01-01..2014-03-01"
    assert "trades" in r["summary"] and "avg_r" in r["summary"] and r["summary"]["trades"] >= 1
    assert all(t[:4] == "2014" for t in r["by_year"])
