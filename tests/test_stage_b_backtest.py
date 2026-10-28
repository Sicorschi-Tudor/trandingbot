"""Stage B: strategy candidate, labeler (shadow outcomes) and walk-forward backtest."""
import random

import pytest

from app import backtest, labeler, strategy
from app.config import Settings
from app.db import DB
from app.strategy import AnalysisContext, analyze
from tests.conftest import make_bars, make_settings, quote, zigzag

S = Settings(_env_file=None)
CFG = S.default_strategy()
RISK = S.default_risk()

EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars_archive (symbol TEXT, tf TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, tv REAL,
    sp REAL, PRIMARY KEY(symbol, tf, t));
CREATE TABLE IF NOT EXISTS spread_stats (symbol TEXT, hour_utc INTEGER, n INTEGER, sum_pts REAL, sum_sq REAL,
    max_pts REAL, PRIMARY KEY(symbol, hour_utc));
CREATE TABLE IF NOT EXISTS analyses (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, tf TEXT, bar_time INTEGER,
    created_ts REAL, account_key TEXT, direction TEXT, candidate_side TEXT, entry REAL, sl REAL, tp REAL, score REAL,
    blockers_json TEXT, signal_id TEXT, ai_verdict TEXT, outcome TEXT, outcome_r REAL, mfe_r REAL, mae_r REAL,
    labeled_ts REAL, UNIQUE(symbol, tf, bar_time));
"""
T0 = 1_790_000_000 // 86400 * 86400  # Monday 2026-09-21 00:00 UTC
SPEC = {"EURUSD": {"point": 0.00001, "digits": 5, "tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01},
        "GBPUSD": {"point": 0.00001, "digits": 5, "tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}}


@pytest.fixture
def db(tmp_path):
    make_settings(tmp_path)
    d = DB(tmp_path / "bt.db")
    d.conn.executescript(EXTRA_SCHEMA)
    yield d
    d.close()


def ctx(**kw):
    base = dict(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.5, now_ts=T0)
    base.update(kw)
    return AnalysisContext(**base)


def up_data():
    sig = make_bars(zigzag(150, 0.0003, 0.0002, end_up=True), 300)
    htf = make_bars(zigzag(80, 0.0030, 0.0020, end_up=True), 3600, wick=0.0005)
    return sig, htf, quote(bid=sig[-1]["c"])


def walk(n, sec, t0, seed, p0=1.08, vol=0.0004, sp=3):
    """Random walk with persistent drift regimes (so the trend strategy fires); weekends skipped."""
    rng = random.Random(seed)
    out, p, drift, t = [], p0, 0.0, t0
    while len(out) < n:
        if (t // 86400 + 3) % 7 < 5:  # 1970-01-01 was a Thursday
            if rng.random() < 0.02:
                drift = rng.gauss(0, vol * 0.25)
            o = p
            p = o + drift + rng.gauss(0, vol)
            out.append({"t": t, "o": round(o, 5), "h": round(max(o, p) + abs(rng.gauss(0, vol / 2)), 5),
                        "l": round(min(o, p) - abs(rng.gauss(0, vol / 2)), 5), "c": round(p, 5), "sp": sp})
        t += sec
    return out


def resample(bars, sec):
    agg = {}
    for b in bars:
        k = b["t"] - b["t"] % sec
        if k not in agg:
            agg[k] = dict(b, t=k)
        else:
            a = agg[k]
            a["h"], a["l"], a["c"] = max(a["h"], b["h"]), min(a["l"], b["l"]), b["c"]
    return [agg[k] for k in sorted(agg)]


def store(db, symbol, tf, bars, table="bars_archive"):
    db.executemany(f"INSERT OR REPLACE INTO {table}(symbol,tf,t,o,h,l,c,tv,sp) VALUES(?,?,?,?,?,?,?,?,?)",
                   [(symbol, tf, b["t"], b["o"], b["h"], b["l"], b["c"], 0, b["sp"]) for b in bars])


def seed_market(db, symbol="EURUSD", days=6, seed=11, p0=1.08):
    m5 = walk(days * 288, 300, T0, seed, p0=p0)
    store(db, symbol, "M5", m5)
    store(db, symbol, "H1", resample(m5, 3600))
    return m5


# ---- strategy candidate -----------------------------------------------------------------

def test_candidate_present_when_blocked_with_identical_levels():
    sig, htf, q = up_data()
    live = analyze("EURUSD", sig, htf, q, CFG, RISK, ctx())
    assert live["direction"] == "BUY"
    c = live["candidate"]
    assert c["side"] == "BUY" and c["blockers"] == []
    assert (c["entry"], c["stop_loss"], c["take_profit"]) == (live["entry"], live["stop_loss"], live["take_profit"])
    assert c["score"] == live["score"]
    blocked = analyze("EURUSD", sig, htf, q, CFG, RISK, ctx(has_bot_position=True, session_open=False))
    assert blocked["direction"] == "HOLD" and blocked["entry"] is None
    bc = blocked["candidate"]
    assert (bc["side"], bc["entry"], bc["stop_loss"], bc["take_profit"]) == ("BUY", live["entry"], live["stop_loss"],
                                                                           live["take_profit"])
    assert any("poziție a botului" in b for b in bc["blockers"])
    assert any("Sesiunea" in b for b in bc["blockers"])
    # market blocker (spread) also keeps the candidate
    wide = analyze("EURUSD", sig, htf, quote(bid=sig[-1]["c"], spread_pts=80), CFG, RISK, ctx())
    assert wide["direction"] == "HOLD" and wide["candidate"]["side"] == "BUY"
    assert any("Spread" in b for b in wide["candidate"]["blockers"])


def test_candidate_none_without_setup():
    flat = make_bars([1.08] * 150, 300)
    htf = make_bars([1.08] * 80, 3600)
    a = analyze("EURUSD", flat, htf, quote(bid=1.08), CFG, RISK, ctx())
    assert a["direction"] == "HOLD" and a["candidate"] is None


def test_neutral_scan_plus_context_blockers_equals_live_analyze():
    m5 = walk(900, 300, T0, 5)
    h1 = resample(m5, 3600)
    risk = dict(RISK, max_signals_per_symbol_per_session=3)
    checked = 0
    for i in range(400, 900, 7):
        T = m5[i]["t"] + 300
        sig = m5[max(0, i - 399): i + 1]
        htf = [b for b in h1 if b["t"] + 3600 <= T][-200:]
        q = quote(bid=sig[-1]["c"], spread_pts=3)
        for real in (ctx(now_ts=T, tick_age=0.0), ctx(now_ts=T, tick_age=0.0, has_bot_position=True),
                     ctx(now_ts=T, tick_age=0.0, open_exposures=[("GBPUSD", "BUY"), ("USDJPY", "SELL")]),
                     ctx(now_ts=T, tick_age=0.0, signals_this_session=3)):
            live = analyze("EURUSD", sig, htf, q, CFG, risk, real)
            neutral = analyze("EURUSD", sig, htf, q, CFG, risk, ctx(now_ts=T, tick_age=0.0))
            nc = neutral["candidate"]
            if nc is None:
                assert live["candidate"] is None and live["direction"] == "HOLD"
                continue
            checked += 1
            blockers = nc["blockers"] + strategy.context_blockers("EURUSD", nc["side"], real, risk)
            assert blockers == live["candidate"]["blockers"]
            assert (live["direction"] != "HOLD") == (not blockers)
            if not blockers:
                assert (live["entry"], live["stop_loss"], live["take_profit"]) == (nc["entry"], nc["stop_loss"],
                                                                                   nc["take_profit"])
    assert checked > 20


# ---- labeler ------------------------------------------------------------------------------

def m1(t, o, h, l, c, sp=0):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "sp": sp}


def test_simposition_same_bar_sl_first_and_be_lock():
    p = labeler.SimPosition("BUY", 1.10000, 1.09900, 1.10200, 0.00001, T0, be_at_r=1.0)
    assert p.step(m1(T0, 1.1, 1.1025, 1.0985, 1.1), 0.0) == ("SL", pytest.approx(1.099))
    p = labeler.SimPosition("BUY", 1.10000, 1.09900, 1.10200, 0.00001, T0, be_at_r=1.0)
    assert p.step(m1(T0, 1.1, 1.1010, 1.0995, 1.1008), 0.0) is None  # +1R reached -> lock from the next bar
    assert p.be_done and p.sl == pytest.approx(1.10002)  # entry + 2 pts, as position_manager.plan
    reason, px = p.step(m1(T0 + 60, 1.1008, 1.1009, 1.0990, 1.0995), 0.0)
    assert reason == "BE" and p.r_of(px) == pytest.approx(0.02)
    # SELL exits on ask: a bid high below the SL still stops out once the spread is added
    s = labeler.SimPosition("SELL", 1.10000, 1.10100, 1.09800, 0.00001, T0)
    assert s.step(m1(T0, 1.1, 1.10095, 1.0999, 1.1), 0.0001)[0] == "SL"


def test_label_pending_outcomes(db):
    D = T0 + 300  # decision time (close of the M5 bar at T0); every row has its own symbol and bars
    rows = {  # symbol: side, sl, tp, M1 bars from D
        "EURUSD": ("BUY", 1.0990, 1.1020, [m1(D, 1.1, 1.1005, 1.0995, 1.1003, sp=10), m1(D + 60, 1.1003, 1.1021, 1.1, 1.102)]),
        "GBPUSD": ("BUY", 1.0990, 1.1020, [m1(D, 1.1, 1.1030, 1.0980, 1.1)]),
        "AUDUSD": ("SELL", 1.1010, 1.0980, [m1(D, 1.1, 1.10095, 1.0999, 1.1, sp=10)]),
        "NZDUSD": ("BUY", 1.0990, 1.1020, [m1(D + k * 60, 1.1, 1.1004, 1.0996, 1.1002) for k in range(25)]),
        "EURGBP": ("BUY", 1.0990, 1.1020, [m1(D + k * 60, 1.1, 1.1004, 1.0996, 1.1002) for k in range(5)]),
    }
    for sym, (side, sl, tp, _) in rows.items():
        bar_time = T0 if sym != "EURGBP" else T0 + 3600
        db.execute("INSERT INTO analyses(symbol, tf, bar_time, candidate_side, entry, sl, tp, direction) "
                   "VALUES(?,'M5',?,?,1.1,?,?,'HOLD')", (sym, bar_time, side, sl, tp))
    db.execute("INSERT INTO analyses(symbol, tf, bar_time, candidate_side, direction) VALUES('USDCHF','M5',?,NULL,'HOLD')",
               (T0,))
    rows["EURGBP"] = ("BUY", 1.099, 1.102, [m1(T0 + 3900 + k * 60, 1.1, 1.1004, 1.0996, 1.1002) for k in range(5)])

    def get_bars(symbol, tf, a, b):
        assert tf == "M1"
        return [x for x in rows[symbol][3] if a <= x["t"] < b]

    cfg = {"break_even_at_r": 1.0, "max_hold_minutes": 20, "label_tf": "M1", "spread_mode": "bar", "slippage_pts": 0}
    now = T0 + 3900 + 6 * 60
    n = labeler.label_pending(db, get_bars, cfg, now)
    got = {r["symbol"]: r for r in db.query("SELECT * FROM analyses")}
    # entry = next open + bar spread (10 pts) = 1.1001; R = 0.0011
    assert got["EURUSD"]["outcome"] == "TP"
    assert got["EURUSD"]["outcome_r"] == pytest.approx((1.102 - 1.1001) / 0.0011, rel=1e-3)
    assert got["EURUSD"]["mfe_r"] >= got["EURUSD"]["outcome_r"]
    assert got["GBPUSD"]["outcome"] == "SL" and got["GBPUSD"]["outcome_r"] == pytest.approx(-1.0)  # same bar: SL first
    assert got["AUDUSD"]["outcome"] == "SL"  # SELL stops on ask = bid high + spread
    assert got["NZDUSD"]["outcome"] == "TIME" and got["NZDUSD"]["outcome_r"] == pytest.approx(0.2)
    assert got["EURGBP"]["outcome"] is None  # deadline not reached, nothing hit
    assert got["USDCHF"]["outcome"] is None  # no candidate: never labeled
    assert n == 4 and got["EURUSD"]["labeled_ts"] == now


def test_label_pending_be_and_time_buy(db):
    db.execute("INSERT INTO analyses(symbol, tf, bar_time, candidate_side, entry, sl, tp) VALUES('EURUSD','M5',?,?,?,?,?)",
               (T0, "BUY", 1.1, 1.099, 1.102))
    db.execute("INSERT INTO analyses(symbol, tf, bar_time, candidate_side, entry, sl, tp) VALUES('GBPUSD','M5',?,?,?,?,?)",
               (T0, "BUY", 1.3, 1.299, 1.302))
    eu = [m1(T0 + 300, 1.1, 1.1011, 1.0999, 1.101), m1(T0 + 360, 1.101, 1.101, 1.0995, 1.0996)]
    gu = [m1(T0 + 300 + k * 60, 1.3, 1.3004, 1.2996, 1.3002) for k in range(8)]

    def get_bars(symbol, tf, a, b):
        if tf != "M1":
            return []
        return [x for x in (eu if symbol == "EURUSD" else gu) if a <= x["t"] < b]

    n = labeler.label_pending(db, get_bars, {"break_even_at_r": 1.0, "max_hold_minutes": 5, "spread_mode": "bar",
                                            "slippage_pts": 0}, T0 + 3600)
    got = {r["symbol"]: r for r in db.query("SELECT * FROM analyses")}
    assert n == 2
    assert got["EURUSD"]["outcome"] == "BE" and got["EURUSD"]["outcome_r"] == pytest.approx(0.02)
    assert got["GBPUSD"]["outcome"] == "TIME" and got["GBPUSD"]["outcome_r"] == pytest.approx(0.2)


# ---- execution simulation (phase 2) on hand-made decisions -------------------------------

def sim_case(db, decisions, bars_by_sym, **params):
    base = {"symbols": list(bars_by_sym), "start_ts": T0, "end_ts": T0 + 86400, "session": None,
            "initial_equity": 10000, "risk_pct": 1.0, "specs": SPEC, "now_ts": T0 + 10 * 86400,
            "cost": {"mode": "fixed", "fixed_spread_pts": 10, "commission_per_lot_usd": 7, "slippage_pts": 1},
            "risk": {"max_open_positions": 5, "max_total_open_risk_pct": 50, "max_same_currency_direction": 1}}
    for k, v in params.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    cfg = backtest.resolve(db, base)
    sess = backtest.Session(None, "UTC", T0, T0 + 86400)
    spreads = {s: [cfg["cost"]["fixed_spread_pts"]] * len(b) for s, b in bars_by_sym.items()}
    specs = {s: backtest.symbol_spec(s, b[0]["c"], SPEC) for s, b in bars_by_sym.items()}
    scans = {s: {"symbol": s, "decisions": decisions.get(s, {}), "n": 0} for s in bars_by_sym}
    warnings = []
    return backtest._simulate(cfg, bars_by_sym, spreads, specs, scans, sess,
                              backtest._Prices(bars_by_sym, 300), warnings), warnings


def flat_bars(n, px=1.10000):
    return [{"t": T0 + i * 300, "o": px, "h": px + 0.00005, "l": px - 0.00005, "c": px, "sp": 0} for i in range(n)]


def test_costs_spread_slippage_commission(db):
    bars = flat_bars(6)
    bars[2] = {"t": T0 + 600, "o": 1.1, "h": 1.10215, "l": 1.09995, "c": 1.1021, "sp": 0}  # TP bar
    # decision on bar 0 close: BUY, ask entry 1.10010, SL 1.09910, TP 1.10210
    res, _ = sim_case(db, {"EURUSD": {0: ("BUY", 1.10010, 1.09910, 1.10210, 0.8, ())}}, {"EURUSD": bars})
    (t,) = res["trades"]
    assert t["entry"] == pytest.approx(1.10011)  # next open 1.10000 + 10 pts spread + 1 pt slippage
    assert t["outcome"] == "TP"
    # volume: 1% of 10000 = 100 USD over 100 pts (signal SL distance) at 1 USD/pt/lot -> 1.0 lot
    assert t["volume"] == pytest.approx(1.0)
    r_dist = 1.10011 - 1.09910
    assert t["gross_r"] == pytest.approx((1.10210 - 1.10011) / r_dist, abs=1e-4)
    assert t["usd"] == pytest.approx((1.10210 - 1.10011) / 0.00001 * 1.0 - 7.0, abs=0.01)
    assert t["r"] == pytest.approx(t["usd"] / (r_dist / 0.00001), abs=1e-3)
    assert res["equity"] == pytest.approx(10000 + t["usd"], abs=0.01)


def test_same_bar_sl_first_with_stop_slippage(db):
    bars = flat_bars(5)
    bars[1] = {"t": T0 + 300, "o": 1.1, "h": 1.1030, "l": 1.0980, "c": 1.1, "sp": 0}
    res, _ = sim_case(db, {"EURUSD": {0: ("BUY", 1.10010, 1.09910, 1.10210, 0.8, ())}}, {"EURUSD": bars})
    (t,) = res["trades"]
    assert t["outcome"] == "SL"
    assert t["exit"] == pytest.approx(1.09909)  # SL - 1 pt slippage
    assert t["r"] < -1.0


def test_break_even_lock(db):
    bars = flat_bars(6)
    bars[1] = {"t": T0 + 300, "o": 1.1, "h": 1.10115, "l": 1.09995, "c": 1.1011, "sp": 0}  # +1R (bid) reached
    bars[2] = {"t": T0 + 600, "o": 1.1011, "h": 1.1012, "l": 1.0990, "c": 1.0992, "sp": 0}  # falls back
    res, _ = sim_case(db, {"EURUSD": {0: ("BUY", 1.10010, 1.09910, 1.10210, 0.8, ())}}, {"EURUSD": bars},
                      cost={"slippage_pts": 0, "commission_per_lot_usd": 0})
    (t,) = res["trades"]
    assert t["outcome"] == "BE"
    assert t["gross_r"] == pytest.approx(0.02, abs=1e-6)  # lock = entry + 2 pts (position_manager.plan)


def test_time_stop_and_end(db):
    bars = flat_bars(60)
    res, _ = sim_case(db, {"EURUSD": {0: ("BUY", 1.10010, 1.09910, 1.10210, 0.8, ())}}, {"EURUSD": bars},
                      strategy={"max_hold_minutes": 60})
    (t,) = res["trades"]
    assert t["outcome"] == "TIME" and t["exit_t"] - t["entry_t"] == 3600


def test_correlation_limit_blocks_same_currency_direction(db):
    eu, gu = flat_bars(6), flat_bars(6, 1.30000)
    dec = {"EURUSD": {0: ("BUY", 1.10010, 1.09910, 1.10210, 0.8, ())},
           "GBPUSD": {0: ("BUY", 1.30010, 1.29910, 1.30210, 0.8, ())}}
    res, _ = sim_case(db, dec, {"EURUSD": eu, "GBPUSD": gu})
    assert [t["symbol"] for t in res["trades"]] == ["EURUSD"]
    assert res["counts"]["blocked_context"].get("Expunere") == 1
    res2, _ = sim_case(db, dec, {"EURUSD": eu, "GBPUSD": gu}, risk={"max_same_currency_direction": 2})
    assert sorted(t["symbol"] for t in res2["trades"]) == ["EURUSD", "GBPUSD"]
    # max open positions like live
    res3, _ = sim_case(db, dec, {"EURUSD": eu, "GBPUSD": gu},
                       risk={"max_same_currency_direction": 2, "max_open_positions": 1})
    assert len(res3["trades"]) == 1 and res3["counts"]["risk_rejected"]["max_open_positions"] == 1


def test_min_lot_rejected_like_live_unless_r_only(db):
    bars = flat_bars(6)
    dec = {"EURUSD": {0: ("BUY", 1.10010, 1.09910, 1.10210, 0.8, ())}}
    res, _ = sim_case(db, dec, {"EURUSD": bars}, initial_equity=50, risk_pct=0.25)
    assert res["trades"] == [] and res["counts"]["risk_rejected"]["volume_ok"] == 1
    res2, _ = sim_case(db, dec, {"EURUSD": bars}, initial_equity=50, risk_pct=0.25, size_mode="r_only")
    assert len(res2["trades"]) == 1 and res2["trades"][0]["volume"] < 0.01


def test_bootstrap_ci_deterministic():
    vals = [random.Random(3).gauss(0.1, 1) for _ in range(5)] + [(-1) ** i * 0.7 for i in range(200)]
    a, b = backtest.bootstrap_ci(vals), backtest.bootstrap_ci(list(vals))
    assert a == b and a[0] < sum(vals) / len(vals) < a[1]
    assert backtest.bootstrap_ci([0.5]) == [None, None]


# ---- full run -----------------------------------------------------------------------------

def run_params(**kw):
    p = {"symbols": ["EURUSD"], "start_ts": T0 + 3 * 86400, "end_ts": T0 + 5 * 86400, "session": None,
         "initial_equity": 10000, "risk_pct": 1.0, "specs": SPEC, "workers": 1, "now_ts": T0 + 30 * 86400,
         "cost": {"mode": "fixed", "fixed_spread_pts": 3},
         "strategy": {"min_score": 4}, "risk": {"max_signals_per_symbol_per_session": 50, "max_trades_per_day": 50,
                                                "max_consecutive_losses": 99, "max_daily_loss_pct": 50}}
    p.update(kw)
    return p


def test_run_shape_and_determinism(db):
    seed_market(db)
    r1 = backtest.run(db, run_params())
    r2 = backtest.run(db, run_params())
    s = r1["summary"]
    for k in ("trades", "win_rate", "avg_r", "expectancy_r", "profit_factor", "max_dd_r", "max_consec_losses",
              "net_usd", "sample_days", "ci90_avg_r"):
        assert k in s
    assert s["trades"] > 0, r1["counts"]
    assert r1["summary"] == r2["summary"] and r1["trades"] == r2["trades"]
    assert set(r1) >= {"summary", "by_symbol", "by_hour", "equity_r", "trades", "warnings"}
    assert r1["equity_r"][-1][1] == pytest.approx(sum(t["r"] for t in r1["trades"]), abs=1e-2)
    assert r1["counts"]["analyses"] > 500


def test_no_lookahead(db):
    m5 = seed_market(db)
    base = backtest.run(db, run_params())
    cut = T0 + 4 * 86400 + 6 * 3600
    # rewrite the future: bars that are not closed at `cut` get wild prices
    fut = [dict(b, o=b["o"] * 1.03, h=b["h"] * 1.05, l=b["l"] * 0.99, c=b["c"] * 0.97) for b in m5 if b["t"] >= cut]
    store(db, "EURUSD", "M5", fut)
    h1 = resample([b for b in m5 if b["t"] < cut] + fut, 3600)
    store(db, "EURUSD", "H1", [b for b in h1 if b["t"] + 3600 > cut])
    changed = backtest.run(db, run_params())
    before = [t for t in base["trades"] if t["exit_t"] < cut]
    assert before, "need trades closed before the cut"
    assert before == [t for t in changed["trades"] if t["exit_t"] < cut]
    assert base["trades"] != changed["trades"]


def test_cost_mode_stats_and_fallback_warning(db):
    seed_market(db)
    p = run_params(cost={"mode": "stats"})
    r_fb = backtest.run(db, p)
    assert any("Statistici de spread lipsă" in w for w in r_fb["warnings"])
    db.executemany("INSERT INTO spread_stats(symbol, hour_utc, n, sum_pts, sum_sq, max_pts) VALUES(?,?,?,?,?,?)",
                   [("EURUSD", h, 10, 10 * 25.0, 0, 30) for h in range(24)])
    r_wide = backtest.run(db, p)
    assert not any("Statistici de spread lipsă" in w for w in r_wide["warnings"])
    assert all(t["spread_pts"] == 25.0 for t in r_wide["trades"])
    if r_wide["trades"] and r_fb["trades"]:
        assert r_wide["summary"]["avg_r"] < r_fb["summary"]["avg_r"] + 0.5


def test_session_filter_and_live_bars_union(db):
    m5 = seed_market(db)
    # live `bars` table rows are merged (archive wins on overlap)
    store(db, "EURUSD", "M5", [dict(b, c=b["c"] + 1) for b in m5[:50]], table="bars")
    got = backtest.load_bars(db, "EURUSD", "M5", T0, T0 + 86400, now_ts=T0 + 30 * 86400)
    assert got[0]["c"] == m5[0]["c"] and len(got) == 288
    r = backtest.run(db, run_params(session={"start": "06:00", "end": "18:00", "tz": "UTC",
                                             "days": ["MON", "TUE", "WED", "THU", "FRI"]}))
    assert r["trades"] and all(6 <= int(t["signal_t"] % 86400 // 3600) <= 18 for t in r["trades"])


def test_parallel_workers_match_sequential(db, monkeypatch):
    seed_market(db)
    seq = backtest.run(db, run_params())
    monkeypatch.setattr(backtest, "CHUNK", 150)
    par = backtest.run(db, run_params(workers=2))
    assert not any("paralelă" in w for w in par["warnings"]), par["warnings"]
    assert par["timing"]["workers"] == 2
    assert par["trades"] == seq["trades"] and par["counts"] == seq["counts"]
