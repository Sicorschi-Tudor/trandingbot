"""Stage F, family F4 (new session strategies disc-f4-orb / disc-f4-sessmom): smoke tests — import through the registry
(discovery auto-loader), analyze() on synthetic M15/H1 bars returns exactly the trend-v1 output shape, decisions use
closed bars only (the side never depends on the quote), the opening-range / session-move logic fires only at the right
UTC bars and once per day, and runner-level sanity of research/run_bt.py (fixed pre-registered periods, TEST split
refused, the F4 harness resolves in the backtest with the fixed-hour session exit)."""
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
F4 = ["disc-f4-orb", "disc-f4-sessmom"]
H = 3600
M15 = 900
T0 = 1388966400  # 2014-01-06 00:00 UTC (Monday) — inside TRAIN
SHAPE = {"symbol", "timeframe", "time_utc", "bar_time", "direction", "score", "probabilities", "probability_method",
         "reasons", "indicators", "regime", "spread_points", "entry", "stop_loss", "take_profit", "rr", "candidate"}
HARNESS_RISK = {"max_open_positions": 4, "max_trades_per_day": 20, "max_same_currency_direction": 2,
                "max_spread_points": 60, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000}
SESSION = {"start": "07:00", "end": "16:00", "tz": "UTC", "days": ["MON", "TUE", "WED", "THU", "FRI"]}


def walk(n, seed, sec=H, start=1.3000, vol=0.0008):
    rnd = random.Random(seed)
    bars, c, drift = [], start, 0.0
    for i in range(n):
        if i % 120 == 0:
            drift = rnd.choice([-1, 0, 1]) * vol * 0.2
        o = c
        c = round(o + drift + rnd.gauss(0, vol), 5)
        bars.append({"t": T0 + i * sec, "o": o, "h": round(max(o, c) + abs(rnd.gauss(0, vol / 2)), 5),
                     "l": round(min(o, c) - abs(rnd.gauss(0, vol / 2)), 5), "c": c})
    return bars


def q_at(c, sp=12):
    d = dict(EURUSD_INFO)
    d.update(bid=c, ask=round(c + sp * 1e-5, 5), spread_points=sp)
    return d


def analyze(name, sig, tf="H1", q=None, htf=None, **over):
    sec = H if tf == "H1" else M15
    cfg = S.merged_cfg(name, {**CFG, "signal_timeframe": tf, "htf_timeframe": "NONE"}, over)
    ctx = AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.0,
                          now_ts=sig[-1]["t"] + sec)
    return S.get(name).analyze("EURUSD", sig, htf or [], q or q_at(sig[-1]["c"]), cfg, RISK, ctx)


def ref_shape():
    bars = walk(1200, seed=7)
    htf = []
    for b in bars[:-1]:
        t = b["t"] - b["t"] % (4 * H)
        if htf and htf[-1]["t"] == t:
            x = htf[-1]
            x["h"], x["l"], x["c"] = max(x["h"], b["h"]), min(x["l"], b["l"]), b["c"]
        else:
            htf.append({"t": t, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]})
    cfg = S.merged_cfg("trend-v1", {**CFG, "signal_timeframe": "H1", "htf_timeframe": "H4"}, {})
    ctx = AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.0,
                          now_ts=bars[-1]["t"] + H)
    return S.get("trend-v1").analyze("EURUSD", bars[-400:], htf[-200:], q_at(bars[-1]["c"]), cfg, RISK, ctx)


@pytest.mark.parametrize("name", F4)
def test_import_and_shape(name):
    mod = S.get(name)
    assert mod.NAME == name and mod.VERSION and mod.DESCRIPTION_RO and isinstance(mod.PARAMS, dict)
    assert mod.RUN_DEFAULTS["close_at_session_end"] is True and mod.RUN_DEFAULTS["break_even_at_r"] == 0
    assert name not in S.names(True)  # research only: never offered to the live engine / UI
    ref = ref_shape()
    bars = walk(1200, seed=7)
    a = analyze(name, bars[-400:])
    assert set(a) == set(ref) == SHAPE
    assert a["direction"] in ("BUY", "SELL", "HOLD") and a["timeframe"] == "H1"
    assert isinstance(a["reasons"], list) and isinstance(a["indicators"], dict)
    assert abs(sum(a["probabilities"].values()) - 1) < 1e-6
    assert set(a["indicators"]) >= {"ema_fast", "ema_slow", "rsi", "atr", "macd_hist", "htf_trend"}


@pytest.mark.parametrize("name,tf", [("disc-f4-orb", "M15"), ("disc-f4-orb", "H1"), ("disc-f4-sessmom", "H1")])
def test_decisions_over_history_are_well_formed_and_once_per_day(name, tf):
    sec = H if tf == "H1" else M15
    n = 3000 if tf == "H1" else 6000
    bars = walk(n, seed=11, sec=sec, vol=0.0008 if tf == "H1" else 0.0003)
    over = {"range_bars": 1} if (name == "disc-f4-orb" and tf == "H1") else {}
    days = {}
    n_dec = 0
    for i in range(400, len(bars)):
        a = analyze(name, bars[max(0, i - 399): i + 1], tf=tf, **over)
        if a["direction"] != "HOLD":
            n_dec += 1
            sgn = 1 if a["direction"] == "BUY" else -1
            assert sgn * (a["entry"] - a["stop_loss"]) > 0 and sgn * (a["take_profit"] - a["entry"]) > 0
            assert a["rr"] and a["rr"] > 0
            t = bars[i]["t"]
            hour = (t % 86400) // 3600
            assert 7 <= hour < 12  # London open 07:00 UTC + range/move bars, inside the trade window
            day = t - t % 86400
            days[day] = days.get(day, 0) + 1
    assert n_dec > 0
    assert max(days.values()) == 1  # one setup per symbol per day, stateless


def test_sessmom_fires_only_at_the_decision_bar():
    bars = walk(3000, seed=5)
    decided = []
    for i in range(400, len(bars)):
        a = analyze("disc-f4-sessmom", bars[max(0, i - 399): i + 1], k_move=0.0)  # every day qualifies
        if a["direction"] != "HOLD" or a["candidate"]:
            decided.append(bars[i]["t"] % 86400)
    assert decided and set(decided) == {7 * H}  # only the bar opening 07:00 UTC (closing 08:00) decides
    # the side is the sign of the closed move
    for i in range(400, len(bars)):
        if bars[i]["t"] % 86400 == 7 * H:
            a = analyze("disc-f4-sessmom", bars[max(0, i - 399): i + 1], k_move=0.0)
            side = a["direction"] if a["direction"] != "HOLD" else (a["candidate"] or {}).get("side")
            if side:
                assert side == ("BUY" if bars[i]["c"] > bars[i]["o"] else "SELL")
            break


def test_orb_range_and_breakout_logic():
    """Synthetic day: flat 07:00–07:30 range, break above on the 07:45 bar (M15, range_bars 2)."""
    bars = walk(1200, seed=3, sec=M15, vol=0.0002)
    # find a day start inside the walk and shape its first bars
    i0 = next(i for i, b in enumerate(bars) if i > 500 and b["t"] % 86400 == 7 * H)
    base = bars[i0 - 1]["c"]
    for k in range(2):  # range bars: 07:00, 07:15
        bars[i0 + k].update(o=base, h=round(base + 0.0010, 5), l=round(base - 0.0010, 5), c=base)
    # 07:30 bar stays inside, 07:45 bar closes above the range
    bars[i0 + 2].update(o=base, h=round(base + 0.0005, 5), l=round(base - 0.0005, 5), c=base)
    bars[i0 + 3].update(o=base, h=round(base + 0.0020, 5), l=base, c=round(base + 0.0015, 5))
    bars[i0 + 4].update(o=round(base + 0.0015, 5), h=round(base + 0.0025, 5), l=base, c=round(base + 0.0020, 5))
    over = {"min_range_atr": 0.0, "max_range_atr": 0.0}
    inside = analyze("disc-f4-orb", bars[: i0 + 3], tf="M15", **over)
    assert inside["direction"] == "HOLD" and inside["candidate"] is None
    assert inside["indicators"]["range_high"] == round(base + 0.0010, 5)
    brk = analyze("disc-f4-orb", bars[: i0 + 4], tf="M15", **over)
    assert (brk["candidate"] or {}).get("side") == "BUY"
    if brk["direction"] == "BUY":
        assert brk["stop_loss"] < brk["entry"] < brk["take_profit"]
        assert abs(brk["stop_loss"] - round(base - 0.0010, 5)) < 2e-4  # SL at the opposite side (floor may widen)
    again = analyze("disc-f4-orb", bars[: i0 + 5], tf="M15", **over)
    assert again["candidate"] is None and any("deja spart" in r for r in again["reasons"])
    mid = analyze("disc-f4-orb", bars[: i0 + 4], tf="M15", sl_mode="mid", **over)
    if mid["direction"] == "BUY":
        assert mid["stop_loss"] >= brk["stop_loss"]
    too_wide = analyze("disc-f4-orb", bars[: i0 + 4], tf="M15", min_range_atr=0.0, max_range_atr=0.01)
    assert too_wide["candidate"] is None and any("prea larg" in r for r in too_wide["reasons"])


@pytest.mark.parametrize("name,tf", [("disc-f4-orb", "M15"), ("disc-f4-orb", "H1"), ("disc-f4-sessmom", "H1")])
def test_direction_does_not_depend_on_quote(name, tf):
    """No lookahead through the quote: the side is decided on closed bars; the quote only sets the fill level."""
    sec = H if tf == "H1" else M15
    bars = walk(3000 if tf == "H1" else 6000, seed=23, sec=sec, vol=0.0008 if tf == "H1" else 0.0003)
    over = {"range_bars": 1} if (name == "disc-f4-orb" and tf == "H1") else {}
    seen = 0
    for i in range(400, len(bars)):
        sig = bars[max(0, i - 399): i + 1]
        c = sig[-1]["c"]
        a1 = analyze(name, sig, tf=tf, q=q_at(c), **over)
        a2 = analyze(name, sig, tf=tf, q=q_at(round(c + 0.0004, 5)), **over)  # quote moved 4 pips after the close
        d1 = a1["direction"] if a1["direction"] != "HOLD" else (a1["candidate"] or {}).get("side")
        d2 = a2["direction"] if a2["direction"] != "HOLD" else (a2["candidate"] or {}).get("side")
        if d1 or d2:
            seen += 1
            assert d1 == d2
    assert seen > 0


def test_runner_fixed_periods_and_test_split_refused():
    assert run_bt.SPLITS["train"] == ("2013-06-01", "2019-01-01")
    assert run_bt.SPLITS["valid"] == ("2019-01-01", "2021-01-01")
    with pytest.raises(SystemExit):
        run_bt.main(["--strategy", "disc-f4-orb", "--split", "test"])
    with pytest.raises(SystemExit):
        run_bt.main(["--strategy", "disc-f4-sessmom", "--start", "2020-06-01", "--end", "2021-06-01"])


def test_runner_builds_f4_params():
    a = argparse.Namespace(specs=str(ROOT / "data" / "research_specs.json"), split="train", start=None, end=None,
                           strategy_json='{"range_bars": 2, "tp_range_mult": 1.5}', risk_json=json.dumps(HARNESS_RISK),
                           tf="M15", cost="stats", commission=7.0, slippage=1.0, swap_mode=None, spread=None,
                           strategy="disc-f4-orb", workers=1, symbols="EURUSD,GBPJPY", htf="NONE", equity=10000.0,
                           exit=None, exit_params="", session_json=json.dumps(SESSION))
    if not Path(a.specs).exists():
        pytest.skip("data/research_specs.json missing")
    p = run_bt.build_params(a)
    assert p["start_ts"] == run_bt.ts("2013-06-01") and p["end_ts"] == run_bt.ts("2019-01-01")
    assert p["symbols"] == ["EURUSD", "GBPJPY"] and p["signal_tf"] == "M15" and p["htf_tf"] == "NONE"
    assert p["risk"]["max_open_positions"] == 4 and p["session"]["end"] == "16:00" and "exit" not in p


def test_backtest_resolver_applies_f4_run_defaults():
    """RUN_DEFAULTS (no BE, no time stop, no RESCUE, close at session end) + the session = fixed-hour exit at 16:00."""
    specs = json.load(open(ROOT / "data" / "research_specs.json")) if (ROOT / "data" / "research_specs.json").exists() else {}

    class _NoDB:
        def query(self, *a, **k):
            return []

    for name in F4:
        cfg = bt.resolve(_NoDB(), {"strategy_name": name, "specs": specs, "symbols": ["EURUSD"], "signal_tf": "H1",
                                   "htf_tf": "NONE", "start_ts": run_bt.ts("2014-01-01"), "end_ts": run_bt.ts("2014-02-01"),
                                   "strategy": {}, "risk": HARNESS_RISK, "session": SESSION, "size_mode": "r_only"})
        assert cfg["strategy_name"] == name and cfg["live_rescue"] is None
        assert cfg["strategy"]["close_at_session_end"] is True and cfg["strategy"]["break_even_at_r"] == 0
        assert cfg["strategy"]["max_hold_minutes"] == 0 and cfg["max_hold_s"] is None
        assert cfg["schedule"]["start"] == "07:00" and cfg["schedule"]["end"] == "16:00" and cfg["exit"].is_base
        assert cfg["strategy"]["htf_timeframe"] == "NONE"


@pytest.mark.skipif(not (ROOT / "data" / "research.db").exists(), reason="research DB not present")
def test_runner_end_to_end_small_window(tmp_path):
    """Runner-level sanity on the research DB: one symbol, two TRAIN months, trades exit by 16:00 UTC at the latest."""
    out = tmp_path / "r.json"
    trades = tmp_path / "t.jsonl"
    rc = run_bt.main(["--db", str(ROOT / "data" / "research.db"), "--strategy", "disc-f4-sessmom", "--tf", "H1", "--htf",
                      "NONE", "--symbols", "EURUSD", "--start", "2014-01-01", "--end", "2014-03-01",
                      "--strategy-json", '{"k_move": 0.3}', "--risk-json", json.dumps(HARNESS_RISK),
                      "--session-json", json.dumps(SESSION), "--workers", "1", "--json-out", str(out),
                      "--trades-out", str(trades)])
    assert rc == 0
    r = json.loads(out.read_text())
    assert r["strategy"] == "disc-f4-sessmom" and r["summary"]["trades"] >= 5
    assert all(t[:4] == "2014" for t in r["by_year"])
    for line in trades.read_text().splitlines():
        t = json.loads(line)
        assert t["entry_utc"].endswith("T08:00:00Z")  # fill at the open of the bar after the 07:00–08:00 move bar
        assert t["exit_utc"][11:13] <= "16"  # fixed-hour exit (SESSION_END at 16:00 UTC at the latest)
        assert t["outcome"] in ("SL", "TP", "SESSION_END")
