"""Stage B archive: additive migrations, HISTX planner, archive ingestion, spread_stats, archive_status."""
import sqlite3
import time

from app import archive
from app.bridge import process_sync
from app.db import DB
from app.engine import Engine
from tests.conftest import make_settings
from tests.test_bridge import payload

M5 = 300


def histx_lines(txt: str) -> list[str]:
    return [l for l in txt.splitlines() if l.startswith("HISTX|")]


def quiet_hist(rt, now):
    """Live HIST refills have priority over the archive; pretend they are all satisfied."""
    rt.hist_force.clear()
    for s in rt.active_symbols():
        for tf in rt.needed_tfs():
            rt.hist_requested[(s, tf)] = now


def sync(engine, now, version="1.11", quiet=True, **kw):
    """One EA sync at `now`, as if the EA had been syncing continuously and live refills were done."""
    rt = engine.rt
    if quiet and rt.bridge.get("last_sync_ts") is not None:
        rt.bridge["last_sync_ts"] = now - 0.5
        quiet_hist(rt, now)
    return process_sync(engine, payload(now, ea_version=version, **kw), now)


def first_sync(engine, now, version="1.11"):
    sync(engine, now, version)
    quiet_hist(engine.rt, now)


def bars_before(end, n, tf_sec=M5, start_price=1.08):
    out = []
    for i in range(n):
        t = end - (n - i) * tf_sec
        p = round(start_price + 0.0001 * (i % 7), 5)
        out.append({"t": t, "o": p, "h": p + 0.0002, "l": p - 0.0002, "c": p + 0.0001, "tv": 50, "sp": 7})
    return out


def parse_histx(line):
    _, sym, tf, end, count = line.split("|")
    return sym, tf, int(end), int(count)


def archive_count(db, sym="EURUSD", tf="M5"):
    return db.query_one("SELECT COUNT(*) AS n FROM bars_archive WHERE symbol=? AND tf=?", (sym, tf))["n"]


# ---- migrations ------------------------------------------------------------
OLD_SCHEMA = """
CREATE TABLE signals (id TEXT PRIMARY KEY, created_at_utc TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
    bar_time INTEGER NOT NULL, direction TEXT NOT NULL, status TEXT NOT NULL, dedup_key TEXT NOT NULL UNIQUE,
    data_json TEXT NOT NULL);
CREATE TABLE intents (id TEXT PRIMARY KEY, created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL, kind TEXT NOT NULL,
    signal_id TEXT, symbol TEXT, state TEXT NOT NULL, mode TEXT NOT NULL, ticket INTEGER, comment TEXT, reason TEXT,
    delivered_at REAL, data_json TEXT NOT NULL);
CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, deal_key TEXT NOT NULL UNIQUE, ticket INTEGER, symbol TEXT,
    side TEXT, volume REAL, price_open REAL, price_close REAL, profit REAL, commission REAL, swap REAL,
    opened_at_utc TEXT, closed_at_utc TEXT, close_reason TEXT, mode TEXT, signal_id TEXT);
CREATE TABLE bars (symbol TEXT NOT NULL, tf TEXT NOT NULL, t INTEGER NOT NULL, o REAL, h REAL, l REAL, c REAL, tv REAL,
    sp REAL, PRIMARY KEY (symbol, tf, t));
"""


def cols(db, table):
    return {r["name"] for r in db.query(f"PRAGMA table_info({table})")}


def test_migration_on_old_schema_is_additive(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.execute("INSERT INTO trades(deal_key, ticket, symbol, profit) VALUES('d1', 5, 'EURUSD', 1.5)")
    con.execute("INSERT INTO signals VALUES('s1','2026-01-01T00:00:00Z','EURUSD','M5',1,'BUY','NEW','k1','{}')")
    con.execute("INSERT INTO bars VALUES('EURUSD','M5',1730000000,1,1,1,1,1,1)")
    con.commit()
    con.close()

    db = DB(path)
    assert set(db.migrated) == {"trades.account_key", "trades.signal_entry", "trades.initial_sl", "trades.risk_money",
                                "trades.r_multiple", "signals.account_key", "intents.account_key"}
    assert {"account_key", "signal_entry", "initial_sl", "risk_money", "r_multiple"} <= cols(db, "trades")
    assert "account_key" in cols(db, "signals") and "account_key" in cols(db, "intents")
    for t in ("bars_archive", "spread_stats", "analyses", "equity_snapshots", "claude_usage", "backtest_runs"):
        assert db.query_one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)), t
    assert {"symbol", "tf", "bar_time", "candidate_side", "blockers_json", "ai_verdict", "outcome_r", "mfe_r", "mae_r",
            "labeled_ts", "account_key"} <= cols(db, "analyses")
    # existing data untouched
    assert db.query_one("SELECT profit FROM trades WHERE deal_key='d1'")["profit"] == 1.5
    assert db.query_one("SELECT COUNT(*) AS n FROM signals")["n"] == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM bars")["n"] == 1
    db.execute("INSERT INTO analyses(symbol, tf, bar_time) VALUES('EURUSD','M5',1)")
    db.execute("INSERT OR IGNORE INTO analyses(symbol, tf, bar_time) VALUES('EURUSD','M5',1)")
    assert db.query_one("SELECT COUNT(*) AS n FROM analyses")["n"] == 1
    db.close()
    db2 = DB(path)  # idempotent
    assert db2.migrated == []
    db2.close()


# ---- planner -----------------------------------------------------------------
def test_first_histx_and_version_gating(engine):
    now = time.time()
    first_sync(engine, now, version="1.10")
    assert histx_lines(sync(engine, now + 1, version="1.10")) == []
    assert histx_lines(sync(engine, now + 2, version="1.00")) == []
    lines = histx_lines(sync(engine, now + 3))
    assert len(lines) == 1
    sym, tf, end, count = parse_histx(lines[0])
    assert (sym, tf) == ("EURUSD", "M5")  # signal tf first
    assert end == int((now + 3) // M5 * M5)  # current (forming) bar open: only closed bars are older
    assert count == 5000
    st = engine.rt.archive_status["EURUSD"]
    assert set(st) == {"M5", "H1", "M15"}
    assert st["M5"]["done"] is False and st["M5"]["target_ts"] == int(now + 3 - 120 * 86400)


def test_disabled_and_hist_priority(tmp_path):
    s = make_settings(tmp_path, ARCHIVE_ENABLED=False)
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    now = time.time()
    first_sync(eng, now)
    assert histx_lines(sync(eng, now + 1)) == []
    eng.db.close()

    s2 = make_settings(tmp_path / "b")
    eng2 = Engine(s2, DB(s2.db_path))
    eng2.startup()
    sync(eng2, now)  # first sync: forced HIST refills pending -> no archive request
    assert histx_lines(sync(eng2, now + 1, quiet=False)) == []
    eng2.db.close()


def test_one_request_outstanding_and_timeout(engine):
    now = time.time()
    first_sync(engine, now)
    assert len(histx_lines(sync(engine, now + 1))) == 1
    # no reply yet: nothing more is asked
    assert histx_lines(sync(engine, now + 2)) == []
    assert histx_lines(sync(engine, now + 30)) == []
    # reply lost: after the timeout the pair is retried with backoff, meanwhile the next pair is served
    lines = histx_lines(sync(engine, now + 1 + archive.PENDING_TIMEOUT_S + 1))
    assert engine.rt.archive_plan["retry_at"][("EURUSD", "M5")] > now + 60
    assert [parse_histx(l)[:2] for l in lines] == [("EURUSD", "H1")]


def test_no_histx_when_commands_pending(engine):
    now = time.time()
    first_sync(engine, now)
    engine.rt.trading.update(mode="DEMO", execution_enabled=True)
    it = engine.executor.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"],
                                     {"passed": True, "checks": []})
    txt = sync(engine, now + 1)
    assert any(l.startswith("OPEN|") for l in txt.splitlines())
    assert histx_lines(txt) == []
    # delivered but no result yet -> still no HISTX
    assert histx_lines(sync(engine, now + 2)) == []
    res = {"cmd_id": it["id"], "ok": True, "retcode": 10009, "retcode_desc": "done", "order": 1, "deal": 2,
           "position": 777, "price": 1.0801, "volume": 0.01, "comment": ""}
    # result received -> nothing in flight any more: the archive request goes out on this same sync
    assert len(histx_lines(sync(engine, now + 3, results=[res]))) == 1


def test_backwards_walk_done_and_next_pair(tmp_path):
    s = make_settings(tmp_path, ARCHIVE_DAYS=2, ARCHIVE_BATCH_BARS=400)
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    rt = eng.rt
    now = time.time()
    first_sync(eng, now)
    sym, tf, end1, count1 = parse_histx(histx_lines(sync(eng, now + 1))[0])
    assert (tf, count1) == ("M5", 400)
    # EA reply: 400 closed bars strictly older than end1, plus junk that must be dropped
    batch = bars_before(end1, 400)
    junk = [dict(batch[-1], t=end1), dict(batch[-1], t=end1 + M5), dict(batch[0], t=batch[0]["t"] + 7)]
    txt = sync(eng, now + 2, archive=[{"symbol": "EURUSD", "tf": "M5", "end_utc": end1, "bars": batch + junk}])
    archive.drain()
    assert archive_count(eng.db) == 400
    e = rt.archive_status["EURUSD"]["M5"]
    assert e["oldest_ts"] == batch[0]["t"] and e["bars"] == 400 and not e["done"]
    # next request continues from the oldest bar, only what is left to reach the target
    sym, tf, end2, count2 = parse_histx(histx_lines(txt)[0])
    assert (tf, end2) == ("M5", batch[0]["t"])
    left = -(-(end2 - e["target_ts"]) // M5)
    assert count2 == min(400, left)
    batch2 = bars_before(end2, count2)
    txt = sync(eng, now + 3, archive=[{"symbol": "EURUSD", "tf": "M5", "end_utc": end2, "bars": batch2}])
    archive.drain()
    assert rt.archive_status["EURUSD"]["M5"]["done"] is True
    assert archive_count(eng.db) == 400 + count2
    # M5 complete -> H1 is next
    sym, tf, end3, _ = parse_histx(histx_lines(txt)[0])
    assert tf == "H1" and end3 == int((now + 3) // 3600 * 3600)
    # broker has nothing older for H1: empty list -> done (exhausted), then M15
    txt = sync(eng, now + 4, archive=[{"symbol": "EURUSD", "tf": "H1", "end_utc": end3, "bars": []}])
    assert rt.archive_status["EURUSD"]["H1"]["done"] is True
    assert parse_histx(histx_lines(txt)[0])[1] == "M15"
    eng.db.close()


def test_err_reply_is_retried_not_done(engine):
    now = time.time()
    first_sync(engine, now)
    _, _, end, _ = parse_histx(histx_lines(sync(engine, now + 1))[0])
    txt = sync(engine, now + 2, archive=[{"symbol": "EURUSD", "tf": "M5", "end_utc": end, "bars": [], "err": 4401}])
    st = engine.rt.archive_status["EURUSD"]["M5"]
    assert st["done"] is False
    assert ("EURUSD", "M5") not in engine.rt.archive_plan["exhausted"]
    # M5 waits for its backoff; the planner moves on to H1 meanwhile
    assert parse_histx(histx_lines(txt)[0])[1] == "H1"
    retry = engine.rt.archive_plan["retry_at"][("EURUSD", "M5")]
    assert retry == now + 2 + archive.ERR_BACKOFF_S
    sync(engine, now + 3, archive=[{"symbol": "EURUSD", "tf": "H1", "end_utc": 0, "bars": [], "err": 4401}])
    assert histx_lines(sync(engine, now + 4)) == [] or parse_histx(histx_lines(sync(engine, now + 5))[0])[1] == "M15"
    engine.rt.archive_plan["pending"] = None
    lines = histx_lines(sync(engine, retry + 1))
    assert [parse_histx(l)[:2] for l in lines] == [("EURUSD", "M5")]


def test_ingestion_dedup_and_clock_skew(engine):
    now = time.time()
    first_sync(engine, now)
    _, _, end, _ = parse_histx(histx_lines(sync(engine, now + 1))[0])
    batch = bars_before(end, 100)
    sync(engine, now + 2, archive=[{"symbol": "EURUSD", "tf": "M5", "end_utc": end, "bars": batch}])
    archive.drain()
    assert archive_count(engine.db) == 100
    # the same bars again (EA resent after a lost HTTP 200): no duplicates
    archive.ingest_payload(engine.rt, [{"symbol": "EURUSD", "tf": "M5", "end_utc": end, "bars": batch}], now + 3)
    archive.drain()
    assert archive_count(engine.db) == 100
    assert engine.rt.archive_status["EURUSD"]["M5"]["bars"] == 100
    row = engine.db.query_one("SELECT * FROM bars_archive WHERE t=?", (batch[5]["t"],))
    assert row["o"] == batch[5]["o"] and row["sp"] == 7 and row["tv"] == 50

    # EA clock one hour ahead: archive bar times are corrected like live bars
    p = payload(now + 4, ea_version="1.11")
    p["time_utc"] = int(now + 4 + 3600)
    p["symbols"][0]["time_msc"] = int((now + 4 + 3600) * 1000)
    old_end = batch[0]["t"]
    shifted = [dict(b, t=b["t"] + 3600) for b in bars_before(old_end, 10)]
    p["archive"] = [{"symbol": "EURUSD", "tf": "M5", "end_utc": old_end + 3600, "bars": shifted}]
    process_sync(engine, p, now + 4)
    archive.drain()
    assert engine.rt.bridge["clock_skew_s"] == 3600
    assert archive_count(engine.db) == 110
    assert engine.rt.archive_status["EURUSD"]["M5"]["oldest_ts"] == old_end - 10 * M5


def test_histx_end_in_ea_clock_frame(engine):
    now = time.time()
    first_sync(engine, now)
    engine.rt.bridge["clock_skew_s"] = 0
    p = payload(now + 1, ea_version="1.11")
    p["time_utc"] = int(now + 1 + 7200)
    p["symbols"][0]["time_msc"] = int((now + 1 + 7200) * 1000)
    _, _, end, _ = parse_histx(histx_lines(process_sync(engine, p, now + 1))[0])
    assert end == int((now + 1) // M5 * M5) + 7200
    assert engine.rt.archive_plan["pending"]["end_utc"] == int((now + 1) // M5 * M5)


def test_live_bars_mirrored_into_archive(engine):
    now = time.time()
    t = int(now // M5 * M5) - M5
    engine.rt.ingest_bar("EURUSD", "M5", {"t": t, "o": 1.1, "h": 1.2, "l": 1.0, "c": 1.15, "tv": 3, "sp": 9})
    engine.rt.flush_bars()
    row = engine.db.query_one("SELECT * FROM bars_archive WHERE symbol='EURUSD' AND tf='M5' AND t=?", (t,))
    assert row and row["c"] == 1.15


# ---- archive_status rebuild ----------------------------------------------------
def test_archive_status_rebuild_from_db(tmp_path):
    s = make_settings(tmp_path, ARCHIVE_DAYS=10)
    db = DB(s.db_path)
    now = time.time()
    old = int(now - 11 * 86400) // M5 * M5
    rows = [("EURUSD", "M5", old + i * M5, 1, 1, 1, 1, 1, 1) for i in range(50)]
    rows += [("EURUSD", "H1", int(now - 3 * 86400) // 3600 * 3600 + i * 3600, 1, 1, 1, 1, 1, 1) for i in range(20)]
    rows += [("GBPUSD", "M15", int(now - 86400) // 900 * 900, 1, 1, 1, 1, 1, 1)]
    db.executemany("INSERT INTO bars_archive VALUES(?,?,?,?,?,?,?,?,?)", rows)
    eng = Engine(s, db)
    eng.startup()
    st = eng.rt.archive_status
    assert st["EURUSD"]["M5"] == {"bars": 50, "oldest_ts": old, "target_ts": st["EURUSD"]["M5"]["target_ts"],
                                  "done": True}
    assert abs(st["EURUSD"]["M5"]["target_ts"] - (now - 10 * 86400)) < 5
    assert st["EURUSD"]["H1"]["bars"] == 20 and st["EURUSD"]["H1"]["done"] is False
    assert st["EURUSD"]["M15"] == {"bars": 0, "oldest_ts": None, "target_ts": st["EURUSD"]["M15"]["target_ts"],
                                   "done": False}
    assert st["GBPUSD"]["M15"]["bars"] == 1  # inactive symbols are still reported
    # planner skips the completed M5 pair
    first_sync(eng, now)
    _, tf, end, _ = parse_histx(histx_lines(sync(eng, now + 1))[0])
    assert tf == "H1" and end == st["EURUSD"]["H1"]["oldest_ts"]
    db.close()


# ---- spread_stats ----------------------------------------------------------------
def test_spread_stats_sampling_and_flush(engine):
    rt = engine.rt
    now = time.time()
    hour = int(now // 3600) % 24
    sync(engine, now, version="1.00", symbols=[dict(payload(now)["symbols"][0], spread_points=10)])
    # within 10 s: not sampled again
    sync(engine, now + 5, version="1.00", symbols=[dict(payload(now + 5)["symbols"][0], spread_points=50)])
    sync(engine, now + 11, version="1.00", symbols=[dict(payload(now + 11)["symbols"][0], spread_points=14)])
    hour2 = int((now + 11) // 3600) % 24
    acc = rt.spread_agg["acc"]
    assert sum(a[0] for a in acc.values()) == 2
    # stale tick (market closed): not sampled
    stale = dict(payload(now + 30)["symbols"][0], spread_points=99, time_msc=int((now - 600) * 1000))
    sync(engine, now + 30, version="1.00", symbols=[stale])
    assert sum(a[0] for a in rt.spread_agg["acc"].values()) == 2
    fut = archive.flush_spreads(rt, now + 31, force=True)
    fut.result(5)
    assert rt.spread_agg["acc"] == {}
    rows = engine.db.query("SELECT * FROM spread_stats WHERE symbol='EURUSD'")
    assert sum(r["n"] for r in rows) == 2
    assert sum(r["sum_pts"] for r in rows) == 24 and sum(r["sum_sq"] for r in rows) == 100 + 196
    assert max(r["max_pts"] for r in rows) == 14
    assert {r["hour_utc"] for r in rows} == {hour, hour2}
    # later flushes accumulate into the same hour rows
    rt.spread_agg["acc"] = {("EURUSD", hour): [1, 30.0, 900.0, 30.0]}
    archive.flush_spreads(rt, now + 200).result(5)
    r = engine.db.query_one("SELECT * FROM spread_stats WHERE symbol='EURUSD' AND hour_utc=?", (hour,))
    assert r["max_pts"] == 30 and r["n"] >= 2
    # automatic flush happens at most every 60 s from the bridge
    assert archive.flush_spreads(rt, now + 201) is None
