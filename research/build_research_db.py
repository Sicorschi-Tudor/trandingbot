"""Build a research database (bars_archive + spread_stats) from public daily/intraday CSV history.

Source: github.com/ejtraderLabs/historical-data — 12 symbols (AUDJPY AUDUSD EURCHF EURGBP EURJPY EURUSD GBPJPY GBPUSD
USDCAD USDCHF USDJPY XAUUSD), M15/M30/H1/H4/D1, Nov 2012 → Mar 2022, broker server time of a "New York close"
broker (week opens Monday 00:00 server = Sunday 22:00 UTC; GMT+2 in winter / GMT+3 in summer, the project's `NY` rule),
prices as integers in points (EURUSD 130583 = 1.30583, USDJPY 82173 = 82.173, XAUUSD 172497 = 1724.97).

Output: a SQLite file with the live schema's `bars_archive` (symbol, tf, t = UTC bar open, o/h/l/c, tv, sp) and
`spread_stats` (synthetic but realistic per-hour spreads, see SPREAD_PTS), so `python -m app.backtest`, the strategy
plugins and `python -m app.ml train` run on it unchanged. The CSVs carry no spread: `sp` is the modelled spread of the
bar's UTC hour (used by cost.mode 'bar'); cost.mode 'stats' reads spread_stats; both come from the same table.

    python research/build_research_db.py --src /path/to/historical-data --out data/research.db [--account standard|ecn]

Nothing here touches the live bot or data/bot.db.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402  (same tables as the live DB)

NY = ZoneInfo("America/New_York")
TF_MAP = {"m15": "M15", "m30": "M30", "h1": "H1", "h4": "H4", "d1": "D1"}
DIGITS = {"AUDJPY": 3, "EURJPY": 3, "GBPJPY": 3, "USDJPY": 3, "XAUUSD": 2}  # others 5
# typical average spreads in POINTS (standard account, no commission) during London/NY hours; ECN raw ≈ 1/5 + commission
SPREAD_PTS = {"EURUSD": 11, "GBPUSD": 16, "USDJPY": 12, "USDCHF": 18, "USDCAD": 20, "AUDUSD": 14, "EURGBP": 18,
              "EURJPY": 20, "EURCHF": 24, "GBPJPY": 32, "AUDJPY": 26, "XAUUSD": 30}
ECN_PTS = {"EURUSD": 2, "GBPUSD": 4, "USDJPY": 3, "USDCHF": 5, "USDCAD": 5, "AUDUSD": 3, "EURGBP": 5,
           "EURJPY": 6, "EURCHF": 8, "GBPJPY": 10, "AUDJPY": 8, "XAUUSD": 12}
# hour-of-day (UTC) multipliers: wide at the 21-23 UTC rollover, wider in Asia, tight London/NY
HOUR_MULT = {**{h: 1.0 for h in range(24)}, 21: 2.2, 22: 2.6, 23: 1.9, 0: 1.5, 1: 1.4, 2: 1.3, 3: 1.3, 4: 1.3, 5: 1.2, 6: 1.1,
             7: 1.0, 8: 0.95, 9: 0.9, 10: 0.9, 11: 0.9, 12: 0.9, 13: 0.9, 14: 0.9, 15: 0.9, 16: 0.95, 17: 1.0, 18: 1.05,
             19: 1.1, 20: 1.3}


def server_to_utc(dt_server: datetime) -> int:
    """NY-close broker: server time = New York local time + 7 h (DST follows New York)."""
    # the server clock is NY + 7h; find the NY instant whose (local + 7h) equals dt_server
    guess = dt_server - timedelta(hours=7)
    ny = guess.replace(tzinfo=NY)
    utc = ny.astimezone(timezone.utc)
    # correct DST ambiguity: recompute from the UTC instant
    back = utc.astimezone(NY).replace(tzinfo=None) + timedelta(hours=7)
    if back != dt_server:
        utc = utc + (dt_server - back)
    return int(utc.timestamp())


def spread_for(symbol: str, hour_utc: int, account: str) -> float:
    base = (ECN_PTS if account == "ecn" else SPREAD_PTS)[symbol]
    return round(base * HOUR_MULT[hour_utc], 1)


def load_symbol(conn: sqlite3.Connection, src: Path, symbol: str, account: str) -> dict:
    digits = DIGITS.get(symbol, 5)
    scale = 10 ** digits
    counts = {}
    for tf_file, tf in TF_MAP.items():
        f = src / symbol / f"{symbol}{tf_file}.csv"
        if not f.exists():
            continue
        rows = []
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh):
                d = r["Date"]
                dt_server = datetime.strptime(d, "%Y-%m-%d %H:%M:%S" if len(d) > 10 else "%Y-%m-%d")
                t = server_to_utc(dt_server)
                o, h, l, c = (round(float(r[k]) / scale, digits) for k in ("open", "high", "low", "close"))
                if not (l <= min(o, c) and h >= max(o, c)) or o <= 0:
                    continue
                hour = datetime.fromtimestamp(t, timezone.utc).hour
                rows.append((symbol, tf, t, o, h, l, c, float(r.get("tick_volume") or 0), spread_for(symbol, hour, account)))
        conn.executemany("INSERT OR IGNORE INTO bars_archive(symbol, tf, t, o, h, l, c, tv, sp) VALUES(?,?,?,?,?,?,?,?,?)", rows)
        counts[tf] = len(rows)
    for hour in range(24):
        sp = spread_for(symbol, hour, account)
        n = 1000
        conn.execute("INSERT OR REPLACE INTO spread_stats(symbol, hour_utc, n, sum_pts, sum_sq, max_pts) VALUES(?,?,?,?,?,?)",
                     (symbol, hour, n, sp * n, sp * sp * n, sp * 2))
    return counts


def specs(symbols: list[str]) -> dict:
    out = {}
    for s in symbols:
        d = DIGITS.get(s, 5)
        point = 10 ** -d
        out[s] = {"point": point, "digits": d, "tick_size": point, "volume_min": 0.01, "volume_step": 0.01,
                  "volume_max": 100.0, "stops_level": 0,
                  "contract_size": 100 if s.startswith("XAU") else 100000}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", default=str(ROOT / "data" / "research.db"))
    ap.add_argument("--account", choices=("standard", "ecn"), default="standard")
    ap.add_argument("--symbols", default=",".join(sorted(SPREAD_PTS)))
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    conn = sqlite3.connect(str(out))
    conn.executescript(SCHEMA)
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    summary = {}
    for s in syms:
        summary[s] = load_symbol(conn, src, s, a.account)
        conn.commit()
        print(s, summary[s], flush=True)
    conn.execute("INSERT OR REPLACE INTO settings(key, value_json) VALUES('research_specs', ?)", (json.dumps(specs(syms)),))
    conn.execute("INSERT OR REPLACE INTO settings(key, value_json) VALUES('research_source', ?)",
                 (json.dumps({"source": "ejtraderLabs/historical-data", "server_tz": "NY", "account": a.account,
                              "spread_model": {"base_pts": ECN_PTS if a.account == "ecn" else SPREAD_PTS, "hour_mult": HOUR_MULT}}),))
    conn.commit()
    (out.parent / "research_specs.json").write_text(json.dumps(specs(syms), indent=1))
    r = conn.execute("SELECT tf, COUNT(*), MIN(t), MAX(t) FROM bars_archive GROUP BY tf").fetchall()
    for tf, n, t0, t1 in r:
        print(f"{tf}: {n} bars  {datetime.fromtimestamp(t0, timezone.utc):%Y-%m-%d} → {datetime.fromtimestamp(t1, timezone.utc):%Y-%m-%d}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
