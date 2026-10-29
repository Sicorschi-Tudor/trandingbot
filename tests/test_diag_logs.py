"""Diagnostic logs: a Risk Engine rejection names the failed checks; a bridge outage logs its duration."""
import time

from tests.conftest import ADMIN, make_bars, quote, zigzag


def _logs(engine, n=5):
    return [r["message"] for r in engine.db.query("SELECT message FROM system_events ORDER BY id DESC LIMIT ?", (n,))]


def test_risk_rejection_log_names_failed_checks(client, engine):
    now = time.time()
    for b in make_bars(zigzag(120, 0.0003, 0.0002), 300, end_t=int(now // 300 * 300) - 300):
        engine.rt.ingest_bar("EURUSD", "M5", b)
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08))
    r = client.post("/api/v1/control/test-order", json={"symbol": "EURUSD", "side": "BUY"}, headers=ADMIN)
    assert r.json()["state"] == "REJECTED_RISK"
    msg = next(m for m in _logs(engine) if "respins de Risk Engine" in m)
    assert "mode_allows_execution" in msg and "(" in msg  # name + detail of the failed check


def test_bridge_outage_logged_with_duration(engine):
    engine.settings = engine.settings.model_copy(update={"data_source": "mt5_bridge"})
    engine.rt.bridge["last_sync_ts"] = time.time()
    engine.loop_once()
    assert engine._prev_bridge_connected is True
    engine.rt.bridge["last_sync_ts"] = time.time() - 8.0      # 8 s of silence -> disconnected
    engine.loop_once()
    assert engine._prev_bridge_connected is False
    assert any("s-a deconectat" in m for m in _logs(engine))
    engine._bridge_down_ts = time.time() - 9.0                 # pretend the outage started 9 s ago
    engine.rt.bridge["last_sync_ts"] = time.time()             # EA posts again
    engine.loop_once()
    assert engine._prev_bridge_connected is True
    msg = next(m for m in _logs(engine) if "s-a reconectat" in m)
    assert "după 9 s" in msg
