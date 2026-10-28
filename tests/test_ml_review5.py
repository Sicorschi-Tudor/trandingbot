"""Review fixes (ML live): checklist = pre-registered Stage D gate, ML_EXIT never blocks protective SL moves, approved
models act only on the strategy/TFs they were trained on, CLI approve = API approve (checklist + audit), override
limits."""
import json
import time
import types

import pytest

from app import ml_live
from app.ml import registry
from app.position_manager import FAIL_MAX_ATTEMPTS, PositionManager
from tests.conftest import ADMIN
from tests.test_ml_e2e import tiny_entry_bundle
from tests.test_ml_live import (GOOD_ENTRY_METRICS, GOOD_EXIT_METRICS, FakeModel, add_model, analysis_stub, armed,
                                seed_shadow_history, wire)


def metrics(kind, avg, base, n=800, ece=0.006, calib=0.03, ci=(0.01, 0.2)):
    m = {"oos": {"policy": {"n": n, "avg_r": avg, "ci90": list(ci), "pf": 1.2},
                 ("baseline_take_all" if kind == "entry" else "baseline_base_exits"): {"n": 1662, "avg_r": base},
                 "model": {"ece": ece} if kind == "entry" else {"calib_mae": calib}},
         "final": {"threshold": 0.5} if kind == "entry" else {"exit_margin": 0.15}}
    return m


def bad_keys(chk):
    return {i["key"] for i in chk["items"] if not i["ok"]}


# ---- 1) live checklist enforces the pre-registered gate (beat > 0.05R, exit calib < 0.05R, n >= 150) ----------
def test_checklist_matches_stage_d_gate(engine):
    ml = engine.ml
    # Stage D H1 entry filter: -0.064 vs -0.1025 = +0.0385 -> does NOT beat the baseline by > 0.05R
    add_model(engine, "e", "entry", "shadow", metrics("entry", -0.064, -0.1025, n=799, ci=(-0.11, -0.02)))
    assert {"beats_baseline", "oos_avg_r"} <= bad_keys(ml.checklist(ml.model_row("e")))
    # Stage D H1 exit model: calib 0.0796R -> calibration fails (gate < 0.05R)
    add_model(engine, "x", "exit", "shadow", metrics("exit", -0.067, -0.103, n=1662, calib=0.0796))
    assert "calibration" in bad_keys(ml.checklist(ml.model_row("x")))
    # positive but tiny edge over baseline, and too few test trades
    add_model(engine, "s", "entry", "shadow", metrics("entry", 0.10, 0.06, n=120))
    assert {"beats_baseline", "oos_avg_r"} <= bad_keys(ml.checklist(ml.model_row("s")))
    add_model(engine, "ok", "entry", "shadow", metrics("entry", 0.10, 0.04, n=150))
    assert not ({"beats_baseline", "oos_avg_r", "calibration"} & bad_keys(ml.checklist(ml.model_row("ok"))))


def test_trainer_checklist_aligned():
    from app.ml import train
    pol = {"avg_r": 0.08, "ci90": [0.0, 0.2], "n": 400}
    assert train.checklist(pol, {"avg_r": 0.04}, 0.01, train.ECE_MAX)["beats_baseline"] is False
    assert train.checklist(pol, {"avg_r": 0.0}, 0.01, train.ECE_MAX)["passed"] is True
    assert train.checklist(dict(pol, n=100), {"avg_r": 0.0}, 0.01, train.ECE_MAX)["passed"] is False
    assert train.checklist(pol, {"avg_r": 0.0}, 0.0796, train.EXIT_CALIB_MAX)["calibration_ok"] is False


# ---- 2) a failing ML_EXIT CLOSE never suppresses BE / trailing / NEWS_BE ----------------------------------------
class _Ex:
    def __init__(self, n_close, last=0.0):
        self.n_close, self.last = n_close, last
        self.calls = []

    def consecutive_failures(self, t, kind):
        return (self.n_close, self.last) if kind == "CLOSE" and self.n_close else (0, None)

    def has_pending_for_ticket(self, *a, **k):
        return False

    def submit_modify(self, *a, **k):
        self.calls.append(("MODIFY", k.get("reason")))
        return {"state": "SENT"}


def _pm(n_close, last=0.0):
    calls = []
    ml_calls = []

    class RT:
        strategy = {"break_even_at_r": 1.0, "trailing_enabled": False, "signal_timeframe": "M5"}
        risk = {"max_tick_age_seconds": 10}
        settings = types.SimpleNamespace(MT5_MAGIC=1)
        mt5_account = {}
        mt5_positions = []
        quotes = {"EURUSD": {"bid": 1.1020, "ask": 1.1021, "point": 0.00001, "digits": 5}}

        def bridge_connected(self):
            return False

        def tick_age(self, s, n):
            return 1.0

        def log(self, *a):
            pass

    class ML:
        def exit_action(self, s, p, n):
            ml_calls.append(n)
            return {"type": "CLOSE", "reason": "ML_EXIT"}

    ex = _Ex(n_close, last)

    class Eng:
        rt = RT()
        executor = ex
        paper = types.SimpleNamespace(positions={1: {"ticket": 1, "symbol": "EURUSD", "side": "BUY", "price_open": 1.1000,
                                                     "sl": 1.0990, "tp": 1.1100, "opened_ts": 0, "initial_sl": 1.0990}})
        ml = ML()

        def session(self):
            return {"open_now": True}

        def risk_alert(self, *a, **k):
            calls.append(("ALERT",))

        def close_position(self, *a, **k):
            calls.append(("CLOSE", k.get("reason")))

        def notify_modify(self, *a):
            pass
    return PositionManager(Eng()), calls, ex, ml_calls


def test_ml_exit_gave_up_keeps_break_even():
    pm, calls, ex, ml_calls = _pm(FAIL_MAX_ATTEMPTS)
    pm.run(1000.0)  # +2R: the plan's BREAK_EVEN must go out although the ML CLOSE was abandoned
    assert ex.calls == [("MODIFY", "BREAK_EVEN")]
    assert calls == [("ALERT",)]  # one give-up alert, no CLOSE
    pm.run(1020.0)
    assert len(ml_calls) == 1  # (ticket, CLOSE) abandoned: the exit model is no longer asked


def test_ml_exit_backoff_window_keeps_break_even():
    pm, calls, ex, _ = _pm(1, last=995.0)  # one failed close 5 s ago: backing off for 30 s
    pm.run(1000.0)
    assert ex.calls == [("MODIFY", "BREAK_EVEN")] and not calls


def test_ml_exit_close_supersedes_modify_when_sendable():
    pm, calls, ex, _ = _pm(0)
    pm.run(1000.0)
    assert calls == [("CLOSE", "ML_EXIT")] and not ex.calls


# ---- 3) strategy / timeframe match --------------------------------------------------------------------------
def _set_params(eng, mid, **p):
    eng.db.execute("UPDATE ml_models SET params_json=? WHERE id=?", (json.dumps(p), mid))


def test_entry_gate_ignored_on_other_strategy(engine):
    wire(engine, {"e": FakeModel("entry", 0.01)})  # would veto
    add_model(engine, "e", "entry", "approved", GOOD_ENTRY_METRICS)
    live = engine.ml.live_strategy()
    _set_params(engine, "e", base_strategy="fade-v1", signal_tf=live["signal_tf"], htf_tf=live["htf_tf"])
    engine.ml.refresh_models()
    assert engine.ml.entry_gate({"symbol": "EURUSD", "direction": "BUY", "score": 0.8}) is None
    armed(engine)
    sig = engine.create_signal(analysis_stub())
    engine.try_execute(sig)
    assert engine.get_signal(sig["id"])["status"] == "EXECUTED"  # base behaviour
    ev = engine.db.query("SELECT message FROM system_events WHERE message LIKE '%antrenat pe fade-v1%'")
    assert ev
    # same strategy, other signal TF -> still ignored; matching -> acts (veto)
    _set_params(engine, "e", base_strategy=live["strategy_name"], signal_tf="H4", htf_tf=live["htf_tf"])
    engine.ml.refresh_models()
    assert engine.ml.entry_gate({"symbol": "EURUSD", "direction": "BUY", "score": 0.8}) is None
    _set_params(engine, "e", **{"base_strategy": live["strategy_name"], "signal_tf": live["signal_tf"],
                                "htf_tf": live["htf_tf"]})
    engine.ml.refresh_models()
    g = engine.ml.entry_gate({"symbol": "EURUSD", "direction": "BUY", "score": 0.8})
    assert g is not None and g["veto"] is True


def test_exit_action_ignored_on_other_tf(engine):
    from tests.test_ml_live import open_position
    wire(engine, {"x": FakeModel("exit", -2.0, exit_margin=0.15)})
    add_model(engine, "x", "exit", "approved", GOOD_EXIT_METRICS)
    _set_params(engine, "x", base_strategy="trend-v1", signal_tf="H1", htf_tf="H4")
    engine.ml.refresh_models()
    if engine.ml.live_strategy()["signal_tf"] == "H1":
        pytest.skip("live TF is H1 in this config")
    pos, now = open_position(engine)
    engine.pm.run(now)
    assert engine.paper.positions  # no ML_EXIT


def test_strategy_mismatch_blocks_approval_even_with_override(engine, client):
    wire(engine, {"m": FakeModel("entry", 0.6)})
    add_model(engine, "m", "entry", "shadow", GOOD_ENTRY_METRICS)
    _set_params(engine, "m", base_strategy="fade-v1")
    seed_shadow_history(engine, "m")
    det = client.get("/api/v1/models/m").json()
    assert det["checklist"]["passed"] is False and det["checklist"]["strategy"]["ok"] is False
    r = client.post("/api/v1/models/m/approve", headers=ADMIN,
                    json={"confirm": "APROB", "override": True, "confirm_risk": "ȘTIU RISCUL"})
    assert r.status_code == 409 and "nu pot fi forțate" in r.json()["detail"]
    assert engine.ml.model_row("m")["status"] == "shadow"


# ---- 4) override: approved model is capped at size factor 1.0 -----------------------------------------------
def test_override_caps_size_factor(engine, client):
    wire(engine, {"o": FakeModel("entry", 0.99)})
    add_model(engine, "o", "entry", "shadow", GOOD_ENTRY_METRICS)  # no shadow history -> checklist fails
    engine.ml.update_settings({"size_max": 2.0})
    r = client.post("/api/v1/models/o/approve", headers=ADMIN,
                    json={"confirm": "APROB", "override": True, "confirm_risk": "ȘTIU RISCUL"})
    assert r.status_code == 200, r.text
    g = engine.ml.entry_gate({"symbol": "EURUSD", "direction": "BUY", "score": 0.8})
    assert g is not None and g["veto"] is False and g["size_factor"] <= 1.0
    engine.ml.refresh_models()  # override flag survives a reload (from ml_approvals)
    assert engine.ml.approved("entry").overridden is True
    assert engine.ml.entry_gate({"symbol": "EURUSD", "direction": "BUY", "score": 0.8})["size_factor"] <= 1.0


# ---- 5) CLI approve = API approve -----------------------------------------------------------------------------
def _register(engine, tmp_path, mid_metrics):
    b = tiny_entry_bundle()
    mid = registry.register(engine.db, b, kind="entry", name="cli", version="1", feature_schema=b["feature_schema"],
                            dataset_version="synthetic", params={"base_strategy": "trend-v1"}, metrics=mid_metrics,
                            trained_until_ts=None, out_dir=tmp_path / "models")
    registry.to_shadow(engine.db, mid, archive_others=False)
    return mid


def test_cli_approve_runs_checklist_and_audits(engine, tmp_path, capsys):
    from app.ml.__main__ import main
    db = str(engine.settings.db_path)
    bad = _register(engine, tmp_path, metrics("entry", -0.064, -0.1025, n=799))
    assert main(["approve", "--db", db, "--id", bad, "--by", "tudor", "--confirm", "APROB"]) == 3
    assert registry.get(engine.db, bad)["status"] == "shadow"
    assert engine.db.query_one("SELECT COUNT(*) AS n FROM ml_approvals")["n"] == 0
    assert main(["approve", "--db", db, "--id", bad, "--by", "tudor", "--confirm", "APROB", "--override"]) == 2
    # passing checklist -> approved + ml_approvals row
    good = _register(engine, tmp_path, GOOD_ENTRY_METRICS)
    seed_shadow_history(engine, good)
    assert main(["approve", "--db", db, "--id", good, "--by", "tudor", "--confirm", "APROB"]) == 0, capsys.readouterr()
    row = registry.get(engine.db, good)
    assert row["status"] == "approved" and row["approved_by"] == "tudor"
    ap = engine.db.query_one("SELECT * FROM ml_approvals WHERE model_id=?", (good,))
    assert ap["action"] == "approve" and ap["override"] == 0 and json.loads(ap["checklist_json"])["passed"] is True
    # override path is audited as such
    assert main(["approve", "--db", db, "--id", bad, "--by", "tudor", "--confirm", "APROB", "--override",
                 "--confirm-risk", "ȘTIU RISCUL"]) == 0
    ap2 = engine.db.query_one("SELECT * FROM ml_approvals WHERE model_id=?", (bad,))
    assert ap2["override"] == 1 and ap2["prev_model_id"] == good


def test_constants_never_looser_than_gate():
    assert ml_live.CHK_BEAT_MIN >= 0.05 and ml_live.CHK_EXIT_CALIB_MAX <= 0.05 and ml_live.CHK_MIN_OOS_N >= 150
    assert ml_live.CHK_ECE_MAX <= 0.05 and ml_live.CHK_CI_LO_MIN >= -0.02
    _ = time  # noqa
