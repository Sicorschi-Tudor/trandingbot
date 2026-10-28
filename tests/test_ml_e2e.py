"""ML end-to-end (integration of app.ml + app.ml_live + API): REAL sklearn bundles, the REAL registry (sha256),
REAL fs-v1/xs-v1 featurizers and app.ml.infer scorers (nothing injected), driven through the HTTP API.

shadow -> ml_decisions rows, identical intents/positions vs ML off; approve (checklist fails -> 409, override ->
200 + WARN) -> the approved entry model vetoes; retire -> back to the base strategy's behaviour."""
import time

import pytest

from app.db import DB
from app.engine import Engine
from tests.conftest import ADMIN, make_bars, make_settings, quote, zigzag
from tests.test_ml_live import BAD_ENTRY_METRICS, analysis_stub

sklearn = pytest.importorskip("sklearn")
F = pytest.importorskip("app.ml.features")
registry = pytest.importorskip("app.ml.registry")
infer = pytest.importorskip("app.ml.infer")

import numpy as np  # noqa: E402
from sklearn.dummy import DummyRegressor  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.pipeline import make_pipeline  # noqa: E402


def tiny_entry_bundle(pos_rate=0.1):
    """P(R>0) ~ pos_rate for any input (below the 0.5 threshold -> veto once approved)."""
    names = list(F.ENTRY_FEATURE_NAMES)
    rng = np.random.default_rng(3)
    X = rng.normal(size=(400, len(names) + 1))  # + symbol target-encoding column (infer.score_entry)
    y = (rng.random(400) < pos_rate).astype(int)
    clf = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True),
                        LogisticRegression(C=1e-3)).fit(X, y)
    return {"kind": "entry", "feature_schema": F.ENTRY_SCHEMA, "feature_names": names, "model": clf,
            "sym_te": {"map": {"EURUSD": 0.45}, "prior": 0.5}, "threshold": 0.5, "p_hi": 0.7}


def tiny_exit_bundle(value=-5.0):
    names = list(F.EXIT_FEATURE_NAMES)
    X = np.zeros((10, len(names)))
    reg = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True),
                        DummyRegressor(strategy="constant", constant=value)).fit(X, np.full(10, value))
    return {"kind": "exit", "feature_schema": F.EXIT_SCHEMA, "feature_names": names, "model": reg, "exit_margin": 0.1}


def new_engine(tmp_path, name, **kw):
    s = make_settings(tmp_path / name, **kw)
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    return eng


def feed(eng):
    end = int(time.time()) // 300 * 300 - 300
    for b in make_bars(zigzag(320, 0.0004, 0.0002), tf_sec=300, end_t=end):
        eng.rt.ingest_bar("EURUSD", "M5", b, publish=False)
    for b in make_bars(zigzag(220, 0.0010, 0.0005), tf_sec=3600, end_t=end // 3600 * 3600 - 3600):
        eng.rt.ingest_bar("EURUSD", "H1", b, publish=False)
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08))
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    return [b["t"] for b in eng.rt.closed_bars("EURUSD", "M5")]


def signal(eng, bar_time):
    a = analysis_stub(bar_time=bar_time)
    eng.record_analysis(a)
    eng.ml.on_analysis(a)
    sig = eng.create_signal(a)
    eng.try_execute(sig)
    s = eng.get_signal(sig["id"])
    it = eng.executor.get(s["intent_id"]) if s.get("intent_id") else None
    return {"status": s["status"], "reason": s["status_reason"], "volume": it and it["volume"],
            "state": it and it["state"]}


def scenario(eng):
    times = feed(eng)
    first = signal(eng, times[-3])
    now = time.time()
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07990, now=now))
    eng.track_trade_paths(now)
    eng.ml.tick(now)
    eng.pm.run(now)
    eng.ml.drain()
    pos = sorted((p["symbol"], p["side"], p["volume"], p["sl"], p["tp"]) for p in eng.paper.public_positions())
    intents = [(r["kind"], r["reason"]) for r in eng.db.query("SELECT kind, reason FROM intents ORDER BY id")]
    return times, {"first": first, "positions": pos, "intents": intents}


def register_shadow(eng, tmp_path, bundle, kind, metrics):
    mid = registry.register(eng.db, bundle, kind=kind, name="e2e", version="1", feature_schema=bundle["feature_schema"],
                            dataset_version="synthetic", params={}, metrics=metrics, trained_until_ts=None,
                            out_dir=tmp_path / "models")
    registry.to_shadow(eng.db, mid)
    return mid


def test_e2e_shadow_approve_veto_retire(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import api, bridge

    base_eng = new_engine(tmp_path, "base", ML_ENABLED=False)
    _, base = scenario(base_eng)
    assert base["first"]["status"] == "EXECUTED" and base["positions"]

    eng = new_engine(tmp_path, "ml")
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(bridge.router)
    app.state.engine = eng
    client = TestClient(app)

    e_id = register_shadow(eng, tmp_path, tiny_entry_bundle(), "entry", BAD_ENTRY_METRICS)
    x_id = register_shadow(eng, tmp_path, tiny_exit_bundle(), "exit", {})
    eng.ml.refresh_models()
    assert {e_id, x_id} <= set(eng.ml.models)

    # 1) shadow: real features + real scores logged, trading identical to ML off
    times, got = scenario(eng)
    assert got == base
    rows = eng.db.query("SELECT * FROM ml_decisions ORDER BY id")
    ent = [r for r in rows if r["model_id"] == e_id]
    ext = [r for r in rows if r["model_id"] == x_id]
    assert ent and ent[0]["kind"] == "entry" and ent[0]["would_action"] == "SKIP" and 0 < ent[0]["score"] < 0.5
    assert ent[0]["actual_action"] == "SIGNAL" and ent[0]["features_hash"]
    assert ext and ext[0]["would_action"] == "CLOSE" and ext[0]["actual_action"] == "HOLD"
    assert ext[0]["score"] == pytest.approx(-5.0)
    assert len(eng.paper.positions) == 1
    assert not [r for r in rows if r["kind"] == "entry_gate"]

    # live features equal the backtest/dataset featurizer on the same closed bars (single function per schema)
    bt = times[-3]
    ctx = eng.ml.entry_context("EURUSD", "BUY", bt, 1.0801, 1.0795, 1.0811, 0.8, eng.rt.quotes["EURUSD"])
    f_live = eng.ml.compute_features("entry", ctx)
    assert sum(1 for v in f_live.values() if v == v) >= len(F.ENTRY_FEATURE_NAMES) - 2  # mostly non-NaN
    assert F.features_hash(f_live) == ent[0]["features_hash"]

    # 2) API: list/detail
    lst = client.get("/api/v1/models").json()
    assert {m["id"] for m in lst["models"]} >= {e_id, x_id}
    det = client.get(f"/api/v1/models/{e_id}").json()
    assert det["checklist"]["passed"] is False and det["shadow"]["n_decisions"] >= 1

    # 3) approve: checklist fails -> 409; override path -> 200 + WARN
    r = client.post(f"/api/v1/models/{e_id}/approve", headers=ADMIN, json={"confirm": "APROB"})
    assert r.status_code == 409, r.text
    assert eng.ml.model_row(e_id)["status"] == "shadow"
    r = client.post(f"/api/v1/models/{e_id}/approve", headers=ADMIN,
                    json={"confirm": "APROB", "override": True, "confirm_risk": "ȘTIU RISCUL"})
    assert r.status_code == 200, r.text
    assert eng.ml.model_row(e_id)["status"] == "approved"
    assert registry.get(eng.db, e_id)["approved_by"] == "admin"
    warns = eng.db.query("SELECT level FROM system_events WHERE message LIKE '%OVERRIDE%'")
    assert any(w["level"] == "WARN" for w in warns)
    assert eng.ml.approved("entry").id == e_id

    # 4) the approved entry model vetoes (P < threshold), nothing is opened
    n_pos = len(eng.paper.positions)
    v = signal(eng, times[-2])
    assert v["status"] == "NO_TRADE" and v["reason"].startswith("ML:") and v["volume"] is None
    assert len(eng.paper.positions) == n_pos
    eng.ml.drain()
    gate = eng.db.query_one("SELECT * FROM ml_decisions WHERE kind='entry_gate' AND model_id=?", (e_id,))
    assert gate and gate["actual_action"] == "VETO"

    # 5) retire -> no approved model: same decision as the ML-off engine for the same next signal
    r = client.post(f"/api/v1/models/{e_id}/retire", headers=ADMIN)
    assert r.status_code == 200 and r.json()["restored"] is None
    assert eng.ml.approved("entry") is None
    after = signal(eng, times[-1])
    base_after = signal(base_eng, times[-1])
    assert after == base_after
    assert not (after["reason"] or "").startswith("ML:")
    eng.ml.stop()
    base_eng.ml.stop()


def test_retrain_files_pruned_but_never_registered_artifacts(tmp_path):
    import os

    from app import ml_live
    mdir = tmp_path / "models"
    eng = new_engine(tmp_path, "prune", ML_MODELS_DIR=str(mdir))
    mid = register_shadow(eng, tmp_path, tiny_exit_bundle(), "exit", {})
    art = eng.ml.model_row(mid)["artifact_path"]
    names = [f"ds-{i:03d}.joblib" for i in range(6)] + [f"summary-{i}.json" for i in range(ml_live.KEEP_SUMMARIES + 2)]
    for k, n in enumerate(names):
        p = mdir / n
        p.write_bytes(b"x")
        os.utime(p, (1_700_000_000 + k, 1_700_000_000 + k))
    os.utime(art, (1, 1))
    removed = eng.ml.prune_retrain_files()
    assert sorted(removed) == ["ds-000.joblib", "ds-001.joblib", "ds-002.joblib", "summary-0.json", "summary-1.json"]
    assert os.path.isfile(art) and len(list(mdir.glob("ds-*.joblib"))) == ml_live.KEEP_DATASETS
    eng.ml.stop()
