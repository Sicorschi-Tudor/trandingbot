"""Stage B end to end: EA v1.11 archive backfill over the bridge, analyses on bar close (incl. blocked candidates),
shadow labels, /stats /shadow /archive/status, and a real backtest run through the API on the synthetic archive."""
import json
import time

import pytest

from app import archive
from app.config import TF_SECONDS
from tests.conftest import ADMIN, make_bars, quote, zigzag
from tests.test_stage_a_engine import ready
from tests.test_stage_b_archive import bars_before, first_sync, parse_histx, quiet_hist, sync
from tests.test_stage_b_backtest import SPEC, T0, seed_market

M5 = 300


def histx(txt):
    return [l for l in txt.splitlines() if l.startswith("HISTX|")]


def archived(db, sym="EURUSD", tf="M5"):
    return db.query_one("SELECT COUNT(*) AS n, MIN(t) AS mn, MAX(t) AS mx FROM bars_archive WHERE symbol=? AND tf=?",
                        (sym, tf))


# ---- 1. archive backfill over the bridge ---------------------------------------------
def test_fake_ea_archive_backfill_walks_back_and_finishes(engine, client):
    rt = engine.rt
    now = time.time()
    first_sync(engine, now)
    line = histx(sync(engine, now + 1))
    assert len(line) == 1
    sym, tf, end, count = parse_histx(line[0])
    assert (sym, tf, count) == ("EURUSD", "M5", 5000)
    assert end % M5 == 0 and end <= now + 1

    # EA v1.11 answers in the next payload: 5000 closed bars strictly older than end_utc, oldest -> newest
    bars = bars_before(end, 5000)
    txt = sync(engine, now + 2, archive=[{"symbol": sym, "tf": tf, "end_utc": end, "bars": bars}])
    archive.drain()
    r = archived(engine.db)
    assert r["n"] == 5000 and r["mn"] == bars[0]["t"] and r["mx"] == end - M5
    st = rt.archive_status["EURUSD"]["M5"]
    assert st["bars"] == 5000 and st["oldest_ts"] == bars[0]["t"] and st["done"] is False
    assert st["target_ts"] == pytest.approx(now - rt.settings.ARCHIVE_DAYS * 86400, abs=5)

    # the planner keeps walking backwards from the oldest archived bar (same sync or the next one)
    nxt = histx(txt) or histx(sync(engine, now + 3))
    assert len(nxt) == 1
    s2, tf2, end2, c2 = parse_histx(nxt[0])
    assert (s2, tf2, end2) == ("EURUSD", "M5", bars[0]["t"]) and 1 <= c2 <= 5000

    # broker history exhausted: empty reply without err -> pair done, planner moves to the next timeframe
    txt = sync(engine, now + 4, archive=[{"symbol": "EURUSD", "tf": "M5", "end_utc": end2, "bars": []}])
    archive.drain()
    assert rt.archive_status["EURUSD"]["M5"]["done"] is True
    nxt = histx(txt) or histx(sync(engine, now + 5))
    assert len(nxt) == 1 and parse_histx(nxt[0])[1] == rt.strategy["htf_timeframe"]

    # pending trading commands -> no HISTX in that sync
    quiet_hist(rt, now + 70)
    rt.archive_plan["pending"] = None
    assert archive.next_request(rt, now + 70, commands_pending=True) is None

    body = client.get("/api/v1/archive/status").json()
    assert body["enabled"] is True and body["ea_version"] == "1.11"
    m5 = body["symbols"]["EURUSD"]["M5"]
    assert m5["bars"] == 5000 and m5["done"] is True and m5["oldest_ts"] == bars[0]["t"]
    assert {"target_ts", "days_loaded", "days_target"} <= set(m5)


def test_old_ea_gets_no_histx(engine):
    now = time.time()
    first_sync(engine, now, version="1.10")
    for i in range(3):
        assert histx(sync(engine, now + 1 + i, version="1.10")) == []


# ---- 2. analyses on bar close + labeler + shadow -------------------------------------
def past_market(engine, end_t, n=150, spread_pts=10):
    sig = make_bars(zigzag(n, 0.0003, 0.0002, end_up=True), M5, end_t=end_t)
    htf = make_bars(zigzag(80, 0.0030, 0.0020, end_up=True), 3600, end_t=end_t // 3600 * 3600 - 3600, wick=0.0005)
    engine.rt.ingest_history("EURUSD", "M5", sig)
    engine.rt.ingest_history("EURUSD", "H1", htf)
    engine.rt.ingest_quote("EURUSD", quote(bid=sig[-1]["c"], spread_pts=spread_pts))
    return sig


def test_bar_close_records_blocked_candidate_then_labels_it(engine, client):
    ready(engine)
    now = time.time()
    end_t = int(now // M5 * M5) - 3 * 3600  # decision bar 3 h ago: the bars after it are closed, the time stop is not
    max_spread = int(engine.rt.risk.get("max_spread_points", 30))
    sig = past_market(engine, end_t, spread_pts=max_spread + 20)  # spread filter blocks the setup
    engine.loop_once()
    rows = engine.db.query("SELECT * FROM analyses")
    assert len(rows) == 1
    r = rows[0]
    assert r["bar_time"] == sig[-1]["t"] and r["tf"] == "M5"
    assert r["direction"] == "HOLD" and r["candidate_side"] == "BUY", r
    assert r["sl"] < r["entry"] < r["tp"]
    blockers = json.loads(r["blockers_json"])
    assert blockers and any("pread" in b for b in blockers)
    assert r["signal_id"] is None and r["outcome"] is None

    engine.loop_once()  # same closed bar: no second row
    assert len(engine.db.query("SELECT * FROM analyses")) == 1

    # not enough bars yet -> unresolved
    assert engine.run_labeler(now) == 0
    assert engine.db.query_one("SELECT outcome FROM analyses")["outcome"] is None

    # price rallies through TP after the decision bar
    risk = r["entry"] - r["sl"]
    p = sig[-1]["c"]
    after = []
    for i in range(1, 30):
        o, p = p, round(p + risk / 8, 5)
        after.append((
            "EURUSD", "M5", sig[-1]["t"] + i * M5, o, round(p + 0.00002, 5), round(o - 0.00002, 5), p, 50, 10))
    engine.db.executemany("INSERT OR IGNORE INTO bars_archive(symbol,tf,t,o,h,l,c,tv,sp) VALUES(?,?,?,?,?,?,?,?,?)",
                          after)
    assert engine.run_labeler(now) == 1
    r = engine.db.query_one("SELECT * FROM analyses")
    assert r["outcome"] == "TP" and r["labeled_ts"] is not None
    # fill = next bar open + that bar's spread (10 points), exits at TP; R measured from the fill
    fill = after[0][3] + 10 * 0.00001
    assert r["outcome_r"] == pytest.approx((r["tp"] - fill) / (fill - r["sl"]), abs=1e-3)
    assert r["mfe_r"] > 0 and r["mae_r"] >= 0

    sh = client.get("/api/v1/shadow?days=30").json()
    assert sh["candidates"] == 1 and sh["labeled"] == 1 and sh["pending"] == 0
    for k in ("by_ai_verdict", "by_symbol"):
        assert k in sh
    blob = json.dumps(sh, ensure_ascii=False)
    assert "Candidat blocat de filtre" in blob and "EURUSD" in blob


def test_stats_shape(engine, client):
    engine.snapshot_equity(time.time())
    st = client.get("/api/v1/stats?days=30").json()
    for k in ("summary", "by_symbol", "by_hour", "by_ai_verdict"):
        assert k in st, k
    for k in ("trades", "win_rate", "avg_r", "expectancy_r", "profit_factor", "max_dd_r", "net_usd"):
        assert k in st["summary"], k
    blob = json.dumps(st)
    assert "today_usd" in blob and "total_usd" in blob
    assert client.get("/api/v1/stats?days=0&account_key=PAPER").status_code == 200


# ---- 3. backtest through the API on the synthetic archive ---------------------------
def wait_done(client, run_id, timeout=120.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/api/v1/backtest/runs/{run_id}").json()
        if r["status"] != "RUNNING":
            return r
        time.sleep(0.1)
    raise AssertionError("backtest did not finish")


def test_backtest_run_on_archive_via_api(engine, client):
    seed_market(engine.db, "EURUSD", days=6, seed=11)
    seed_market(engine.db, "GBPUSD", days=6, seed=5, p0=1.27)
    body = {"symbols": ["EURUSD", "GBPUSD"], "start_ts": T0 + 3 * 86400, "end_ts": T0 + 5 * 86400, "session": None,
            "initial_equity": 10000, "risk_pct": 1.0, "cost": {"mode": "fixed", "fixed_spread_pts": 3},
            "strategy": {"min_score": 4}, "risk": {"max_signals_per_symbol_per_session": 50}}
    assert client.post("/api/v1/backtest/run", json=body).status_code in (401, 403)
    r = client.post("/api/v1/backtest/run", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    run_id = r.json()["id"]
    res = wait_done(client, run_id)
    assert res["status"] == "DONE", res.get("error")
    m = res["metrics"]
    s = m["summary"]
    assert s["trades"] > 0
    assert 0 <= s["win_rate"] <= 1
    assert s["max_dd_r"] >= 0 and s["max_consec_losses"] >= 0
    lo, hi = s["ci90_avg_r"]
    assert lo <= s["avg_r"] <= hi
    assert set(m["by_symbol"]) <= {"EURUSD", "GBPUSD"} and m["by_symbol"]
    assert len(m["equity_r"]) <= 500 and len(m["trades"]) <= 300
    assert all(t["symbol"] in ("EURUSD", "GBPUSD") and t["outcome"] in ("TP", "SL", "BE", "TIME", "END")
               for t in m["trades"])
    assert any(x["id"] == run_id for x in client.get("/api/v1/backtest/runs").json())


def test_backtest_uses_live_specs(engine, monkeypatch):
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08))
    seen = {}

    def fake_run(db, params, progress=None):
        seen.update(params)
        return {"summary": {}}

    import app.backtest as bt
    monkeypatch.setattr(bt, "run", fake_run)
    engine._backtest_job("x", {"symbols": ["EURUSD"]})
    assert seen["specs"]["EURUSD"]["point"] == 0.00001 and seen["specs"]["EURUSD"]["tick_value"] == 1.0
    assert engine.labeler_cfg()["points"]["EURUSD"] == 0.00001
    assert TF_SECONDS["M5"] == M5
