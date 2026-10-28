"""ML pipeline: features without lookahead, dataset from the backtest, time-ordered splits (purge/embargo),
calibration fold, deterministic training, exit target, registry lifecycle + sha256, inference helpers."""
import math
import os

import numpy as np
import pytest

from app.db import DB
from app.ml import dataset, features as F, infer, registry, train
from tests.conftest import make_settings
from tests.test_stage_b_backtest import SPEC, T0, resample, store, walk


# ---- helpers ----------------------------------------------------------------------------------

def _bars(n=700, sec=300, seed=3):
    return walk(n, sec, T0, seed)


def _cand(bars, side="BUY"):
    c = bars[-1]["c"]
    sg = 1 if side == "BUY" else -1
    return {"side": side, "entry": c + 0.00003, "sl": c - sg * 0.0012, "tp": c + sg * 0.002, "score": 0.8}


def _quote(c):
    return {"bid": c, "ask": c + 0.00003, "point": 0.00001, "spread_points": 3, "digits": 5}


def _same(a: dict, b: dict) -> bool:
    return all((math.isnan(a[k]) and math.isnan(b[k])) or a[k] == b[k] for k in a)


@pytest.fixture(autouse=True)
def small_models(monkeypatch):
    # fewer boosting rounds keep the suite fast; behaviour under test is unchanged
    monkeypatch.setattr(train, "HGB_PARAMS", dict(train.HGB_PARAMS, max_iter=80))


@pytest.fixture
def mdb(tmp_path):
    make_settings(tmp_path)
    d = DB(tmp_path / "ml.db")
    yield d
    d.close()


# ---- features: no lookahead, one code path --------------------------------------------------------

def test_entry_features_schema_and_no_lookahead():
    m5 = _bars(1100)
    h1 = resample(m5, 3600)
    i = 900
    T = m5[i]["t"] + 300
    htf = [b for b in h1 if b["t"] + 3600 <= T]
    f0 = F.entry_features(m5[i - 399: i + 1], htf[-200:], _quote(m5[i]["c"]), _cand(m5[: i + 1]))
    assert list(f0) == F.ENTRY_FEATURE_NAMES and all(isinstance(v, float) for v in f0.values())
    assert sum(math.isnan(v) for v in f0.values()) == 0
    # future bars (any values) cannot change the features at i: they are not part of the closed-bar window
    fut = [dict(b, c=b["c"] * 1.05, h=b["h"] * 1.05) for b in m5[i + 1:]]
    series = m5[: i + 1] + fut
    f1 = F.entry_features(series[i - 399: i + 1], htf[-200:], _quote(m5[i]["c"]), _cand(m5[: i + 1]))
    assert _same(f0, f1)
    # window invariance: live (400 bars) == any window ≥ FS_WINDOW
    f2 = F.entry_features(m5[i - F.FS_WINDOW + 1: i + 1], htf[-200:], _quote(m5[i]["c"]), _cand(m5[: i + 1]))
    f3 = F.entry_features(m5[: i + 1], htf, _quote(m5[i]["c"]), _cand(m5[: i + 1]))
    assert _same(f0, f2) and _same(f0, f3)
    # the decision bar itself does matter (sanity: features are not constant)
    alt = m5[i - 399: i] + [dict(m5[i], c=m5[i]["c"] + 0.002, h=m5[i]["h"] + 0.002)]
    assert not _same(f0, F.entry_features(alt, htf[-200:], _quote(m5[i]["c"]), _cand(m5[: i + 1])))
    # side-oriented copies flip with the side
    fs = F.entry_features(m5[i - 399: i + 1], htf[-200:], _quote(m5[i]["c"]), _cand(m5[: i + 1], "SELL"))
    assert fs["side"] == -1.0 and fs["ema20_dist_dir"] == pytest.approx(-f0["ema20_dist_dir"])


def test_exit_features_no_lookahead_and_values():
    m5 = _bars(500)
    i = 400
    entry = m5[380]["o"]
    pos = {"side": "BUY", "entry": entry, "sl0": entry - 0.001, "sl": entry - 0.001, "tp": entry + 0.002,
           "opened_ts": m5[380]["t"], "mfe_r": 0.4, "mae_r": 0.2, "spread_points": 3, "point": 0.00001}
    x0 = F.exit_features(m5[: i + 1], pos)
    assert list(x0) == F.EXIT_FEATURE_NAMES
    x1 = F.exit_features((m5[: i + 1] + [dict(b, c=9.9) for b in m5[i + 1:]])[: i + 1], pos)
    assert _same(x0, x1)
    assert x0["r_now"] == pytest.approx((m5[i]["c"] - entry) / 0.001)
    assert x0["dist_sl_r"] == pytest.approx(x0["r_now"] + 1.0)
    assert x0["dist_tp_r"] == pytest.approx(2.0 - x0["r_now"])
    assert x0["minutes_in_trade"] == pytest.approx((m5[i]["t"] + 300 - m5[380]["t"]) / 60)
    assert x0["mfe_r"] >= max(0.4, x0["r_now"]) - 1e-12
    r3 = (m5[i - 3]["c"] - entry) / 0.001
    assert x0["dr_3"] == pytest.approx(x0["r_now"] - r3)
    # SELL closes at the ask: spread counts against it
    xs = F.exit_features(m5[: i + 1], dict(pos, side="SELL", sl0=entry + 0.001, sl=entry + 0.001, tp=entry - 0.002))
    assert xs["r_now"] == pytest.approx((entry - (m5[i]["c"] + 3e-5)) / 0.001)


# ---- dataset through the backtest -------------------------------------------------------------------

@pytest.fixture
def market_db(tmp_path):
    make_settings(tmp_path)
    d = DB(tmp_path / "bt.db")
    for sym, seed, p0 in (("EURUSD", 11, 1.08), ("GBPUSD", 12, 1.27)):
        m5 = walk(14 * 288, 300, T0, seed, p0=p0)
        store(d, sym, "M5", m5)
        store(d, sym, "H1", resample(m5, 3600))
    yield d
    d.close()


def _ds_params():
    end = T0 + 14 * 86400 * 7 // 5  # covers 14 trading days (weekends skipped by walk)
    return {"strategy_name": "trend-v1", "signal_tf": "M5", "htf_tf": "H1", "symbols": ["EURUSD", "GBPUSD"],
            "start_ts": T0 + 2 * 86400, "end_ts": end, "now_ts": end + 86400, "session": None, "workers": 1,
            "specs": SPEC, "strategy": {"min_score": 3}, "cost": {"mode": "fixed", "fixed_spread_pts": 5}}


def test_dataset_rows_use_only_past_bars_and_reuse_backtest(market_db):
    ds = dataset.build(market_db, _ds_params())
    e, x, meta = ds["entry"], ds["exit"], ds["meta"]
    assert meta["n_entry"] > 30 and meta["n_trades"] > 5 and meta["n_exit"] > 20
    assert meta["exit_dropped"]["path_mismatch"] == 0 and meta["dataset_version"].startswith("ds-")
    assert np.all(np.diff(e["t"]) >= 0) and np.all(e["t_end"] >= e["t"])
    assert e["taken"].sum() == meta["n_trades"] or e["taken"].sum() >= meta["n_trades"] - 1
    # every row recomputed from bars strictly before its decision time gives the same features (no lookahead)
    from app.backtest import load_bars
    for k in range(0, len(e["t"]), max(1, len(e["t"]) // 15)):
        sym, T = e["symbol"][k], int(e["t"][k])
        past = load_bars(market_db, sym, "M5", T - 12 * 86400, T, now_ts=T)
        htf = load_bars(market_db, sym, "H1", T - 20 * 86400, T, now_ts=T)
        assert past[-1]["t"] + 300 == T and len(past) >= F.FS_WINDOW
        side = "BUY" if e["X"][k][F.ENTRY_FEATURE_NAMES.index("side")] > 0 else "SELL"
        sl_atr = e["X"][k][F.ENTRY_FEATURE_NAMES.index("sl_dist_atr")]
        f = F.entry_features(past[-400:], htf[-200:], {"bid": past[-1]["c"], "point": 1e-5, "spread_points": 5},
                             {"side": side, "entry": 0.0, "sl": 0.0, "tp": 0.0, "score": e["score"][k]})
        for name in ("ret_1_atr", "ret_24_atr", "ema50_dist_atr", "rsi14", "macd_hist_atr", "atr_pct100",
                     "htf_slope_atr", "htf_dist_atr", "hour_sin", "weekday", "spread_atr"):
            assert f[name] == pytest.approx(e["X"][k][F.ENTRY_FEATURE_NAMES.index(name)], rel=1e-9, abs=1e-12), name
        assert sl_atr > 0
    # exit rows: target = gross_R - r_now (commission paid either way), rows strictly before the exit
    assert np.all(x["t"] < x["t_end"]) and np.all(x["t"] > x["t_entry"])
    np.testing.assert_allclose(x["r_now"], x["X"][:, F.EXIT_FEATURE_NAMES.index("r_now")])


def test_exit_target_definition():
    # held to the end: final net R 1.2 (gross 1.3, commission 0.1R); now at +0.5R
    t = dataset.exit_target(1.2, 1.3, 0.5)
    assert t == pytest.approx(0.8)  # = 1.2 - (0.5 - 0.1)
    assert dataset.exit_target(1.2, 1.3, 0.5, exit_cost_r=0.05) == pytest.approx(0.85)
    # closing now realizes final - target = r_now - cost
    assert 1.2 - t == pytest.approx(0.5 - 0.1)
    # exit policy uses exactly that realized value
    ds = {"trade_id": np.array(["a", "a", "a"]), "t": np.array([1, 2, 3]), "final_r": np.array([-1.0, -1.0, -1.0]),
          "target": np.array([-0.5, -1.2, -0.3])}
    pol, base = train.exit_policy_r(ds, np.array([0, 1, 2]), np.array([0.1, -0.9, -2.0]), 0.5)
    assert base == [-1.0] and pol == [pytest.approx(-1.0 + 1.2)]  # closed at row 1 (first pred < -0.5)
    pol, _ = train.exit_policy_r(ds, np.array([0, 1, 2]), np.array([0.1, -0.9, -2.0]), math.inf)
    assert pol == [-1.0]


# ---- splits ---------------------------------------------------------------------------------------------

def _times(n=2000, seed=1, hold=3 * 3600):
    rng = np.random.default_rng(seed)
    t = np.sort(rng.uniform(0, 120 * 86400, n)).astype(int)
    return t, t + rng.integers(300, hold, n)


def test_walk_forward_time_ordered_purged_embargoed():
    t, t_end = _times()
    emb = 86400
    folds = train.walk_forward(t, t_end, n_folds=4, embargo_s=emb)
    assert len(folds) == 4
    prev_test_end = None
    for f in folds:
        segs = [f["train"], f["cal"], f["val"], f["test"]]
        assert all(len(s) for s in segs)
        b = f["bounds"]
        for k in range(3):
            # strictly later, purged (labels end before the next segment), embargoed (gap after the boundary)
            assert t[segs[k]].max() < t[segs[k + 1]].min()
            assert t_end[segs[k]].max() < b[k + 1][0]
            assert t[segs[k + 1]].min() >= b[k + 1][0] + emb
        assert len(set().union(*[set(s.tolist()) for s in segs])) == sum(len(s) for s in segs)  # disjoint
        # calibration fold: disjoint from and later than train
        assert not set(f["cal"].tolist()) & set(f["train"].tolist())
        assert t[f["cal"]].min() > t_end[f["train"]].max()
        if prev_test_end is not None:
            assert b[3][0] == pytest.approx(prev_test_end)  # contiguous test blocks, expanding train
        prev_test_end = b[3][1]
    assert len(folds[-1]["train"]) > len(folds[0]["train"])
    fs = train.final_split(t, t_end, embargo_s=emb)
    assert t[fs["train"]].max() < t[fs["cal"]].min() <= t[fs["cal"]].max() < t[fs["val"]].min()
    assert t_end[fs["train"]].max() < fs["bounds"][1][0] and t_end[fs["cal"]].max() < fs["bounds"][2][0]


def test_purge_drops_overlapping_labels():
    t = np.array([0, 10, 20, 30, 40, 50])
    t_end = np.array([5, 35, 25, 45, 55, 60])
    tr, te = train.purged_segments(t, t_end, [(0, 30), (30, 70)], embargo_s=0)
    assert tr.tolist() == [0, 2]  # sample 1 (label ends at 35 ≥ 30) purged
    assert te.tolist() == [3, 4, 5]
    tr, te = train.purged_segments(t, t_end, [(0, 30), (30, 70)], embargo_s=10)
    assert te.tolist() == [4, 5]


# ---- training ----------------------------------------------------------------------------------------------

def _synthetic_entry(n=3000, seed=5):
    rng = np.random.default_rng(seed)
    t, t_end = _times(n, seed)
    X = rng.normal(size=(n, len(F.ENTRY_FEATURE_NAMES)))
    X[rng.random((n, X.shape[1])) < 0.02] = np.nan
    sig = np.nan_to_num(X[:, 0])
    win = rng.random(n) < 1 / (1 + np.exp(-(1.5 * sig - 0.6)))
    r = np.where(win, 1.5, -1.0) - 0.05
    sym = np.where(rng.random(n) < 0.5, "EURUSD", "GBPUSD")
    return {"t": t, "t_end": t_end, "X": X, "r": r, "symbol": sym, "taken": rng.random(n) < 0.3,
            "feature_names": list(F.ENTRY_FEATURE_NAMES), "schema": F.ENTRY_SCHEMA}


def _synthetic_exit(n_trades=400, seed=6):
    rng = np.random.default_rng(seed)
    rows = {k: [] for k in ("t", "t_entry", "t_end", "trade_id", "r_now", "final_r", "target")}
    X = []
    t0 = 0
    for k in range(n_trades):
        t0 += int(rng.integers(3600, 6 * 3600))
        nb = int(rng.integers(3, 12))
        final = float(rng.choice([-1.0, 1.5]))
        for j in range(nb):
            r_now = float(rng.normal(0, 0.5))
            x = rng.normal(size=len(F.EXIT_FEATURE_NAMES))
            x[0] = r_now
            X.append(x)
            rows["t"].append(t0 + 300 * (j + 1))
            rows["t_entry"].append(t0)
            rows["t_end"].append(t0 + 300 * (nb + 1))
            rows["trade_id"].append(f"T{k}")
            rows["r_now"].append(r_now)
            rows["final_r"].append(final)
            rows["target"].append(dataset.exit_target(final, final + 0.05, r_now))
    out = {k: np.asarray(v) for k, v in rows.items()}
    out.update(X=np.asarray(X), feature_names=list(F.EXIT_FEATURE_NAMES), schema=F.EXIT_SCHEMA)
    return out


def test_entry_training_deterministic_calibrated_and_finds_signal():
    ds = _synthetic_entry()
    b1, m1 = train.train_entry(ds, n_folds=3, seed=7)
    b2, m2 = train.train_entry(ds, n_folds=3, seed=7)
    Xq = np.column_stack([ds["X"][:200], np.full(200, 0.4)])
    np.testing.assert_array_equal(b1["model"].predict_proba(Xq), b2["model"].predict_proba(Xq))
    assert m1["oos"]["model"]["brier"] == m2["oos"]["model"]["brier"]
    assert m1["oos"]["policy"] == m2["oos"]["policy"] and b1["threshold"] == b2["threshold"]
    o = m1["oos"]
    assert o["model"]["auc"] > 0.65 and o["model"]["brier"] < o["model"]["brier_climatology"]
    assert o["model"]["calibration"] and o["model"]["ece"] is not None and "log_loss" in o["model"]
    assert o["baseline_lr"]["auc"] > 0.6
    assert o["policy"]["avg_r"] > o["baseline_take_all"]["avg_r"]
    assert set(m1["checklist"]) == {"oos_avg_r_pos", "ci_lower_ok", "beats_baseline", "calibration_ok", "passed"}
    assert b1["feature_schema"] == "fs-v1" and b1["model_inputs"][-1] == "sym_te"
    assert set(b1["sym_te"]["map"]) == {"EURUSD", "GBPUSD"}
    # isotonic calibrator on a frozen (prefit) model
    assert type(b1["model"]).__name__ == "CalibratedClassifierCV"


def test_symbol_encoding_uses_train_rows_only():
    te = train.fit_symbol_te(np.array(["A", "A", "B"]), np.array([1, 1, 0]), smooth=0)
    assert te["map"] == {"A": 1.0, "B": 0.0}
    assert train.apply_symbol_te(te, ["C"])[0] == pytest.approx(te["prior"])
    ds = _synthetic_entry(1200)
    f = train.walk_forward(ds["t"], ds["t_end"], n_folds=2)[0]
    ds2 = dict(ds, r=ds["r"].copy())
    ds2["r"][np.concatenate([f["cal"], f["val"], f["test"]])] = 5.0  # later outcomes change
    m1 = train.fit_entry(ds, f["train"], f["cal"], np.array([], dtype=int))
    te_a = train.fit_symbol_te(ds["symbol"][f["train"]], (ds["r"][f["train"]] > 0).astype(int))
    te_b = train.fit_symbol_te(ds2["symbol"][f["train"]], (ds2["r"][f["train"]] > 0).astype(int))
    assert te_a == te_b == m1["te"]


def test_exit_training_deterministic_policy_vs_base():
    ds = _synthetic_exit()
    b1, m1 = train.train_exit(ds, n_folds=3, seed=7)
    b2, m2 = train.train_exit(ds, n_folds=3, seed=7)
    np.testing.assert_array_equal(b1["model"].predict(ds["X"][:50]), b2["model"].predict(ds["X"][:50]))
    assert m1 == m2 or m1["oos"] == m2["oos"]
    o = m1["oos"]
    assert o["policy"]["n"] == o["baseline_base_exits"]["n"] > 0
    assert {"mae", "rmse", "r2", "calib_mae"} <= set(o["model"])
    assert b1["feature_schema"] == "xs-v1"


def test_threshold_and_margin_selection():
    p = np.linspace(0, 1, 200)
    r = np.where(p > 0.6, 1.0, -1.0)
    thr = train.choose_threshold(p, r)
    assert 0.55 <= thr <= 0.75
    assert train.choose_threshold(p, np.ones(200)) == 0.0  # nothing beats take-all -> take all


# ---- registry ----------------------------------------------------------------------------------------------------

def _reg(db, tmp_path, kind="entry", name="m", ts=None):
    bundle = {"kind": kind, "feature_schema": "fs-v1" if kind == "entry" else "xs-v1", "w": [1, 2, 3]}
    return registry.register(db, bundle, kind=kind, name=name, version="1", feature_schema=bundle["feature_schema"],
                             dataset_version="ds-x", params={"a": 1}, metrics={"x": float("nan"), "y": np.float64(2)},
                             trained_until_ts=T0, out_dir=tmp_path / "models", created_ts=ts)


def test_registry_sha256_and_lifecycle(mdb, tmp_path):
    a = _reg(mdb, tmp_path, ts=T0)
    row = registry.get(mdb, a)
    assert row["status"] == "candidate" and row["metrics"] == {"x": None, "y": 2.0}
    assert len(row["sha256"]) == 64 and os.path.isfile(row["artifact_path"])
    assert registry.load(mdb, a)["w"] == [1, 2, 3]
    # no shortcut to approved
    with pytest.raises(registry.RegistryError):
        registry.approve(mdb, a, "tudor")
    registry.to_shadow(mdb, a)
    with pytest.raises(registry.RegistryError):
        registry.approve(mdb, a, "")
    with pytest.raises(registry.RegistryError):
        registry.approve(mdb, a, None)
    registry.approve(mdb, a, "tudor")
    r = registry.get(mdb, a)
    assert r["status"] == "approved" and r["approved_by"] == "tudor" and r["approved_at"]
    with pytest.raises(registry.RegistryError):
        registry.to_shadow(mdb, a)
    # a second model: candidate -> shadow -> approved archives the first
    b = _reg(mdb, tmp_path, ts=T0 + 60)
    registry.to_shadow(mdb, b)
    registry.approve(mdb, b, "tudor")
    assert registry.get(mdb, a)["status"] == "archived"
    assert [m["id"] for m in registry.active(mdb, "entry")] == [b]
    # rollback restores the previously approved one
    res = registry.retire(mdb, b, "tudor", restore_previous=True)
    assert res == {"retired": b, "restored": a}
    assert registry.get(mdb, a)["status"] == "approved" and registry.get(mdb, b)["status"] == "archived"
    # archived is terminal
    with pytest.raises(registry.RegistryError):
        registry.to_shadow(mdb, b)
    # plain retire -> none approved
    assert registry.retire(mdb, a, "tudor") == {"retired": a, "restored": None}
    assert registry.active(mdb, "entry") == []


def test_registry_tampered_artifact_is_refused(mdb, tmp_path):
    a = _reg(mdb, tmp_path)
    path = registry.get(mdb, a)["artifact_path"]
    with open(path, "ab") as f:
        f.write(b"x")
    with pytest.raises(registry.RegistryError, match="sha256"):
        registry.load(mdb, a)
    registry.to_shadow(mdb, a)
    with pytest.raises(registry.RegistryError):
        registry.approve(mdb, a, "tudor")  # an artifact that does not verify can never be approved
    cache = infer.ModelCache()
    assert cache.active(mdb, "entry") == [] and cache.errors and cache.errors[0][0] == a


def test_promote_newest_candidate_archives_old_shadows_never_approves(mdb, tmp_path):
    a = _reg(mdb, tmp_path, ts=T0)
    assert registry.promote_newest_candidate(mdb, "entry") == a
    b = _reg(mdb, tmp_path, ts=T0 + 10)
    c = _reg(mdb, tmp_path, kind="exit", ts=T0 + 20)
    assert registry.promote_newest_candidate(mdb, "entry") == b
    assert registry.get(mdb, a)["status"] == "archived" and registry.get(mdb, b)["status"] == "shadow"
    assert registry.get(mdb, c)["status"] == "candidate"
    assert registry.promote_newest_candidate(mdb, "entry") is None
    assert not registry.list_models(mdb, status="approved")


def test_register_record_from_subprocess(mdb, tmp_path):
    rec = registry.make_record({"kind": "exit"}, kind="exit", name="n", version="1", feature_schema="xs-v1",
                               dataset_version="ds", params={}, metrics={}, trained_until_ts=None,
                               out_dir=tmp_path / "m")
    assert registry.register_record(mdb, dict(rec, status="approved")) == rec["id"]
    assert registry.get(mdb, rec["id"])["status"] == "candidate"  # always inserted as candidate
    with pytest.raises(registry.RegistryError):
        registry.register_record(mdb, rec)  # duplicate
    with pytest.raises(registry.RegistryError):
        registry.register_record(mdb, dict(rec, id="other", sha256="0" * 64))


def test_db_tables_additive(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
    c.execute("INSERT INTO settings VALUES('k', '1')")
    c.commit()
    c.close()
    d = DB(p)
    names = {r["name"] for r in d.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"ml_models", "ml_decisions"} <= names
    assert d.get_setting("k") == 1
    d.close()


# ---- inference ------------------------------------------------------------------------------------------------------

def test_infer_scores_and_actions(mdb, tmp_path):
    ds = _synthetic_entry(1500)
    bundle, _ = train.train_entry(ds, n_folds=2)
    mid = registry.register(mdb, bundle, kind="entry", name="e", version="1", feature_schema="fs-v1",
                            dataset_version="d", params={}, metrics={}, trained_until_ts=None,
                            out_dir=tmp_path / "models")
    cache = infer.ModelCache()
    assert cache.active(mdb, "entry") == []  # candidates are never scored live
    registry.to_shadow(mdb, mid)
    [(row, b)] = cache.active(mdb, "entry")
    feats = dict(zip(F.ENTRY_FEATURE_NAMES, ds["X"][0]))
    p = infer.score_entry(b, feats, "EURUSD")
    x = np.column_stack([ds["X"][:1], [b["sym_te"]["map"]["EURUSD"]]])
    assert p == pytest.approx(float(bundle["model"].predict_proba(x)[0, 1]))
    b = dict(b, threshold=0.4, p_hi=0.6)
    assert infer.entry_action(b, 0.39)["veto"] is True
    assert infer.entry_action(b, 0.4, 0.5, 1.0)["size_factor"] == 0.5
    assert infer.entry_action(b, 0.9, 0.5, 1.0)["size_factor"] == 1.0
    assert infer.entry_action(b, 0.5, 0.5, 1.5)["size_factor"] == pytest.approx(1.0)
    assert infer.entry_action(b, 0.9, 0.5, 5.0)["size_factor"] == 2.0  # hard cap
    with pytest.raises(ValueError):
        infer.score_exit(b, {})
    assert infer.exit_action({"exit_margin": 0.2}, -0.25) is True
    assert infer.exit_action({"exit_margin": 0.2}, -0.1) is False
    assert infer.exit_action({"exit_margin": None}, -5) is False  # no margin chosen -> never closes
    h = F.features_hash(feats, F.ENTRY_FEATURE_NAMES)
    infer.log_decision(mdb, mid, "entry", "EURUSD", "a1", h, p, "TAKE", "TAKE", ts=1.0)
    r = mdb.query_one("SELECT * FROM ml_decisions")
    assert r["model_id"] == mid and r["features_hash"] == h and r["would_action"] == "TAKE"


def test_cli_train_end_to_end(market_db, tmp_path):
    from app.ml.__main__ import main
    ds = dataset.build(market_db, _ds_params())
    p = str(tmp_path / "ds.joblib")
    dataset.save(ds, p)
    # tiny synthetic market: training may refuse (too few rows); the dataset path and registry flags still work
    rc = main(["train", "--db", str(tmp_path / "bt.db"), "--out", str(tmp_path / "out"), "--dataset", p,
               "--registry-db", str(tmp_path / "reg.db"), "--shadow", "--folds", "2", "--json"])
    assert rc in (0, 2)
    reg = DB(tmp_path / "reg.db")
    assert not registry.list_models(reg, status="approved")
    reg.close()
