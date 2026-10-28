"""Shared plumbing for the discovery builder #2 plugins (disc-H03, disc-H07, disc-H11, disc-H15, disc-H19).

Research only. Cross-sectional plugins (H03, H19) need the closes of the six USD pairs at the decision time; the
backtest hands a plugin only its own symbol, so they read a *panel* (all six pairs on the signal timeframe):
- set_panel(tf, {symbol: [bars]}) puts one in memory (tests, scripts), or
- cfg["panel_db"] (or env DISC_PANEL_DB) = path of a bot database opened READ-ONLY (bars_archive, then bars).
No lookahead: every panel value used for a decision at bar time t comes from panel bars with time <= t (the bar
closing at the same moment as the analysed bar, or earlier). Derived series (USD index, ATR) are causal running
computations, so computing them over the whole panel once and reading position <= t equals computing them on the
truncated panel (pinned by tests/test_discovery_2.py).

Time-based exits that the stock exit policies cannot express in bars (H03 month turn, H07 hold N D1 bars, H15 Friday
19:00 UTC) use the 'signal_flip' exit with flip_on_flat=1: the plugin's bias() returns the trade direction while the
trade should stay open and 0 at the bar whose close is the planned exit."""
from __future__ import annotations

import bisect
import math
import os
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone

from .. import _htf as X

USD_PAIRS: dict[str, int] = {"EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1, "USDJPY": 1, "USDCAD": 1}
MIN_PAIRS = 4  # USD index value at t needs returns of at least 4 of the 6 pairs at t
_TF_SEC = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}

_MEM: dict[str, dict[str, list[dict]]] = {}
_DB: dict[tuple[str, str], dict[str, list[dict]]] = {}
_DERIVED: dict[tuple, object] = {}


def run_defaults(tf: str) -> dict:
    d = dict(X.RUN_DEFAULTS)
    d["signal_timeframe"] = tf
    return d


# ---- panel ------------------------------------------------------------------

def set_panel(tf: str, panel: dict[str, list[dict]]) -> None:
    _MEM[str(tf).upper()] = {s: sorted((dict(b) for b in bars), key=lambda b: b["t"]) for s, bars in panel.items()}
    _DERIVED.clear()


def clear_panel() -> None:
    _MEM.clear()
    _DB.clear()
    _DERIVED.clear()


def _load_db(path: str, tf: str) -> dict[str, list[dict]]:
    sec = _TF_SEC.get(tf, 0)
    now = time.time()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        out = {}
        for sym in USD_PAIRS:
            m: dict[int, dict] = {}
            for table in ("bars_archive", "bars"):
                try:
                    rows = conn.execute(f"SELECT t,o,h,l,c FROM {table} WHERE symbol=? AND tf=? ORDER BY t", (sym, tf))
                    rows = rows.fetchall()
                except sqlite3.OperationalError:
                    continue
                for t, o, h, l, c in rows:
                    t = int(t)
                    if o is None or t in m or t + sec > now:
                        continue
                    m[t] = {"t": t, "o": float(o), "h": float(h), "l": float(l), "c": float(c)}
            out[sym] = [m[t] for t in sorted(m)]
    finally:
        conn.close()
    return out


def get_panel(cfg: dict, tf: str) -> dict[str, list[dict]] | None:
    tf = str(tf).upper()
    if tf in _MEM:
        return _MEM[tf]
    path = cfg.get("panel_db") or os.environ.get("DISC_PANEL_DB")
    if not path:
        return None
    key = (str(path), tf)
    if key not in _DB:
        _DB[key] = _load_db(str(path), tf)
    return _DB[key]


def _memo(key: tuple, fn, owner=None):
    """Memoised fn(); keys carry id(panel), so the entry also keeps the panel itself and is rebuilt when that id
    now belongs to another object (a freed panel's id can be reused)."""
    hit = _DERIVED.get(key)
    if hit is not None and (owner is None or hit[0] is owner):
        return hit[1]
    v = fn()
    _DERIVED[key] = (owner, v)
    return v


def pair_view(panel: dict, sym: str) -> tuple[list[int], list[dict], list]:
    """(times, bars, Wilder ATR14 series) of one panel pair (memoised per panel)."""
    def build():
        bars = panel.get(sym) or []
        return [b["t"] for b in bars], bars, X.atr_series(bars, 14) if bars else []
    return _memo(("pair", id(panel), sym), build, panel)


def usdx(panel: dict) -> tuple[list[int], list[float], list[float]]:
    """(times, r_U, cumulative level): r_U(t) = mean over the USD pairs with a bar at t of the USD-signed log return
    against that pair's previous bar (sign inverted for XXXUSD)."""
    def build():
        acc: dict[int, list[float]] = {}
        for sym, sg in USD_PAIRS.items():
            bars = panel.get(sym) or []
            for a, b in zip(bars, bars[1:]):
                acc.setdefault(b["t"], []).append(sg * X.log_ret(a["c"], b["c"]))
        ts = sorted(t for t, v in acc.items() if len(v) >= MIN_PAIRS)
        r = [math.fsum(acc[t]) / len(acc[t]) for t in ts]
        lv, s = [], 0.0
        for x in r:
            s += x
            lv.append(s)
        return ts, r, lv
    return _memo(("usdx", id(panel)), build, panel)


def idx_at(ts: list[int], t: int) -> int | None:
    k = bisect.bisect_left(ts, t)
    return k if k < len(ts) and ts[k] == t else None


# ---- calendar ----------------------------------------------------------------

def utc(t: float) -> datetime:
    return datetime.fromtimestamp(t, tz=timezone.utc)


def d1_date(t: float) -> date:
    """Trading date of a broker D1 bar (opens 21:00/22:00 UTC the evening before)."""
    return utc(t + 6 * 3600).date()


def weekdays_left_in_month(d: date) -> int:
    """Mon–Fri days strictly after d in d's month (holidays ignored: FX trades on most of them)."""
    n, x = 0, d + timedelta(days=1)
    while x.month == d.month:
        if x.weekday() < 5:
            n += 1
        x += timedelta(days=1)
    return n


def week_id(t: float) -> tuple[int, int]:
    """FX trading week of an intraday bar (the week opens Sunday ~21:00/22:00 UTC -> shifted into Monday)."""
    iso = utc(t + 3 * 3600).isocalendar()
    return iso[0], iso[1]


def sstdev(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = math.fsum(xs) / n
    return math.sqrt(max(0.0, math.fsum((x - m) ** 2 for x in xs) / (n - 1)))
