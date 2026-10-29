"""Calendar-history release times vs broker DST: MT5 stamps every calendar value with the server's CURRENT offset,
so ToUtc(value.time) is the real UTC in both seasons. The old _fix_time applied the broker DST rule (like for bars),
moving values on the other side of the last DST switch by one hour; repair_dst_times undoes that once."""
import json
import sqlite3
from datetime import datetime, timezone

from app import calendar_hist as ch
from app.db import DB
from tests.test_calendar_hist import item, make_engine, reply_for, rows, ts

NFP_SUMMER = ts(2025, 7, 3, 12, 30)   # 08:30 New York (EDT)
NFP_WINTER = ts(2025, 12, 5, 13, 30)  # 08:30 New York (EST)
CPI_MISMATCH = ts(2025, 3, 12, 12, 30)  # US already on DST, EU not yet
WINTER_NOW = ts(2026, 10, 29, 7)      # EET broker at GMT+2
SUMMER_NOW = ts(2026, 7, 1, 12)       # EET broker at GMT+3


def _old_fix(t_ea, now, rule="EET"):
    """What the old _fix_time stored (rule path, which ran whenever the rule matched today's offset)."""
    from app import archive
    off = archive.rule_offset_utc(rule, now)
    t_srv = t_ea + off
    return t_srv - archive.rule_offset_local(rule, t_srv)


def test_old_conversion_was_one_hour_early_in_summer():
    # the bug as found in the live DB: ingested in winter -> summer releases 1 h early, winter ones right
    assert _old_fix(NFP_SUMMER, WINTER_NOW) == NFP_SUMMER - 3600
    assert _old_fix(NFP_WINTER, WINTER_NOW) == NFP_WINTER
    # ... and ingested in summer it was the winter releases that moved (1 h late)
    assert _old_fix(NFP_WINTER, SUMMER_NOW) == NFP_WINTER + 3600
    assert _old_fix(NFP_SUMMER, SUMMER_NOW) == NFP_SUMMER


def _ingest(tmp_path, now, srv_off, skew=0):
    eng = make_engine(tmp_path, MT5_SERVER_TZ="EET")
    rt = eng.rt
    rt.bridge["srv_offset"] = srv_off
    rt.bridge["clock_skew_s"] = skew
    line = ch.next_request(rt, now)
    its = [item(1, NFP_SUMMER + skew), item(2, NFP_WINTER + skew), item(3, CPI_MISMATCH + skew)]
    ch.ingest_payload(rt, reply_for(line, its, srv_off=srv_off), now)
    got = {r["event_id"]: r["t"] for r in rows(eng)}
    eng.db.close()
    return got


def test_ingest_in_winter_keeps_both_seasons(tmp_path):
    assert _ingest(tmp_path, WINTER_NOW, 2 * 3600) == {1: NFP_SUMMER, 2: NFP_WINTER, 3: CPI_MISMATCH}


def test_ingest_in_summer_keeps_both_seasons(tmp_path):
    assert _ingest(tmp_path, SUMMER_NOW, 3 * 3600) == {1: NFP_SUMMER, 2: NFP_WINTER, 3: CPI_MISMATCH}


def test_ingest_removes_clock_skew(tmp_path):
    assert _ingest(tmp_path, WINTER_NOW, 2 * 3600 - 90, skew=90) == {1: NFP_SUMMER, 2: NFP_WINTER, 3: CPI_MISMATCH}


def test_request_window_not_shifted_by_dst_rule(tmp_path):
    eng = make_engine(tmp_path, MT5_SERVER_TZ="EET")
    rt = eng.rt
    rt.bridge["srv_offset"] = 2 * 3600
    a, b, _ = ch.next_request(rt, WINTER_NOW).split("|")[1:]
    p = rt.calhist_plan["pending"]
    assert int(a) == p["lo"] - ch.EDGE_PAD_S and int(b) == p["hi"] + ch.EDGE_PAD_S
    eng.db.close()


def _seed_old_rows(path, now, rule="EET"):
    """A DB written by the old code: times converted with the rule, flag absent, archive rule saved."""
    db = DB(path)
    db.execute("DELETE FROM settings WHERE key=?", (ch.DST_FIX_KEY,))
    db.set_setting("archive_server_tz", {"MetaQuotes-Demo": rule})
    for vid, t in ((11, NFP_SUMMER), (21, NFP_WINTER), (31, CPI_MISMATCH)):
        db.execute("INSERT INTO calendar_history(value_id, event_id, t, updated_ts) VALUES(?,?,?,?)",
                   (vid, vid // 10, _old_fix(t, now, rule), now))
    db.close()


def _times(path):
    c = sqlite3.connect(path)
    out = dict(c.execute("SELECT value_id, t FROM calendar_history").fetchall())
    flag = c.execute("SELECT value_json FROM settings WHERE key=?", (ch.DST_FIX_KEY,)).fetchone()
    c.close()
    return out, (json.loads(flag[0]) if flag else None)


EXPECTED = {11: NFP_SUMMER, 21: NFP_WINTER, 31: CPI_MISMATCH}


def test_repair_rows_ingested_in_winter_runs_once(tmp_path):
    path = tmp_path / "w.db"
    _seed_old_rows(path, WINTER_NOW)
    assert _times(path)[0][11] == NFP_SUMMER - 3600
    DB(path).close()  # migration on open (= next server start)
    got, flag = _times(path)
    assert got == EXPECTED and flag["moved"] == 1 and flag["rule"] == "EET"
    # idempotent: a second start moves nothing, rows written by the new code are never touched
    db = DB(path)
    db.execute("UPDATE calendar_history SET t=t+7 WHERE value_id=11")
    db.close()
    DB(path).close()
    got2, flag2 = _times(path)
    assert got2 == {**EXPECTED, 11: NFP_SUMMER + 7} and flag2 == flag
    c = sqlite3.connect(path, isolation_level=None)
    assert ch.repair_dst_times(c) == 0
    c.close()


def test_repair_rows_ingested_in_summer(tmp_path):
    path = tmp_path / "s.db"
    _seed_old_rows(path, SUMMER_NOW)
    assert _times(path)[0][21] == NFP_WINTER + 3600
    DB(path).close()
    got, flag = _times(path)
    assert got == EXPECTED and flag["moved"] == 2  # winter NFP + March CPI (EU still on winter time)


def test_repair_ny_rule(tmp_path):
    path = tmp_path / "ny.db"
    _seed_old_rows(path, WINTER_NOW, rule="NY")  # 29 Oct 2026: NY still on EDT -> GMT+3
    DB(path).close()
    assert _times(path)[0] == EXPECTED


def test_fresh_db_sets_flag_without_rule(tmp_path):
    path = tmp_path / "fresh.db"
    DB(path).close()
    got, flag = _times(path)
    assert got == {} and flag["rule"] is None and flag["moved"] == 0
