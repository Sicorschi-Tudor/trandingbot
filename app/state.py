"""In-memory runtime state + EventBus for SSE."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from .config import TF_SECONDS, Settings, iso, redact, utcnow
from .db import DB

log = logging.getLogger("bot")

MAX_BARS = 2000


class EventBus:
    def __init__(self) -> None:
        self.subscribers: set[asyncio.Queue] = set()
        self.seq = 0

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def publish(self, event_type: str, data: Any) -> None:
        if not self.subscribers:
            return
        self.seq += 1
        msg = (self.seq, event_type, data)
        for q in list(self.subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # slow client: drop it, it will reconnect and resync
                self.subscribers.discard(q)
                while True:
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                try:
                    q.put_nowait(None)
                except Exception:
                    pass


def format_sse(seq: int, event_type: str, data: Any) -> str:
    return f"id: {seq}\nevent: {event_type}\ndata: {json.dumps(data, default=str, separators=(',', ':'))}\n\n"


class Runtime:
    def __init__(self, settings: Settings, db: DB):
        self.settings = settings
        self.db = db
        self.bus = EventBus()
        self.started_ts = time.time()
        self.quotes: dict[str, dict] = {}
        self.bars: dict[tuple[str, str], list[dict]] = {}
        self._dirty: set[tuple[str, str, int]] = set()
        self._last_tick_pub: dict[str, float] = {}
        self._last_bar_pub: dict[tuple[str, str], float] = {}
        self.quote_hooks: list[Callable[[str], None]] = []
        self.persist_bars = settings.data_source != "sim"
        # bridge
        self.bridge: dict = {"last_sync_ts": None, "ea_version": None, "terminal": None, "sync_count": 0}
        self.mt5_account: dict | None = None
        self.mt5_positions: list[dict] = []
        self.mt5_positions_ts: float | None = None
        self.hist_requested: dict[tuple[str, str], float] = {}
        # pairs needing a forced HIST refill (gap): {"since": ts, "sent": ts | None}
        self.hist_force: dict[tuple[str, str], dict] = {}
        # economic calendar from the EA (v1.10+): [{"t": utc_s, "ccy": "USD", "imp": 1..3, "name": str}]
        self.calendar: list[dict] = []
        self.calendar_ts: float | None = None
        # runtime config
        self.risk = settings.default_risk()
        self.schedule = settings.default_schedule()
        self.strategy = settings.default_strategy()
        self.trading = {"mode": "PAPER", "execution_enabled": False, "entries_paused": False}
        self.session_override: str | None = None
        # analysis/engine
        self.last_analysis: dict[str, dict] = {}
        self.engine_info: dict = {"last_loop_utc": None, "last_analysis_utc": None}

    # ---- runtime config --------------------------------------------------
    def load_runtime_config(self) -> None:
        s = self.settings
        for key, attr in (("risk", "risk"), ("schedule", "schedule"), ("strategy", "strategy")):
            saved = self.db.get_setting(key)
            if isinstance(saved, dict):
                cur = getattr(self, attr)
                for k, v in saved.items():
                    if k in cur:
                        cur[k] = v
        self.risk["must_have_sl_tp"] = True
        mode = s.TRADING_MODE.strip().upper()
        if mode not in ("SIGNAL_ONLY", "PAPER", "DEMO"):
            mode = "PAPER"
        if mode == "DEMO" and s.data_source == "sim":
            mode = "PAPER"
        saved_trading = self.db.get_setting("trading") or {}
        self.trading = {
            "mode": mode,
            "execution_enabled": bool(s.EXECUTION_ENABLED),
            "entries_paused": bool(saved_trading.get("entries_paused", False)),
        }
        self.save_trading()

    def save_trading(self) -> None:
        self.db.set_setting("trading", self.trading)

    def active_symbols(self) -> list[str]:
        return list(self.strategy.get("symbols") or [])

    def needed_tfs(self) -> list[str]:
        tfs = list(self.settings.chart_timeframes())
        for tf in (self.strategy["signal_timeframe"], self.strategy["htf_timeframe"]):
            if tf in TF_SECONDS and tf not in tfs:
                tfs.append(tf)
        return sorted(tfs, key=lambda t: TF_SECONDS[t])

    # ---- logging ---------------------------------------------------------
    def log(self, level: str, component: str, message: str) -> None:
        level = level.upper()
        if level == "WARNING":
            level = "WARN"
        msg = redact(message, self.settings.secret_values())
        pylevel = {"INFO": logging.INFO, "WARN": logging.WARNING, "ERROR": logging.ERROR}.get(level, logging.INFO)
        log.log(pylevel, "[%s] %s", component, msg)
        t = iso(utcnow())
        try:
            cur = self.db.execute(
                "INSERT INTO system_events(time_utc, level, component, message) VALUES(?,?,?,?)",
                (t, level, component, msg),
            )
            rid = cur.lastrowid
        except Exception:
            rid = None
        self.bus.publish("log", {"id": rid, "time_utc": t, "level": level, "component": component, "message": msg})

    # ---- quotes ----------------------------------------------------------
    def ingest_quote(self, symbol: str, info: dict) -> None:
        q = self.quotes.setdefault(symbol, {})
        q.update(info)
        q["symbol"] = symbol
        q["recv_ts"] = time.time()
        point = q.get("point") or 0
        if point and q.get("bid") and q.get("ask") and info.get("spread_points") is None:
            q["spread_points"] = round((q["ask"] - q["bid"]) / point)
        now = time.time()
        if now - self._last_tick_pub.get(symbol, 0) >= 0.25:
            self._last_tick_pub[symbol] = now
            self.bus.publish("tick", {
                "symbol": symbol, "bid": q.get("bid"), "ask": q.get("ask"),
                "spread_points": q.get("spread_points"), "time_msc": q.get("time_msc"),
            })
        for hook in list(self.quote_hooks):
            try:
                hook(symbol)
            except Exception as e:  # pragma: no cover
                self.log("ERROR", "engine", f"quote hook error: {e!r}")

    def tick_age(self, symbol: str, now: float | None = None) -> float | None:
        q = self.quotes.get(symbol)
        if not q:
            return None
        now = now if now is not None else time.time()
        tm = q.get("time_msc")
        ref = tm / 1000.0 if tm else q.get("recv_ts", now)
        if ref > now + 5:
            return None  # tick stamped in the future: clock error, treat as unusable
        return max(0.0, now - ref)

    # ---- bars ------------------------------------------------------------
    def ingest_bar(self, symbol: str, tf: str, bar: dict, publish: bool = True) -> None:
        """bar uses short keys t,o,h,l,c,tv,sp."""
        if tf not in TF_SECONDS:
            return
        key = (symbol, tf)
        series = self.bars.setdefault(key, [])
        t = int(bar["t"])
        b = {"t": t, "o": float(bar["o"]), "h": float(bar["h"]), "l": float(bar["l"]), "c": float(bar["c"]),
             "tv": float(bar.get("tv") or 0), "sp": float(bar.get("sp") or 0)}
        new_bar = False
        if not series or t > series[-1]["t"]:
            series.append(b)
            new_bar = True
            if len(series) > MAX_BARS:
                del series[: len(series) - MAX_BARS]
        elif t == series[-1]["t"]:
            if series[-1] == b:
                return
            series[-1] = b
        else:
            # historical insert (binary search)
            lo, hi = 0, len(series)
            while lo < hi:
                mid = (lo + hi) // 2
                if series[mid]["t"] < t:
                    lo = mid + 1
                else:
                    hi = mid
            if lo < len(series) and series[lo]["t"] == t:
                series[lo] = b
            else:
                series.insert(lo, b)
                if len(series) > MAX_BARS:
                    del series[0]
            publish = False
        if self.persist_bars:
            self._dirty.add((symbol, tf, t))
        if publish:
            now = time.time()
            if new_bar or now - self._last_bar_pub.get(key, 0) >= 0.5:
                self._last_bar_pub[key] = now
                self.bus.publish("bar", {"symbol": symbol, "tf": tf, "bar": bar_public(b)})

    def ingest_history(self, symbol: str, tf: str, bars: list[dict]) -> None:
        for b in sorted(bars, key=lambda x: x["t"]):
            self.ingest_bar(symbol, tf, b, publish=False)

    def flush_bars(self) -> None:
        if not self._dirty:
            return
        dirty, self._dirty = self._dirty, set()
        rows = []
        for (sym, tf, t) in dirty:
            for b in reversed(self.bars.get((sym, tf), [])[-50:]):
                if b["t"] == t:
                    rows.append((sym, tf, t, b["o"], b["h"], b["l"], b["c"], b["tv"], b["sp"]))
                    break
            else:
                b = self.find_bar(sym, tf, t)
                if b:
                    rows.append((sym, tf, t, b["o"], b["h"], b["l"], b["c"], b["tv"], b["sp"]))
        try:
            self.db.executemany(
                "INSERT OR REPLACE INTO bars(symbol, tf, t, o, h, l, c, tv, sp) VALUES(?,?,?,?,?,?,?,?,?)", rows
            )
        except Exception as e:
            self.log("ERROR", "db", f"flush bars failed: {e!r}")

    def find_bar(self, symbol: str, tf: str, t: int) -> dict | None:
        for b in self.bars.get((symbol, tf), []):
            if b["t"] == t:
                return b
        return None

    def load_bars_from_db(self) -> None:
        if not self.persist_bars:
            return
        rows = self.db.query("SELECT DISTINCT symbol, tf FROM bars")
        for r in rows:
            data = self.db.query(
                "SELECT t,o,h,l,c,tv,sp FROM bars WHERE symbol=? AND tf=? ORDER BY t DESC LIMIT ?",
                (r["symbol"], r["tf"], MAX_BARS),
            )
            self.bars[(r["symbol"], r["tf"])] = list(reversed(data))
        # trim old rows
        try:
            for r in rows:
                self.db.execute(
                    "DELETE FROM bars WHERE symbol=? AND tf=? AND t < COALESCE((SELECT t FROM bars WHERE symbol=? AND tf=? "
                    "ORDER BY t DESC LIMIT 1 OFFSET ?), 0)",
                    (r["symbol"], r["tf"], r["symbol"], r["tf"], MAX_BARS * 2),
                )
        except Exception:
            pass

    def closed_bars(self, symbol: str, tf: str, now: float | None = None) -> list[dict]:
        series = self.bars.get((symbol, tf), [])
        if not series:
            return []
        now = now if now is not None else time.time()
        sec = TF_SECONDS[tf]
        if series[-1]["t"] + sec <= now:
            return list(series)
        return series[:-1]

    # ---- bridge ----------------------------------------------------------
    def bridge_age(self) -> float | None:
        ts = self.bridge.get("last_sync_ts")
        return None if ts is None else max(0.0, time.time() - ts)

    def bridge_connected(self) -> bool:
        age = self.bridge_age()
        return age is not None and age < 5.0


def bar_public(b: dict) -> dict:
    return {"time": b["t"], "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"], "tick_volume": b.get("tv", 0)}
