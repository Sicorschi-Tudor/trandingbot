"""Stage C2 review fixes: swap rollover at broker midnight (17:00 New York, strict interval), extended HISTX throttle
counted from the EA reply, extended walk off by default and paused while the bot holds MT5 positions."""
import time
from datetime import datetime, timezone

from app import archive, backtest
from app.config import TF_SECONDS
from tests.conftest import make_settings
from tests.test_stage_c2_archive import answer, connect, make_engine, parse

H = 3600
DAY = 86400


def ts(*a) -> int:
    return int(datetime(*a, tzinfo=timezone.utc).timestamp())


# ---- swap rollovers ---------------------------------------------------------------
def test_d1_bar_open_to_close_pays_no_swap_winter_and_summer():
    # winter: D1 bar Sun 22:00 UTC -> Mon 22:00 UTC (broker midnight = 17:00 NY = 22:00 UTC)
    assert backtest.rollovers(ts(2026, 1, 4, 22), ts(2026, 1, 5, 22)) == 0
    # summer: D1 bar Sun 21:00 UTC -> Mon 21:00 UTC (17:00 NY = 21:00 UTC); the old 22:00 UTC model charged 1
    assert backtest.rollovers(ts(2026, 7, 5, 21), ts(2026, 7, 6, 21)) == 0
    # Wednesday bar: no triple when the position never stays over broker midnight
    assert backtest.rollovers(ts(2026, 1, 6, 22), ts(2026, 1, 7, 22)) == 0
    assert backtest.rollovers(ts(2026, 7, 8, 21), ts(2026, 7, 9, 21)) == 0
    # held over the next broker midnight: charged (Wednesday x3)
    assert backtest.rollovers(ts(2026, 1, 6, 22), ts(2026, 1, 8, 22)) == 3
    assert backtest.rollovers(ts(2026, 7, 7, 20), ts(2026, 7, 8, 21, 0, 1)) == 4  # Tue 1 + Wed 3


def test_rollover_hour_follows_new_york_dst():
    # summer: 21:00 UTC is the rollover, 22:00 UTC is not
    assert backtest.rollovers(ts(2026, 7, 6, 20), ts(2026, 7, 6, 21, 30)) == 1
    assert backtest.rollovers(ts(2026, 7, 6, 21, 30), ts(2026, 7, 6, 23)) == 0
    # winter: 22:00 UTC
    assert backtest.rollovers(ts(2026, 1, 5, 21), ts(2026, 1, 5, 21, 59)) == 0
    assert backtest.rollovers(ts(2026, 1, 5, 21), ts(2026, 1, 5, 22, 1)) == 1
    # weekend: Friday rollover 1, Saturday/Sunday none; a full week = 7 swap days in either season
    assert backtest.rollovers(ts(2026, 7, 10, 20), ts(2026, 7, 13, 20)) == 1
    assert backtest.rollovers(ts(2026, 7, 6, 12), ts(2026, 7, 13, 12)) == 7
    assert backtest.rollovers(ts(2026, 1, 5, 12), ts(2026, 1, 12, 12)) == 7
    assert backtest.rollovers(ts(2026, 1, 5, 12), ts(2026, 1, 5, 12)) == 0


def test_h4_bar_ending_at_broker_midnight_pays_nothing():
    # summer H4 bar 17:00 -> 21:00 UTC: an intrabar exit is stamped at the open and charged up to the bar close
    t = ts(2026, 7, 8, 17)  # Wednesday
    assert backtest.rollovers(t - 4 * H, t + TF_SECONDS["H4"]) == 0
    assert backtest.rollovers(t - 4 * H, t + TF_SECONDS["H4"] + 4 * H) == 3


# ---- archive: extended walk ------------------------------------------------------
def _finish_base(rt, now):
    while True:
        connect(rt)
        line = archive.next_request(rt, now)
        assert line is not None
        if rt.archive_plan["pending"]["extended"]:
            return line, now
        answer(rt, line, now)
        now += 1


def test_extended_walk_off_by_default(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD", ARCHIVE_DAYS=1)
    rt = eng.rt
    assert archive.extra_targets(rt.settings) == {}
    assert make_settings(tmp_path).ARCHIVE_EXTRA_INTERVAL_S == archive.EXTRA_INTERVAL_DEFAULT_S == 60
    assert make_settings(tmp_path).ARCHIVE_EXTRA == "off"
    now = time.time()
    for i in range(3):  # base set only
        connect(rt)
        line = archive.next_request(rt, now + i)
        assert line is not None and rt.archive_plan["pending"]["extended"] is False
        answer(rt, line, now + i)
    connect(rt)
    assert archive.next_request(rt, now + 1000) is None
    assert archive.status_view(rt, now + 1000)["phase"] == "done"
    eng.db.close()


def test_extended_interval_counts_from_the_reply(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD", ARCHIVE_DAYS=1, ARCHIVE_EXTRA="D1:4000",
                      ARCHIVE_BATCH_BARS=100, ARCHIVE_EXTRA_INTERVAL_S=60)
    rt = eng.rt
    line, t_send = _finish_base(rt, time.time())
    assert parse(line)[1] == "D1"
    reply_ts = t_send + 30  # the EA spent 30 s in a CopyRates download
    connect(rt)
    assert archive.next_request(rt, t_send + 29) is None  # still pending
    answer(rt, line, reply_ts)
    assert rt.archive_plan["extra_last_ts"] == reply_ts
    connect(rt)
    assert archive.next_request(rt, t_send + 61) is None  # old behaviour sent here (60 s after the send)
    assert archive.next_request(rt, reply_ts + 59.9) is None
    line2 = archive.next_request(rt, reply_ts + 60)
    assert line2 is not None and parse(line2)[1] == "D1"
    eng.db.close()


def test_extended_timeout_restarts_the_pause(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD", ARCHIVE_DAYS=1, ARCHIVE_EXTRA="D1:4000",
                      ARCHIVE_EXTRA_INTERVAL_S=60)
    rt = eng.rt
    _, t_send = _finish_base(rt, time.time())
    t_drop = t_send + archive.PENDING_TIMEOUT_S + 1
    connect(rt)
    assert archive.next_request(rt, t_drop) is None  # pending dropped, pause starts now
    assert rt.archive_plan["pending"] is None and rt.archive_plan["extra_last_ts"] == t_drop
    rt.archive_plan["retry_at"].clear()  # isolate the throttle from the error backoff
    assert archive.next_request(rt, t_drop + 59) is None
    assert archive.next_request(rt, t_drop + 60) is not None
    eng.db.close()


def test_extended_walk_waits_while_bot_positions_open(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD", ARCHIVE_DAYS=1, ARCHIVE_EXTRA="D1:4000",
                      ARCHIVE_EXTRA_INTERVAL_S=0)
    rt = eng.rt
    line, now = _finish_base(rt, time.time())
    answer(rt, line, now)
    rt.mt5_positions = [{"ticket": 1, "symbol": "EURUSD", "magic": rt.settings.MT5_MAGIC}]
    connect(rt)
    assert archive.next_request(rt, now + 1) is None
    rt.mt5_positions = [{"ticket": 2, "symbol": "EURUSD", "magic": 0}]  # manual position: not the bot's
    assert archive.next_request(rt, now + 2) is not None
    eng.db.close()


def test_base_phase_unaffected_by_open_positions(tmp_path):
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD", ARCHIVE_DAYS=1, ARCHIVE_EXTRA="D1:4000")
    rt = eng.rt
    rt.mt5_positions = [{"ticket": 1, "symbol": "EURUSD", "magic": rt.settings.MT5_MAGIC}]
    connect(rt)
    line = archive.next_request(rt, time.time())
    assert line is not None and rt.archive_plan["pending"]["extended"] is False
    eng.db.close()
