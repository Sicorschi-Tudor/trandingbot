"""Stage C (live): trade path recording, strategy registry selection, /strategies, PUT /strategy name, backtest params."""
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import api
from app import strategy as strategy_mod
from app.db import DB
from app.engine import Engine
from tests.conftest import ADMIN, quote
from tests.test_stage_a_engine import load_uptrend, ready

T0 = 1_800_000_000  # minute aligned


def paths(engine, trade_id=None):
    if trade_id is None:
        return engine.db.query("SELECT * FROM trade_paths ORDER BY trade_id, ts")
    return engine.db.query("SELECT * FROM trade_paths WHERE trade_id=? ORDER BY ts", (str(trade_id),))


def set_quote(engine, bid, now, symbol="EURUSD"):
    engine.rt.ingest_quote(symbol, quote(bid=bid, now=now))


# ---- trade paths -------------------------------------------------------------
def test_paper_buy_path_rows_every_minute_with_r_mfe_mae(engine):
    now = T0 + 1
    set_quote(engine, 1.08000, now)
    pos = engine.paper.open("EURUSD", "BUY", 0.1, 1.07910, 1.08300, engine.rt.quotes["EURUSD"], now=now)
    assert pos["price_open"] == 1.08010  # ask; initial risk 0.00100
    assert engine.track_trade_paths(now) == 1
    for dt, bid in ((20, 1.08160), (40, 1.07960)):  # +1.5R then -0.5R inside the same minute: no rows
        set_quote(engine, bid, now + dt)
        assert engine.track_trade_paths(now + dt) == 0
    set_quote(engine, 1.08060, now + 60)
    assert engine.track_trade_paths(now + 60) == 1
    set_quote(engine, 1.08060, now + 61)
    assert engine.track_trade_paths(now + 61) == 0
    rows = paths(engine, pos["ticket"])
    assert [r["ts"] for r in rows] == [T0, T0 + 60]
    a, b = rows
    assert a["source"] == "live" and a["symbol"] == "EURUSD" and a["side"] == "BUY"
    assert a["r_now"] == pytest.approx(-0.1) and a["mfe_r"] == 0 and a["mae_r"] == pytest.approx(0.1)
    assert a["minutes"] == 0 and a["price"] == 1.08000 and a["spread_pts"] == 10
    assert b["r_now"] == pytest.approx(0.5) and b["mfe_r"] == pytest.approx(1.5) and b["mae_r"] == pytest.approx(0.5)
    assert b["minutes"] == pytest.approx(1.0)


def test_paper_sell_path_uses_ask_and_initial_sl(engine):
    now = T0 + 5
    set_quote(engine, 1.08000, now)  # ask 1.08010
    pos = engine.paper.open("EURUSD", "SELL", 0.1, 1.08200, 1.07700, engine.rt.quotes["EURUSD"], now=now)
    assert pos["price_open"] == 1.08000  # bid; risk 0.00200
    pos["sl"] = 1.08150  # SL tightened later: R stays relative to the initial risk
    set_quote(engine, 1.07790, now + 10)  # ask 1.07800 -> +1R
    engine.track_trade_paths(now + 10)
    set_quote(engine, 1.08090, now + 70)  # ask 1.08100 -> -0.5R
    engine.track_trade_paths(now + 70)
    rows = paths(engine, pos["ticket"])
    assert len(rows) == 2
    assert rows[0]["r_now"] == pytest.approx(1.0) and rows[0]["price"] == 1.07800
    assert rows[1]["r_now"] == pytest.approx(-0.5)
    assert rows[1]["mfe_r"] == pytest.approx(1.0) and rows[1]["mae_r"] == pytest.approx(0.5)


def demo_setup(engine, monkeypatch, positions, intents):
    engine.rt.mt5_account = {"login": 123, "trade_mode": 0, "equity": 1000, "balance": 1000}
    engine.rt.mt5_positions = positions
    monkeypatch.setattr(engine.rt, "bridge_connected", lambda: True)
    monkeypatch.setattr(engine.executor, "open_intent_for_ticket", lambda t: intents.get(int(t)))
    monkeypatch.setattr(engine.executor, "open_intent_by_comment", lambda c: None)


def test_demo_sell_path_from_open_intent_and_resume_after_restart(engine, monkeypatch):
    now = T0 + 2
    mt5 = [
        {"ticket": 777, "symbol": "EURUSD", "type": 1, "volume": 0.1, "price_open": 1.08000, "sl": 1.08000,
         "tp": 1.07700, "magic": engine.settings.MT5_MAGIC, "comment": "ai:abc", "time": now - 600},
        {"ticket": 888, "symbol": "EURUSD", "type": 0, "volume": 0.1, "price_open": 1.08000, "sl": 1.07900,
         "tp": 1.08300, "magic": 1, "comment": "manual", "time": now - 600},  # not bot-owned
    ]
    demo_setup(engine, monkeypatch, mt5, {777: {"sl": 1.08100}})  # initial risk 0.00100 from the OPEN intent
    set_quote(engine, 1.07940, now)  # ask 1.07950 -> +0.5R
    engine.track_trade_paths(now)
    rows = paths(engine)
    assert [r["trade_id"] for r in rows] == ["777"]
    assert rows[0]["side"] == "SELL" and rows[0]["r_now"] == pytest.approx(0.5)
    assert rows[0]["minutes"] == pytest.approx(10.0, abs=0.05)
    # restart: state lost, MFE/MAE resume from the rows already written
    engine._paths.clear()
    set_quote(engine, 1.07990, now + 60)  # ask 1.08000 -> 0R
    engine.track_trade_paths(now + 60)
    r = paths(engine, 777)[-1]
    assert r["r_now"] == pytest.approx(0.0) and r["mfe_r"] == pytest.approx(0.5)
    # closed position: in-memory state dropped
    engine.rt.mt5_positions = []
    engine.track_trade_paths(now + 120)
    assert engine._paths == {}


def test_paths_skip_stale_quotes_and_unknown_risk(engine, monkeypatch):
    now = T0 + 3
    demo_setup(engine, monkeypatch, [
        {"ticket": 5, "symbol": "EURUSD", "type": 0, "volume": 0.1, "price_open": 1.08, "sl": 1.079, "tp": 1.083,
         "magic": engine.settings.MT5_MAGIC, "comment": "", "time": now}], {})  # no OPEN intent -> no R base
    set_quote(engine, 1.08000, now)
    assert engine.track_trade_paths(now) == 0
    demo_setup(engine, monkeypatch, engine.rt.mt5_positions, {5: {"sl": 1.079}})
    set_quote(engine, 1.08000, now - 300)  # tick 5 min old
    assert engine.track_trade_paths(now + 60) == 0
    assert paths(engine) == []


def test_path_indicators_columns(engine):
    ready(engine)
    load_uptrend(engine)
    q = engine.rt.quotes["EURUSD"]
    pos = engine.paper.open("EURUSD", "BUY", 0.01, q["bid"] - 0.003, q["bid"] + 0.006, q)
    now = time.time()
    engine.track_trade_paths(now)
    r = paths(engine, pos["ticket"])[0]
    assert r["atr"] and r["atr"] > 0 and r["ema_dist_atr"] is not None


# ---- strategy selection ------------------------------------------------------
def test_default_strategy_is_trend_v1_unchanged(engine):
    ready(engine)
    load_uptrend(engine)
    name, fn, cfg = engine.active_strategy()
    assert name == "trend-v1" and fn is strategy_mod.analyze and cfg is engine.rt.strategy
    assert "name" not in engine.rt.strategy
    a = engine.analyze_symbol("EURUSD", allow_signal=True)
    assert a["direction"] == "BUY" and "strategy_version" not in a
    assert engine.list_signals()[0]["strategy_version"] == "trend-v1"


def fake_plugin(calls):
    def analyze(symbol, sig_bars, htf_bars, q, cfg, risk, ctx):
        calls.append(cfg)
        return {"symbol": symbol, "timeframe": "M5", "bar_time": sig_bars[-1]["t"], "direction": "HOLD", "score": 0.0,
                "reasons": ["x"], "indicators": {}, "time_utc": "2026-01-01T00:00:00Z", "candidate": None}
    mod = SimpleNamespace(NAME="fake-v1", VERSION="fake-v1.0", DESCRIPTION_RO="Strategie de test",
                          PARAMS={"bb_period": 20, "k": 1.5}, analyze=analyze)
    reg = SimpleNamespace(names=lambda: ["trend-v1", "fake-v1"],
                          get=lambda n: {"fake-v1": mod}.get(n) or (_ for _ in ()).throw(KeyError(n)))
    return reg


def test_put_strategy_name_validation_and_switch(engine, client, monkeypatch):
    calls = []
    monkeypatch.setattr(Engine, "strategy_registry", staticmethod(lambda: fake_plugin(calls)))
    assert client.put("/api/v1/strategy", json={"name": "fake-v1"}).status_code == 401
    r = client.put("/api/v1/strategy", json={"name": "nope-v9"}, headers=ADMIN)
    assert r.status_code == 422 and "nope-v9" in r.json()["detail"]
    assert client.put("/api/v1/strategy", json={"name": 5}, headers=ADMIN).status_code == 422
    assert engine.active_strategy_name() == "trend-v1"
    r = client.put("/api/v1/strategy", json={"name": "fake-v1"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["name"] == "fake-v1"
    assert engine.db.query("SELECT * FROM system_events WHERE level='WARN' AND message LIKE '%Strategia live schimbată%'")
    assert client.get("/api/v1/settings").json()["strategy"]["name"] == "fake-v1"
    # applies on the next analysis with live settings + plugin PARAMS
    ready(engine)
    load_uptrend(engine)
    a = engine.analyze_symbol("EURUSD", allow_signal=False)
    assert calls and calls[-1]["bb_period"] == 20 and calls[-1]["atr_sl_mult"] == engine.rt.strategy["atr_sl_mult"]
    assert a["strategy_version"] == "fake-v1"
    # persisted: a restarted engine keeps it
    eng2 = Engine(engine.settings, DB(engine.settings.db_path))
    eng2.startup()
    assert eng2.active_strategy_name() == "fake-v1"
    # back to default
    assert client.put("/api/v1/strategy", json={"name": "trend-v1"}, headers=ADMIN).json()["name"] == "trend-v1"
    assert engine.active_strategy()[1] is strategy_mod.analyze


def test_missing_plugin_falls_back_to_trend_v1(engine, monkeypatch):
    monkeypatch.setattr(Engine, "strategy_registry", staticmethod(lambda: None))
    engine.rt.strategy["name"] = "gone-v1"
    name, fn, _ = engine.active_strategy()
    assert name == "trend-v1" and fn is strategy_mod.analyze
    assert engine.strategy_names() == ["trend-v1"]


def test_strategies_endpoint_shape(client, monkeypatch):
    rows = client.get("/api/v1/strategies").json()
    assert isinstance(rows, list) and rows
    for r in rows:
        assert set(r) == {"name", "version", "description", "params"}
        assert isinstance(r["params"], dict) and isinstance(r["name"], str)
    assert "trend-v1" in [r["name"] for r in rows]
    monkeypatch.setattr(Engine, "strategy_registry", staticmethod(lambda: fake_plugin([])))
    rows = client.get("/api/v1/strategies").json()
    fake = next(r for r in rows if r["name"] == "fake-v1")
    assert fake == {"name": "fake-v1", "version": "fake-v1.0", "description": "Strategie de test",
                    "params": {"bb_period": 20, "k": 1.5}}


# ---- backtest params -----------------------------------------------------------
def test_backtest_params_strategy_exit_and_spreads(engine, monkeypatch):
    p = api.validate_backtest_params({"symbols": ["EURUSD"]}, engine)
    assert p["strategy_name"] == "trend-v1" and p["exit"] == {"mode": "base", "params": {}}
    p = api.validate_backtest_params({"symbols": ["EURUSD"], "exit": {"mode": "trailing", "params": {"trail_start_r": 1}},
                                      "cost": {"mode": "fixed", "fixed_spread_pts": 15,
                                               "per_symbol_spread_pts": {"eurusd": 11}}}, engine)
    assert p["exit"] == {"mode": "trailing", "params": {"trail_start_r": 1.0}}
    assert p["cost"]["per_symbol_spread_pts"] == {"EURUSD": 11.0}
    p = api.validate_backtest_params({"exit": {"mode": "combo", "params": {"modes": ["stall", "giveback"]}}}, engine)
    assert p["exit"]["params"]["modes"] == ["stall", "giveback"]
    for bad in ({"exit": {"mode": "magic"}}, {"exit": {"mode": "combo", "params": {}}},
                {"exit": {"mode": "combo", "params": {"modes": ["combo"]}}},
                {"exit": {"mode": "stall", "params": {"stall_minutes": "x"}}},
                {"strategy_name": "nope"}, {"cost": {"per_symbol_spread_pts": {"EURUSD": -1}}},
                {"strategy": {"bb_period": 10}}):
        with pytest.raises(HTTPException):
            api.validate_backtest_params(bad, engine)
    monkeypatch.setattr(Engine, "strategy_registry", staticmethod(lambda: fake_plugin([])))
    p = api.validate_backtest_params({"strategy_name": "fake-v1", "strategy": {"bb_period": 30, "k": 2}}, engine)
    assert p["strategy_name"] == "fake-v1" and p["strategy"] == {"bb_period": 30, "k": 2.0}


def test_exits_endpoint(client):
    rows = client.get("/api/v1/exits").json()
    modes = [r["mode"] for r in rows]
    assert modes[0] == "base" and "combo" in modes and all(isinstance(r["params"], dict) for r in rows)
