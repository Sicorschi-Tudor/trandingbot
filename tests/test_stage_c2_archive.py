"""Stage C2 archive: extended higher-TF targets (ARCHIVE_EXTRA), ordering, throttling, exhaustion, status, D1 DST."""
import time
from datetime import datetime, timezone

from app import archive
from app.backtest import load_bars
from app.config import TF_SECONDS
from app.db import DB
from app.engine import Engine
from tests.conftest import make_settings

DAY = 86400


def ts(*a) -> int:
    return int(datetime(*a, tzinfo=timezone.utc).timestamp())


def make_engine(tmp_path, **kw):
    kw.setdefault("ACTIVE_SYMBOLS", "EURUSD,GBPUSD")
    kw.setdefault("ARCHIVE_EXTRA_INTERVAL_S", 20)  # planner timings below use a 21 s step
    s = make_settings(tmp_path, **kw)
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    rt = eng.rt
    rt.bridge.update({"ea_version": "1.12", "clock_skew_s": 0, "srv_offset": None})
    return eng


def connect(rt):
    rt.bridge["last_sync_ts"] = time.time()


def parse(line):
    _, sym, tf, end, count = line.split("|")
    return sym, tf, int(end), int(count)


def fake_bars(end, n, sec):
    return [{"t": end - (n - i) * sec, "o": 1.1, "h": 1.2, "l": 1.0, "c": 1.15, "tv": 1, "sp": 5} for i in range(n)]


def answer(rt, line, now, bars=None):
    """EA reply to a HISTX line: `count` bars strictly older than end_utc (or the given bars)."""
    sym, tf, end, count = parse(line)
    if bars is None:
        bars = fake_bars(end, count, TF_SECONDS[tf])
    archive.ingest_payload(rt, [{"symbol": sym, "tf": tf, "end_utc": end, "bars": bars}], now)


# ---- targets -----------------------------------------------------------------
def test_extra_targets_parsing_and_days_per_tf(tmp_path):
    eng = make_engine(tmp_path, ARCHIVE_EXTRA="H1:1095,H4:1825,D1:3650")
    rt = eng.rt
    assert archive.extra_targets(rt.settings) == {"H1": 1095, "H4": 1825, "D1": 3650}
    assert archive.extra_order(rt.settings) == ["D1", "H4", "H1"]
    # base set [M5, H1, M15] at ARCHIVE_DAYS, extended TFs at max(base, extra)
    assert archive.days_target(rt, "M5") == 120
    assert archive.days_target(rt, "M15") == 120
    assert archive.days_target(rt, "H1") == 1095
    assert archive.days_target(rt, "H4") == 1825
    assert archive.days_target(rt, "D1") == 3650
    now = time.time()
    assert archive.target_ts(rt, now) == int(now - 120 * DAY)  # base target unchanged
    assert archive.target_ts(rt, now, "D1") == int(now - 3650 * DAY)
    rt.settings.ARCHIVE_DAYS = 2000
    assert archive.days_target(rt, "H1") == 2000 and archive.days_target(rt, "H4") == 1825
    eng.db.close()


def test_extra_targets_bad_items_and_off(tmp_path):
    s = make_settings(tmp_path, ARCHIVE_EXTRA="h4:10, X9:5, D1:abc, M30:0;D1:30, H4:20")
    assert archive.extra_targets(s) == {"H4": 20, "D1": 30}
    for off in ("off", "none", "0", "false"):
        assert archive.extra_targets(make_settings(tmp_path, ARCHIVE_EXTRA=off)) == {}


# ---- ordering / throttling -----------------------------------------------------
def run_planner(rt, now, steps=200, step_s=21.0):
    seq = []
    for _ in range(steps):
        connect(rt)
        line = archive.next_request(rt, now)
        if line is None:
            break
        pend = rt.archive_plan["pending"]
        seq.append((parse(line), pend["extended"]))
        answer(rt, line, now)
        now += step_s
    archive.drain()
    return seq, now


def test_base_first_then_d1_h4_h1(tmp_path):
    eng = make_engine(tmp_path, ARCHIVE_DAYS=1, ARCHIVE_EXTRA="H1:3,H4:10,D1:30")
    rt = eng.rt
    seq, now = run_planner(rt, time.time())
    pairs = [(p[0], p[1]) for p, _ in seq]
    ext = [x for _, x in seq]
    n_base = ext.index(True)
    assert all(ext[n_base:]) and not any(ext[:n_base])  # base strictly before extended
    assert {pairs[i] for i in range(n_base)} == {(s, tf) for s in ("EURUSD", "GBPUSD") for tf in ("M5", "H1", "M15")}
    # base phase: symbol by symbol, [signal_tf, htf_tf, M15]
    assert pairs[:3] == [("EURUSD", "M5"), ("EURUSD", "H1"), ("EURUSD", "M15")]
    # extended: D1 for all symbols, then H4, then H1 (cheapest first)
    assert [p for p in pairs[n_base:]] == [("EURUSD", "D1"), ("GBPUSD", "D1"), ("EURUSD", "H4"), ("GBPUSD", "H4"),
                                            ("EURUSD", "H1"), ("GBPUSD", "H1")]
    for (sym, tf, end, count), x in seq:
        if x:
            assert count <= archive.EXTRA_MAX_BATCH
    st = rt.archive_status
    for sym in ("EURUSD", "GBPUSD"):
        for tf, days in (("M5", 1), ("M15", 1), ("H1", 3), ("H4", 10), ("D1", 30)):
            e = st[sym][tf]
            assert e["done"] is True and e["oldest_ts"] <= e["target_ts"], (sym, tf)
            assert abs(e["target_ts"] - (now - days * DAY)) < 400 * 21
    assert archive.next_request(rt, now + 100) is None  # everything reached
    eng.db.close()


def test_extended_batch_is_capped(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD", ARCHIVE_DAYS=1, ARCHIVE_EXTRA="H1:3650")
    rt = eng.rt
    now = time.time()
    seq, _ = run_planner(rt, now, steps=6)
    ext = [(p, x) for p, x in seq if x]
    assert ext and ext[0][0][1] == "H1" and ext[0][0][3] == 3000
    rt.settings.ARCHIVE_BATCH_BARS = 500
    rt.archive_plan["extra_last_ts"] = None
    rt.archive_plan["pending"] = None
    connect(rt)
    assert parse(archive.next_request(rt, now + 10_000))[3] == 500
    eng.db.close()


def test_extended_interval_throttle_and_trading_priority(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD", ARCHIVE_DAYS=1, ARCHIVE_EXTRA="D1:400,H4:400",
                      ARCHIVE_BATCH_BARS=100, ARCHIVE_EXTRA_INTERVAL_S=20)
    rt = eng.rt
    now = time.time()
    # finish the base set (no throttle between base requests: one after another)
    while True:
        connect(rt)
        line = archive.next_request(rt, now)
        assert line is not None
        if rt.archive_plan["pending"]["extended"]:
            break
        answer(rt, line, now)
        now += 1
    t0 = now
    assert parse(line)[1] == "D1" and rt.archive_plan["extra_last_ts"] == t0
    answer(rt, line, t0)
    connect(rt)
    assert archive.next_request(rt, t0 + 1) is None    # answered, but the interval has not passed
    assert archive.next_request(rt, t0 + 19.9) is None
    # trading commands / live refills pending: nothing, even after the interval
    assert archive.next_request(rt, t0 + 25, commands_pending=True) is None
    assert archive.next_request(rt, t0 + 25, hist_pending=True) is None
    assert rt.archive_plan["extra_last_ts"] == t0
    line2 = archive.next_request(rt, t0 + 20)
    assert line2 is not None and parse(line2)[1] == "D1" and rt.archive_plan["extra_last_ts"] == t0 + 20
    # interval 0: no throttle
    answer(rt, line2, t0 + 20)
    rt.settings.ARCHIVE_EXTRA_INTERVAL_S = 0
    assert archive.next_request(rt, t0 + 20.5) is not None
    eng.db.close()


def test_base_has_priority_over_extended(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD", ARCHIVE_DAYS=1, ARCHIVE_EXTRA="D1:30")
    rt = eng.rt
    now = time.time()
    seq, now = run_planner(rt, now, steps=4)
    assert [p[1] for p, _ in seq] == ["M5", "H1", "M15", "D1"]
    # a new base TF appears (signal TF changed): it goes before the remaining extended work
    rt.strategy["signal_timeframe"] = "M1"
    connect(rt)
    rt.archive_plan["pending"] = None
    line = archive.next_request(rt, now + 100)
    assert parse(line)[1] == "M1" and rt.archive_plan["pending"]["extended"] is False
    eng.db.close()


def test_base_pair_in_backoff_does_not_block_extended(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD", ARCHIVE_DAYS=1, ARCHIVE_EXTRA="D1:30")
    rt = eng.rt
    now = time.time()
    connect(rt)
    line = archive.next_request(rt, now)
    sym, tf, end, _ = parse(line)
    archive.ingest_payload(rt, [{"symbol": sym, "tf": tf, "end_utc": end, "bars": [], "err": 4401}], now)
    seq, _ = run_planner(rt, now + 1, steps=3, step_s=1.0)  # M5 still in its 30 s backoff
    assert [p[1] for p, _ in seq] == ["H1", "M15", "D1"]
    eng.db.close()


# ---- exhaustion ------------------------------------------------------------------
def test_extended_exhaustion_records_history_start(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD", ARCHIVE_DAYS=1, ARCHIVE_EXTRA="D1:3650,H4:100",
                      ARCHIVE_EXTRA_INTERVAL_S=0)
    rt = eng.rt
    now = time.time()
    _, now = run_planner(rt, now, steps=3)  # base (planner clock moves on: never ask with an older now)
    connect(rt)
    line = archive.next_request(rt, now + 10)
    sym, tf, end, count = parse(line)
    assert tf == "D1" and count == 3000
    bars = fake_bars(end, 500, DAY)  # broker only has 500 days
    answer(rt, line, now + 10, bars=bars)
    e = rt.archive_status["EURUSD"]["D1"]
    assert e["done"] is False and e["oldest_ts"] == bars[0]["t"]
    line = archive.next_request(rt, now + 11)
    assert parse(line)[:3] == ("EURUSD", "D1", bars[0]["t"])
    answer(rt, line, now + 11, bars=[])  # nothing older: history exhausted
    archive.drain()
    assert e["done"] is True and ("EURUSD", "D1") in rt.archive_plan["exhausted"]
    assert rt.archive_plan["history_start"][("EURUSD", "D1")] == bars[0]["t"]
    view = archive.status_view(rt, now + 12)
    d1 = view["symbols"]["EURUSD"]["D1"]
    assert d1["exhausted"] is True and d1["history_start_ts"] == bars[0]["t"] and d1["days_target"] == 3650
    assert 499 <= d1["days_loaded"] <= 502 and d1["oldest_ts"] == bars[0]["t"]
    # next extended TF
    assert parse(archive.next_request(rt, now + 12))[1] == "H4"
    eng.db.close()


def test_base_exhaustion_skips_extended_walk(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD", ARCHIVE_DAYS=1, ARCHIVE_EXTRA="H1:30",
                      ARCHIVE_EXTRA_INTERVAL_S=0)
    rt = eng.rt
    now = time.time()
    connect(rt)
    answer(rt, archive.next_request(rt, now), now)  # M5
    line = archive.next_request(rt, now + 1)
    assert parse(line)[1] == "H1"
    answer(rt, line, now + 1, bars=[])  # broker has no H1 at all
    assert rt.archive_status["EURUSD"]["H1"]["done"] is True
    answer(rt, archive.next_request(rt, now + 2), now + 2)  # M15
    assert archive.next_request(rt, now + 3) is None  # H1 extended walk skipped: history exhausted
    assert rt.archive_plan["history_start"][("EURUSD", "H1")] is None
    eng.db.close()


# ---- status ----------------------------------------------------------------------
def test_status_view_shape(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD", ARCHIVE_DAYS=1, ARCHIVE_EXTRA="H1:3,H4:10,D1:30")
    rt = eng.rt
    now = time.time()
    v = archive.status_view(rt, now)
    assert v["phase"] == "base" and v["days_target"] == 1 and v["extra"] == {"H1": 3, "H4": 10, "D1": 30}
    assert v["extra_interval_s"] == 20 and v["enabled"] is True and v["ea_version"] == "1.12"
    eu = v["symbols"]["EURUSD"]
    assert list(eu) == ["D1", "H4", "H1", "M15", "M5"]  # extended TFs listed before their walk starts
    assert {tf: eu[tf]["days_target"] for tf in eu} == {"D1": 30, "H4": 10, "H1": 3, "M15": 1, "M5": 1}
    keys = {"bars", "oldest_ts", "target_ts", "done", "days_target", "days_loaded", "extended", "exhausted",
            "history_start_ts"}
    for tf, e in eu.items():
        assert set(e) == keys, tf
        assert e["extended"] is (tf in ("D1", "H4", "H1"))
        assert abs(e["target_ts"] - (now - e["days_target"] * DAY)) <= 1
    # rt.archive_status keeps its stage-B shape and base pairs only until the extended walk starts
    assert set(rt.archive_status["EURUSD"]) == {"M5", "H1", "M15"}
    assert set(rt.archive_status["EURUSD"]["M5"]) == {"bars", "oldest_ts", "target_ts", "done"}
    run_planner(rt, now, steps=3)
    assert archive.status_view(rt, now + 70)["phase"] == "extra"
    run_planner(rt, now + 100)
    v = archive.status_view(rt, now + 1000)
    assert v["phase"] == "done"
    assert all(e["done"] for e in v["symbols"]["EURUSD"].values())
    assert set(rt.archive_status["EURUSD"]) == {"M5", "H1", "M15", "H4", "D1"}
    eng.db.close()


# ---- D1 / H4 bar times and the broker DST rule -----------------------------------------
def test_d1_h4_bars_keep_broker_day_boundary(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD", MT5_SERVER_TZ="NY")
    rt = eng.rt
    now = ts(2027, 1, 15, 12)       # winter: NY close broker at GMT+2
    rt.bridge["srv_offset"] = 7200
    # broker server-time midnights (D1 opens): summer days (GMT+3) and winter days (GMT+2)
    srv_days = [ts(2026, 7, 7), ts(2026, 7, 8), ts(2026, 7, 9), ts(2026, 12, 8), ts(2026, 12, 9)]
    ea_bars = [{"t": d - 7200, "o": 1.1, "h": 1.2, "l": 1.0, "c": 1.15, "tv": 1, "sp": 5} for d in srv_days]
    end = ts(2026, 12, 10) - 7200
    rt.archive_plan["pending"] = {"symbol": "EURUSD", "tf": "D1", "end_utc": end, "ea_end": end, "count": 5,
                                  "sent_ts": now, "extended": True}
    archive.ingest_payload(rt, [{"symbol": "EURUSD", "tf": "D1", "end_utc": end, "srv_off": 7200,
                                 "bars": ea_bars}], now)
    archive.drain()
    got = [r["t"] for r in eng.db.query("SELECT t FROM bars_archive WHERE tf='D1' ORDER BY t")]
    # D1 bar open = broker midnight = 17:00 New York = 21:00 UTC (summer) / 22:00 UTC (winter), previous UTC day
    assert got == [d - 10800 for d in srv_days[:3]] + [d - 7200 for d in srv_days[3:]]
    assert [datetime.fromtimestamp(t, timezone.utc).hour for t in got] == [21, 21, 21, 22, 22]
    # H4: server 00/04/08 summer -> 21/01/05 UTC
    h4_srv = [ts(2026, 7, 8, 0), ts(2026, 7, 8, 4), ts(2026, 7, 8, 8)]
    end4 = ts(2026, 7, 8, 12) - 7200
    rt.archive_plan["pending"] = {"symbol": "EURUSD", "tf": "H4", "end_utc": end4, "ea_end": end4, "count": 3,
                                  "sent_ts": now, "extended": True}
    archive.ingest_payload(rt, [{"symbol": "EURUSD", "tf": "H4", "end_utc": end4, "srv_off": 7200,
                                 "bars": [dict(ea_bars[0], t=t - 7200) for t in h4_srv]}], now)
    archive.drain()
    got4 = [r["t"] for r in eng.db.query("SELECT t FROM bars_archive WHERE tf='H4' ORDER BY t")]
    assert got4 == [t - 10800 for t in h4_srv]
    # backtests read bars by OPEN time; a bar counts as closed at t + TF seconds (not aligned to UTC midnight)
    bars = load_bars(eng.db, "EURUSD", "D1", 0, 2**31, now_ts=now)
    assert [b["t"] for b in bars] == got
    assert load_bars(eng.db, "EURUSD", "D1", 0, 2**31, now_ts=got[-1] + DAY - 1)[-1]["t"] == got[-2]
    eng.db.close()


def test_extra_walk_with_positions_override():
    from types import SimpleNamespace
    from app import archive
    rt = SimpleNamespace(settings=SimpleNamespace(MT5_MAGIC=552211), mt5_positions=[{"magic": 552211}])
    assert archive._bot_positions_open(rt)
