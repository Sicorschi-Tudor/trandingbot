import time

from app.bridge import build_response, check_bridge_auth, process_sync
from tests.conftest import EURUSD_INFO, make_settings


def test_build_response_format():
    txt = build_response(1730000000, ["EURUSD", "GBPUSD"], ["M1", "M5", "H1"], [("EURUSD", "M5")],
                         ["OPEN|abc|EURUSD|BUY|0.01|1.07|1.09|20|ai:abc"])
    assert txt.splitlines() == [
        "OK|1730000000", "SYMBOLS|EURUSD,GBPUSD", "TF|M1,M5,H1", "HIST|EURUSD|M5",
        "OPEN|abc|EURUSD|BUY|0.01|1.07|1.09|20|ai:abc",
    ]
    assert txt.endswith("\n")


def test_auth_codes():
    assert check_bridge_auth("", "x") == 403
    assert check_bridge_auth("", None) == 403
    assert check_bridge_auth("k1", None) == 401
    assert check_bridge_auth("k1", "k2") == 401
    assert check_bridge_auth("k1", "k1") is None


def test_endpoint_auth(client):
    r = client.post("/bridge/v1/sync", json={})
    assert r.status_code == 401
    r = client.post("/bridge/v1/sync", json={}, headers={"X-Bridge-Key": "wrong"})
    assert r.status_code == 401
    r = client.post("/bridge/v1/sync", json={"ea_version": "1.00"}, headers={"X-Bridge-Key": "bridge-key"})
    assert r.status_code == 200 and r.text.startswith("OK|")


def test_endpoint_403_when_key_empty(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import bridge
    from app.db import DB
    from app.engine import Engine
    s = make_settings(tmp_path, MT5_BRIDGE_KEY="")
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    app = FastAPI()
    app.include_router(bridge.router)
    app.state.engine = eng
    r = TestClient(app).post("/bridge/v1/sync", json={}, headers={"X-Bridge-Key": "anything"})
    assert r.status_code == 403


def payload(now, **kw):
    sym = dict(EURUSD_INFO, symbol="EURUSD", bid=1.08, ask=1.0801, time_msc=int(now * 1000), spread_points=10)
    p = {
        "ea_version": "1.00", "magic": 552211, "time_utc": int(now),
        "terminal": {"connected": True, "trade_allowed": True, "build": 4000},
        "account": {"login": 12345678, "server": "Broker-Demo", "company": "Broker", "trade_mode": 0, "currency": "USD",
                    "balance": 10000.0, "equity": 10000.0, "margin": 0.0, "margin_free": 10000.0, "leverage": 100,
                    "profit": 0.0, "trade_allowed": True, "trade_expert": True, "margin_mode": 2},
        "symbols": [sym], "bars": [], "positions": [], "deals": [], "results": [],
    }
    p.update(kw)
    return p


def test_process_sync_hist_commands_and_results(engine):
    now = time.time()
    txt = process_sync(engine, payload(now), now)
    lines = txt.splitlines()
    assert lines[1] == "SYMBOLS|EURUSD"
    assert any(l == "HIST|EURUSD|M5" for l in lines)
    assert engine.rt.bridge_connected()
    st = engine.status()
    assert st["account"]["login_masked"] == "****5678" and st["account"]["is_demo"] is True
    assert st["paper_account"]["server"] == "PAPER"
    # HIST not repeated within retry window
    txt2 = process_sync(engine, payload(now + 1), now + 1)
    assert "HIST|" not in txt2

    # queue a DEMO OPEN and check delivery once
    engine.rt.trading.update(mode="DEMO", execution_enabled=True)
    rc = {"passed": True, "checks": []}
    it = engine.executor.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"], rc)
    assert it["state"] == "QUEUED"
    txt3 = process_sync(engine, payload(now + 2), now + 2)
    open_lines = [l for l in txt3.splitlines() if l.startswith("OPEN|")]
    assert open_lines == [f"OPEN|{it['id']}|EURUSD|BUY|0.01|1.079|1.082|20|ai:{it['id'][:8]}"]
    assert engine.executor.get(it["id"])["state"] == "DELIVERED"
    txt4 = process_sync(engine, payload(now + 3), now + 3)
    assert "OPEN|" not in txt4  # never re-sent
    res = {"cmd_id": it["id"], "ok": True, "retcode": 10009, "retcode_desc": "done", "order": 1, "deal": 2,
           "position": 777, "price": 1.0801, "volume": 0.01, "comment": ""}
    process_sync(engine, payload(now + 4, results=[res]), now + 4)
    done = engine.executor.get(it["id"])
    assert done["state"] == "FILLED" and done["ticket"] == 777
    assert done["result"]["retcode_desc"] == "DONE"


def test_commands_delivered_to_real_account(engine):
    now = time.time()
    process_sync(engine, payload(now), now)
    it = engine.executor.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"],
                                     {"passed": True, "checks": []})
    p = payload(now + 1)
    p["account"]["trade_mode"] = 2
    txt = process_sync(engine, p, now + 1)
    assert "OPEN|" in txt
    assert engine.executor.get(it["id"])["state"] == "DELIVERED"


def test_deals_create_trade_once(engine):
    now = time.time()
    deal = {"ticket": 456, "order": 789, "position_id": 123, "symbol": "EURUSD", "type": 1, "entry": 1, "volume": 0.01,
            "price": 1.11, "profit": 1.0, "commission": -0.07, "swap": 0.0, "magic": 552211, "reason": 5,
            "time": int(now), "comment": "tp"}
    foreign = dict(deal, ticket=999, magic=1)
    process_sync(engine, payload(now, deals=[deal, foreign]), now)
    process_sync(engine, payload(now + 1, deals=[deal]), now + 1)
    trades = engine.list_trades()
    assert len(trades) == 1
    assert trades[0]["close_reason"] == "TP" and trades[0]["side"] == "BUY" and trades[0]["mode"] == "DEMO"


def test_bars_history_ingest(engine):
    now = time.time()
    t0 = int(now // 300 * 300) - 300 * 150
    bars = [{"symbol": "EURUSD", "tf": "M5", "t": t0 + i * 300, "o": 1.08, "h": 1.0805, "l": 1.0795, "c": 1.08,
             "tv": 10, "sp": 10} for i in range(150)]
    process_sync(engine, payload(now, bars=bars), now)
    assert len(engine.rt.bars[("EURUSD", "M5")]) == 150


def test_clock_skew_corrected_and_future_bars_dropped():
    from app.bridge import normalize_clock

    class RT:
        def __init__(self):
            self.bridge, self.logs = {}, []

        def log(self, *a):
            self.logs.append(a)

    rt, now = RT(), 1_800_000_000.0
    payload = {"time_utc": int(now) + 7200,
               "bars": [{"t": int(now) + 7200 - 60}], "symbols": [{"time_msc": (int(now) + 7200) * 1000}],
               "positions": [{"time": int(now) + 7000}], "deals": []}
    assert normalize_clock(rt, payload, now) == 7200
    assert payload["bars"][0]["t"] == int(now) - 60
    assert payload["symbols"][0]["time_msc"] == int(now) * 1000
    assert payload["positions"][0]["time"] == int(now) - 200
    ok = {"time_utc": int(now) + 2, "bars": [{"t": int(now)}]}
    assert normalize_clock(rt, ok, now) == 0 and ok["bars"][0]["t"] == int(now)


def test_tick_ahead_detects_bad_server_offset():
    from app.bridge import normalize_clock

    class RT:
        def __init__(self):
            self.bridge = {}

        def log(self, *a):
            pass

    rt, now = RT(), 1_800_000_000.0
    # EA's TimeGMT is right, but its offset was 0 -> server time (UTC+2) leaks through
    payload = {"time_utc": int(now), "symbols": [{"time_msc": (int(now) + 7200) * 1000}],
               "bars": [{"t": int(now) + 7200 - 120}, {"t": int(now) - 3600}]}
    assert normalize_clock(rt, payload, now) == 7200
    assert payload["bars"][0]["t"] == int(now) - 120
