"""CALHIST wiring in bridge.process_sync (EA >= 1.14): version gating, line emission, ingestion of "calendar_hist"."""
import time

from app import archive, bridge, calendar_hist as ch
from app.config import TF_SECONDS
from app.db import DB
from app.engine import Engine
from tests.conftest import EURUSD_INFO, make_bars, make_settings

LONG_MIN = -(2 ** 63)


def make_engine(tmp_path, **kw):
    kw.setdefault("ACTIVE_SYMBOLS", "EURUSD")
    kw.setdefault("ARCHIVE_ENABLED", False)  # no HISTX in the way unless a test turns it on
    s = make_settings(tmp_path, **kw)
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    return eng


def payload(eng, now, ea="1.14", **kw):
    sym = dict(EURUSD_INFO, symbol="EURUSD", bid=1.08, ask=1.0801, time_msc=int(now * 1000), spread_points=10)
    p = {
        "ea_version": ea, "magic": int(eng.settings.MT5_MAGIC), "time_utc": int(now),
        "terminal": {"connected": True, "trade_allowed": True, "build": 4000},
        "account": {"login": 12345678, "server": "Broker-Demo", "company": "Broker", "trade_mode": 0,
                    "currency": "USD", "balance": 10000.0, "equity": 10000.0, "margin": 0.0, "margin_free": 10000.0,
                    "leverage": 100, "profit": 0.0, "trade_allowed": True, "trade_expert": True, "margin_mode": 2},
        "symbols": [sym], "bars": [], "positions": [], "deals": [], "results": [],
    }
    p.update(kw)
    return p


def settle_history(eng, now):
    """Every active pair has a full, fresh series: no HIST refill pending (that would hold CALHIST back)."""
    rt = eng.rt
    for s in rt.active_symbols():
        for tf in rt.needed_tfs():
            sec = TF_SECONDS[tf]
            rt.ingest_history(s, tf, [dict(b, symbol=s, tf=tf) for b in
                                      make_bars([1.08] * 150, tf_sec=sec, end_t=int(now) // sec * sec - sec)])
            rt.hist_requested[(s, tf)] = now
    rt.hist_force.clear()


def sync(eng, now, **kw):
    return bridge.process_sync(eng, payload(eng, now, **kw), now)


def calhist_lines(text):
    return [x for x in text.splitlines() if x.startswith("CALHIST|")]


def warm(eng, now, ea="1.14"):
    """First sync (connect: refills every pair), then the history is in place."""
    sync(eng, now, ea=ea)
    settle_history(eng, now)


def test_old_ea_never_gets_calhist(tmp_path):
    eng = make_engine(tmp_path)
    now = time.time()
    for ea in ("1.13", "1.12", "1.00", "", "abc"):
        warm(eng, now, ea=ea)
        for k in range(3):
            assert calhist_lines(sync(eng, now + 1 + k, ea=ea)) == []
    assert ch.plan_of(eng.rt)["pending"] is None
    assert ch.ea_supported("1.13") is False and ch.ea_supported("1.14") is True


def test_v114_gets_one_calhist_line_then_waits(tmp_path):
    eng = make_engine(tmp_path)
    now = time.time()
    warm(eng, now)
    text = sync(eng, now + 1)
    lines = calhist_lines(text)
    assert len(lines) == 1
    tag, a, b, imp = lines[0].split("|")
    assert int(a) < int(b) and imp == "1"
    assert text.splitlines()[-1] == lines[0]  # appended after everything else
    pend = ch.plan_of(eng.rt)["pending"]
    assert pend is not None and (pend["ea_from"], pend["ea_to"]) == (int(a), int(b))
    # at most one in flight: no new line until the reply (or the timeout)
    for k in range(2, 6):
        assert calhist_lines(sync(eng, now + k)) == []


def test_no_calhist_while_hist_refill_pending(tmp_path):
    eng = make_engine(tmp_path)
    now = time.time()
    # first sync: every pair is forced into a HIST refill -> no CALHIST in that response
    text = sync(eng, now)
    assert any(x.startswith("HIST|") for x in text.splitlines())
    assert calhist_lines(text) == []
    assert ch.plan_of(eng.rt)["pending"] is None


def test_no_calhist_next_to_histx(tmp_path):
    eng = make_engine(tmp_path, ARCHIVE_ENABLED=True)
    now = time.time()
    warm(eng, now)
    seen_histx = False
    for k in range(1, 6):
        text = sync(eng, now + k)
        kinds = {x.split("|", 1)[0] for x in text.splitlines()}
        seen_histx = seen_histx or "HISTX" in kinds
        assert not ({"HISTX", "CALHIST"} <= kinds)
        if "HISTX" in kinds or (eng.rt.archive_plan or {}).get("pending") is not None:
            assert "CALHIST" not in kinds
    assert seen_histx  # the archive backfill really was in flight: CALHIST waited for it


def test_reply_ingested_through_process_sync(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    now = time.time()
    warm(eng, now)
    line = calhist_lines(sync(eng, now + 1))[0]
    _, a, b, _ = line.split("|")
    pend = ch.plan_of(rt)["pending"]
    t1 = max(pend["lo"], int(now) - 10 * 86400)
    items = [
        {"id": 11, "eid": 1001, "t": t1, "ccy": "USD", "cc": "US", "imp": 3, "name": 'Nonfarm "NFP" Payrolls',
         "tm": 0, "unit": 4, "mult": 1, "dig": 0, "a": 250000000, "f": 180000000, "p": None, "r": LONG_MIN,
         "imx": 1, "rev": 0, "per": t1},
        {"id": 12, "eid": 1002, "t": t1 + 3600, "ccy": "EUR", "cc": "EU", "imp": 2, "name": "CPI y/y",
         "tm": 0, "unit": 1, "mult": 0, "dig": 1, "a": 2400000, "f": 2500000, "p": 2600000, "r": None,
         "imx": 2, "rev": 1, "per": t1},
    ]
    reply = {"from_utc": int(a), "to_utc": int(b), "srv_off": None, "ok": True, "err": 0, "total": 2,
             "truncated": False, "items": items}
    text = bridge.process_sync(eng, payload(eng, now + 2, calendar_hist=reply), now + 2)
    archive.drain()
    got = rt.db.query("SELECT * FROM calendar_history ORDER BY t")
    assert [r["event_id"] for r in got] == [1001, 1002]
    nfp = got[0]
    assert nfp["actual"] == 250.0 and nfp["forecast"] == 180.0
    assert nfp["previous"] is None and nfp["revised_previous"] is None  # null / LONG_MIN -> NULL
    assert nfp["name"] == 'Nonfarm "NFP" Payrolls' and nfp["ccy"] == "USD" and nfp["unit_name"] == "JOB"
    assert got[1]["actual"] == 2.4
    plan = ch.plan_of(rt)
    assert plan["pending"] is None and plan["last_reply_ts"] == now + 2
    assert rt.db.query("SELECT COUNT(*) AS n FROM calendar_history_windows")[0]["n"] == 1
    # the reply never disturbs the rest of the sync
    assert text.startswith("OK|") and "EURUSD" in rt.quotes


def test_late_or_resent_reply_keeps_rows_but_not_plan(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    now = time.time()
    warm(eng, now)
    calhist_lines(sync(eng, now + 1))
    pend = dict(ch.plan_of(rt)["pending"])
    t1 = max(pend["lo"], int(now) - 5 * 86400)
    stale = {"from_utc": 1, "to_utc": 2, "srv_off": None, "ok": True, "err": 0, "total": 1, "truncated": False,
             "items": [{"id": 1, "eid": 77, "t": t1, "ccy": "GBP", "cc": "GB", "imp": 1, "name": "x",
                        "a": 1000000, "f": None, "p": None, "r": None}]}
    bridge.process_sync(eng, payload(eng, now + 2, calendar_hist=stale), now + 2)
    archive.drain()
    assert [r["event_id"] for r in rt.db.query("SELECT event_id FROM calendar_history")] == [77]
    assert ch.plan_of(rt)["pending"] is not None  # still waiting for the real reply


def test_error_reply_backs_off(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    now = time.time()
    warm(eng, now)
    _, a, b, _ = calhist_lines(sync(eng, now + 1))[0].split("|")
    err = {"from_utc": int(a), "to_utc": int(b), "srv_off": None, "ok": False, "err": 5400, "total": 0,
           "truncated": False, "items": []}
    text = bridge.process_sync(eng, payload(eng, now + 2, calendar_hist=err), now + 2)
    assert calhist_lines(text) == []
    plan = ch.plan_of(rt)
    assert plan["pending"] is None and plan["errors"] == 1 and plan["retry_at"] >= now + 2 + ch.ERR_BACKOFF_S


def test_calendar_hist_times_not_touched_by_normalize_clock(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    now = time.time()
    p = payload(eng, now, calendar_hist={"from_utc": 5, "to_utc": 6, "items": [{"t": 1_700_000_000}]})
    p["time_utc"] = int(now) + 3600  # EA TimeGMT() off by one hour
    bridge.normalize_clock(rt, p, now)
    assert rt.bridge["clock_skew_s"] == 3600
    assert p["calendar_hist"]["items"][0]["t"] == 1_700_000_000  # calendar_hist removes the skew itself


def test_on_sync_failure_never_breaks_sync(tmp_path, monkeypatch):
    eng = make_engine(tmp_path)
    now = time.time()
    warm(eng, now)

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(ch, "next_request", boom)
    text = sync(eng, now + 1, calendar_hist={"garbage": True})
    assert text.startswith("OK|") and calhist_lines(text) == []
