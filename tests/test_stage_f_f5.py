"""Stage F, family F5 (ML meta-labeling with the existing fs-v1 / xs-v1 layer): smoke tests.

- the research wrapper plugin disc-f5-ml imports through the registry and analyze() on synthetic H1/H4 bars returns
  exactly the trend-v1 output shape; threshold 0 reproduces the base plugin's decision, threshold 1 vetoes every
  candidate with a MARKET blocker (the direction is never inverted, only vetoed);
- no lookahead through the quote: the candidate side never depends on the current quote;
- research/f5_ml.py: fixed pre-registered periods, TRAIN rows purged of labels reaching into VALID, VALID rows
  embargoed, Gate-1 checklist;
- runner-level sanity: research/run_bt.py refuses TEST, app.backtest.resolve accepts the F5 harness with disc-f5-ml,
  and (when the research DB is present) a tiny dataset build + train-row cut works end to end.
"""
import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np
import pytest

from app import backtest as bt
from app import strategies as S
from app.config import Settings
from app.ml import features as F
from app.ml import train
from app.strategy import AnalysisContext
from tests.conftest import EURUSD_INFO

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "research"))
import f5_ml  # noqa: E402
import run_bt  # noqa: E402

SET = Settings(_env_file=None)
CFG = SET.default_strategy()
RISK = SET.default_risk()
H = 3600
T0 = 1388966400  # 2014-01-06 00:00 UTC (Monday) — inside TRAIN
SHAPE = {"symbol", "timeframe", "time_utc", "bar_time", "direction", "score", "probabilities", "probability_method",
         "reasons", "indicators", "regime", "spread_points", "entry", "stop_loss", "take_profit", "rr", "candidate"}


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


@pytest.fixture(scope="module")
def bundle_path(tmp_path_factory):
    """A tiny but real fs-v1 entry bundle (LogisticRegression pipeline on random features + symbol encoding)."""
    import joblib
    rnd = np.random.default_rng(3)
    n = 300
    X = rnd.normal(size=(n, len(F.ENTRY_FEATURE_NAMES)))
    y = (X[:, 0] + rnd.normal(size=n) > 0).astype(int)
    te = train.fit_symbol_te(np.array(["EURUSD"] * n), y)
    Xf = np.column_stack([X, train.apply_symbol_te(te, ["EURUSD"] * n)])
    model = train._lr(7).fit(Xf, y)
    b = {"kind": "entry", "feature_schema": F.ENTRY_SCHEMA, "feature_names": list(F.ENTRY_FEATURE_NAMES),
         "model_inputs": list(F.ENTRY_FEATURE_NAMES) + ["sym_te"], "model": model, "sym_te": te, "threshold": 0.5}
    p = tmp_path_factory.mktemp("f5") / "bundle.joblib"
    joblib.dump(b, p)
    return str(p)


def analyze(name, sig, htf, q=None, **over):
    cfg = S.merged_cfg(name, {**CFG, "signal_timeframe": "H1", "htf_timeframe": "H4"}, over)
    ctx = AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.0,
                          now_ts=sig[-1]["t"] + H)
    return S.get(name).analyze("EURUSD", sig, htf, q or q_at(sig[-1]["c"]), cfg, RISK, ctx)


def test_import_and_shape(bundle_path):
    mod = S.get("disc-f5-ml")
    assert mod.NAME == "disc-f5-ml" and mod.VERSION and mod.DESCRIPTION_RO and isinstance(mod.PARAMS, dict)
    bars = h1_walk(1200, seed=7)
    htf = h4_of(bars[:-1])
    ref = analyze("trend-v1", bars[-400:], htf[-200:])
    a = analyze("disc-f5-ml", bars[-400:], htf[-200:], ml_bundle=bundle_path)
    assert set(a) == set(ref) == SHAPE
    assert a["direction"] in ("BUY", "SELL", "HOLD") and a["timeframe"] == "H1"
    assert isinstance(a["reasons"], list) and isinstance(a["indicators"], dict)
    assert abs(sum(a["probabilities"].values()) - 1) < 1e-6


@pytest.mark.parametrize("base", ["trend-v1", "breakout-v1"])
def test_threshold_zero_is_base_and_threshold_one_vetoes(bundle_path, base):
    bars = h1_walk(3000, seed=11)
    htf_all = h4_of(bars)
    n_cand = 0
    for i in range(600, len(bars), 3):
        sig = bars[max(0, i - 399): i + 1]
        T = bars[i]["t"] + H
        htf = [h for h in htf_all if h["t"] + 4 * H <= T][-200:]
        ref = analyze(base, sig, htf)
        a0 = analyze("disc-f5-ml", sig, htf, ml_base=base, ml_bundle=bundle_path, ml_threshold=0.0)
        a1 = analyze("disc-f5-ml", sig, htf, ml_base=base, ml_bundle=bundle_path, ml_threshold=1.0)
        assert a0["direction"] == ref["direction"] and a0["entry"] == ref["entry"] and a0["stop_loss"] == ref["stop_loss"]
        if ref["candidate"]:
            n_cand += 1
            assert 0.0 <= a0["indicators"]["ml_p"] <= 1.0
            assert a1["direction"] == "HOLD" and a1["entry"] is None
            assert a1["candidate"]["side"] == ref["candidate"]["side"]  # vetoed, never inverted
            assert any(x.startswith("ML fs-v1") for x in a1["candidate"]["blockers"])
            assert a0["candidate"]["blockers"] == ref["candidate"]["blockers"]
        else:
            assert a1["candidate"] is None and a1["direction"] == "HOLD"
    assert n_cand >= 1


def test_ml_blocker_is_a_market_blocker(bundle_path):
    """The veto must not be in the neutral context set, otherwise the backtest would ignore it (scan_symbol)."""
    from app import strategy as strat
    ctx = AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.0, now_ts=T0,
                          open_exposures=[], corr_limit=2)
    neutral = set(strat.context_blockers("EURUSD", "BUY", ctx, RISK))
    assert not any(x.startswith("ML fs-v1") for x in neutral)


def test_side_does_not_depend_on_quote(bundle_path):
    bars = h1_walk(3000, seed=23)
    htf_all = h4_of(bars)
    for i in range(600, len(bars), 5):
        sig = bars[max(0, i - 399): i + 1]
        T = bars[i]["t"] + H
        htf = [h for h in htf_all if h["t"] + 4 * H <= T][-200:]
        c = sig[-1]["c"]
        a1 = analyze("disc-f5-ml", sig, htf, q_at(c), ml_bundle=bundle_path)
        a2 = analyze("disc-f5-ml", sig, htf, q_at(round(c + 0.0002, 5)), ml_bundle=bundle_path)
        s1 = (a1["candidate"] or {}).get("side")
        s2 = (a2["candidate"] or {}).get("side")
        assert s1 == s2  # the quote may move the fill level and the ML score, never the side


def test_f5_periods_and_row_cuts():
    assert f5_ml.T_TRAIN0 == run_bt.ts("2013-06-01") and f5_ml.T_VALID0 == run_bt.ts("2019-01-01")
    assert f5_ml.T_VALID1 == run_bt.ts("2021-01-01") and f5_ml.T_NOW == run_bt.ts(run_bt.NOW_TS)
    p = f5_ml.dataset_params("trend-v1", {}, hold_hours=24)
    assert p["start_ts"] == f5_ml.T_TRAIN0 and p["end_ts"] == f5_ml.T_VALID1 and p["signal_tf"] == "H1"
    assert p["htf_tf"] == "H4" and len(p["symbols"]) == 12 and p["risk"]["max_open_positions"] == 4
    assert p["exit"] == {"mode": "base", "params": {"max_hold_hours": 24}} and p["strategy"]["rescue_enabled"] is False
    v0 = f5_ml.T_VALID0
    ds = {"t": np.array([v0 - 10 * 86400, v0 - 86400, v0 - 3600, v0 + 3600, v0 + f5_ml.EMBARGO_S, f5_ml.T_VALID1 + 1]),
          "t_end": np.array([v0 - 9 * 86400, v0 + 3600, v0 + 7200, v0 + 86400, v0 + f5_ml.EMBARGO_S + 86400,
                             f5_ml.T_VALID1 + 86400]),
          "r": np.array([0.5, -1.0, 0.2, 0.1, -0.3, 0.4]), "feature_names": ["a"], "schema": "x"}
    assert f5_ml.train_rows(ds).tolist() == [0]  # rows 1, 2 reach into VALID -> purged
    assert f5_ml.valid_rows(ds).tolist() == [4]  # row 3 inside the embargo, row 5 after VALID
    sub = f5_ml.subset(ds, [0, 4])
    assert sub["r"].tolist() == [0.5, -0.3] and sub["feature_names"] == ["a"]
    g = f5_ml.gate1({"n": 120, "avg_r": 0.05, "ci90": [0.01, 0.09], "pf": 1.2}, {"2019": {"avg_r": 0.1}, "2020": {"avg_r": -0.1}})
    assert g["passed"]
    g = f5_ml.gate1({"n": 120, "avg_r": 0.05, "ci90": [-0.03, 0.09], "pf": 1.2}, {"2019": {"avg_r": 0.1}})
    assert not g["passed"] and not g["ci90_lo_gt_-0.02"]


def test_runner_refuses_test_and_accepts_f5_harness(bundle_path):
    assert run_bt.SPLITS["train"] == ("2013-06-01", "2019-01-01") and run_bt.SPLITS["valid"] == ("2019-01-01", "2021-01-01")
    with pytest.raises(SystemExit):
        run_bt.main(["--strategy", "disc-f5-ml", "--split", "test"])
    with pytest.raises(SystemExit):
        run_bt.main(["--strategy", "disc-f5-ml", "--start", "2020-06-01", "--end", "2021-06-01"])
    specs_path = ROOT / "data" / "research_specs.json"
    specs = json.load(open(specs_path)) if specs_path.exists() else {}

    class _NoDB:
        def query(self, *a, **k):
            return []

    strat = {**f5_ml.HARNESS_STRATEGY, "ml_base": "breakout-v1", "ml_bundle": bundle_path}
    cfg = bt.resolve(_NoDB(), {"strategy_name": "disc-f5-ml", "specs": specs, "symbols": ["EURUSD"], "signal_tf": "H1",
                               "htf_tf": "H4", "start_ts": run_bt.ts("2014-01-01"), "end_ts": run_bt.ts("2014-02-01"),
                               "strategy": strat, "risk": f5_ml.HARNESS_RISK, "session": None, "size_mode": "r_only",
                               "exit": {"mode": "base", "params": {"max_hold_hours": 24}}})
    assert cfg["strategy_name"] == "disc-f5-ml" and cfg["strategy"]["ml_base"] == "breakout-v1"
    assert cfg["strategy"]["ml_bundle"] == bundle_path and cfg["live_rescue"] is None
    assert cfg["max_hold_s"] == 24 * 3600 and cfg["risk"]["max_open_positions"] == 4 and cfg["schedule"] is None
    if specs_path.exists():
        a = argparse.Namespace(specs=str(specs_path), split="valid", start=None, end=None, strategy_json=json.dumps(strat),
                               risk_json=json.dumps(f5_ml.HARNESS_RISK), tf="H1", cost="stats", commission=7.0,
                               slippage=1.0, swap_mode=None, spread=None, strategy="disc-f5-ml", workers=1,
                               symbols=",".join(f5_ml.SYMBOLS), htf="H4", equity=10000.0, exit="base",
                               exit_params='{"max_hold_hours": 24}', session_json="")
        p = run_bt.build_params(a)
        assert p["start_ts"] == f5_ml.T_VALID0 and p["end_ts"] == f5_ml.T_VALID1 and len(p["symbols"]) == 12


@pytest.mark.skipif(not (ROOT / "data" / "research.db").exists(), reason="research DB not present")
def test_dataset_build_small_window_and_train_cut():
    """dataset.build with the F5 params on one symbol / two months: fs-v1 rows, labels after the decision, finite X."""
    from app.ml import dataset
    specs = json.load(open(ROOT / "data" / "research_specs.json"))
    p = f5_ml.dataset_params("trend-v1", specs, workers=1)
    p.update(symbols=["EURUSD"], start_ts=run_bt.ts("2014-01-01"), end_ts=run_bt.ts("2014-03-01"))
    db = bt.ReadOnlyDB(str(ROOT / "data" / "research.db"))
    try:
        ds = dataset.build(db, p)
    finally:
        db.close()
    e = ds["entry"]
    assert e["schema"] == F.ENTRY_SCHEMA and e["X"].shape == (len(e["t"]), len(F.ENTRY_FEATURE_NAMES))
    assert len(e["t"]) > 50 and np.all(e["t_end"] >= e["t"]) and np.isfinite(e["X"]).mean() > 0.95
    assert ds["meta"]["exit_policy"] == {"mode": "base", "params": {"max_hold_hours": 24.0}}
    assert len(f5_ml.train_rows(e)) == len(e["t"]) and len(f5_ml.valid_rows(e)) == 0  # all inside TRAIN
