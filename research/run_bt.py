"""Research runner: app.backtest.run on a research DB with FIXED UTC periods (pre-registered splits), symbol specs
from data/research_specs.json and the research defaults (no session, R-only sizing, portfolio risk caps).

    python research/run_bt.py --db data/research.db --strategy tsmom-v1 --tf D1 --htf NONE --split train \
        --strategy-json '{"lookback": 126}' --risk-json '{"max_open_positions": 12}' --json-out research/results/x.json

Splits (UTC, bar open time in [start, end)):
    train  2013-06-01 → 2019-01-01   (warm-up bars before 2013-06 are loaded by the backtest as needed)
    valid  2019-01-01 → 2021-01-01
    test   2021-01-01 → 2022-03-04   (ONE evaluation per pre-registered config — see docs/research/STAGE_F_PROTOCOL.md)
    all    2013-06-01 → 2022-03-04   (diagnostics only, never for decisions)

D1 strategies: the live-parity rollover blackout (±20 min around 17:00 New York) would block EVERY D1 decision
(D1 bars close at the rollover), so for signal_tf D1 the runner sets rollover_blackout_minutes=0 and
cooldown_after_loss_minutes=0 unless --risk-json sets them. Portfolio research (several symbols held at once) needs
max_open_positions / max_trades_per_day / max_same_currency_direction raised explicitly (--risk-json).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import backtest as bt  # noqa: E402

SPLITS = {"train": ("2013-06-01", "2019-01-01"), "valid": ("2019-01-01", "2021-01-01"),
          "test": ("2021-01-01", "2022-03-04"), "all": ("2013-06-01", "2022-03-04")}
NOW_TS = "2022-03-10"
ALL_SYMBOLS = ["AUDJPY", "AUDUSD", "EURCHF", "EURGBP", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "USDCAD", "USDCHF",
               "USDJPY", "XAUUSD"]


def ts(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def build_params(a) -> dict:
    specs = json.load(open(a.specs))
    start, end = SPLITS[a.split] if a.split else (a.start, a.end)
    strat = json.loads(a.strategy_json or "{}")
    risk = json.loads(a.risk_json or "{}")
    if a.tf.upper() == "D1":
        risk.setdefault("rollover_blackout_minutes", 0)
        risk.setdefault("cooldown_after_loss_minutes", 0)
    cost = {"mode": a.cost, "commission_per_lot_usd": a.commission, "slippage_pts": a.slippage}
    if a.swap_mode:
        cost["swap_mode"] = a.swap_mode
    if a.spread is not None:
        cost["fixed_spread_pts"] = a.spread
    p = {"strategy_name": a.strategy, "specs": specs, "size_mode": "r_only", "workers": a.workers, "session": None,
         "symbols": [s.strip().upper() for s in a.symbols.split(",") if s.strip()],
         "signal_tf": a.tf.upper(), "htf_tf": a.htf.upper(), "start_ts": ts(start), "end_ts": ts(end),
         "cost": cost, "now_ts": ts(NOW_TS), "strategy": strat, "risk": risk, "initial_equity": a.equity}
    if a.exit:
        p["exit"] = {"mode": a.exit, "params": json.loads(a.exit_params or "{}")}
    if a.session_json:
        p["session"] = json.loads(a.session_json)
    return p


def compact(r: dict, p: dict, a, elapsed: float) -> dict:
    s = r["summary"]
    years = {}
    for t in r.get("trades") or []:
        y = str(t.get("exit_utc") or t.get("entry_utc") or "")[:4]
        if y:
            years.setdefault(y, []).append(float(t.get("r") or 0))
    by_year = {y: {"n": len(v), "avg_r": round(sum(v) / len(v), 4), "sum_r": round(sum(v), 2)} for y, v in sorted(years.items())}
    return {"strategy": a.strategy, "tf": a.tf.upper(), "htf": a.htf.upper(), "split": a.split or f"{a.start}..{a.end}",
            "db": a.db, "symbols": p["symbols"], "strategy_params": p["strategy"], "risk_params": p["risk"],
            "exit": p.get("exit"), "cost": p["cost"], "elapsed_s": round(elapsed, 1),
            "summary": s, "by_year": by_year, "by_symbol": r.get("by_symbol"), "by_outcome": r.get("by_outcome"),
            "counts": r.get("counts"), "warnings": r.get("warnings")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "research.db"))
    ap.add_argument("--specs", default=str(ROOT / "data" / "research_specs.json"))
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--htf", default="H4")
    ap.add_argument("--symbols", default=",".join(ALL_SYMBOLS))
    ap.add_argument("--split", choices=list(SPLITS), default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--strategy-json", default="")
    ap.add_argument("--risk-json", default="")
    ap.add_argument("--exit", default=None)
    ap.add_argument("--exit-params", default="")
    ap.add_argument("--session-json", default="", help='e.g. {"start":"07:00","end":"17:00","tz":"UTC","days":["MON","TUE","WED","THU","FRI"]}')
    ap.add_argument("--cost", default="stats", choices=["stats", "bar", "fixed"])
    ap.add_argument("--spread", type=float, default=None)
    ap.add_argument("--commission", type=float, default=7.0)
    ap.add_argument("--slippage", type=float, default=1.0)
    ap.add_argument("--swap-mode", default=None)
    ap.add_argument("--equity", type=float, default=10000.0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--allow-test", action="store_true", help="required for --split test (final evaluation only)")
    ap.add_argument("--trades-out", default=None, help="write the trade list (JSONL) here")
    a = ap.parse_args(argv)
    if not a.split and not (a.start and a.end):
        ap.error("--split or --start/--end")
    if (a.split == "test" or (a.start and a.start >= "2021-01-01") or (a.end and a.end > "2021-01-01" and a.split != "all")) and not a.allow_test:
        ap.error("the TEST period (2021-01-01 → 2022-03-04) is reserved for the single final evaluation: pass --allow-test")
    if a.split == "all" and not a.allow_test:
        ap.error("--split all touches the TEST period: pass --allow-test")
    p = build_params(a)
    db = bt.ReadOnlyDB(a.db)
    t0 = time.time()
    try:
        r = bt.run(db, p)
    finally:
        db.close()
    out = compact(r, p, a, time.time() - t0)
    if a.trades_out:
        with open(a.trades_out, "w") as f:
            for t in r.get("trades") or []:
                f.write(json.dumps(t, default=str) + "\n")
    if a.json_out:
        Path(a.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json_out).write_text(json.dumps(out, indent=1, default=str))
    s = out["summary"]
    print(json.dumps({"strategy": a.strategy, "tf": out["tf"], "split": out["split"], "trades": s["trades"],
                      "avg_r": s["avg_r"], "ci90": s.get("ci90_avg_r"), "pf": s["profit_factor"], "max_dd_r": s["max_dd_r"],
                      "net_r": s.get("net_r"), "by_year": {y: v["avg_r"] for y, v in out["by_year"].items()},
                      "elapsed_s": out["elapsed_s"]}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
