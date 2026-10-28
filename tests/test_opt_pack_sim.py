"""Live-parity of the optimisation pack in the simulators (labeler.SimPosition, app.exits, app.backtest phase 2):
RESCUE exit, smart break-even lock (be_lock_r), loss cooldown, rollover blackout, pre-AI arbitration order."""
from datetime import datetime, timezone

import pytest

from app import backtest, exits, labeler, risk as risk_mod
from app.db import DB
from app.exits import ExitPolicy
from app.labeler import SimPosition
from tests.conftest import make_settings

EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars_archive (symbol TEXT, tf TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, tv REAL,
    sp REAL, PRIMARY KEY(symbol, tf, t));
CREATE TABLE IF NOT EXISTS spread_stats (symbol TEXT, hour_utc INTEGER, n INTEGER, sum_pts REAL, sum_sq REAL,
    max_pts REAL, PRIMARY KEY(symbol, hour_utc));
"""
T0 = 1_790_000_000 // 86400 * 86400  # Monday 2026-09-21 00:00 UTC (New York on EDT: rollover 21:00 UTC)
TW = int(datetime(2026, 12, 7, tzinfo=timezone.utc).timestamp())  # Monday, New York on EST: rollover 22:00 UTC
PT = 0.00001
SPEC = {s: {"point": PT, "digits": 5, "tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
        for s in ("EURUSD", "GBPUSD")}
NOCOST = {"slippage_pts": 0, "commission_per_lot_usd": 0}
BUY_EU = ("BUY", 1.10010, 1.09910, 1.10210, 0.8, ())


@pytest.fixture
def db(tmp_path):
    make_settings(tmp_path)
    d = DB(tmp_path / "bt.db")
    d.conn.executescript(EXTRA_SCHEMA)
    yield d
    d.close()


def flat_bars(n, px=1.10000, t0=T0):
    return [{"t": t0 + i * 300, "o": px, "h": px + 0.00005, "l": px - 0.00005, "c": px, "sp": 0} for i in range(n)]


def sim(db, decisions, bars_by_sym, t0=T0, aligned=None, **params):
    base = {"symbols": list(bars_by_sym), "start_ts": t0, "end_ts": t0 + 86400, "session": None,
            "initial_equity": 10000, "risk_pct": 1.0, "specs": SPEC, "now_ts": t0 + 10 * 86400,
            "cost": {"mode": "fixed", "fixed_spread_pts": 10, "commission_per_lot_usd": 7, "slippage_pts": 1},
            "risk": {"max_open_positions": 5, "max_total_open_risk_pct": 50, "max_same_currency_direction": 1,
                     "max_daily_loss_pct": 50}}
    for k, v in params.items():
        base[k] = {**base[k], **v} if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    cfg = backtest.resolve(db, base)
    sess = backtest.Session(None, "UTC", t0, t0 + 86400)
    spreads = {s: [cfg["cost"]["fixed_spread_pts"]] * len(b) for s, b in bars_by_sym.items()}
    specs = {s: backtest.symbol_spec(s, b[0]["c"], SPEC) for s, b in bars_by_sym.items()}
    scans = {s: {"symbol": s, "decisions": decisions.get(s, {}), "n": 0, "aligned": (aligned or {}).get(s, {})}
             for s in bars_by_sym}
    return backtest._simulate(cfg, bars_by_sym, spreads, specs, scans, sess,
                              backtest._Prices(bars_by_sym, 300), []), cfg


# ---- config defaults -----------------------------------------------------------------------------------

def test_pack_defaults_resolved_from_live_settings(db):
    cfg = backtest.resolve(db, {"symbols": ["EURUSD"], "start_ts": T0, "end_ts": T0 + 86400, "now_ts": T0 + 86400})
    st, rk = cfg["strategy"], cfg["risk"]
    assert st["rescue_enabled"] is True and st["be_lock_r"] == pytest.approx(0.1)
    assert (st["rescue_minutes"], st["rescue_mae_r"], st["rescue_neg_frac"], st["rescue_close_r"]) == (30, 0.3, 0.6, 0.1)
    assert rk["cooldown_after_loss_minutes"] == 30 and rk["rollover_blackout_minutes"] == 20
    # rescue = overlay on the run's exit policy; the policy itself is reported as given
    assert cfg["exit"].is_base and cfg["live_rescue"] == {"rescue_minutes": 30.0, "rescue_mae_r": 0.3,
                                                          "rescue_neg_frac": 0.6, "rescue_close_r": 0.1}
    off = backtest.resolve(db, {"symbols": ["EURUSD"], "start_ts": T0, "end_ts": T0 + 86400, "now_ts": T0 + 86400,
                                "strategy": {"rescue_enabled": False}})
    assert off["live_rescue"] is None
    # research plugins with backtest-only RUN_DEFAULTS / their own exit are left alone
    fade = backtest.resolve(db, {"symbols": ["EURUSD"], "start_ts": T0, "end_ts": T0 + 86400, "now_ts": T0 + 86400,
                                 "strategy_name": "fade-v1"})
    assert fade["live_rescue"] is None


# ---- 1. RESCUE exit ---------------------------------------------------------------------------------------

def test_with_rescue_combines_with_base_exit():
    live = {"rescue_enabled": True, "rescue_minutes": 30, "rescue_mae_r": 0.3, "rescue_neg_frac": 0.6,
            "rescue_close_r": 0.1}
    p = exits.with_rescue(ExitPolicy(), live)
    assert p.modes == ("rescue",) and p.p["rescue_mae_r"] == 0.3 and p.p["rescue_close_r"] == 0.1
    c = exits.with_rescue(ExitPolicy("trailing", {"trail_start_r": 2.0}), live)
    assert c.mode == "combo" and c.modes == ("trailing", "rescue") and c.p["trail_start_r"] == 2.0
    assert exits.with_rescue(ExitPolicy("combo", {"modes": ["stall"]}), live).modes == ("stall", "rescue")
    held = exits.with_rescue(ExitPolicy("base", {"max_hold_hours": 2}), live)
    assert held.hold_minutes == 120 and "rescue" in held.modes
    explicit = ExitPolicy("rescue", {"rescue_minutes": 99})
    assert exits.with_rescue(explicit, live) is explicit
    assert exits.with_rescue(ExitPolicy(), {"rescue_enabled": False}).is_base
    assert exits.with_rescue(ExitPolicy(), {}).is_base
    # missing keys -> the live defaults, not the research defaults
    assert exits.with_rescue(ExitPolicy(), {"rescue_enabled": True}).p == exits.LIVE_RESCUE_DEFAULTS


def _under_water_then_up(n_under=6):
    bars = flat_bars(20)
    for k in range(1, 1 + n_under):  # BUY entry 1.10010 (bar 1 open + spread): lows at -0.6R, closes at -0.4R
        bars[k] = {"t": T0 + k * 300, "o": 1.1 if k == 1 else 1.0998, "h": 1.1, "l": 1.0995, "c": 1.0997, "sp": 0}
    k = 1 + n_under
    bars[k] = {"t": T0 + k * 300, "o": 1.0998, "h": 1.1003, "l": 1.0997, "c": 1.1002, "sp": 0}
    return bars


def test_backtest_rescue_closes_at_small_profit_like_live(db):
    bars = _under_water_then_up()
    (res, cfg) = sim(db, {"EURUSD": {0: BUY_EU}}, {"EURUSD": bars}, cost=NOCOST, strategy={"be_lock_r": 0})
    (t,) = res["trades"]
    assert t["outcome"] == "RESCUE" and t["gross_r"] == pytest.approx(0.1, abs=1e-6)
    assert t["exit"] == pytest.approx(1.10020) and t["exit_t"] == T0 + 7 * 300  # intrabar: stamped with the bar open
    # parity: the same position stepped by hand with the live rescue params
    pos = SimPosition("BUY", 1.10010, 1.09910, 1.10210, PT, T0 + 300, be_at_r=1.0)
    exits.attach(pos, exits.with_rescue(ExitPolicy(), {"rescue_enabled": True}), history=[], bar_sec=300)
    out = None
    for b in bars[1:]:
        out = pos.step(b, 10 * PT)
        if out:
            break
    assert out[0] == "RESCUE" and out[1] == pytest.approx(t["exit"])
    # disabled -> no RESCUE (old behaviour)
    (res2, _) = sim(db, {"EURUSD": {0: BUY_EU}}, {"EURUSD": bars}, cost=NOCOST,
                    strategy={"rescue_enabled": False, "be_lock_r": 0})
    assert all(x["outcome"] != "RESCUE" for x in res2["trades"])


def test_backtest_rescue_not_armed_before_rescue_minutes(db):
    bars = _under_water_then_up(n_under=4)  # 20 min under water < 30
    (res, _) = sim(db, {"EURUSD": {0: BUY_EU}}, {"EURUSD": bars}, cost=NOCOST)
    assert all(x["outcome"] != "RESCUE" for x in res["trades"])
    (res2, _) = sim(db, {"EURUSD": {0: BUY_EU}}, {"EURUSD": bars}, cost=NOCOST, strategy={"rescue_minutes": 20})
    assert [x["outcome"] for x in res2["trades"]] == ["RESCUE"]


def test_rescue_combines_with_explicit_research_exit(db):
    bars = _under_water_then_up()
    (res, cfg) = sim(db, {"EURUSD": {0: BUY_EU}}, {"EURUSD": bars}, cost=NOCOST,
                     exit={"mode": "trailing", "params": {"trail_start_r": 5}})
    assert cfg["exit"].modes == ("trailing",) and [x["outcome"] for x in res["trades"]] == ["RESCUE"]


# ---- 2. smart BE lock ------------------------------------------------------------------------------------

def test_be_lock_level_rule():
    # be_lock_r = 0: old entry ± 2 points exactly
    assert labeler.be_lock_level(1.1, 1, PT, 0.001, 0.0002, 0.0) == pytest.approx(1.1 + 2 * PT)
    assert labeler.be_lock_level(1.1, -1, PT, 0.001, 0.0002, 0) == pytest.approx(1.1 - 2 * PT)
    # spread + 3 points dominates
    assert labeler.be_lock_level(1.1, 1, PT, 0.001, 0.0002, 0.1) == pytest.approx(1.1 + 0.00023)
    # be_lock_r × initial risk dominates
    assert labeler.be_lock_level(1.1, -1, PT, 0.004, 0.0002, 0.1) == pytest.approx(1.1 - 0.0004)


@pytest.mark.parametrize("be_lock_r,gross", [(0, 0.02), (0.1, 0.13), (0.2, 0.2)])
def test_backtest_break_even_lock(db, be_lock_r, gross):
    bars = flat_bars(6)
    bars[1] = {"t": T0 + 300, "o": 1.1, "h": 1.10115, "l": 1.09995, "c": 1.1011, "sp": 0}  # +1R (bid) reached
    bars[2] = {"t": T0 + 600, "o": 1.1011, "h": 1.1012, "l": 1.0990, "c": 1.0992, "sp": 0}  # falls back
    (res, _) = sim(db, {"EURUSD": {0: BUY_EU}}, {"EURUSD": bars}, cost=NOCOST, strategy={"be_lock_r": be_lock_r})
    (t,) = res["trades"]
    assert t["outcome"] == "BE" and t["gross_r"] == pytest.approx(gross, abs=1e-6)


def _be_bars():
    return [{"t": 0, "o": 1.1, "h": 1.10105, "l": 1.0999, "c": 1.1008},   # +1.05R: BE triggers
            {"t": 300, "o": 1.1008, "h": 1.1009, "l": 1.1000, "c": 1.1001}]


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("be_lock_r", [0.0, 0.1, 0.5])
def test_be_lock_parity_base_path_and_exit_policies(side, be_lock_r):
    """labeler base path == exits._try_be (any non-base policy) for the same bars."""
    sg = 1 if side == "BUY" else -1
    bars = _be_bars() if sg > 0 else [{"t": b["t"], "o": 2.2 - b["o"], "h": 2.2 - b["l"], "l": 2.2 - b["h"],
                                       "c": 2.2 - b["c"]} for b in _be_bars()]
    entry = 1.1
    sl, tp = entry - sg * 0.001, entry + sg * 0.003

    def run(policy):
        pos = SimPosition(side, entry, sl, tp, PT, 0, be_at_r=1.0, be_lock_r=be_lock_r)
        if policy is not None:
            exits.attach(pos, policy, history=[], bar_sec=300)
        pos.step(bars[0], 5 * PT)
        return pos

    base = run(None)
    far = ExitPolicy("rescue", {"rescue_minutes": 10 ** 6})  # never arms: only the BE logic is exercised
    other = run(far)
    assert base.be_done and other.be_done
    assert base.sl == pytest.approx(other.sl)
    assert base.sl == pytest.approx(labeler.be_lock_level(entry, sg, PT, 0.001, 5 * PT, be_lock_r))


def test_be_lock_only_tightens_and_needs_room():
    # SL already beyond the lock: nothing moves, BE counts as done
    pos = SimPosition("BUY", 1.1, 1.0990, 1.103, PT, 0, be_at_r=1.0, be_lock_r=0.1)
    pos.sl = 1.1005
    pos.step({"t": 0, "o": 1.1, "h": 1.1011, "l": 1.1006, "c": 1.101}, 5 * PT)
    assert pos.be_done and pos.sl == pytest.approx(1.1005)
    # lock at +0.5R but the bar only reached +1R with a 60-point spread: not enough room -> retried later
    pos = SimPosition("BUY", 1.1, 1.0990, 1.103, PT, 0, be_at_r=1.0, be_lock_r=0.5)
    pos.step({"t": 0, "o": 1.1, "h": 1.1010, "l": 1.0999, "c": 1.1009}, 60 * PT)
    assert not pos.be_done and pos.sl == pytest.approx(1.0990)


def test_labeler_simulate_and_label_cfg_pass_be_lock_r():
    bars = [{"t": 0, "o": 1.1, "h": 1.1, "l": 1.1, "c": 1.1, "sp": 0}] + [
        {"t": 300, "o": 1.1, "h": 1.10105, "l": 1.0999, "c": 1.1008, "sp": 0},
        {"t": 600, "o": 1.1008, "h": 1.1009, "l": 1.0990, "c": 1.0992, "sp": 0}]
    cfg = {"break_even_at_r": 1.0, "_bar_sec": 300, "max_hold_minutes": 60}
    old = labeler.simulate("BUY", 1.0990, 1.1030, bars, PT, 300, cfg, 10 ** 6)
    new = labeler.simulate("BUY", 1.0990, 1.1030, bars, PT, 300, {**cfg, "be_lock_r": 0.2}, 10 ** 6)
    assert old["outcome"] == "BE" and old["outcome_r"] == pytest.approx(0.02)
    assert new["outcome"] == "BE" and new["outcome_r"] == pytest.approx(0.2)


# ---- 3. loss cooldown --------------------------------------------------------------------------------------

def _sl_then_flat():
    bars = flat_bars(30)
    bars[1] = {"t": T0 + 300, "o": 1.1, "h": 1.1001, "l": 1.0985, "c": 1.099, "sp": 0}  # SL of the first BUY
    return bars


def test_cooldown_after_loss_blocks_symbol_like_live(db):
    bars = _sl_then_flat()
    # loss inside bar 1 (closed by T0+600); 30 min -> first decision allowed at T0+2400 = close of bar 7
    (blocked, _) = sim(db, {"EURUSD": {0: BUY_EU, 6: BUY_EU}}, {"EURUSD": bars})
    assert [t["outcome"] for t in blocked["trades"]] == ["SL"]
    assert blocked["counts"]["blocked_context"] == {"Pauză": 1}
    (ok, _) = sim(db, {"EURUSD": {0: BUY_EU, 7: BUY_EU}}, {"EURUSD": bars})
    assert len(ok["trades"]) == 2 and ok["trades"][1]["signal_t"] == T0 + 2400
    (off, _) = sim(db, {"EURUSD": {0: BUY_EU, 6: BUY_EU}}, {"EURUSD": bars}, risk={"cooldown_after_loss_minutes": 0})
    assert len(off["trades"]) == 2


def test_cooldown_only_after_a_net_loss_and_only_on_that_symbol(db):
    win = flat_bars(30)
    win[1] = {"t": T0 + 300, "o": 1.1, "h": 1.1025, "l": 1.0999, "c": 1.102, "sp": 0}  # TP
    (res, _) = sim(db, {"EURUSD": {0: BUY_EU, 6: BUY_EU}}, {"EURUSD": win})
    assert [t["outcome"] for t in res["trades"]][0] == "TP" and len(res["trades"]) == 2
    gu = flat_bars(30, 1.3)
    (res2, _) = sim(db, {"EURUSD": {0: BUY_EU}, "GBPUSD": {6: ("BUY", 1.30010, 1.29910, 1.30210, 0.8, ())}},
                    {"EURUSD": _sl_then_flat(), "GBPUSD": gu})
    assert sorted(t["symbol"] for t in res2["trades"]) == ["EURUSD", "GBPUSD"]


# ---- 5. rollover blackout -----------------------------------------------------------------------------------

@pytest.mark.parametrize("t0,roll_utc_h", [(T0, 21), (TW, 22)])
def test_rollover_blackout_dst_safe(db, t0, roll_utc_h):
    bars = flat_bars(300, t0=t0)
    roll = t0 + roll_utc_h * 3600
    dec = lambda T: {(T - t0) // 300 - 1: BUY_EU}  # noqa: E731  decision at the close T of a bar
    for T, blocked in ((roll - 1200, True), (roll - 1500, False), (roll, True), (roll + 1200, True),
                       (roll + 1500, False)):
        (res, _) = sim(db, {"EURUSD": dec(T)}, {"EURUSD": bars}, t0=t0)
        assert (res["counts"]["blocked_context"] == {"Rollover": 1}) is blocked, (T - roll) / 60
        assert (res["counts"]["signals"] == 0) is blocked
        assert (risk_mod.rollover_blocker(T, 20) is not None) is blocked  # the live rule, same verdict
    (off, _) = sim(db, {"EURUSD": dec(roll)}, {"EURUSD": bars}, t0=t0, risk={"rollover_blackout_minutes": 0})
    assert off["counts"]["signals"] == 1


# ---- 4. pre-AI arbitration order ------------------------------------------------------------------------------

EU_N = ("BUY", 1.10010, 1.09910, 1.10210, 0.8, ())   # SL 100 points
GU_N = ("BUY", 1.30010, 1.29910, 1.30210, 0.8, ())   # SL 100 points
GU_W = ("BUY", 1.30010, 1.29810, 1.30410, 0.8, ())   # SL 200 points: cheaper spread/SL ratio


def _two(db, eu, gu, aligned=None):
    bars = {"EURUSD": flat_bars(6), "GBPUSD": flat_bars(6, 1.3)}
    (res, _) = sim(db, {"EURUSD": {0: eu}, "GBPUSD": {0: gu}}, bars, aligned=aligned)
    assert res["counts"]["blocked_context"] == {"Expunere": 1}  # USD short twice: only one admitted
    return [t["symbol"] for t in res["trades"]]


def test_arbitration_order_htf_aligned_then_cost_ratio(db):
    assert _two(db, EU_N, GU_N) == ["EURUSD"]  # tie: symbol order (as before)
    assert _two(db, EU_N, GU_W) == ["GBPUSD"]  # lower spread / SL-distance ratio first
    assert _two(db, EU_N, GU_N, aligned={"GBPUSD": {0: 1}}) == ["GBPUSD"]  # HTF-aligned first
    assert _two(db, EU_N, GU_W, aligned={"EURUSD": {0: 1}}) == ["EURUSD"]  # alignment beats a cheaper ratio


def test_arbitration_helpers():
    assert backtest.htf_aligned("BUY", {"indicators": {"htf_trend": "UP"}}) == 1
    assert backtest.htf_aligned("SELL", {"indicators": {"htf_trend": "DOWN"}}) == 1
    assert backtest.htf_aligned("BUY", {"indicators": {"htf_trend": "FLAT"}}) == 0
    assert backtest.htf_aligned("SELL", {"indicators": {}}) == 0
    keys = [backtest.arbitration_key(0, 10 * PT, 1.1, 1.099), backtest.arbitration_key(1, 30 * PT, 1.1, 1.099),
            backtest.arbitration_key(0, 10 * PT, 1.1, 1.098), backtest.arbitration_key(1, 0.0, 1.1, 1.1)]
    assert sorted(range(4), key=lambda k: keys[k]) == [1, 3, 2, 0]


def test_pack_keys_do_not_invalidate_phase1_cache(db, monkeypatch):
    calls = {"n": 0}
    real = backtest.scan_symbol

    def counting(job):
        calls["n"] += 1
        return real(job)
    monkeypatch.setattr(backtest, "_run_scans", lambda jobs, w, warn, prog=None: [counting(j) for j in jobs])
    for k in range(400):
        px = 1.1 + 0.0005 * ((k % 40) - 20) / 20
        db.execute("INSERT INTO bars_archive(symbol, tf, t, o, h, l, c, tv, sp) VALUES(?,?,?,?,?,?,?,?,?)",
                   ("EURUSD", "M5", T0 + k * 300, px, px + 0.0002, px - 0.0002, px, 1, 5))
    cache: dict = {}
    p = {"symbols": ["EURUSD"], "start_ts": T0 + 150 * 300, "end_ts": T0 + 400 * 300, "now_ts": T0 + 2 * 86400,
         "session": None, "specs": SPEC, "workers": 1, "htf_tf": "M5"}
    backtest.run_cached(db, p, cache)
    n1 = calls["n"]
    assert n1 > 0
    for extra in ({"strategy": {"be_lock_r": 0, "rescue_enabled": False, "rescue_minutes": 45}},
                  {"risk": {"cooldown_after_loss_minutes": 0, "rollover_blackout_minutes": 5}}):
        r = backtest.run_cached(db, {**p, **extra}, cache)
        assert r["timing"]["phase1_cached"] and calls["n"] == n1
