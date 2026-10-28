"""ML live integration (app.ml_live): shadow logging never changes trading, approved-model guardrails, retrain glue."""
import json
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app import risk as risk_mod
from app.db import DB
from app.engine import Engine
from tests.conftest import ADMIN, make_settings, quote


def analysis_stub(direction="BUY", bar_time=1730000000):
    return {
        "symbol": "EURUSD", "timeframe": "M5", "time_utc": "2026-10-28T08:00:00Z", "bar_time": bar_time,
        "direction": direction, "score": 0.8, "probabilities": {"up": 0.7, "down": 0.18, "neutral": 0.12},
        "probability_method": "heuristic-v1 (NOT calibrated)", "reasons": ["test"],
        "indicators": {"ema_fast": 1.08, "ema_slow": 1.079, "rsi": 60, "atr": 0.0004, "macd_hist": 0.00001, "htf_trend": "UP"},
        "regime": "TREND_UP", "spread_points": 10, "entry": 1.0801, "stop_loss": 1.0795, "take_profit": 1.0811, "rr": 1.67,
        "candidate": {"side": direction, "entry": 1.0801, "stop_loss": 1.0795, "take_profit": 1.0811, "score": 0.8,
                      "blockers": []},
    }


GOOD_ENTRY_METRICS = {
    "oos": {"policy": {"n": 220, "avg_r": 0.12, "ci90": [0.02, 0.21], "pf": 1.45, "win_rate": 0.52},
            "baseline_take_all": {"n": 900, "avg_r": -0.04, "ci90": [-0.09, 0.01], "pf": 0.93},
            "model": {"n": 900, "brier": 0.231, "brier_climatology": 0.245, "log_loss": 0.65, "auc": 0.58, "ece": 0.031,
                      "calibration": [{"n": 300, "mean_p": 0.31, "frac_pos": 0.30}, {"n": 300, "mean_p": 0.45, "frac_pos": 0.47},
                                      {"n": 300, "mean_p": 0.6, "frac_pos": 0.58}]},
            "coverage": 0.25},
    "final": {"threshold": 0.5, "p_hi": 0.7},
}
BAD_ENTRY_METRICS = {"oos": {"policy": {"n": 50, "avg_r": -0.03, "ci90": [-0.2, 0.1], "pf": 0.9},
                             "baseline_take_all": {"n": 900, "avg_r": -0.01, "ci90": [-0.05, 0.03]},
                             "model": {"ece": 0.12, "brier": 0.26}}, "final": {"threshold": 0.5}}
GOOD_EXIT_METRICS = {"oos": {"policy": {"n": 120, "avg_r": 0.08, "ci90": [0.01, 0.15], "pf": 1.3},
                             "baseline_base_exits": {"n": 120, "avg_r": 0.02, "ci90": [-0.05, 0.09]},
                             "model": {"calib_mae": 0.05, "calibration": [{"n": 10, "mean_pred": -0.2, "mean_target": -0.15}]}},
                     "final": {"exit_margin": 0.15}}


class FakeModel:
    """Stands in for a sha-verified bundle; the score comes from the test."""

    def __init__(self, kind, value, threshold=0.5, p_hi=0.7, exit_margin=0.15):
        self.kind = kind
        self.value = value
        self.bundle = {"kind": kind, "threshold": threshold, "p_hi": p_hi, "exit_margin": exit_margin, "fake": self}


def wire(eng, models: dict):
    """Inject test featurizers / scorers / loader (no app.ml needed)."""
    ml = eng.ml
    ml.entry_featurizer = lambda ctx: {"side": 1.0 if ctx["candidate"]["side"] == "BUY" else -1.0,
                                       "score": ctx["candidate"]["score"] or 0.0, "n_bars": float(len(ctx["sig_bars"]))}
    ml.exit_featurizer = lambda ctx: {"r_now": ctx["r_now"] if ctx["r_now"] is not None else 0.0,
                                      "mfe_r": ctx["pos"]["mfe_r"]}
    ml.loader = lambda row: models[row["id"]].bundle
    ml.entry_scorer = lambda bundle, feats, symbol: bundle["fake"].value
    ml.exit_scorer = lambda bundle, feats: bundle["fake"].value


def add_model(eng, mid, kind, status, metrics=None, created="2026-10-01T00:00:00Z"):
    eng.db.execute(
        "INSERT INTO ml_models(id, kind, name, version, feature_schema, dataset_version, params_json, metrics_json, "
        "trained_until_utc, artifact_path, sha256, status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (mid, kind, "t", "1", "fs-v1" if kind == "entry" else "xs-v1", "ds1", "{}", json.dumps(metrics or {}),
         "2026-09-30T00:00:00Z", f"/nonexistent/{mid}.joblib", "0" * 64, status, created))


def armed(eng, bid=1.08000):
    eng.rt.ingest_quote("EURUSD", quote(bid=bid))
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)


def new_engine(tmp_path, name, **kw):
    s = make_settings(tmp_path / name, **kw)
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    return eng


def run_scenario(eng):
    """Signal -> execution -> one PM pass with an open position. Returns a comparable summary."""
    armed(eng)
    a = analysis_stub()
    eng.record_analysis(a)
    eng.ml.on_analysis(a)
    sig = eng.create_signal(a)
    eng.try_execute(sig)
    s = eng.get_signal(sig["id"])
    it = eng.executor.get(s["intent_id"]) if s.get("intent_id") else None
    now = time.time()
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07990, now=now))
    eng.track_trade_paths(now)
    eng.ml.tick(now)
    eng.pm.run(now)
    pos = [(p["symbol"], p["side"], p["volume"], p["sl"], p["tp"]) for p in eng.paper.public_positions()]
    eng.ml.drain()
    return {"status": s["status"], "reason": s["status_reason"], "volume": it and it["volume"],
            "state": it and it["state"], "positions": pos, "ml": s.get("ml")}


# ---- defaults: no approved model = no change -------------------------------------------------------
def test_tables_and_defaults(engine):
    cols = {r["name"] for r in engine.db.query("PRAGMA table_info(ml_decisions)")}
    assert {"model_id", "ts", "kind", "symbol", "ref_id", "features_hash", "score", "would_action", "actual_action",
            "detail_json"} <= cols
    assert engine.db.query("SELECT * FROM ml_approvals") == []
    s = engine.ml.settings_public()
    assert s["enabled"] is True and s["size_min"] == 0.5 and s["size_max"] == 1.0 and s["size_max_limit"] == 2.0
    assert engine.ml.entry_gate({"symbol": "EURUSD", "direction": "BUY"}) is None
    assert engine.ml.exit_action("PAPER", {"ticket": 1, "symbol": "EURUSD"}, time.time()) is None


def test_no_model_ml_on_equals_ml_off(tmp_path):
    on = run_scenario(new_engine(tmp_path, "on", ML_ENABLED=True))
    off = run_scenario(new_engine(tmp_path, "off", ML_ENABLED=False))
    assert on == off
    assert on["status"] == "EXECUTED" and on["ml"] is None


def test_shadow_models_never_change_trading(tmp_path):
    base = run_scenario(new_engine(tmp_path, "base", ML_ENABLED=False))
    eng = new_engine(tmp_path, "shadow")
    models = {"e-sh": FakeModel("entry", 0.01), "x-sh": FakeModel("exit", -9.0)}  # would SKIP / would CLOSE
    wire(eng, models)
    add_model(eng, "e-sh", "entry", "shadow")
    add_model(eng, "x-sh", "exit", "shadow")
    eng.ml.refresh_models()
    assert {m.id for m in eng.ml.active("entry")} == {"e-sh"}
    got = run_scenario(eng)
    assert got == base  # identical decisions, volumes and positions
    rows = eng.db.query("SELECT * FROM ml_decisions ORDER BY id")
    entry = [r for r in rows if r["kind"] == "entry"]
    exit_ = [r for r in rows if r["kind"] == "exit"]
    assert entry and entry[0]["model_id"] == "e-sh" and entry[0]["would_action"] == "SKIP"
    assert entry[0]["actual_action"] == "SIGNAL" and entry[0]["ref_id"] == "M5|1730000000" and entry[0]["features_hash"]
    assert exit_ and exit_[0]["would_action"] == "CLOSE" and exit_[0]["actual_action"] == "HOLD"
    assert json.loads(exit_[0]["detail_json"])["r_now"] < 0
    assert len(eng.paper.positions) == 1  # the shadow exit model did not close anything
    assert not [r for r in rows if r["kind"] == "entry_gate"]


def test_shadow_logging_is_async_batched_and_capped(tmp_path):
    eng = new_engine(tmp_path, "cap", ML_DECISIONS_MAX_ROWS=5)
    models = {"e": FakeModel("entry", 0.7)}
    wire(eng, models)
    add_model(eng, "e", "entry", "shadow")
    eng.ml.refresh_models()
    gate = threading.Event()
    orig = eng.ml.entry_featurizer
    eng.ml.entry_featurizer = lambda ctx: (gate.wait(5), orig(ctx))[1]
    t0 = time.time()
    for i in range(8):
        eng.ml.on_analysis(analysis_stub(bar_time=1730000000 + 300 * i))
    assert time.time() - t0 < 1.0  # the engine thread never waits for inference
    assert eng.db.query_one("SELECT COUNT(*) AS n FROM ml_decisions")["n"] == 0
    gate.set()
    eng.ml.drain()
    assert eng.db.query_one("SELECT COUNT(*) AS n FROM ml_decisions")["n"] == 8  # one batched write
    assert eng.ml.prune() == 3
    assert eng.db.query_one("SELECT COUNT(*) AS n FROM ml_decisions")["n"] == 5


def test_ml_package_missing_degrades_to_noop(engine, monkeypatch):
    add_model(engine, "e", "entry", "approved")
    monkeypatch.setattr(engine.ml, "_mod", lambda name: None)
    engine.ml.refresh_models()  # cannot load without app.ml.registry -> skipped
    assert engine.ml.models == {}
    armed(engine)
    sig = engine.create_signal(analysis_stub())
    engine.try_execute(sig)
    assert engine.get_signal(sig["id"])["status"] == "EXECUTED"


# ---- approved entry model: veto + bounded size factor, Risk Engine caps intact ----------------------
def approved_entry(eng, p, **kw):
    models = {"e-ap": FakeModel("entry", p, **kw)}
    wire(eng, models)
    add_model(eng, "e-ap", "entry", "approved", GOOD_ENTRY_METRICS)
    eng.ml.refresh_models()
    return models["e-ap"]


def test_approved_entry_vetoes_below_threshold(engine):
    approved_entry(engine, 0.31)
    armed(engine)
    sig = engine.create_signal(analysis_stub())
    engine.try_execute(sig)
    s = engine.get_signal(sig["id"])
    assert s["status"] == "NO_TRADE" and s["status_reason"].startswith("ML:") and "0.31" in s["status_reason"]
    assert not engine.paper.positions and s.get("intent_id") is None
    assert any(e["event_type"] == "ML_VETO" for e in engine.signal_events(sig["id"]))
    engine.ml.drain()
    g = engine.db.query_one("SELECT * FROM ml_decisions WHERE kind='entry_gate'")
    assert g["actual_action"] == "VETO" and g["ref_id"] == sig["id"]


@pytest.mark.parametrize("p,size_max,expect_vol", [
    (0.50, 1.0, 0.20),   # at the threshold -> ML_SIZE_MIN 0.5 x base 0.41
    (0.70, 1.0, 0.41),   # p_hi -> ML_SIZE_MAX 1.0 = base volume (never above by default)
    (0.99, 1.0, 0.41),
    (0.99, 2.0, 0.83),   # user raised the max: 2 x 0.25% = 0.5% (<= ML_MAX_RISK_PCT 0.5)
    (0.99, 9.0, 0.83),   # clamped to 2.0
])
def test_approved_entry_size_factor_bounded(engine, p, size_max, expect_vol):
    approved_entry(engine, p)
    engine.ml.update_settings({"size_max": size_max})
    armed(engine)
    sig = engine.create_signal(analysis_stub())
    engine.try_execute(sig)
    s = engine.get_signal(sig["id"])
    assert s["status"] == "EXECUTED", s["status_reason"]
    it = engine.executor.get(s["intent_id"])
    assert it["volume"] == pytest.approx(expect_vol)
    assert 0.5 <= s["ml"]["size_factor"] <= 2.0


def test_scaled_risk_still_capped_by_risk_engine(tmp_path):
    eng = new_engine(tmp_path, "cap1", ML_MAX_RISK_PCT=0.3)
    approved_entry(eng, 0.99)
    eng.ml.update_settings({"size_max": 2.0})
    armed(eng)
    sig = eng.create_signal(analysis_stub())
    eng.try_execute(sig)
    it = eng.executor.get(eng.get_signal(sig["id"])["intent_id"])
    assert it["volume"] == pytest.approx(0.5)  # 0.3% of 10000 / 60 USD per lot, not 2 x 0.25%

    eng2 = new_engine(tmp_path, "cap2")
    approved_entry(eng2, 0.99)
    eng2.ml.update_settings({"size_max": 2.0})
    eng2.rt.risk["max_total_open_risk_pct"] = 0.4
    armed(eng2)
    sig2 = eng2.create_signal(analysis_stub())
    eng2.try_execute(sig2)
    s2 = eng2.get_signal(sig2["id"])
    assert s2["status"] == "NO_TRADE" and "total_open_risk_ok" in s2["status_reason"]
    assert not eng2.paper.positions


def test_effective_risk_pct_bounds():
    f = risk_mod.effective_risk_pct
    assert f(0.25, 1.0, 0.5) == 0.25
    assert f(0.25, 0.5, 0.5) == pytest.approx(0.125)
    assert f(0.25, 2.0, 0.5) == pytest.approx(0.5)
    assert f(0.25, 2.0, 0.3) == pytest.approx(0.3)
    assert f(0.25, 2.0, None) == pytest.approx(0.25)  # no cap given -> never above the base risk
    assert f(0.25, 50.0, 10.0) == pytest.approx(0.5)  # hard 2x ceiling
    assert f(0.25, float("nan"), 1.0) == 0.25
    assert f(0.25, -3, 1.0) == pytest.approx(0.025)  # floor 0.1x


def test_risk_check_default_factor_identical(engine):
    armed(engine)
    a = engine.risk_check("EURUSD", "BUY", 1.0801, 1.0795, 1.0811)
    b = engine.risk_check("EURUSD", "BUY", 1.0801, 1.0795, 1.0811, size_factor=1.0)
    assert a == b and not any(c["name"] == "ml_size_factor" for c in a["checks"])


# ---- approved exit model: only an early CLOSE -------------------------------------------------------
def open_position(eng):
    armed(eng)
    sig = eng.create_signal(analysis_stub())
    eng.try_execute(sig)
    assert len(eng.paper.positions) == 1
    now = time.time()
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07990, now=now))
    eng.track_trade_paths(now)
    return next(iter(eng.paper.positions.values())), now


def test_approved_exit_closes_with_ml_exit(engine):
    models = {"x": FakeModel("exit", -2.0, exit_margin=0.15)}
    wire(engine, models)
    add_model(engine, "x", "exit", "approved", GOOD_EXIT_METRICS)
    engine.ml.refresh_models()
    pos, now = open_position(engine)
    sl_before = pos["sl"]
    engine.pm.run(now)
    assert not engine.paper.positions
    assert len(engine.list_trades()) == 1
    intents = engine.db.query("SELECT kind, reason FROM intents ORDER BY created_at_utc")
    assert [i["kind"] for i in intents] == ["OPEN", "CLOSE"] and intents[1]["reason"] == "ML_EXIT"
    engine.ml.drain()
    d = engine.db.query_one("SELECT * FROM ml_decisions WHERE kind='exit'")
    assert d["would_action"] == "CLOSE" and d["actual_action"] == "ML_EXIT"


def test_approved_exit_holds_when_remaining_r_ok(engine):
    models = {"x": FakeModel("exit", 0.4)}
    wire(engine, models)
    add_model(engine, "x", "exit", "approved")
    engine.ml.refresh_models()
    pos, now = open_position(engine)
    engine.pm.run(now)
    assert len(engine.paper.positions) == 1
    assert engine.db.query("SELECT kind FROM intents WHERE kind<>'OPEN'") == []


def test_approved_exit_without_margin_never_closes(engine):
    models = {"x": FakeModel("exit", -50.0, exit_margin=None)}
    wire(engine, models)
    add_model(engine, "x", "exit", "approved")
    engine.ml.refresh_models()
    pos, now = open_position(engine)
    engine.pm.run(now)
    assert len(engine.paper.positions) == 1


def test_ml_exit_can_never_modify_sl(engine, monkeypatch):
    pos, now = open_position(engine)
    sl0 = pos["sl"]
    wider = round(sl0 - 0.0050, 5)
    monkeypatch.setattr(engine.ml, "exit_action", lambda *a, **k: {"type": "MODIFY", "sl": wider, "tp": pos["tp"],
                                                                   "reason": "ML"})
    engine.pm.run(now)
    p = next(iter(engine.paper.positions.values()))
    assert p["sl"] == sl0
    assert engine.db.query("SELECT kind FROM intents WHERE kind<>'OPEN'") == []
    # whatever the model returns, the PM only ever turns it into a CLOSE with reason ML_EXIT
    engine.pm.last_action.clear()
    monkeypatch.setattr(engine.ml, "exit_action", lambda *a, **k: {"type": "CLOSE", "reason": "WHATEVER", "sl": wider})
    engine.pm.run(now + 11)
    assert not engine.paper.positions
    closes = engine.db.query("SELECT reason FROM intents WHERE kind='CLOSE'")
    assert [c["reason"] for c in closes] == ["ML_EXIT"]


def test_ml_disabled_switch_disarms_approved_models(engine):
    approved_entry(engine, 0.01)
    engine.ml.update_settings({"enabled": False})
    armed(engine)
    sig = engine.create_signal(analysis_stub())
    engine.try_execute(sig)
    assert engine.get_signal(sig["id"])["status"] == "EXECUTED"


# ---- retrain: subprocess glue, single flight, never approves ---------------------------------------
def test_retrain_single_flight_and_import(engine, client):
    release = threading.Event()
    seen = {}

    def runner(cmd, env, timeout):
        seen["cmd"], seen["env"] = cmd, env
        snap = cmd[cmd.index("--db") + 1]
        snap_db = DB(snap)  # the training run registers into the snapshot, never the live DB
        snap_db.execute(
            "INSERT INTO ml_models(id, kind, name, version, feature_schema, params_json, metrics_json, artifact_path, "
            "sha256, status, created_at) VALUES('e-new','entry','t','1','fs-v1','{}','{}','/x','0','approved',"
            "'2026-10-28T00:00:00Z')")
        snap_db.close()
        release.wait(10)
        return 0, "ok"

    engine.ml.retrain_runner = runner
    engine.ml.loader = lambda row: {"kind": row["kind"]}
    add_model(engine, "e-old", "entry", "shadow", created="2026-01-01T00:00:00Z")
    r1 = engine.ml.start_retrain("manual")
    assert r1["started"]
    r2 = engine.ml.start_retrain("nightly")
    assert r2 == {"started": False, "reason": "O reantrenare este deja în curs"}
    res = client.post("/api/v1/models/retrain", headers=ADMIN)
    assert res.status_code == 409
    assert client.get("/api/v1/models").json()["retrain"]["running"] is True
    release.set()
    engine.ml.retrain_thread.join(10)
    assert not engine.ml.retrain_running
    assert seen["cmd"][:3] == ["nice", "-n", "10"] and "app.ml" in seen["cmd"] and "train" in seen["cmd"]
    assert seen["env"]["OMP_NUM_THREADS"] == "3"
    snap = seen["cmd"][seen["cmd"].index("--db") + 1]
    assert snap != str(engine.settings.db_path) and not __import__("os").path.exists(snap)  # snapshot cleaned up
    new = engine.ml.model_row("e-new")
    assert new["status"] == "shadow" and new["approved_by"] is None  # imported as candidate -> shadow, never approved
    assert engine.ml.model_row("e-old")["status"] == "archived"
    last = engine.ml.retrain_status()["last"]
    assert last["status"] == "OK" and last["imported"] == ["e-new"]
    assert engine.ml.start_retrain("manual")["started"]  # free again
    engine.ml.retrain_thread.join(10)


def test_retrain_failure_logged_and_released(engine):
    engine.ml.retrain_runner = lambda cmd, env, timeout: (2, "eroare antrenare")
    assert engine.ml.start_retrain("manual")["started"]
    engine.ml.retrain_thread.join(10)
    last = engine.ml.retrain_status()["last"]
    assert last["status"] == "FAILED" and last["returncode"] == 2 and "eroare" in last["log_tail"]
    assert not engine.ml.retrain_running


def test_nightly_schedule_once_per_day(engine):
    calls = []
    engine.ml.start_retrain = lambda trigger="manual": calls.append(trigger) or {"started": True}
    engine.ml.retrain_runner = lambda *a: (0, "")
    tz = ZoneInfo("Europe/Chisinau")
    before = datetime(2026, 10, 28, 22, 0, tzinfo=tz).timestamp()
    after = datetime(2026, 10, 28, 22, 31, tzinfo=tz).timestamp()
    engine.rt.started_ts = before - 3600
    assert engine.ml.nightly_check(before) is False
    assert engine.ml.nightly_check(after) is True
    assert engine.ml.nightly_check(after + 60) is False
    assert calls == ["nightly"]
    engine.rt.started_ts = after + 7200  # just restarted: no run in the first minutes
    nxt = datetime(2026, 10, 29, 22, 45, tzinfo=tz).timestamp()
    engine.rt.started_ts = nxt - 30
    assert engine.ml.nightly_check(nxt) is False


# ---- approval guardrails (API) ------------------------------------------------------------------------
def seed_shadow_history(eng, model_id, n=110, edge=True):
    rows = []
    for i in range(n):
        bt = 1720000000 + 300 * i
        take = i % 2 == 0
        eng.db.execute("INSERT INTO analyses(symbol, tf, bar_time, outcome_r) VALUES('EURUSD','M5',?,?)",
                       (bt, (0.6 if take else -0.6) if edge else (-0.6 if take else 0.6)))
        rows.append((model_id, time.time() - 86400 * 3, "entry", "EURUSD", f"M5|{bt}", "h", 0.6 if take else 0.4,
                     "TAKE" if take else "SKIP", "SIGNAL", "{}"))
    eng.db.executemany("INSERT INTO ml_decisions(model_id, ts, kind, symbol, ref_id, features_hash, score, would_action, "
                       "actual_action, detail_json) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)


def test_approve_requires_admin_and_confirm(engine, client):
    wire(engine, {"e": FakeModel("entry", 0.6)})
    add_model(engine, "e", "entry", "shadow", GOOD_ENTRY_METRICS)
    assert client.post("/api/v1/models/e/approve", json={"confirm": "APROB"}).status_code == 401
    assert client.post("/api/v1/models/e/approve", headers=ADMIN, json={"confirm": "da"}).status_code == 400
    assert client.post("/api/v1/models/e/approve", headers=ADMIN, json={}).status_code == 400
    assert client.post("/api/v1/models/nope/approve", headers=ADMIN, json={"confirm": "APROB"}).status_code == 404
    assert engine.ml.model_row("e")["status"] == "shadow"


def test_approve_refused_when_checklist_fails_unless_override(engine, client):
    models = {"bad": FakeModel("entry", 0.6)}
    wire(engine, models)
    add_model(engine, "bad", "entry", "shadow", BAD_ENTRY_METRICS)
    r = client.post("/api/v1/models/bad/approve", headers=ADMIN, json={"confirm": "APROB"})
    assert r.status_code == 409 and "Checklist" in r.json()["detail"]
    r = client.post("/api/v1/models/bad/approve", headers=ADMIN, json={"confirm": "APROB", "override": True})
    assert r.status_code == 400  # override needs the second confirmation
    r = client.post("/api/v1/models/bad/approve", headers=ADMIN,
                    json={"confirm": "APROB", "override": True, "confirm_risk": "stiu riscul"})
    assert r.status_code == 400
    assert engine.ml.model_row("bad")["status"] == "shadow"
    r = client.post("/api/v1/models/bad/approve", headers=ADMIN,
                    json={"confirm": "APROB", "override": True, "confirm_risk": "ȘTIU RISCUL"})
    assert r.status_code == 200, r.text
    row = engine.ml.model_row("bad")
    assert row["status"] == "approved" and row["approved_by"] == "admin" and row["approved_at"]
    ap = engine.db.query_one("SELECT * FROM ml_approvals WHERE model_id='bad'")
    assert ap["override"] == 1 and ap["actor"] == "admin" and json.loads(ap["checklist_json"])["passed"] is False
    logs = engine.db.query("SELECT level, message FROM system_events WHERE message LIKE '%OVERRIDE%'")
    assert any(lg["level"] == "WARN" for lg in logs)


def test_approve_with_passing_checklist_and_rollback(engine, client):
    models = {"a": FakeModel("entry", 0.6), "b": FakeModel("entry", 0.6)}
    wire(engine, models)
    add_model(engine, "a", "entry", "shadow", GOOD_ENTRY_METRICS)
    seed_shadow_history(engine, "a")
    det = client.get("/api/v1/models/a").json()
    assert det["checklist"]["passed"] is True, det["checklist"]
    assert det["shadow"]["n_decisions"] == 110 and det["shadow"]["consistent"] is True
    r = client.post("/api/v1/models/a/approve", headers=ADMIN, json={"confirm": "APROB"})
    assert r.status_code == 200 and r.json()["checklist"]["passed"] is True
    assert engine.ml.approved("entry").id == "a"
    # a candidate (never in shadow) cannot be approved
    add_model(engine, "c", "entry", "candidate", GOOD_ENTRY_METRICS)
    assert client.post("/api/v1/models/c/approve", headers=ADMIN, json={"confirm": "APROB"}).status_code == 409
    # b replaces a; retiring b restores a
    add_model(engine, "b", "entry", "shadow", GOOD_ENTRY_METRICS)
    seed_shadow_history(engine, "b", n=0)
    r = client.post("/api/v1/models/b/approve", headers=ADMIN,
                    json={"confirm": "APROB", "override": True, "confirm_risk": "ȘTIU RISCUL"})
    assert r.status_code == 200 and r.json()["previous"] == "a"
    assert engine.ml.model_row("a")["status"] == "archived" and engine.ml.approved("entry").id == "b"
    r = client.post("/api/v1/models/b/retire", headers=ADMIN)
    assert r.status_code == 200 and r.json()["restored"] == "a"
    assert engine.ml.model_row("a")["status"] == "approved" and engine.ml.approved("entry").id == "a"
    # retire a -> no approved model at all (baseline behaviour)
    assert client.post("/api/v1/models/a/retire", headers=ADMIN).json()["restored"] is None
    assert engine.ml.approved("entry") is None
    assert client.post("/api/v1/models/a/retire", headers=ADMIN).status_code == 409
    assert client.post("/api/v1/models/a/retire").status_code == 401


def test_shadow_inconsistent_sign_fails_checklist(engine):
    add_model(engine, "s", "entry", "shadow", GOOD_ENTRY_METRICS)
    seed_shadow_history(engine, "s", edge=False)
    chk = engine.ml.checklist(engine.ml.model_row("s"))
    bad = {i["key"] for i in chk["items"] if not i["ok"]}
    assert bad == {"shadow_consistent"} and chk["passed"] is False


# ---- UI endpoints ---------------------------------------------------------------------------------------
def test_models_endpoints_shape(engine, client):
    wire(engine, {"e": FakeModel("entry", 0.6), "x": FakeModel("exit", 0.1)})
    add_model(engine, "e", "entry", "shadow", GOOD_ENTRY_METRICS)
    add_model(engine, "x", "exit", "candidate", GOOD_EXIT_METRICS)
    engine.ml.refresh_models()
    d = client.get("/api/v1/models").json()
    assert set(d) >= {"settings", "active", "retrain", "models"}
    assert d["active"] == {"entry": None, "exit": None}
    assert set(d["retrain"]) >= {"running", "last", "at", "enabled"}
    by = {m["id"]: m for m in d["models"]}
    assert by["e"]["status"] == "shadow" and by["e"]["loaded"] is True and by["e"]["shadow"]["n_decisions"] == 0
    o = by["e"]["oos"]
    assert o["avg_r"] == 0.12 and o["baseline_avg_r"] == -0.04 and o["ci90"] == [0.02, 0.21] and o["pf"] == 1.45
    assert o["brier"] == 0.231 and o["ece"] == 0.031 and o["threshold"] == 0.5 and "calibration" not in o
    det = client.get("/api/v1/models/e").json()
    assert {"oos", "shadow", "checklist", "approvals", "metrics", "params"} <= set(det)
    assert det["oos"]["calibration"][0] == {"p": 0.31, "obs": 0.30, "n": 300}
    assert {i["key"] for i in det["checklist"]["items"]} == {"oos_avg_r", "oos_ci_lo", "beats_baseline", "calibration",
                                                             "shadow_period", "shadow_consistent", "status_shadow"}
    dx = client.get("/api/v1/models/x").json()
    assert dx["oos"]["avg_r"] == 0.08 and dx["oos"]["baseline_avg_r"] == 0.02 and dx["oos"]["calib_mae"] == 0.05
    assert dx["oos"]["exit_margin"] == 0.15
    assert client.get("/api/v1/models/nope").status_code == 404


def test_models_settings_endpoint(engine, client):
    assert client.put("/api/v1/models/settings", json={"size_max": 1.5}).status_code == 401
    assert client.put("/api/v1/models/settings", headers=ADMIN, json={"size_max": 3}).status_code == 422
    assert client.put("/api/v1/models/settings", headers=ADMIN, json={"bogus": 1}).status_code == 422
    r = client.put("/api/v1/models/settings", headers=ADMIN, json={"size_max": 1.5, "enabled": False})
    assert r.status_code == 200 and r.json()["size_max"] == 1.5 and r.json()["enabled"] is False
    assert engine.ml.size_bounds() == (0.5, 1.5) and engine.ml.enabled is False


# ---- real app.ml integration (registry sha256 + features + infer) ----------------------------------------
def test_real_bundle_shadow_scoring_and_tamper_refusal(engine, tmp_path):
    pytest.importorskip("sklearn")
    F = pytest.importorskip("app.ml.features")
    registry = pytest.importorskip("app.ml.registry")
    pytest.importorskip("app.ml.infer")
    import numpy as np
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from tests.conftest import make_bars, zigzag

    names = list(F.ENTRY_FEATURE_NAMES)
    rng = np.random.default_rng(1)
    X = rng.normal(size=(200, len(names) + 1))
    y = (X[:, 0] > 0).astype(int)
    clf = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), LogisticRegression()).fit(X, y)

    bundle = {"kind": "entry", "feature_schema": F.ENTRY_SCHEMA, "feature_names": names, "model": clf,
              "sym_te": {"map": {"EURUSD": 0.5}, "prior": 0.5}, "threshold": 0.4, "p_hi": 0.8}
    mid = registry.register(engine.db, bundle, kind="entry", name="t", version="1", feature_schema=F.ENTRY_SCHEMA,
                            dataset_version="d", params={}, metrics=GOOD_ENTRY_METRICS, trained_until_ts=None,
                            out_dir=tmp_path / "models")
    registry.to_shadow(engine.db, mid)
    engine.ml.refresh_models()
    assert mid in engine.ml.models
    for b in make_bars(zigzag(300, 0.0004, 0.0002), tf_sec=300):
        engine.rt.ingest_bar("EURUSD", "M5", b, publish=False)
    for b in make_bars(zigzag(120, 0.0010, 0.0005), tf_sec=3600):
        engine.rt.ingest_bar("EURUSD", "H1", b, publish=False)
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08))
    bt = engine.rt.closed_bars("EURUSD", "M5")[-1]["t"]
    engine.ml.on_analysis(analysis_stub(bar_time=bt))
    engine.ml.drain()
    row = engine.db.query_one("SELECT * FROM ml_decisions WHERE model_id=?", (mid,))
    assert row and 0.0 <= row["score"] <= 1.0 and row["would_action"] in ("TAKE", "SKIP")
    # tampered artifact: refused on reload, never scored
    path = engine.ml.model_row(mid)["artifact_path"]
    with open(path, "ab") as fh:
        fh.write(b"x")
    engine.ml.models.clear()
    engine.ml.refresh_models()
    assert mid not in engine.ml.models
