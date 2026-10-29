"""Bot-2 (position manager) audit fixes, from the DEMO trades of 28-29 Oct:
- the live MT5 position handed to the rescue exit carried no swap -> a rescue closed NZDUSD at -0.18R net;
- after a NEWS_BE (entry ± 2 points) the +1R smart break-even lock was skipped forever;
- trades of EA closes were all recorded as "BOT" (RESCUE / TIME_STOP / manual close indistinguishable);
- deals ingested before the EA sent symbol specs got no risk_money / r_multiple."""
import time

import pytest

from app.db import DB
from app.engine import Engine, risk_money_from_fill
from app.position_manager import plan
from tests.conftest import make_settings, quote

MAGIC_POS = {"ticket": 501, "symbol": "EURUSD", "type": 1, "price_open": 1.08236, "sl": 1.08326, "tp": 1.08056,
             "volume": 0.01}


@pytest.fixture
def eng(tmp_path):
    s = make_settings(tmp_path)
    db = DB(s.db_path)
    e = Engine(s, db)
    e.startup()
    yield e
    db.close()


def _mt5(e, monkeypatch, pos: dict, now: float):
    e.rt.mt5_account = {"trade_mode": 0, "login": 7}
    monkeypatch.setattr(e.rt, "bridge_connected", lambda: True)
    p = dict(pos, magic=e.settings.MT5_MAGIC, time=now - 3 * 3600)
    e.rt.mt5_positions = [p]
    monkeypatch.setattr(e.executor, "open_intent_for_ticket", lambda t: {"sl": pos["sl"]} if t == pos["ticket"] else None)
    closes = []
    monkeypatch.setattr(e, "close_position", lambda src, t, reason="BOT": closes.append((src, t, reason)) or {})
    mods = []
    monkeypatch.setattr(e.executor, "submit_modify", lambda *a, **k: mods.append((a, k)) or {"state": "QUEUED"})
    return closes, mods


def _armed_path(e, pos: dict, now: float):
    e._paths[("MT5", str(pos["ticket"]))] = {"entry": pos["price_open"], "risk": abs(pos["price_open"] - pos["sl"]),
                                              "side": "SELL", "symbol": "EURUSD", "opened_ts": now - 3 * 3600,
                                              "mfe": 0.2, "mae": 0.8, "n": 118, "neg": 100, "last_ts": 0}


def test_live_rescue_sees_mt5_swap(eng, monkeypatch):
    now = time.time()
    # SELL, risk 90 points; ask 1.08226 -> +0.11R on price, but -0.20 USD of swap = -0.22R -> net negative
    closes, mods = _mt5(eng, monkeypatch, dict(MAGIC_POS, swap=-0.20), now)
    _armed_path(eng, MAGIC_POS, now)
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08219, spread_pts=7, now=now))
    eng.pm.run(now)
    assert closes == [] and mods == []
    # same price without swap -> RESCUE
    closes2, _ = _mt5(eng, monkeypatch, dict(MAGIC_POS, swap=0.0), now)
    eng.pm.last_action.clear()
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08219, spread_pts=7, now=now + 11))
    eng.pm.run(now + 11)
    assert closes2 == [("MT5", 501, "RESCUE")]


def test_break_even_after_news_be_still_locks_profit():
    cfg = {"break_even_at_r": 1.0, "be_lock_r": 0.1}
    pos = {"side": "BUY", "price_open": 1.08000, "sl": 1.08002, "tp": 1.08200, "opened_ts": 0}  # NEWS_BE at +2 pts
    info = {"bid": 1.08065, "ask": 1.08066, "point": 0.00001, "digits": 5}
    a = plan(pos, info, cfg, 60, True, initial_sl=1.07940)  # +1.08R
    assert a and a[0]["reason"] == "BREAK_EVEN" and a[0]["sl"] == pytest.approx(1.08006)
    pos["sl"] = a[0]["sl"]
    info.update(bid=1.08070, ask=1.08080)  # spread widens: no second MODIFY
    assert plan(pos, info, cfg, 70, True, initial_sl=1.07940) == []


def test_break_even_once_with_old_two_point_lock():
    cfg = {"break_even_at_r": 1.0, "be_lock_r": 0}
    pos = {"side": "SELL", "price_open": 1.08000, "sl": 1.08060, "tp": 1.07800, "opened_ts": 0}
    info = {"bid": 1.07930, "ask": 1.07935, "point": 0.00001, "digits": 5}
    a = plan(pos, info, cfg, 60, True, initial_sl=1.08060)
    assert a and a[0]["sl"] == pytest.approx(1.07998)
    pos["sl"] = a[0]["sl"]
    assert plan(pos, info, cfg, 70, True, initial_sl=1.08060) == []


def test_sl_never_loosened_by_break_even():
    cfg = {"break_even_at_r": 1.0, "be_lock_r": 0.1}
    pos = {"side": "BUY", "price_open": 1.08000, "sl": 1.08030, "tp": 1.08200, "opened_ts": 0}  # already +0.5R
    info = {"bid": 1.08070, "ask": 1.08071, "point": 0.00001, "digits": 5}
    assert plan(pos, info, cfg, 60, True, initial_sl=1.07940) == []


def _deal(ticket, pos_id, reason, price, profit, t):
    return {"ticket": ticket, "position_id": pos_id, "entry": 1, "type": 0, "symbol": "EURUSD", "volume": 0.01,
            "price": price, "profit": profit, "commission": 0, "swap": 0, "reason": reason, "time": t}


def test_ea_close_reason_named_after_close_intent(eng):
    now = time.time()
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08000, spread_pts=5, now=now))
    eng.known_mt5_positions[900] = {"ticket": 900, "magic": eng.settings.MT5_MAGIC, "price_open": 1.08100,
                                    "time": now - 600, "type": 1}
    eng.known_mt5_positions[901] = dict(eng.known_mt5_positions[900], ticket=901)
    eng.known_mt5_positions[902] = dict(eng.known_mt5_positions[900], ticket=902)
    eng.executor.submit_close("MT5", 900, "EURUSD", "SELL", 0.01, eng.rt.quotes["EURUSD"], reason="RESCUE")
    eng.executor.submit_close("MT5", 902, "EURUSD", "SELL", 0.01, eng.rt.quotes["EURUSD"], reason="TIME_STOP")
    eng.db.execute("UPDATE intents SET state='FAILED' WHERE ticket=902")  # failed close -> not used
    eng.ingest_deals([_deal(1, 900, 3, 1.08000, 1.0, now), _deal(2, 901, 3, 1.08000, 1.0, now),
                      _deal(3, 902, 4, 1.08160, -0.6, now)])
    got = {r["ticket"]: r["close_reason"] for r in eng.db.query("SELECT ticket, close_reason FROM trades")}
    assert got == {900: "RESCUE", 901: "BOT", 902: "SL"}


def test_r_multiple_without_quote_specs(eng):
    now = time.time()
    eng.known_mt5_positions[910] = {"ticket": 910, "magic": eng.settings.MT5_MAGIC, "price_open": 111.342,
                                    "time": now - 600, "type": 0}
    eng.db.execute("INSERT INTO intents(id, created_at_utc, updated_at_utc, kind, symbol, state, mode, ticket, data_json) "
                   "VALUES('o1','2026-10-29T00:50:00Z','2026-10-29T00:50:00Z','OPEN','AUDJPY','FILLED','DEMO',910,?)",
                   ('{"sl": 111.279, "signal_id": null}',))
    d = dict(_deal(5, 910, 5, 111.463, 2.30, now), symbol="AUDJPY", type=1)  # TP of a BUY, no AUDJPY quote known
    eng.ingest_deals([d])
    r = eng.db.query_one("SELECT risk_money, r_multiple, close_reason FROM trades WHERE ticket=910")
    assert r["close_reason"] == "TP"
    assert r["risk_money"] == pytest.approx(2.30 * 0.063 / 0.121, rel=1e-3)
    assert r["r_multiple"] == pytest.approx(1.921, abs=0.002)


def test_risk_money_from_fill_edges():
    tr = {"side": "SELL", "price_open": 1.0, "price_close": 1.0, "profit": 0.0}
    assert risk_money_from_fill(tr, 1.001) is None
    assert risk_money_from_fill({"side": "BUY", "price_open": 1.0, "price_close": 1.002, "profit": 2.0}, 1.0005) == 0.0
    assert risk_money_from_fill({"side": "BUY", "price_open": 1.0, "price_close": 0.999, "profit": -1.0}, 0.999) \
        == pytest.approx(1.0)
