"""Stage A review fixes: calendar clock skew, exact EA clock offset for expires_utc, MT5 account switch."""
import time

from app.bridge import normalize_clock, process_sync
from app.executor import OPEN_MAX_AGE_S
from tests.test_bridge import payload
from tests.test_engine import analysis_stub

RC_OK = {"passed": True, "checks": []}


def usd_event(t, imp=3):
    return {"t": t, "ccy": "USD", "imp": imp, "name": "Nonfarm Payrolls"}


# ---- calendar times get the same skew correction ------------------------------
def test_normalize_clock_shifts_calendar(engine):
    now = time.time()
    p = payload(now, time_utc=int(now) + 3600, bars=[{"symbol": "EURUSD", "tf": "M5", "t": int(now) + 3600}],
                calendar=[usd_event(int(now) + 4200), "junk", {"t": "x"}])
    assert normalize_clock(engine.rt, p, now) == 3600
    assert p["bars"][0]["t"] == int(now)
    assert p["calendar"][0]["t"] == int(now) + 600


def test_skewed_calendar_event_blocks_entries(engine):
    now = time.time()
    p = payload(now, time_utc=int(now) + 3600, calendar=[usd_event(int(now) + 600 + 3600)])
    p["symbols"][0]["time_msc"] = int((now + 3600) * 1000)
    process_sync(engine, p, now)
    assert engine.rt.bridge["clock_skew_s"] == 3600
    assert abs(engine.rt.calendar[0]["t"] - (now + 600)) <= 1
    block, _ = engine.news_check("EURUSD", now)
    assert block
    assert engine.news_event_near("EURUSD", now, window_s=900) is not None


# ---- expires_utc follows the measured EA clock offset ----------------------------
def _expiry(engine, now, time_utc):
    process_sync(engine, payload(now, ea_version="1.10", time_utc=time_utc), now)
    engine.rt.trading.update(mode="DEMO", execution_enabled=True)
    it = engine.executor.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.0790, 1.0820, engine.rt.quotes["EURUSD"], RC_OK)
    line = engine.executor.take_commands(now)[0].split("|")
    return int(line[11]) - int(engine.executor.get(it["id"])["queued_ts"] + OPEN_MAX_AGE_S)


def test_expiry_uses_sub_step_drift(engine):
    now = time.time()
    assert _expiry(engine, now, now + 30) == 30  # EA clock 30 s ahead: below the 900 s step


def test_expiry_ignores_truncation_noise(engine):
    now = time.time()
    assert _expiry(engine, now, now - 0.3) == 0


def test_expiry_step_plus_drift(engine):
    now = time.time()
    assert _expiry(engine, now, now + 3600 - 25) == 3575


# ---- MT5 account switch ----------------------------------------------------------
def test_demo_risk_check_blocked_while_account_settles(engine):
    now = time.time()
    process_sync(engine, payload(now), now)
    engine.rt.trading.update(mode="DEMO", execution_enabled=True)
    p = payload(now)
    p["account"]["login"] = 999
    process_sync(engine, p, now)
    rc = engine.risk_check("EURUSD", "BUY", 1.0801, 1.0795, 1.0815)
    failed = [c["name"] for c in rc["checks"] if not c["ok"]]
    assert rc["passed"] is False and "account_settled" in failed
    # after the settle window the check passes again
    engine.rt.bridge["account_changed_ts"] = time.time() - 16
    rc = engine.risk_check("EURUSD", "BUY", 1.0801, 1.0795, 1.0815)
    assert next(c for c in rc["checks"] if c["name"] == "account_settled")["ok"]


def test_paper_not_affected_by_account_settle(engine):
    engine.rt.bridge["account_changed_ts"] = time.time()
    rc = engine.risk_check("EURUSD", "BUY", 1.0801, 1.0795, 1.0815, mode="PAPER")
    assert all(c["name"] != "account_settled" for c in rc["checks"])


def test_signal_from_old_account_not_executed(engine):
    now = time.time()
    process_sync(engine, payload(now), now)
    engine.rt.trading.update(mode="DEMO", execution_enabled=True, armed_ts=now - 10)
    sig = engine.create_signal(analysis_stub())
    sig["created_ts"] = now - 1
    p = payload(now)
    p["account"]["login"] = 999
    process_sync(engine, p, now)
    assert engine.rt.trading["armed_ts"] > sig["created_ts"]
    engine.try_execute(sig)
    s = engine.get_signal(sig["id"])
    assert s["status"] == "NO_TRADE"
    assert not engine.executor.count_pending_open("DEMO", set())
