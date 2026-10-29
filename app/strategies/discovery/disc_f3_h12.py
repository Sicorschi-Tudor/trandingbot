"""disc-f3-h12: disc-h12 (weekly cross-sectional currency-strength momentum, D1) made runnable through
research/run_bt.py (Stage F, family F3).

disc-h12 reads the other pairs only through the in-process hook _disc3.set_universe(); the runner never calls it,
so run through run_bt.py the original plugin sees a one-pair universe and degenerates to per-pair momentum (the
pair always "wins" its own strength ranking). This wrapper loads the D1 universe from cfg['xs_db'] (a read-only
SQLite file with bars_archive / bars, the 12 research symbols or cfg['xs_symbols']) into that hook, once per
process, and delegates everything else (PARAMS, RUN_DEFAULTS, default_exit, analyze) to disc_h12.

No lookahead: disc_h12.strengths() keeps only the universe bars closed at ctx.now_ts (_closed: t + 1 day <= now),
so the whole history may sit in the hook. Research only (auto-discovered, never offered to the live engine)."""
from __future__ import annotations

import sqlite3

from . import _disc3 as D
from . import disc_h12 as M

NAME = "disc-f3-h12"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare (F3): disc-h12 — momentum transversal pe puterea monedelor (D1, rebalansare vinerea) — cu "
                  "universul celor 12 instrumente încărcat din cfg.xs_db, ca să poată rula prin research/run_bt.py.")
RESEARCH_SYMBOLS = ["AUDJPY", "AUDUSD", "EURCHF", "EURGBP", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "USDCAD", "USDCHF",
                    "USDJPY", "XAUUSD"]
PARAMS = {**M.PARAMS, "xs_db": None, "xs_symbols": None}
RUN_DEFAULTS = dict(M.RUN_DEFAULTS)

_LOADED: dict = {"key": None}


def default_exit(cfg: dict) -> dict:
    return M.default_exit(cfg)


def _symbols(cfg: dict) -> list[str]:
    raw = cfg.get("xs_symbols")
    if not raw:
        return list(RESEARCH_SYMBOLS)
    if isinstance(raw, str):
        raw = raw.split(",")
    return [str(s).strip().upper() for s in raw if str(s).strip()]


def load_universe(path: str, symbols: list[str], tf: str = "D1") -> dict[str, list[dict]]:
    """{symbol: D1 bars} from a read-only SQLite file (bars_archive first, then bars; rows with o=NULL skipped)."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        out: dict[str, list[dict]] = {}
        for s in symbols:
            m: dict[int, dict] = {}
            for table in ("bars_archive", "bars"):
                if table not in tables:
                    continue
                rows = con.execute(f"SELECT t,o,h,l,c FROM {table} WHERE symbol=? AND tf=? ORDER BY t", (s, tf))
                for t, o, h, l, c in rows:
                    if o is None or int(t) in m:
                        continue
                    m[int(t)] = {"t": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c)}
            out[s] = [m[t] for t in sorted(m)]
    finally:
        con.close()
    return out


def ensure_universe(cfg: dict) -> None:
    path = cfg.get("xs_db")
    if not path:
        return  # no DB given: whatever the hook holds (tests / research scripts) is used
    key = (str(path), tuple(_symbols(cfg)))
    if _LOADED["key"] == key and D.universe():
        return
    D.set_universe(load_universe(str(path), list(key[1])))
    _LOADED["key"] = key


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    ensure_universe(cfg)
    return M.analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
