"""Stage A protocol: account binding, OPEN ref price/expiry, retry window, calendar, EA instances."""
import time

from app.bridge import note_auth_failure, process_sync
from app.config import iso
from app.executor import OPEN_MAX_AGE_S, ea_version_tuple
from tests.test_bridge import payload

RC_OK = {"passed": True, "checks": []}


def logs(engine, needle: str) -> list[str]:
    rows = engine.db.query("SELECT message FROM system_events ORDER BY id")
    return [r["message"] for r in rows if needle in r["message"]]


def demo(engine, now, **kw):
    process_sync(engine, payload(now, **kw), now)
    engine.rt.trading.update(mode="DEMO", execution_enabled=True)


def with_login(now, login, **kw):
    p = payload(now, **kw)
    p["account"]["login"] = login
    return p


# ---- account binding ------------------------------------------------------
def test_intents_bound_to_login(engine):
    now = time.time()
    demo(engine, now)
    ex = engine.executor
    it = ex.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"], RC_OK)
    m = ex.submit_modify("MT5", 55, "EURUSD", "BUY", 1.0795, 1.082)
    c = ex.submit_close("MT5", 56, "EURUSD", "BUY", 0.01, None)
    for x in (it, m, c):
        assert ex.get(x["id"])["account_login"] == 12345678
    p = ex.submit_open("PAPER", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"], RC_OK)
    assert p.get("account_login") is None


def test_login_change_fails_pending_and_blocks_delivery(engine):
    now = time.time()
    demo(engine, now)
    ex = engine.executor
    q = ex.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"], RC_OK)
    d = ex.submit_open("DEMO", "EURUSD", "SELL", 0.01, 1.082, 1.079, engine.rt.quotes["EURUSD"], RC_OK)
    ex.take_commands(now)  # both delivered to the old account
    q2 = ex.submit_modify("MT5", 77, "EURUSD", "BUY", 1.0795, 1.082)
    engine.known_mt5_positions[999] = {"ticket": 999}
    engine.pm.last_action[999] = now
    txt = process_sync(engine, with_login(now + 1, 87654321), now + 1)
    assert "OPEN|" not in txt and "MODIFY|" not in txt
    for x in (q, d, q2):
        got = ex.get(x["id"])
        assert got["state"] == "FAILED" and got["result"]["retcode_desc"] == "ACCOUNT_CHANGED"
    assert engine.rt.bridge.get("account_changed_ts") == now + 1
    assert 999 not in engine.known_mt5_positions and not engine.pm.last_action
    # new intents for the new login are delivered normally
    n = ex.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"], RC_OK)
    assert n["account_login"] == 87654321
    txt = process_sync(engine, with_login(now + 2, 87654321), now + 2)
    assert f"OPEN|{n['id']}|" in txt


def test_take_commands_never_delivers_to_other_login(engine):
    now = time.time()
    demo(engine, now)
    ex = engine.executor
    it = ex.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"], RC_OK)
    engine.rt.mt5_account = dict(engine.rt.mt5_account, login=111)  # switched without a sync in between
    assert ex.take_commands(now) == []
    got = ex.get(it["id"])
    assert got["state"] == "FAILED" and got["result"]["retcode_desc"] == "ACCOUNT_CHANGED"


def test_reconcile_ignores_positions_of_other_login(engine):
    now = time.time()
    demo(engine, now)
    ex = engine.executor
    it = ex.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"], RC_OK)
    ex.take_commands(now)
    ex.check_timeouts(now + 31)
    assert ex.get(it["id"])["state"] == "UNKNOWN"
    engine.rt.mt5_account = dict(engine.rt.mt5_account, login=222)
    ex.reconcile([{"ticket": 5, "symbol": "EURUSD", "comment": f"ai:{it['id'][:8]}", "magic": 552211,
                   "price_open": 1.08, "volume": 0.01, "time": int(now)}], [], now + 32, magic=552211)
    got = ex.get(it["id"])
    assert got["state"] == "FAILED" and got["result"]["retcode_desc"] == "ACCOUNT_CHANGED" and got["ticket"] is None


# ---- OPEN line: ref price / max slip / expiry ------------------------------
def test_open_line_v100_has_9_fields(engine):
    now = time.time()
    demo(engine, now)
    it = engine.executor.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"], RC_OK)
    txt = process_sync(engine, payload(now + 1), now + 1)
    line = next(l for l in txt.splitlines() if l.startswith("OPEN|"))
    assert line == f"OPEN|{it['id']}|EURUSD|BUY|0.01|1.079|1.082|20|ai:{it['id'][:8]}"


def test_open_line_v110_has_ref_price_slip_expiry(engine):
    now = time.time()
    demo(engine, now, ea_version="1.10")
    q = engine.rt.quotes["EURUSD"]  # bid 1.08, ask 1.0801
    it = engine.executor.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, q, RC_OK)
    s = engine.executor.submit_open("DEMO", "EURUSD", "SELL", 0.01, 1.0806, 1.079, q, RC_OK)
    txt = process_sync(engine, payload(now + 1, ea_version="1.10"), now + 1)
    lines = {l.split("|")[1]: l.split("|") for l in txt.splitlines() if l.startswith("OPEN|")}
    b = lines[it["id"]]
    assert len(b) == 12
    assert b[8] == f"ai:{it['id'][:8]}"  # v1.00 layout preserved in the first 9 fields
    assert b[9] == "1.0801"
    assert b[10] == str(max(5, round(0.25 * 110)))  # SL distance 110 points from the ask -> 28
    queued = engine.executor.get(it["id"])["queued_ts"]
    assert int(b[11]) == int(queued + OPEN_MAX_AGE_S)
    sl = lines[s["id"]]
    assert sl[9] == "1.08" and sl[10] == "15"  # 60 points from the bid -> 15


def test_open_min_slip_and_skew_shifted_expiry(engine):
    now = time.time()
    demo(engine, now, ea_version="1.10")
    engine.rt.bridge["ea_gmt_skew_s"] = 3600  # EA's TimeGMT() one hour ahead
    it = engine.executor.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.0800, 1.082, engine.rt.quotes["EURUSD"], RC_OK)
    line = engine.executor.take_commands(now)[0].split("|")
    assert line[10] == "5"  # 10 points SL -> 2.5 -> floor at 5
    assert int(line[11]) == int(engine.executor.get(it["id"])["queued_ts"] + OPEN_MAX_AGE_S) + 3600


def test_ea_gmt_skew_measured_from_time_utc(engine):
    now = time.time()
    p = payload(now)
    p["time_utc"] = int(now) + 3600
    process_sync(engine, p, now)
    assert engine.rt.bridge["ea_gmt_skew_s"] == 3600


def test_open_expired_before_delivery(engine):
    now = time.time()
    demo(engine, now)
    ex = engine.executor
    it = ex.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"], RC_OK)
    m = ex.submit_modify("MT5", 77, "EURUSD", "BUY", 1.0795, 1.082)
    lines = ex.take_commands(now + OPEN_MAX_AGE_S + 1)
    assert [l.split("|")[0] for l in lines] == ["MODIFY"]  # MODIFY/CLOSE keep the 30 s queue timeout
    got = ex.get(it["id"])
    assert got["state"] == "FAILED" and got["result"]["retcode_desc"] == "EXPIRED_BEFORE_DELIVERY"
    assert ex.get(m["id"])["state"] == "DELIVERED"


def test_ea_result_price_changed_fails_intent(engine):
    now = time.time()
    demo(engine, now, ea_version="1.10")
    it = engine.executor.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"], RC_OK)
    process_sync(engine, payload(now + 1, ea_version="1.10"), now + 1)
    res = {"cmd_id": it["id"], "ok": False, "retcode": 10020, "retcode_desc": "PRICE_CHANGED (ref drift)", "order": 0,
           "deal": 0, "position": 0, "price": 1.0811, "volume": 0.01, "comment": "Pretul s-a mutat 10 puncte"}
    process_sync(engine, payload(now + 2, ea_version="1.10", results=[res]), now + 2)
    got = engine.executor.get(it["id"])
    assert got["state"] == "FAILED" and got["result"]["retcode_desc"] == "PRICE_CHANGED"


def test_version_tuple():
    assert ea_version_tuple("1.10") >= (1, 10) > ea_version_tuple("1.00")
    assert ea_version_tuple("") == () and ea_version_tuple(None) == ()
    assert ea_version_tuple("1.9") < (1, 10)


# ---- retry window ---------------------------------------------------------
def test_consecutive_failures_only_last_hour(engine):
    ex = engine.executor
    now = time.time()
    demo(engine, now)
    ids = []
    for _ in range(6):
        ids.append(ex.submit_modify("MT5", 77, "EURUSD", "BUY", 1.0801, 1.082)["id"])
        ex.fail_queued("test")
    assert ex.consecutive_failures(77, "MODIFY")[0] == 6
    old = iso(now - 2 * 3600)
    engine.db.execute(f"UPDATE intents SET updated_at_utc=? WHERE id IN ({','.join('?' * 4)})", (old, *ids[:4]))
    n, last = ex.consecutive_failures(77, "MODIFY")
    assert n == 2 and last is not None and now - last < 60
    a = {"type": "MODIFY", "reason": "BREAK_EVEN"}
    assert engine.pm._backing_off(77, a, now + 61) is False  # 2 recent failures -> 60 s backoff only
    # everything older than an hour -> retries resume
    assert ex.consecutive_failures(77, "MODIFY", now=now + 3700)[0] == 0


# ---- calendar -------------------------------------------------------------
def test_calendar_ingestion(engine):
    now = time.time()
    cal = [
        {"t": int(now) + 7200, "ccy": "usd", "imp": 3, "name": "FOMC \"Rate\" Decision"},
        {"t": int(now) + 600, "ccy": "EUR", "imp": 2, "name": "PMI"},
        {"t": "bad", "ccy": "EUR", "imp": 2, "name": "x"},
        {"t": int(now), "ccy": "", "imp": 3, "name": "no ccy"},
        "junk",
    ]
    process_sync(engine, payload(now, ea_version="1.10", calendar=cal, calendar_ok=True), now)
    rt = engine.rt
    assert rt.calendar == [
        {"t": int(now) + 600, "ccy": "EUR", "imp": 2, "name": "PMI"},
        {"t": int(now) + 7200, "ccy": "USD", "imp": 3, "name": "FOMC \"Rate\" Decision"},
    ]
    assert rt.calendar_ts == now and rt.bridge["calendar_ok"] is True
    # syncs without the key change nothing
    process_sync(engine, payload(now + 1, ea_version="1.10"), now + 1)
    assert len(rt.calendar) == 2 and rt.calendar_ts == now
    # failure keeps the previous list, flags it
    process_sync(engine, payload(now + 2, ea_version="1.10", calendar=[], calendar_ok=False), now + 2)
    assert len(rt.calendar) == 2 and rt.calendar_ts == now and rt.bridge["calendar_ok"] is False
    assert logs(engine, "Calendarul economic MT5 nu este disponibil")
    # empty but OK list replaces it
    process_sync(engine, payload(now + 3, ea_version="1.10", calendar=[], calendar_ok=True), now + 3)
    assert rt.calendar == [] and rt.calendar_ts == now + 3 and rt.bridge["calendar_ok"] is True


def test_calendar_request_line_only_for_v110(engine):
    now = time.time()
    txt = process_sync(engine, payload(now), now)
    assert "CALENDAR" not in txt.splitlines()
    txt = process_sync(engine, payload(now + 1, ea_version="1.10"), now + 1)
    assert "CALENDAR" in txt.splitlines()
    txt = process_sync(engine, payload(now + 2, ea_version="1.10"), now + 2)
    assert "CALENDAR" not in txt.splitlines()  # throttled
    process_sync(engine, payload(now + 3, ea_version="1.10", calendar=[], calendar_ok=True), now + 3)
    txt = process_sync(engine, payload(now + 70, ea_version="1.10"), now + 70)
    assert "CALENDAR" not in txt.splitlines()  # fresh calendar
    txt = process_sync(engine, payload(now + 1300, ea_version="1.10"), now + 1300)
    assert "CALENDAR" in txt.splitlines()  # stale


# ---- EA instances / auth failures -----------------------------------------
def test_multiple_instances_warning_throttled(engine):
    now = time.time()
    process_sync(engine, payload(now, instance_id="a"), now)
    process_sync(engine, payload(now + 1, instance_id="a"), now + 1)
    assert not logs(engine, "Mai multe instanțe AIBridgeEA active")
    process_sync(engine, payload(now + 2, instance_id="b"), now + 2)
    process_sync(engine, payload(now + 3, instance_id="a"), now + 3)
    assert len(logs(engine, "Mai multe instanțe AIBridgeEA active")) == 1
    # "a" alone for > 30 s: no longer counted as two
    process_sync(engine, payload(now + 40, instance_id="a"), now + 40)
    assert engine.rt.bridge["instances_active"] == 1
    process_sync(engine, payload(now + 400, instance_id="c"), now + 400)
    assert engine.rt.bridge["instances_active"] == 1
    process_sync(engine, payload(now + 401, instance_id="a"), now + 401)
    assert len(logs(engine, "Mai multe instanțe AIBridgeEA active")) == 2


def test_auth_failures_logged_once_per_minute(client, engine):
    for _ in range(3):
        assert client.post("/bridge/v1/sync", json={}, headers={"X-Bridge-Key": "wrong"}).status_code == 401
    msgs = logs(engine, "Cereri EA respinse (cheie greșită)")
    assert msgs == ["Cereri EA respinse (cheie greșită): 1 în ultimul minut"]
    note_auth_failure(engine.rt, time.time() + 61)
    msgs = logs(engine, "Cereri EA respinse (cheie greșită)")
    assert msgs[-1] == "Cereri EA respinse (cheie greșită): 3 în ultimul minut"
