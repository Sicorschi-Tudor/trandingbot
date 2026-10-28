"""Economic-calendar history (CALHIST, EA >= 1.14): planner, gating, ingestion, dedupe, DST/skew, status."""
import time
from datetime import datetime, timezone

from app import archive, calendar_hist as ch
from app.db import DB
from app.engine import Engine
from tests.conftest import make_settings

DAY = 86400
LONG_MIN = -(2 ** 63)


def ts(*a) -> int:
    return int(datetime(*a, tzinfo=timezone.utc).timestamp())


NOW = ts(2026, 7, 1, 12)  # summer: NY-close broker at GMT+3


def make_engine(tmp_path, ea="1.14", **kw):
    kw.setdefault("ACTIVE_SYMBOLS", "EURUSD")
    s = make_settings(tmp_path, **kw)
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    rt = eng.rt
    rt.bridge.update({"ea_version": ea, "clock_skew_s": 0, "srv_offset": None})
    connect(rt)
    # the stock archive planner is out of the way (no pending HISTX) unless a test sets one
    rt.archive_plan["pending"] = None
    return eng


def connect(rt):
    rt.bridge["last_sync_ts"] = time.time()


def parse(line):
    tag, a, b, imp = line.split("|")
    assert tag == "CALHIST"
    return int(a), int(b), int(imp)


def item(eid, t, a=1500000, f=1200000, p=1000000, r=LONG_MIN, **kw):
    d = {"id": eid * 10 + 1, "eid": eid, "t": t, "ccy": "USD", "cc": "US", "imp": 3, "name": f"Event {eid}",
         "tm": 0, "unit": 1, "mult": 0, "dig": 1, "a": a, "f": f, "p": p, "r": r, "imx": 1, "rev": 0, "per": t}
    d.update(kw)
    return d


def reply_for(line, items=(), **kw):
    a, b, _ = parse(line)
    d = {"from_utc": a, "to_utc": b, "srv_off": None, "ok": True, "err": 0, "total": len(items),
         "truncated": False, "items": list(items)}
    d.update(kw)
    return d


def rows(eng, where="1=1", params=()):
    archive.drain()
    return eng.db.query(f"SELECT * FROM calendar_history WHERE {where} ORDER BY t, event_id", params)


# ---- schema -----------------------------------------------------------------------------
def test_migration_creates_tables_idempotent_and_keeps_db_migrated(tmp_path):
    db = DB(tmp_path / "a.db")
    names = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"calendar_history", "calendar_history_windows"} <= names
    assert not any("calendar" in m for m in db.migrated)  # the column-migration report is untouched
    cols = {r["name"] for r in db.query("PRAGMA table_info(calendar_history)")}
    assert {"event_id", "t", "ccy", "imp", "name", "actual", "forecast", "previous", "revised_previous", "unit",
            "multiplier", "digits", "value_id"} <= cols
    db.close()
    db2 = DB(tmp_path / "a.db")
    assert ch.migrate(db2.conn) == []
    db2.close()


def test_version_gate():
    assert ch.CALHIST_MIN_EA == "1.14"
    assert not ch.ea_supported("1.12") and not ch.ea_supported("1.13") and not ch.ea_supported(None)
    assert ch.ea_supported("1.14") and ch.ea_supported("1.20") and ch.ea_supported("2.0")


# ---- gating ---------------------------------------------------------------------------
def test_not_requested_for_older_ea(tmp_path):
    for v in ("1.12", "1.13", ""):
        eng = make_engine(tmp_path, ea=v)
        assert ch.next_request(eng.rt, NOW) is None
        assert ch.on_sync(eng, {}, [], NOW) == []
        eng.db.close()


def test_gating_rules(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    assert ch.next_request(rt, NOW, commands_pending=True) is None
    assert ch.next_request(rt, NOW, hist_pending=True) is None
    assert ch.next_request(rt, NOW, histx_pending=True) is None
    rt.archive_plan["pending"] = {"symbol": "EURUSD", "tf": "M5", "end_utc": 1, "ea_end": 1, "count": 1,
                                  "sent_ts": NOW, "extended": False}
    assert ch.next_request(rt, NOW) is None
    rt.archive_plan["pending"] = None
    rt.mt5_positions = [{"ticket": 1, "magic": int(rt.settings.MT5_MAGIC), "symbol": "EURUSD"}]
    assert ch.next_request(rt, NOW) is None
    rt.mt5_positions = [{"ticket": 2, "magic": 1, "symbol": "EURUSD"}]  # manual position: no effect
    rt.bridge["last_sync_ts"] = time.time() - 60
    assert ch.next_request(rt, NOW) is None  # bridge not connected
    connect(rt)
    assert ch.next_request(rt, NOW) is not None
    eng.db.close()


def test_on_sync_skips_with_trading_commands_or_histx(tmp_path):
    eng = make_engine(tmp_path)
    assert ch.on_sync(eng, {}, ["OPEN|x|EURUSD|BUY|0.01|1|2|20|c"], NOW) == []
    assert ch.on_sync(eng, {}, ["HISTX|EURUSD|M5|1|10"], NOW) == []
    assert ch.on_sync(eng, {}, [], NOW, hist_pending=True) == []
    out = ch.on_sync(eng, {}, ["CALENDAR"], NOW)
    assert len(out) == 1 and out[0].startswith("CALHIST|")
    eng.db.close()


# ---- planner: year by year back to 2015 -------------------------------------------------
def test_walks_back_year_by_year_to_2015(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD")
    rt = eng.rt
    now = NOW
    seen = []
    for _ in range(40):
        connect(rt)
        line = ch.next_request(rt, now)
        if line is None:
            break
        pend = rt.calhist_plan["pending"]
        a, b, imp = parse(line)
        assert imp == 1
        assert a == pend["lo"] - ch.EDGE_PAD_S and b == pend["hi"] + ch.EDGE_PAD_S
        seen.append((pend["lo"], pend["hi"]))
        ch.ingest_payload(rt, reply_for(line, [item(7, pend["lo"] + DAY, id=700 + len(seen))]), now)
        now += 31
    archive.drain()
    # first: the current year up to now + 7 d, then whole years backwards, never crossing 1 January
    assert seen[0] == (ts(2026, 1, 1), int(NOW + ch.HORIZON_S))
    years = [datetime.fromtimestamp(lo, timezone.utc).year for lo, _ in seen]
    assert years == list(range(2026, 2014, -1))
    for lo, hi in seen[1:]:
        assert lo == ch.year_floor(lo) and hi == ch.year_floor(hi) and datetime.fromtimestamp(hi, timezone.utc).year == \
            datetime.fromtimestamp(lo, timezone.utc).year + 1
    assert seen[-1][0] == ch.CALHIST_START
    assert ch.next_request(rt, now + 100) is None  # backfill done, recent zone not due yet
    st = ch.status_view(rt, now)
    assert st["backfill_done"] and st["rows"] == 12 and st["rows_by_year"]["2015"] == 1
    # windows persisted
    assert len(eng.db.query("SELECT * FROM calendar_history_windows")) == 12
    eng.db.close()


def test_interval_and_pending_timeout_backoff(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD")
    rt = eng.rt
    line = ch.next_request(rt, NOW)
    assert line
    assert ch.next_request(rt, NOW + 10) is None  # one in flight
    ch.ingest_payload(rt, reply_for(line), NOW + 20)
    assert rt.calhist_plan["pending"] is None
    assert ch.next_request(rt, NOW + 30) is None  # 30 s interval from the reply
    line2 = ch.next_request(rt, NOW + 51)
    assert line2
    # no reply -> timeout -> error backoff (30 s) before the next try
    assert ch.next_request(rt, NOW + 51 + ch.PENDING_TIMEOUT_S + 1) is None
    assert rt.calhist_plan["errors"] == 1
    assert ch.next_request(rt, NOW + 51 + ch.PENDING_TIMEOUT_S + 40) is not None
    eng.db.close()


def test_error_reply_backs_off_and_does_not_cover(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    line = ch.next_request(rt, NOW)
    ch.ingest_payload(rt, reply_for(line, ok=False, err=5401), NOW + 1)
    assert rt.calhist_plan["errors"] == 1 and rt.calhist_plan["retry_at"] > NOW + 1
    assert ch.covered(rt) == []
    assert ch.next_request(rt, NOW + 20) is None  # backoff 30 s from the error reply
    again = ch.next_request(rt, NOW + 40)
    assert again and parse(again)[0] == parse(line)[0] and rt.calhist_plan["pending"]["recent"]
    eng.db.close()


def test_truncated_shrinks_window_and_retries_same_top(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    now = NOW
    line = ch.next_request(rt, now)
    p1 = dict(rt.calhist_plan["pending"])
    its = [item(100 + i, p1["lo"] + i * 3600) for i in range(5)]
    ch.ingest_payload(rt, reply_for(line, its, truncated=True, total=20000), now)
    assert ch.covered(rt) == []  # nothing recorded as covered
    assert len(rows(eng)) == 5  # but the rows we got are kept
    span = p1["hi"] - p1["lo"]
    assert rt.calhist_plan["window_s"] == int(span * 0.8 * ch.EA_MAX_ITEMS / 20000)
    line2 = ch.next_request(rt, now + 31)
    p2 = rt.calhist_plan["pending"]
    assert p2["recent"] and p2["hi"] == now + 31 + ch.HORIZON_S  # same (moving) top edge
    assert p2["hi"] - p2["lo"] == rt.calhist_plan["window_s"]
    eng.db.close()


def test_recent_zone_refetched_every_6h(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    # pretend the backfill is complete up to the current year
    rt.calhist_plan = None
    ch.plan_of(rt)["windows"] = [(ch.CALHIST_START, ts(2026, 1, 1), NOW - 100 * DAY)]
    line = ch.next_request(rt, NOW)
    p = rt.calhist_plan["pending"]
    assert p["recent"] and p["hi"] == NOW + ch.HORIZON_S
    ch.ingest_payload(rt, reply_for(line), NOW)
    assert ch.next_request(rt, NOW + 3600) is None
    line = ch.next_request(rt, NOW + ch.RECENT_EVERY_S + 1)
    p = rt.calhist_plan["pending"]
    assert line and p["recent"]
    assert p["lo"] == NOW - ch.SETTLE_S  # only the not-yet-final part is fetched again
    eng.db.close()


def test_resume_after_restart_from_windows_table(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    line = ch.next_request(rt, NOW)
    ch.ingest_payload(rt, reply_for(line), NOW)
    archive.drain()
    rt.calhist_plan = None  # restart: plan rebuilt from calendar_history_windows
    line = ch.next_request(rt, NOW + 100)
    p = rt.calhist_plan["pending"]
    assert p["recent"] and p["lo"] == NOW - ch.SETTLE_S  # recent zone first (not final yet)
    ch.ingest_payload(rt, reply_for(line), NOW + 101)
    line = ch.next_request(rt, NOW + 200)
    p = rt.calhist_plan["pending"]
    assert (p["lo"], p["hi"]) == (ts(2025, 1, 1), ts(2026, 1, 1))  # the backfill resumes below this year
    eng.db.close()


# ---- ingestion ----------------------------------------------------------------------------
def test_values_scaled_missing_and_metadata(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    line = ch.next_request(rt, NOW)
    t0 = ts(2026, 6, 5, 12, 30)
    its = [item(1, t0, a=250000000, f=180000000, p=-50000, r=LONG_MIN, unit=4, mult=1, dig=0),
           item(2, t0 + 60, a=None, f=None, p=3100000, r=3200000, unit=1, mult=0, dig=1),
           {"eid": "bad", "t": t0}, {"eid": 3}, "junk"]
    ch.ingest_payload(rt, reply_for(line, its), NOW)
    r = rows(eng)
    assert len(r) == 2
    a, b = r
    assert (a["event_id"], a["t"], a["ccy"], a["country"], a["imp"], a["name"]) == (1, t0, "USD", "US", 3, "Event 1")
    assert a["actual"] == 250.0 and a["forecast"] == 180.0 and a["previous"] == -0.05 and a["revised_previous"] is None
    assert (a["unit"], a["unit_name"], a["multiplier"], a["multiplier_name"], a["digits"]) == (4, "JOB", 1, "THOUSANDS", 0)
    assert a["value_id"] == 11
    assert b["actual"] is None and b["forecast"] is None and b["previous"] == 3.1 and b["revised_previous"] == 3.2
    assert b["unit_name"] == "PERCENT" and b["multiplier_name"] == "NONE"
    eng.db.close()


def test_dedupe_on_event_id_and_time_updates_numbers(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    t0 = ts(2026, 6, 26, 12, 30)
    # forecast known, actual not yet
    ch.ingest_payload(rt, {"from_utc": 1, "to_utc": 2, "items": [item(5, t0, a=None, f=2000000)]}, NOW)
    # later fetch: actual released; same (event_id, t) -> one row, numbers updated
    ch.ingest_payload(rt, {"from_utc": 1, "to_utc": 2, "items": [item(5, t0, a=2600000, f=None, rev=1)]}, NOW + 10)
    # duplicate inside one reply too
    ch.ingest_payload(rt, {"from_utc": 1, "to_utc": 2, "items": [item(5, t0), item(5, t0)]}, NOW + 20)
    r = rows(eng)
    assert len(r) == 1
    assert r[0]["forecast"] == 1.2 and r[0]["actual"] == 1.5 and r[0]["revision"] == 1
    ch.ingest_payload(rt, {"from_utc": 1, "to_utc": 2, "items": [item(5, t0 + 7 * DAY, id=52)]}, NOW + 30)
    assert len(rows(eng)) == 2  # same event, another release (another MT5 value id) -> new row
    eng.db.close()


def test_rescheduled_release_moves_its_row_instead_of_duplicating(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    t0 = ts(2026, 6, 26, 12, 30)
    ch.ingest_payload(rt, {"from_utc": 1, "to_utc": 2, "items": [item(5, t0, a=None, f=2000000)]}, NOW)
    # MT5 keeps the value id when a release is rescheduled; only value.time changes
    ch.ingest_payload(rt, {"from_utc": 1, "to_utc": 2, "items": [item(5, t0 + 3600, a=2600000, f=None)]}, NOW + 10)
    r = rows(eng)
    assert len(r) == 1
    assert (r[0]["value_id"], r[0]["t"], r[0]["actual"], r[0]["forecast"]) == (51, t0 + 3600, 2.6, 2.0)
    # the DST-fallback / DST-rule branches of _fix_time differing by an hour cannot duplicate a value either
    ch.ingest_payload(rt, {"from_utc": 1, "to_utc": 2, "items": [item(5, t0)]}, NOW + 20)
    assert [(x["value_id"], x["t"]) for x in rows(eng)] == [(51, t0)]
    # items without a usable value id are dropped (no identity to dedupe on)
    ch.ingest_payload(rt, {"from_utc": 1, "to_utc": 2, "items": [item(6, t0, id=None), item(8, t0, id=0)]}, NOW + 30)
    assert len(rows(eng)) == 1
    eng.db.close()


def test_old_event_time_keyed_table_is_rekeyed_on_value_id(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE calendar_history (event_id INTEGER NOT NULL, t INTEGER NOT NULL, value_id INTEGER,
        ccy TEXT, country TEXT, imp INTEGER, name TEXT, time_mode INTEGER, unit INTEGER, unit_name TEXT,
        multiplier INTEGER, multiplier_name TEXT, digits INTEGER, actual REAL, forecast REAL, previous REAL,
        revised_previous REAL, impact INTEGER, revision INTEGER, period_srv INTEGER, updated_ts REAL,
        PRIMARY KEY (event_id, t));
        CREATE INDEX ix_calendar_history_t ON calendar_history(t);
        INSERT INTO calendar_history(event_id, t, value_id, actual, updated_ts) VALUES (5, 100, 51, 1.0, 10),
            (5, 3700, 51, 2.0, 20), (6, 100, 61, 3.0, 5), (7, 100, NULL, 4.0, 5);""")
    c.commit()
    c.close()
    db = DB(path)
    got = [(r["value_id"], r["event_id"], r["t"], r["actual"])
           for r in db.query("SELECT * FROM calendar_history ORDER BY value_id")]
    assert got == [(51, 5, 3700, 2.0), (61, 6, 100, 3.0)]
    pk = {r["name"]: r["pk"] for r in db.query("PRAGMA table_info(calendar_history)")}
    assert pk["value_id"] == 1 and pk["event_id"] == 0
    ix = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='calendar_history'")}
    assert {"ix_calendar_history_t", "ix_calendar_history_ccy", "ix_calendar_history_event"} <= ix
    assert ch.migrate(db.conn) == []  # idempotent
    db.close()


def test_unmatched_reply_writes_rows_but_keeps_plan(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    line = ch.next_request(rt, NOW)
    pend = dict(rt.calhist_plan["pending"])
    stale = {"from_utc": 123, "to_utc": 456, "ok": True, "items": [item(9, ts(2026, 3, 3, 13, 30))]}
    ch.ingest_payload(rt, stale, NOW + 1)
    assert rt.calhist_plan["pending"] == pend and ch.covered(rt) == []
    assert len(rows(eng)) == 1
    ch.ingest_payload(rt, reply_for(line), NOW + 2)
    assert rt.calhist_plan["pending"] is None
    eng.db.close()


def test_clock_skew_removed_from_times_and_added_to_request(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    rt.bridge["clock_skew_s"] = 3600
    line = ch.next_request(rt, NOW)
    p = rt.calhist_plan["pending"]
    a, b, _ = parse(line)
    assert a == p["lo"] + 3600 - ch.EDGE_PAD_S and b == p["hi"] + 3600 + ch.EDGE_PAD_S
    t_real = ts(2026, 6, 5, 12, 30)
    ch.ingest_payload(rt, reply_for(line, [item(1, t_real + 3600)]), NOW)
    assert rows(eng)[0]["t"] == t_real
    eng.db.close()


def test_broker_dst_rule_applied_to_old_values(tmp_path):
    eng = make_engine(tmp_path, MT5_SERVER_TZ="NY")
    rt = eng.rt
    rt.bridge["srv_offset"] = 3 * 3600  # summer: GMT+3
    line = ch.next_request(rt, NOW)
    # a January release at 13:30 UTC = 15:30 broker time (GMT+2 then); the EA stamps it with today's +3 h -> 12:30
    t_jan = ts(2026, 1, 9, 13, 30)
    t_jul = ts(2026, 6, 5, 12, 30)
    ea_items = [item(1, t_jan - 3600), item(2, t_jul)]
    ch.ingest_payload(rt, reply_for(line, ea_items, srv_off=3 * 3600), NOW)
    got = {r["event_id"]: r["t"] for r in rows(eng)}
    assert got == {1: t_jan, 2: t_jul}
    eng.db.close()


def test_on_sync_ingests_and_requests(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    out = ch.on_sync(eng, {}, [], NOW)
    assert len(out) == 1
    payload = {"calendar_hist": reply_for(out[0], [item(1, ts(2026, 2, 6, 13, 30))])}
    assert ch.on_sync(eng, payload, [], NOW + 1) == []  # interval
    assert len(rows(eng)) == 1 and rt.calhist_plan["pending"] is None
    # garbage never raises
    assert ch.on_sync(eng, {"calendar_hist": "nonsense"}, [], NOW + 2) == []
    assert ch.on_sync(eng, {"calendar_hist": [None, 5]}, [], NOW + 3) == []
    eng.db.close()


def test_research_only_live_calendar_untouched(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    before = list(rt.calendar)
    line = ch.next_request(rt, NOW)
    ch.ingest_payload(rt, reply_for(line, [item(1, ts(2026, 6, 5, 12, 30))]), NOW)
    archive.drain()
    assert rt.calendar == before and rt.calendar_ts is None
    eng.db.close()
