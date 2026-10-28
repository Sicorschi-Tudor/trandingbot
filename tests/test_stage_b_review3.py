"""Stage B review fixes: archive reply matching, HISTX vs UNKNOWN intents, broker DST in archive bars,
labeler/live break-even parity, max_hold_minutes=0 horizon, shadow-label costs, read-only backtest DB."""
import time
from datetime import datetime, timezone

import pytest

from app import archive, labeler
from app.position_manager import plan
from tests.conftest import make_settings, quote
from tests.test_stage_b_archive import (M5, bars_before, first_sync, histx_lines, parse_histx, sync)


def ts(*a) -> int:
    return int(datetime(*a, tzinfo=timezone.utc).timestamp())


# ---- archive: a resent batch is not "nothing older" ---------------------------
def test_resent_batch_does_not_exhaust_pair(engine):
    rt = engine.rt
    now = time.time()
    first_sync(engine, now)
    _, _, end1, _ = parse_histx(histx_lines(sync(engine, now + 1))[0])
    batch = bars_before(end1, 50)
    item = {"symbol": "EURUSD", "tf": "M5", "end_utc": end1, "bars": batch}
    txt = sync(engine, now + 2, archive=[dict(item, bars=[dict(b) for b in batch])])
    _, tf, end2, _ = parse_histx(histx_lines(txt)[0])
    assert (tf, end2) == ("M5", batch[0]["t"])
    # the HTTP 200 never reached the EA: it resends the same batch (old end_utc)
    sync(engine, now + 3, archive=[dict(item, bars=[dict(b) for b in batch])])
    archive.drain()
    assert ("EURUSD", "M5") not in rt.archive_plan["exhausted"]
    assert rt.archive_status["EURUSD"]["M5"]["done"] is False
    assert rt.archive_plan["pending"]["end_utc"] == end2  # still waiting for the real reply
    # the real reply to end2 keeps the walk going
    batch2 = bars_before(end2, 50)
    txt = sync(engine, now + 4, archive=[{"symbol": "EURUSD", "tf": "M5", "end_utc": end2, "bars": batch2}])
    sym, tf, end3, _ = parse_histx(histx_lines(txt)[0])
    assert (sym, tf, end3) == ("EURUSD", "M5", batch2[0]["t"])
    archive.drain()
    assert engine.db.query_one("SELECT COUNT(*) AS n FROM bars_archive WHERE tf='M5'")["n"] == 100


def test_unsolicited_batch_never_marks_exhausted(engine):
    rt = engine.rt
    now = time.time()
    first_sync(engine, now)
    _, _, end1, _ = parse_histx(histx_lines(sync(engine, now + 1))[0])
    batch = bars_before(end1, 20)
    sync(engine, now + 2, archive=[{"symbol": "EURUSD", "tf": "M5", "end_utc": end1, "bars": batch}])
    rt.archive_plan["pending"] = None  # e.g. timed out
    archive.ingest_payload(rt, [{"symbol": "EURUSD", "tf": "M5", "end_utc": end1, "bars": batch}], now + 3)
    archive.ingest_payload(rt, [{"symbol": "EURUSD", "tf": "M5", "end_utc": end1, "bars": []}], now + 3)
    archive.drain()
    assert ("EURUSD", "M5") not in rt.archive_plan["exhausted"]


# ---- HISTX is not sent while an intent is UNKNOWN -------------------------------
def test_no_histx_while_intent_unknown(engine):
    now = time.time()
    first_sync(engine, now)
    engine.rt.trading.update(mode="DEMO", execution_enabled=True)
    it = engine.executor.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"],
                                     {"passed": True, "checks": []})
    assert any(l.startswith("OPEN|") for l in sync(engine, now + 1).splitlines())
    engine.executor.check_timeouts(now + 45)
    assert engine.executor.get(it["id"])["state"] == "UNKNOWN"
    assert histx_lines(sync(engine, now + 46)) == []


# ---- broker DST --------------------------------------------------------------------
def test_rule_offsets():
    assert archive.rule_offset_utc("EET", ts(2026, 7, 1)) == 3 * 3600
    assert archive.rule_offset_utc("EET", ts(2026, 10, 28)) == 2 * 3600
    assert archive.rule_offset_utc("NY", ts(2026, 10, 28)) == 3 * 3600  # US switch on 1 Nov
    assert archive.rule_offset_utc("NY", ts(2026, 11, 5)) == 2 * 3600
    # server-local stamp: 2026-07-01 12:00 EET-server = 09:00 UTC
    assert archive.rule_offset_local("EET", ts(2026, 7, 1, 12)) == 3 * 3600
    assert archive.rule_offset_utc("Nope/Zone", 0) is None


def test_archive_bars_reconverted_per_bar_offset(tmp_path):
    from app.db import DB
    from app.engine import Engine
    s = make_settings(tmp_path, MT5_SERVER_TZ="EET")
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    rt = eng.rt
    now = ts(2027, 1, 15, 12)  # winter: EA offset +2 h
    rt.bridge["srv_offset"] = 7200
    rt.bridge["clock_skew_s"] = 0
    end = ts(2026, 7, 1, 12)  # summer bars: server time was UTC+3
    real = bars_before(end, 10)
    ea_bars = [dict(b, t=b["t"] + 3600) for b in real]  # EA: t_srv - today's 2 h = real UTC + 1 h
    assert archive._end_delta(rt, end, now) == 3600  # HISTX end: EA adds +2 h, the bar's server time is +3 h
    ea_end = end + 3600
    rt.archive_plan["pending"] = {"symbol": "EURUSD", "tf": "M5", "end_utc": end, "ea_end": ea_end, "count": 10,
                                  "sent_ts": now}
    archive.ingest_payload(rt, [{"symbol": "EURUSD", "tf": "M5", "end_utc": ea_end, "srv_off": 7200,
                                 "bars": ea_bars}], now)
    archive.drain()
    got = [r["t"] for r in eng.db.query("SELECT t FROM bars_archive WHERE symbol='EURUSD' AND tf='M5' ORDER BY t")]
    assert got == [b["t"] for b in real]
    assert rt.archive_plan["pending"] is None
    # winter bars (same offset as today) are untouched
    assert archive._end_delta(rt, ts(2027, 1, 10), now) == 0
    # EA < 1.12 (no srv_off): unchanged behaviour
    rt.bridge["srv_offset"] = None
    assert archive.server_tz_rule(rt, now + 7200) is None
    eng.db.close()


def test_auto_rule_detection_and_memory(tmp_path):
    from app.db import DB
    from app.engine import Engine
    s = make_settings(tmp_path)
    assert s.MT5_SERVER_TZ == "auto"
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    rt = eng.rt
    rt.mt5_account = {"login": 1, "server": "Broker-Demo"}
    rt.bridge["clock_skew_s"] = 0
    rt.bridge["srv_offset"] = 7200
    assert archive.server_tz_rule(rt, ts(2026, 10, 28, 12)) == "EET"  # rules disagree this week: unambiguous
    assert eng.db.get_setting(archive.TZ_SETTING_KEY) == {"Broker-Demo": "EET"}
    # winter: both rules give +2 h -> the remembered detection wins over the NY default
    assert archive.server_tz_rule(rt, ts(2027, 1, 15, 12)) == "EET"
    rt.mt5_account = {"login": 2, "server": "Other-Server"}
    assert archive.server_tz_rule(rt, ts(2027, 1, 15, 13)) == "NY"
    rt.bridge["srv_offset"] = 10800
    assert archive.server_tz_rule(rt, ts(2026, 10, 28, 14)) == "NY"
    rt.bridge["srv_offset"] = 0  # GMT broker: no known DST rule -> today's offset
    assert archive.server_tz_rule(rt, ts(2026, 10, 28, 15)) is None
    eng.db.close()


def test_bridge_stores_srv_offset(engine):
    now = time.time()
    first_sync(engine, now)
    sync(engine, now + 1, version="1.12", srv_offset=7200)
    assert engine.rt.bridge["srv_offset"] == 7200
    sync(engine, now + 2)
    assert engine.rt.bridge["srv_offset"] is None


# ---- labeler / live break-even parity ------------------------------------------------
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_break_even_lock_matches_position_manager(side):
    sg = 1 if side == "BUY" else -1
    entry, r = 1.10000, 0.00150
    sl, tp = round(entry - sg * r, 5), round(entry + sg * 3 * r, 5)
    cfg = {"break_even_at_r": 1.0, "trailing_enabled": False, "max_hold_minutes": 0, "close_at_session_end": False}
    bid = round(entry + sg * (r + 0.0001), 5) if side == "BUY" else round(entry - r - 0.0001 - 0.00010, 5)
    acts = plan({"side": side, "price_open": entry, "sl": sl, "tp": tp, "opened_ts": time.time()},
                quote(bid=bid, spread_pts=10), cfg, time.time(), True, sl)
    assert acts and acts[0]["reason"] == "BREAK_EVEN"
    p = labeler.SimPosition(side, entry, sl, tp, 0.00001, 0, be_at_r=1.0)
    ext = bid if side == "BUY" else bid  # bar extreme on bid; SELL adds the spread inside step()
    bar = {"t": 0, "o": entry, "h": max(entry, ext), "l": min(entry, ext), "c": ext}
    assert p.step(bar, 0.00010) is None
    assert p.be_done and p.sl == pytest.approx(acts[0]["sl"])
    assert p.r_of(p.sl) == pytest.approx(2 / 150)


def test_break_even_waits_for_stops_room():
    # +1R reached but price too close to the lock for spread + stops level: SL not moved yet (live valid())
    p = labeler.SimPosition("BUY", 1.10000, 1.09998, 1.10100, 0.00001, 0, be_at_r=1.0, stops_px=0.00010)
    assert p.step({"t": 0, "o": 1.1, "h": 1.10005, "l": 1.1, "c": 1.10004}, 0.00001) is None
    assert not p.be_done and p.sl == 1.09998
    assert p.step({"t": 60, "o": 1.1, "h": 1.10020, "l": 1.1, "c": 1.1001}, 0.00001) is None
    assert p.be_done and p.sl == pytest.approx(1.10002)


# ---- max_hold_minutes = 0 ----------------------------------------------------------------
def test_labeler_cfg_keeps_zero_max_hold(engine):
    engine.rt.strategy["max_hold_minutes"] = 0
    assert engine.labeler_cfg()["max_hold_minutes"] == 0
    engine.rt.strategy["max_hold_minutes"] = 90
    assert engine.labeler_cfg()["max_hold_minutes"] == 90


def test_zero_max_hold_resolves_at_horizon(engine):
    db = engine.db
    t0 = ts(2026, 9, 1, 8)
    db.execute("INSERT INTO analyses(symbol, tf, bar_time, candidate_side, entry, sl, tp) VALUES('EURUSD','M5',?,?,?,?,?)",
               (t0, "BUY", 1.1, 1.09, 1.11))
    bars = [{"t": t0 + 300 + k * 60, "o": 1.1, "h": 1.1004, "l": 1.0996, "c": 1.1002, "sp": 0}
            for k in range(26 * 60)]

    def get_bars(symbol, tf, a, b):
        return [x for x in bars if a <= x["t"] < b] if tf == "M1" else []

    cfg = {"break_even_at_r": 1.0, "max_hold_minutes": 0, "label_tf": "M1", "spread_mode": "bar", "slippage_pts": 0}
    assert labeler.label_pending(db, get_bars, cfg, t0 + 30 * 86400) == 1
    r = db.query_one("SELECT * FROM analyses")
    assert r["outcome"] == "HORIZON" and r["outcome_r"] == pytest.approx(0.02)


# ---- shadow label costs ------------------------------------------------------------------
def test_shadow_labels_use_spread_stats_and_slippage(engine):
    db = engine.db
    t0 = ts(2026, 9, 1, 8)
    db.execute("INSERT INTO spread_stats(symbol, hour_utc, n, sum_pts, sum_sq, max_pts) VALUES('EURUSD', 8, 2, 30, 450, 15)")
    db.execute("INSERT INTO analyses(symbol, tf, bar_time, candidate_side, entry, sl, tp) VALUES('EURUSD','M5',?,?,?,?,?)",
               (t0, "BUY", 1.1, 1.0990, 1.1100))
    bars = [{"t": t0 + 300, "o": 1.1, "h": 1.1001, "l": 1.0989, "c": 1.0990, "sp": 2}]

    def get_bars(symbol, tf, a, b):
        return [x for x in bars if a <= x["t"] < b] if tf == "M1" else []

    labeler.label_pending(db, get_bars, {"break_even_at_r": 1.0, "max_hold_minutes": 60, "label_tf": "M1",
                                         "points": {"EURUSD": 0.00001}}, t0 + 86400)
    r = db.query_one("SELECT * FROM analyses")
    entry = 1.1 + 15 * 0.00001  # hour-8 average spread (15 pts), not the bar's minimum (2 pts)
    exit_px = 1.0990 - 0.00001  # 1 pt stop slippage
    assert r["outcome"] == "SL"
    assert r["outcome_r"] == pytest.approx((exit_px - entry) / (entry - 1.0990), abs=1e-4)


# ---- backtest reads through its own read-only connection -------------------------------
def test_backtest_job_uses_read_only_connection(engine, monkeypatch):
    import app.backtest as bt
    seen = {}

    def fake_run(db, params, progress=None):
        seen["db"] = db
        seen["n"] = db.query("SELECT COUNT(*) AS n FROM settings")[0]["n"]
        return {"summary": {}}

    monkeypatch.setattr(bt, "run", fake_run)
    engine._backtest_job("x", {"symbols": ["EURUSD"]})
    assert isinstance(seen["db"], bt.ReadOnlyDB) and seen["db"] is not engine.db
    assert seen["n"] >= 0
