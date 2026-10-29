"""Why is the chart empty? EA 1.15 reports unknown symbols; older EAs: the engine flags active symbols without quotes."""
import time

from app import bridge
from tests.conftest import quote


def _sync(engine, now, **extra):
    payload = {"ea_version": extra.pop("ea_version", "1.15"), "magic": engine.settings.MT5_MAGIC, "time_utc": int(now),
               "terminal": {"connected": True, "trade_allowed": True}, "symbols": [], "bars": [], "positions": [], "deals": [],
               "results": []}
    payload.update(extra)
    return bridge.process_sync(engine, payload, now)


def test_unknown_symbols_reported_by_ea(engine):
    now = time.time()
    _sync(engine, now, unknown_symbols="XAUUSD")
    assert engine.rt.bridge["unknown_symbols"] == ["XAUUSD"]
    assert engine.status()["bridge"]["unknown_symbols"] == ["XAUUSD"]
    logs = [r["message"] for r in engine.db.query("SELECT message FROM system_events ORDER BY id DESC LIMIT 3")]
    assert any("nu cunoaște simbolurile: XAUUSD" in m for m in logs)
    _sync(engine, now + 1, unknown_symbols="")
    assert engine.rt.bridge["unknown_symbols"] == []


def test_no_quote_heuristic_for_old_ea(engine):
    engine.rt.strategy["symbols"] = ["EURUSD", "XAUUSD"]
    now = time.time()
    _sync(engine, now, ea_version="1.14")
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08))
    engine._check_symbol_feed(now + 5)
    assert engine.rt.bridge.get("no_quote_symbols") == []  # less than 30 s since the first sync: no verdict yet
    _sync(engine, now + 20, ea_version="1.14")  # the EA keeps syncing (gaps < 30 s = same connection)
    _sync(engine, now + 40, ea_version="1.14")
    engine._check_symbol_feed(now + 40)
    assert engine.rt.bridge["no_quote_symbols"] == ["XAUUSD"]
    logs = [r["message"] for r in engine.db.query("SELECT message FROM system_events ORDER BY id DESC LIMIT 3")]
    assert any("Fără cotații pentru XAUUSD" in m and "Market Watch" in m for m in logs)
    assert engine.status()["bridge"]["no_quote_symbols"] == ["XAUUSD"]
