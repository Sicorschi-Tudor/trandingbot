"""CALHIST planner with window_s < SETTLE_S + HORIZON_S: 'recent' is decided per requested window, so the backfill
below the not-yet-final zone keeps progressing (regression: it used to stall on the top window forever)."""
from app import archive, calendar_hist as ch
from tests.test_calendar_hist import DAY, NOW, item, make_engine, connect, reply_for, ts


def _cov_len(rt):
    return sum(b - a for a, b in ch.covered(rt))


def test_small_window_recent_request_reaches_below_settle_line(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    ch.plan_of(rt)["window_s"] = 5 * DAY
    line = ch.next_request(rt, NOW)
    p = rt.calhist_plan["pending"]
    assert p["recent"] and p["hi"] == NOW + ch.HORIZON_S
    assert p["lo"] <= NOW - ch.SETTLE_S - ch.RECENT_SLACK_S  # wider than window_s on purpose
    ch.ingest_payload(rt, reply_for(line), NOW)
    assert ch.covered(rt) == [(p["lo"], NOW - ch.SETTLE_S)]  # coverage grows
    eng.db.close()


def test_small_window_backfill_progresses_at_normal_cadence(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    now = NOW
    sent = []
    for _ in range(60):
        connect(rt)
        ch.plan_of(rt)["window_s"] = 5 * DAY  # dense data keeps the window small
        line = ch.next_request(rt, now)
        assert line
        p = dict(rt.calhist_plan["pending"])
        sent.append(p)
        ch.ingest_payload(rt, reply_for(line), now)
        now += 31
    assert sent[0]["recent"] and not any(p["recent"] for p in sent[1:])  # recent zone not due again yet
    assert all(p["hi"] <= int(p["sent_ts"] - ch.SETTLE_S) for p in sent[1:])  # backfill stays below the line
    cov = ch.covered(rt)
    assert len(cov) == 1 and cov[0][1] == NOW - ch.SETTLE_S
    assert cov[0][1] - cov[0][0] >= 50 * 5 * DAY  # ~1 window of history per request, walking back
    assert ch.CALHIST_START < cov[0][0] < ts(2026, 1, 1)  # crossed the year boundary too
    eng.db.close()


def test_small_window_with_sparse_cycles_still_grows(tmp_path):
    # the reported repro: window_s = 5 days, empty ok replies every 7 h over 40 cycles
    eng = make_engine(tmp_path)
    rt = eng.rt
    now = NOW
    grown = []
    for _ in range(40):
        connect(rt)
        ch.plan_of(rt)["window_s"] = 5 * DAY
        line = ch.next_request(rt, now)
        assert line
        ch.ingest_payload(rt, reply_for(line), now)
        grown.append(_cov_len(rt))
        now += 7 * 3600
    assert grown[0] > 0 and all(b >= a for a, b in zip(grown, grown[1:])) and grown[-1] > grown[0]
    assert ch.gaps(rt, now)[0][1] != now + ch.HORIZON_S or len(ch.gaps(rt, now)) > 1
    eng.db.close()


def test_no_sliver_requests_right_after_recent_fetch(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    rt.calhist_plan = None
    ch.plan_of(rt)["windows"] = [(ch.CALHIST_START, NOW - 10 * DAY, NOW - 5 * DAY)]
    ch.plan_of(rt)["window_s"] = 5 * DAY
    line = ch.next_request(rt, NOW)
    p = rt.calhist_plan["pending"]
    assert p["recent"] and p["lo"] == NOW - ch.SETTLE_S - ch.RECENT_SLACK_S
    ch.ingest_payload(rt, reply_for(line), NOW)
    line = ch.next_request(rt, NOW + 31)  # the older part of the gap: a normal backfill window
    p = rt.calhist_plan["pending"]
    assert not p["recent"] and (p["lo"], p["hi"]) == (NOW - 10 * DAY, NOW - ch.SETTLE_S - ch.RECENT_SLACK_S)
    ch.ingest_payload(rt, reply_for(line), NOW + 31)
    assert ch.covered(rt) == [(ch.CALHIST_START, NOW - ch.SETTLE_S)]  # history complete up to the settle line
    assert ch.gaps(rt, NOW) == [(NOW - ch.SETTLE_S, NOW + ch.HORIZON_S)]
    for k in range(1, 20):  # time moves on, recent not due: no tiny non-recent requests
        assert ch.next_request(rt, NOW + k * 600) is None
    eng.db.close()


def test_forced_recent_truncated_makes_progress_without_loop(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    ch.plan_of(rt)["window_s"] = 2 * DAY
    line = ch.next_request(rt, NOW)
    p = dict(rt.calhist_plan["pending"])
    assert p["recent"] and p["forced"]
    last = p["lo"] + DAY // 2
    its = [item(500 + i, p["lo"] + i * 60) for i in range(int(DAY / 120) + 1)]
    ch.ingest_payload(rt, reply_for(line, its, truncated=True, total=99999), NOW)
    archive.drain()
    assert ch.covered(rt) == [(p["lo"], last)]  # only up to the last value received
    assert rt.calhist_plan["recent_ts"] is None  # the rest of the recent zone is still due
    line2 = ch.next_request(rt, NOW + 31)
    p2 = rt.calhist_plan["pending"]
    assert line2 and p2["recent"] and p2["lo"] == last  # continues where it stopped
    ch.ingest_payload(rt, reply_for(line2), NOW + 31)
    assert ch.covered(rt) == [(p["lo"], int(NOW + 31 - ch.SETTLE_S))]
    eng.db.close()


def test_forced_recent_truncated_without_items_backs_off(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    ch.plan_of(rt)["window_s"] = 2 * DAY
    line = ch.next_request(rt, NOW)
    ch.ingest_payload(rt, reply_for(line, [], truncated=True, total=99999), NOW)
    assert ch.covered(rt) == [] and rt.calhist_plan["errors"] == 1
    assert ch.next_request(rt, NOW + 10) is None  # backoff, not a tight retry loop
    eng.db.close()
