"""AI trader: Claude reads the chart and decides (entry / SL / TP / hold), then manages open positions.

Modes (settings key 'ai_trader', defaults from .env AI_TRADER_ENTRY_MODE / AI_TRADER_MANAGE_MODE, both 'off'):
- off     — nothing happens (no API calls).
- shadow  — on every CLOSED bar of AI_TRADER_TIMEFRAME, per symbol, Claude gets the candles (signal TF, H1, H4), the
            indicators, spread, session, news, open positions and its own track record, and answers NONE / BUY / SELL
            with SL, TP and a holding limit. The decision is stored in `ai_trader_decisions` and later LABELLED with
            what would have happened (same simulator as the shadow labels: fill at the next bar, SL first, costs), so
            the dashboard shows n / win rate / avg R / CI90 of the AI trader BEFORE a single order is sent.
            Management (manage_mode shadow): every AI_TRADER_MANAGE_EVERY_MIN minutes, per open bot position, Claude
            answers HOLD / TIGHTEN_SL / CLOSE; recorded only.
- live    — shadow + executed: an entry decision becomes a normal signal (strategy_version 'ai-trader-v1') that goes
            through the SAME pipeline as every other signal (arming, expiry, Risk Engine, pyramiding rule, EA); the
            Claude review step is skipped (the decision already carries its reasoning). Management actions are applied,
            but only the risk-reducing ones: a tighter SL (never wider, never beyond the price) or a market close.

Safety: the AI trader never changes settings or levels of other strategies' positions, never widens a stop, never adds
to a position outside the pyramiding rule, and is capped at AI_TRADER_MAX_CALLS_PER_DAY API calls. Validation rejects
decisions with SL/TP on the wrong side, SL closer than the broker's stops level + 0.3 ATR, or RR < 1.2.
"""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from . import indicators as ind
from . import labeler, scheduler
from .config import TF_SECONDS, iso

MODES = ("off", "shadow", "live")
STRATEGY_VERSION = "ai-trader-v1"
BAR_FRESH_S = 180.0  # decide only on a bar that closed less than 3 minutes ago (no catch-up on old bars)
MIN_RR = 1.2
MIN_SL_ATR = 0.3
LABEL_EVERY_S = 300.0
CANDLES = {"signal": 60, "H1": 30, "H4": 20}


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def candles(bars: list[dict], n: int) -> list[dict]:
    return [{"t": iso(b["t"]), "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]} for b in bars[-n:]]


class AITrader:
    def __init__(self, engine):
        self.engine = engine
        self.db = engine.db
        self.settings = engine.settings
        self.rt = engine.rt
        self.conf = {"entry_mode": "off", "manage_mode": "off", "timeframe": "M15", "symbols": "",
                     "manage_every_min": 15, "max_calls_per_day": 300}
        self.tasks: set[asyncio.Task] = set()
        self.last_bar: dict[str, int] = {}
        self.last_manage: dict[int, float] = {}
        self._last_label = 0.0
        self._last_hold_check = 0.0
        self._warned: set[str] = set()
        self._busy: set[str] = set()

    # ---- configuration ---------------------------------------------------------------------------------------------
    def startup(self) -> None:
        s = self.settings
        self.conf.update({"entry_mode": self._mode(s.AI_TRADER_ENTRY_MODE), "manage_mode": self._mode(s.AI_TRADER_MANAGE_MODE),
                          "timeframe": str(s.AI_TRADER_TIMEFRAME or "M15").upper(), "symbols": s.AI_TRADER_SYMBOLS or "",
                          "manage_every_min": int(s.AI_TRADER_MANAGE_EVERY_MIN), "max_calls_per_day": int(s.AI_TRADER_MAX_CALLS_PER_DAY)})
        saved = self.db.get_setting("ai_trader")
        if isinstance(saved, dict):
            self.update_settings(saved, persist=False)
        if self.conf["timeframe"] not in TF_SECONDS:
            self.conf["timeframe"] = "M15"
        if self.conf["entry_mode"] != "off" or self.conf["manage_mode"] != "off":
            self.rt.log("INFO", "ai", f"AI trader: intrări={self.conf['entry_mode']}, gestionare={self.conf['manage_mode']}, "
                                      f"TF {self.conf['timeframe']}")

    @staticmethod
    def _mode(v) -> str:
        v = str(v or "off").strip().lower()
        return v if v in MODES else "off"

    def update_settings(self, upd: dict, persist: bool = True) -> dict:
        for k, v in upd.items():
            if k in ("entry_mode", "manage_mode"):
                self.conf[k] = self._mode(v)
            elif k == "timeframe" and str(v).upper() in TF_SECONDS:
                self.conf[k] = str(v).upper()
            elif k == "symbols":
                self.conf[k] = ",".join(x.strip() for x in str(v or "").split(",") if x.strip())
            elif k == "manage_every_min":
                self.conf[k] = max(1, min(240, int(v)))
            elif k == "max_calls_per_day":
                self.conf[k] = max(0, min(5000, int(v)))
        if persist:
            self.db.set_setting("ai_trader", dict(self.conf))
        return dict(self.conf)

    @property
    def available(self) -> bool:
        return self.engine.ai.enabled and self.engine.ai.key_set

    def symbols(self) -> list[str]:
        """Symbols the AI trader decides on: its own list (if set) ∩ the bot's tradable symbols (TRADE_SYMBOLS)."""
        act = self.engine.trade_symbols()
        if not self.conf["symbols"]:
            return act
        return [s for s in self.conf["symbols"].split(",") if s in act]

    def _warn_once(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self.rt.log("WARN", "ai", msg)

    # ---- call budget -----------------------------------------------------------------------------------------------
    def calls_today(self) -> int:
        try:
            r = self.db.query_one("SELECT COUNT(*) AS n FROM ai_trader_decisions WHERE ts >= ?",
                                  (scheduler.local_day_start_utc(self.engine.now_utc(), self.engine.tz()).timestamp(),))
            return int(r["n"]) if r else 0
        except sqlite3.Error:
            return 0

    def _budget_ok(self) -> bool:
        if self.calls_today() >= int(self.conf["max_calls_per_day"]):
            self._warn_once("budget", f"AI trader: limita de {self.conf['max_calls_per_day']} apeluri/zi atinsă — pauză până mâine")
            return False
        return True

    # ---- engine hooks ----------------------------------------------------------------------------------------------
    def tick(self, now: float) -> None:
        if self.conf["entry_mode"] == "off" and self.conf["manage_mode"] == "off":
            return
        if not self.available:
            self._warn_once("nokey", "AI trader activat, dar Claude e dezactivat sau lipsește ANTHROPIC_API_KEY")
            return
        if self.conf["entry_mode"] != "off":
            self._tick_entries(now)
        if self.conf["manage_mode"] != "off":
            self._tick_manage(now)
        if now - self._last_hold_check >= 30:
            self._last_hold_check = now
            try:
                self.hold_deadline_closes(now)
            except Exception as e:
                self._warn_once("hold", f"AI trader: verificarea termenului de deținere a eșuat: {e!r}")
        if now - self._last_label >= LABEL_EVERY_S:
            self._last_label = now
            self._spawn_thread(self.label_pending, now)

    def _tick_entries(self, now: float) -> None:
        tf = self.conf["timeframe"]
        sec = TF_SECONDS[tf]
        if not self.engine.session()["open_now"]:
            return
        for sym in self.symbols():
            if sym in self._busy:
                continue
            bars = self.rt.closed_bars(sym, tf, now)
            if len(bars) < 30:
                continue
            t = int(bars[-1]["t"])
            if self.last_bar.get(sym) == t:
                continue
            self.last_bar[sym] = t
            if now - (t + sec) > BAR_FRESH_S:
                continue  # old bar (startup / gap): do not decide on it
            q = self.rt.quotes.get(sym) or {}
            age = self.rt.tick_age(sym, now)
            if not q.get("bid") or age is None or age > float(self.rt.risk.get("max_tick_age_seconds", 10)):
                continue
            if not self._budget_ok():
                return
            self._spawn(self.decide(sym, bar_time=t))

    def _tick_manage(self, now: float) -> None:
        every = int(self.conf["manage_every_min"]) * 60
        for p in self.engine.bot_positions("ALL"):
            ticket = int(p["ticket"])
            if now - self.last_manage.get(ticket, 0) < every or f"m{ticket}" in self._busy:
                continue
            self.last_manage[ticket] = now
            if not self._budget_ok():
                return
            self._spawn(self.manage(p))

    def _spawn(self, coro) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            asyncio.run(coro)
            return
        task = loop.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def _spawn_thread(self, fn, *args) -> None:
        try:
            asyncio.get_running_loop().run_in_executor(None, fn, *args)
        except RuntimeError:
            fn(*args)

    # ---- payloads --------------------------------------------------------------------------------------------------
    def _spec(self, symbol: str) -> dict:
        q = self.rt.quotes.get(symbol) or {}
        return {"bid": q.get("bid"), "ask": q.get("ask"), "spread_points": q.get("spread_points"), "digits": q.get("digits"),
                "point": q.get("point"), "stops_level": q.get("stops_level"), "tick_value": q.get("tick_value")}

    def _atr(self, symbol: str, tf: str, n: int = 14) -> float | None:
        bars = self.rt.closed_bars(symbol, tf)[-200:]
        if len(bars) < n + 2:
            return None
        return ind.last(ind.atr([b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars], n))

    def self_stats(self, days: int = 60) -> dict:
        rows = []
        try:
            rows = self.db.query("SELECT outcome_r, action FROM ai_trader_decisions WHERE kind='ENTRY' AND action IN ('BUY','SELL') "
                                 "AND outcome IS NOT NULL AND ts >= ?", (time.time() - days * 86400,))
        except sqlite3.Error:
            pass
        rs = [float(r["outcome_r"]) for r in rows if r["outcome_r"] is not None]
        from .engine import mean_ci90
        avg, ci = mean_ci90(rs)
        return {"labeled": len(rs), "win_rate": round(sum(1 for x in rs if x > 0) / len(rs), 3) if rs else None,
                "avg_r": round(avg, 3) if avg is not None else None, "ci90_avg_r": ci}

    def market_context(self, symbol: str, now: float | None = None) -> dict | None:
        """Long-term picture from the archive (years of D1 bars): compressed daily/weekly candles, 52-week range,
        200-day trend, realized volatility, major pivots, multi-horizon returns. This is what lets the model 'know the
        whole chart' without receiving thousands of bars."""
        now = now or time.time()
        try:
            d1 = self.engine.get_closed_bars(symbol, "D1", now - 5 * 365 * 86400, now)
        except Exception:
            d1 = []
        if len(d1) < 60:
            return None
        closes = [b["c"] for b in d1]
        last = closes[-1]
        def ret(n):
            return round((last / closes[-1 - n] - 1) * 100, 2) if len(closes) > n else None
        hi52 = max(b["h"] for b in d1[-252:])
        lo52 = min(b["l"] for b in d1[-252:])
        ema200 = ind.last(ind.ema(closes, 200)) if len(closes) >= 200 else None
        ema50 = ind.last(ind.ema(closes, 50)) if len(closes) >= 50 else None
        atr_d = ind.last(ind.atr([b["h"] for b in d1], [b["l"] for b in d1], closes, 14))
        # realized vol percentile: today's 20-day vol vs the last year
        lr = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
        def vol(i):
            w = lr[max(0, i - 20):i]
            return (sum(x * x for x in w) / len(w)) ** 0.5 if w else None
        vols = [v for v in (vol(i) for i in range(21, len(lr) + 1)) if v is not None]
        vnow = vols[-1] if vols else None
        vpct = round(sum(1 for v in vols[-252:] if v <= vnow) / len(vols[-252:]) * 100) if vols and vnow else None
        # major pivots (swing highs/lows on D1 with 10 bars each side), last 8
        piv = []
        for i in range(10, len(d1) - 10):
            w = d1[i - 10:i + 11]
            if d1[i]["h"] == max(b["h"] for b in w):
                piv.append({"t": iso(d1[i]["t"])[:10], "type": "H", "price": d1[i]["h"]})
            elif d1[i]["l"] == min(b["l"] for b in w):
                piv.append({"t": iso(d1[i]["t"])[:10], "type": "L", "price": d1[i]["l"]})
        # weekly candles from D1 (ISO weeks), last 104
        weeks: dict[str, dict] = {}
        for b in d1:
            wk = datetime.fromtimestamp(b["t"], timezone.utc).strftime("%G-W%V")
            w = weeks.get(wk)
            if w is None:
                weeks[wk] = {"w": wk, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
            else:
                w["h"], w["l"], w["c"] = max(w["h"], b["h"]), min(w["l"], b["l"]), b["c"]
        digits = int((self.rt.quotes.get(symbol) or {}).get("digits") or 5)
        r = lambda x: round(x, digits) if isinstance(x, (int, float)) else x  # noqa: E731
        return {
            "history_days": len(d1), "since": iso(d1[0]["t"])[:10],
            "last_close": last, "returns_pct": {"1w": ret(5), "1m": ret(21), "3m": ret(63), "6m": ret(126), "12m": ret(252)},
            "range_52w": {"high": hi52, "low": lo52, "position_pct": round((last - lo52) / (hi52 - lo52) * 100) if hi52 > lo52 else None},
            "trend": {"ema50_d1": r(ema50) if ema50 else None, "ema200_d1": r(ema200) if ema200 else None,
                      "above_ema200": (last > ema200) if ema200 else None,
                      "ema50_vs_200": ("UP" if ema50 > ema200 else "DOWN") if ema50 and ema200 else None},
            "volatility": {"atr14_d1": r(atr_d) if atr_d else None, "atr_pct_of_price": round(atr_d / last * 100, 2) if atr_d else None,
                           "vol20_percentile_1y": vpct},
            "major_pivots_d1": piv[-8:],
            "daily_candles_120": [{"t": iso(b["t"])[:10], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]} for b in d1[-120:]],
            "weekly_candles_104": list(weeks.values())[-104:],
        }

    def build_entry_payload(self, symbol: str, bar_time: int | None = None) -> dict | None:
        tf = self.conf["timeframe"]
        rt = self.rt
        sig = rt.closed_bars(symbol, tf)
        if len(sig) < 30 or not (rt.quotes.get(symbol) or {}).get("bid"):
            return None
        q = self._spec(symbol)
        atr = self._atr(symbol, tf)
        point = float(q.get("point") or 0.00001)
        stops = float(q.get("stops_level") or 0) * point
        min_sl = max(stops + 5 * point, (atr or 0) * MIN_SL_ATR)
        a = rt.last_analysis.get(symbol) or {}
        now = time.time()
        positions = self.engine.symbol_positions(symbol, "ALL")
        rk = rt.risk
        max_per = int(rk.get("max_positions_per_symbol", 1) or 1)
        ev = self.engine.news_event_near(symbol, now, window_s=4 * 3600)
        recent = self.engine.trade_reviews._recent_trades(self.engine.account_key(), None, symbol=symbol, limit=6)
        return {
            "symbol": symbol, "timeframe": tf, "bar_time_utc": iso(bar_time or sig[-1]["t"]),
            "local_time": datetime.fromtimestamp(now, scheduler._tz(self.engine.tz())).strftime("%A %H:%M"),
            "quote": q,
            "candles": {tf: candles(sig, CANDLES["signal"]),
                        "H1": candles(rt.closed_bars(symbol, "H1"), CANDLES["H1"]) if tf != "H1" else [],
                        "H4": candles(rt.closed_bars(symbol, "H4"), CANDLES["H4"]) if tf not in ("H4", "D1") else []},
            "indicators": {"atr14": atr, "strategy_view": {k: a.get(k) for k in ("direction", "score", "regime", "indicators")}},
            "long_term_context": self.market_context(symbol, now),
            "session": {k: self.engine.session().get(k) for k in ("open_now", "start", "end", "tz")},
            "next_news_event": {"name": ev.get("name"), "ccy": ev.get("ccy"), "imp": ev.get("imp"),
                                "minutes_away": round((float(ev["t"]) - now) / 60) if ev.get("t") else None} if ev else None,
            "open_positions_on_symbol": positions or None,
            "recent_trades_on_symbol": [{k: t.get(k) for k in ("side", "r_multiple", "net_usd", "close_reason", "minutes_open")}
                                        for t in recent] or None,
            "self_stats": self.self_stats(),
            "constraints": {"min_sl_distance": round(min_sl, int(q.get("digits") or 5)), "min_rr": MIN_RR,
                            "hold_minutes_range": [15, 1440], "max_spread_points": rk.get("max_spread_points"),
                            "pyramiding": f"permisă până la {max_per} poziții, doar în aceeași direcție, peste poziții pe profit"
                            if max_per > 1 else "nepermisă (o singură poziție pe simbol)"},
            "note": "Cont DEMO / PAPER: nu sunt bani reali." if self.engine.mode() != "LIVE" else None,
        }

    def build_manage_payload(self, pos: dict) -> dict | None:
        symbol = pos["symbol"]
        tf = self.conf["timeframe"]
        sig = self.rt.closed_bars(symbol, tf)
        if len(sig) < 10:
            return None
        src = "PAPER" if pos.get("source") == "PAPER" else "MT5"
        st = self.engine._paths.get((src, str(pos["ticket"]))) or {}
        risk = float(st.get("risk") or 0) or None
        sign = 1 if pos["side"] == "BUY" else -1
        cur = pos.get("price_current")
        open_r = round((float(cur) - float(pos["price_open"])) * sign / risk, 3) if risk and cur else None
        opened = st.get("opened_ts")
        now = time.time()
        ev = self.engine.news_event_near(symbol, now, window_s=2 * 3600)
        return {
            "symbol": symbol, "timeframe": tf, "quote": self._spec(symbol), "atr14": self._atr(symbol, tf),
            "position": {"ticket": pos["ticket"], "side": pos["side"], "price_open": pos["price_open"], "price_current": cur,
                         "sl": pos.get("sl"), "tp": pos.get("tp"), "open_r": open_r, "mfe_r": st.get("mfe"), "mae_r": st.get("mae"),
                         "minutes_open": round((now - float(opened)) / 60) if opened else None,
                         "initial_risk_price": risk},
            "candles": {tf: candles(sig, 40)},
            "next_news_event": {"name": ev.get("name"), "ccy": ev.get("ccy"), "imp": ev.get("imp"),
                                "minutes_away": round((float(ev["t"]) - now) / 60) if ev.get("t") else None} if ev else None,
            "local_time": datetime.fromtimestamp(now, scheduler._tz(self.engine.tz())).strftime("%A %H:%M"),
        }

    # ---- validation ------------------------------------------------------------------------------------------------
    @staticmethod
    def validate_entry(res: dict, q: dict, atr: float | None) -> tuple[str, str]:
        """(status, note): VALID / NONE / INVALID."""
        action = str(res.get("action") or "NONE").upper()
        if action not in ("BUY", "SELL"):
            return "NONE", str(res.get("reasoning") or "")[:200]
        sl, tp = _f(res.get("stop_loss")), _f(res.get("take_profit"))
        bid, ask = _f(q.get("bid")), _f(q.get("ask"))
        if sl is None or tp is None or not bid or not ask:
            return "INVALID", "SL/TP lipsă sau cotație indisponibilă"
        point = float(q.get("point") or 0.00001)
        stops = float(q.get("stops_level") or 0) * point
        entry = ask if action == "BUY" else bid
        sign = 1 if action == "BUY" else -1
        sl_dist, tp_dist = (entry - sl) * sign, (tp - entry) * sign
        if sl_dist <= 0 or tp_dist <= 0:
            return "INVALID", f"SL/TP de partea greșită a prețului ({action} @ {entry}: SL {sl}, TP {tp})"
        min_sl = max(stops + 5 * point, (atr or 0) * MIN_SL_ATR)
        if sl_dist < min_sl:
            return "INVALID", f"SL prea aproape: {sl_dist:.5g} < {min_sl:.5g}"
        if tp_dist / sl_dist < MIN_RR:
            return "INVALID", f"RR {tp_dist / sl_dist:.2f} < {MIN_RR}"
        hold = res.get("hold_minutes")
        try:
            hold = int(hold)
        except (TypeError, ValueError):
            hold = 240
        if not 15 <= hold <= 1440:
            return "INVALID", f"hold_minutes {hold} în afara 15–1440"
        return "VALID", ""

    # ---- storage ---------------------------------------------------------------------------------------------------
    def _store(self, row: dict) -> bool:
        cols = ("id", "ts", "kind", "symbol", "tf", "bar_time", "ticket", "mode", "action", "side", "entry", "sl", "tp",
                "hold_minutes", "confidence", "setup", "reasoning", "status", "note", "signal_id", "payload_json", "model")
        try:
            cur = self.db.execute(f"INSERT OR IGNORE INTO ai_trader_decisions({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                                  tuple(row.get(c) for c in cols))
            return bool(cur.rowcount)
        except sqlite3.Error as e:
            self._warn_once("db", f"AI trader: decizie nescrisă: {e!r}")
            return False

    def _public(self, r: dict) -> dict:
        d = dict(r)
        d["time_utc"] = iso(d.pop("ts"))
        d.pop("payload_json", None)
        return d

    def list_payload(self, limit: int = 50, kind: str | None = None) -> list[dict]:
        sql = "SELECT * FROM ai_trader_decisions"
        params: list = []
        if kind:
            sql += " WHERE kind=?"
            params.append(kind)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(int(limit))
        try:
            return [self._public(r) for r in self.db.query(sql, params)]
        except sqlite3.Error:
            return []

    def stats(self, days: int = 60) -> dict:
        out = {"entries": {}, "manage": {}, "calls_today": self.calls_today()}
        try:
            for r in self.db.query("SELECT kind, action, status, COUNT(*) AS n FROM ai_trader_decisions WHERE ts >= ? "
                                   "GROUP BY kind, action, status", (time.time() - days * 86400,)):
                tgt = out["entries"] if r["kind"] == "ENTRY" else out["manage"]
                tgt[f"{r['action']}/{r['status']}"] = int(r["n"])
            rows = self.db.query("SELECT symbol, outcome, outcome_r, mode FROM ai_trader_decisions WHERE kind='ENTRY' "
                                 "AND action IN ('BUY','SELL') AND outcome IS NOT NULL AND ts >= ?", (time.time() - days * 86400,))
        except sqlite3.Error:
            rows = []
        from .engine import mean_ci90
        rs = [float(r["outcome_r"]) for r in rows if r["outcome_r"] is not None]
        avg, ci = mean_ci90(rs)
        gw = sum(x for x in rs if x > 0)
        gl = -sum(x for x in rs if x < 0)
        by_sym: dict[str, list] = {}
        for r in rows:
            if r["outcome_r"] is not None:
                by_sym.setdefault(r["symbol"], []).append(float(r["outcome_r"]))
        out["shadow"] = {"labeled": len(rs), "win_rate": round(sum(1 for x in rs if x > 0) / len(rs), 3) if rs else None,
                         "avg_r": round(avg, 3) if avg is not None else None, "ci90_avg_r": ci,
                         "profit_factor": round(gw / gl, 3) if gl > 0 else None, "sum_r": round(sum(rs), 2),
                         "by_symbol": {s: {"n": len(v), "avg_r": round(sum(v) / len(v), 3)} for s, v in by_sym.items()},
                         "outcomes": {o: sum(1 for r in rows if r["outcome"] == o) for o in {r["outcome"] for r in rows}}}
        return out

    def status(self) -> dict:
        return {"available": self.available, **self.conf, "symbols_effective": self.symbols() if self.conf["entry_mode"] != "off" else [],
                "pending_tasks": len(self.tasks), "strategy_version": STRATEGY_VERSION}

    # ---- jobs ------------------------------------------------------------------------------------------------------
    async def decide(self, symbol: str, bar_time: int | None = None, force: bool = False) -> dict | None:
        """One entry decision for `symbol` at the latest closed bar. Returns the stored row (public) or None."""
        key = symbol
        if key in self._busy:
            return None
        self._busy.add(key)
        try:
            payload = self.build_entry_payload(symbol, bar_time)
            if not payload:
                return None
            tf = self.conf["timeframe"]
            bt = bar_time if bar_time is not None else int(self.rt.closed_bars(symbol, tf)[-1]["t"])
            if force:
                bt = int(time.time())  # manual run: never collides with the bar-close decision
            mode = self.conf["entry_mode"] if not force else (self.conf["entry_mode"] if self.conf["entry_mode"] != "off" else "shadow")
            res = await self.engine.ai.decide_entry(payload)
            q = self.rt.quotes.get(symbol) or {}
            row = {"id": str(uuid.uuid4()), "ts": time.time(), "kind": "ENTRY", "symbol": symbol, "tf": tf, "bar_time": bt,
                   "ticket": 0, "mode": mode,  # ticket 0 (not NULL): the UNIQUE key must dedup one decision per bar "action": "NONE", "side": None, "entry": None, "sl": None, "tp": None,
                   "hold_minutes": None, "confidence": None, "setup": None, "reasoning": None, "status": "ERROR",
                   "note": None, "signal_id": None, "payload_json": json.dumps(payload, ensure_ascii=False, default=str),
                   "model": res.get("model")}
            if res.get("status") != "DONE":
                row["note"] = str(res.get("error") or res.get("status"))[:300]
                self._store(row)
                self.rt.log("WARN", "ai", f"AI trader {symbol}: decizie eșuată — {row['note']}")
                return self._row(row["id"])
            action = str(res.get("action") or "NONE").upper()
            status, note = self.validate_entry(res, q, payload["indicators"].get("atr14"))
            row.update({"action": action if status != "INVALID" else action, "status": status, "note": note or None,
                        "confidence": _f(res.get("confidence")), "setup": str(res.get("setup") or "")[:80],
                        "reasoning": str(res.get("reasoning") or "")[:600]})
            if status == "VALID":
                side = action
                entry = float(q["ask"] if side == "BUY" else q["bid"])
                row.update({"side": side, "entry": entry, "sl": _f(res.get("stop_loss")), "tp": _f(res.get("take_profit")),
                            "hold_minutes": int(res.get("hold_minutes") or 240)})
            if not self._store(row):
                return None
            self.rt.log("INFO", "ai", f"AI trader {symbol} [{mode}]: {action} {row.get('side') or ''} "
                                      f"{'SL ' + str(row['sl']) + ' TP ' + str(row['tp']) if status == 'VALID' else status} "
                                      f"(încredere {row.get('confidence')})")
            if status == "VALID" and mode == "live":
                await self._execute(row, res, payload)
            out = self._row(row["id"])
            self.rt.bus.publish("ai_trader", out)
            return out
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._warn_once("decide", f"AI trader {symbol}: eroare — {e!r}")
            return None
        finally:
            self._busy.discard(key)

    async def _execute(self, row: dict, res: dict, payload: dict) -> None:
        """Turn a VALID live decision into a normal signal (same pipeline, Risk Engine and EA path as every signal)."""
        eng = self.engine
        q = self.rt.quotes.get(row["symbol"]) or {}
        rr = round(abs(row["tp"] - row["entry"]) / abs(row["entry"] - row["sl"]), 2) if row["sl"] and row["tp"] else None
        conf = float(row.get("confidence") or 0.5)
        p_dir = max(0.5, min(0.9, conf))
        a = {"symbol": row["symbol"], "timeframe": row["tf"], "bar_time": int(row["bar_time"]), "direction": row["side"],
             "score": round((p_dir - 0.5) * 4, 3), "probabilities": {"up" if row["side"] == "BUY" else "down": p_dir,
                                                                      "down" if row["side"] == "BUY" else "up": round((1 - p_dir) * 0.6, 3),
                                                                      "neutral": round((1 - p_dir) * 0.4, 3)},
             "entry": row["entry"], "stop_loss": row["sl"], "take_profit": row["tp"], "rr": rr,
             "spread_points": q.get("spread_points"), "reasons": [f"AI trader ({row.get('setup')}): {row.get('reasoning')}"],
             "indicators": {"atr": payload["indicators"].get("atr14"), "ema_slow": None,
                            **((payload["indicators"].get("strategy_view") or {}).get("indicators") or {})},
             "strategy_version": STRATEGY_VERSION, "regime": ((payload["indicators"].get("strategy_view") or {}).get("regime"))}
        sig = eng.create_signal(a)
        if not sig:
            self.db.execute("UPDATE ai_trader_decisions SET note=? WHERE id=?", ("semnal duplicat (aceeași bară/direcție)", row["id"]))
            return
        sig["ai"] = {"status": "DONE", "verdict": "AGREE", "explanation": str(row.get("reasoning") or "")[:600],
                     "risks": [f"Invalidare (AI trader): {str(res.get('invalidation') or '')[:200]}"],
                     "model": row.get("model") or self.settings.CLAUDE_MODEL}
        sig["ai_trader_decision_id"] = row["id"]
        sig["hold_minutes"] = row.get("hold_minutes")
        eng.save_signal(sig)
        eng.add_signal_event(sig["id"], "AI_TRADER", f"decizie {row['id'][:8]}: {row.get('setup')} (încredere {row.get('confidence')})")
        self.db.execute("UPDATE ai_trader_decisions SET signal_id=? WHERE id=?", (sig["id"], row["id"]))
        await eng.signal_pipeline(sig)

    async def manage(self, pos: dict) -> dict | None:
        ticket = int(pos["ticket"])
        key = f"m{ticket}"
        if key in self._busy:
            return None
        self._busy.add(key)
        try:
            payload = self.build_manage_payload(pos)
            if not payload:
                return None
            mode = self.conf["manage_mode"]
            res = await self.engine.ai.decide_manage(payload)
            row = {"id": str(uuid.uuid4()), "ts": time.time(), "kind": "MANAGE", "symbol": pos["symbol"], "tf": self.conf["timeframe"],
                   "bar_time": int(time.time() * 1000), "ticket": ticket, "mode": mode, "action": "HOLD", "side": pos["side"],
                   "entry": pos.get("price_open"), "sl": pos.get("sl"), "tp": pos.get("tp"), "hold_minutes": None,
                   "confidence": _f(res.get("confidence")), "setup": None, "reasoning": str(res.get("reasoning") or "")[:400],
                   "status": "ERROR", "note": None, "signal_id": None,
                   "payload_json": json.dumps(payload, ensure_ascii=False, default=str), "model": res.get("model")}
            if res.get("status") != "DONE":
                row["note"] = str(res.get("error") or res.get("status"))[:300]
                self._store(row)
                return self._row(row["id"])
            action = str(res.get("action") or "HOLD").upper()
            row["action"] = action if action in ("HOLD", "TIGHTEN_SL", "CLOSE") else "HOLD"
            row["status"] = "VALID"
            new_sl = _f(res.get("new_sl"))
            if row["action"] == "TIGHTEN_SL":
                ok, why = self._tighten_ok(pos, new_sl)
                if not ok:
                    row["status"], row["note"] = "INVALID", why
                else:
                    row["sl"] = new_sl
            self._store(row)
            applied = None
            if mode == "live" and row["status"] == "VALID" and row["action"] != "HOLD":
                applied = self._apply_manage(pos, row)
                self.db.execute("UPDATE ai_trader_decisions SET note=? WHERE id=?", (applied, row["id"]))
            self.rt.log("INFO", "ai", f"AI trader gestionare #{ticket} [{mode}]: {row['action']} {row.get('note') or applied or ''}")
            out = self._row(row["id"])
            self.rt.bus.publish("ai_trader", out)
            return out
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._warn_once("manage", f"AI trader gestionare #{ticket}: eroare — {e!r}")
            return None
        finally:
            self._busy.discard(key)

    @staticmethod
    def _tighten_ok(pos: dict, new_sl: float | None) -> tuple[bool, str]:
        if new_sl is None:
            return False, "new_sl lipsă"
        cur_sl, price = _f(pos.get("sl")), _f(pos.get("price_current"))
        sign = 1 if pos["side"] == "BUY" else -1
        if price is None:
            return False, "preț curent indisponibil"
        if (price - new_sl) * sign <= 0:
            return False, f"SL {new_sl} dincolo de prețul curent {price}"
        if cur_sl and (new_sl - cur_sl) * sign <= 0:
            return False, f"SL {new_sl} nu este mai strâns decât {cur_sl}"
        return True, ""

    def _apply_manage(self, pos: dict, row: dict) -> str:
        eng = self.engine
        src = "PAPER" if pos.get("source") == "PAPER" else "MT5"
        try:
            if row["action"] == "CLOSE":
                eng.close_position(src, int(pos["ticket"]), reason="AI_MANAGE")
                return "închisă la piață (AI_MANAGE)"
            it = eng.executor.submit_modify(src, int(pos["ticket"]), pos["symbol"], pos["side"], float(row["sl"]),
                                            float(pos.get("tp") or 0), reason="AI_SL")
            return f"SL mutat la {row['sl']} ({it.get('state')})"
        except Exception as e:
            return f"aplicare eșuată: {getattr(e, 'detail', e)!r}"[:200]

    def _row(self, row_id: str) -> dict | None:
        try:
            r = self.db.query_one("SELECT * FROM ai_trader_decisions WHERE id=?", (row_id,))
        except sqlite3.Error:
            return None
        return self._public(r) if r else None

    # ---- holding limit of live AI positions ------------------------------------------------------------------------
    def hold_deadline_closes(self, now: float) -> int:
        """Live mode: close positions opened by AI decisions whose hold_minutes elapsed (reason AI_TIME)."""
        if self.conf["entry_mode"] != "live":
            return 0
        n = 0
        try:
            rows = self.db.query("SELECT d.id, d.hold_minutes, d.signal_id FROM ai_trader_decisions d WHERE d.kind='ENTRY' "
                                 "AND d.mode='live' AND d.signal_id IS NOT NULL AND d.hold_minutes IS NOT NULL AND d.ts >= ?",
                                 (now - 3 * 86400,))
        except sqlite3.Error:
            return 0
        for r in rows:
            sig = self.engine.get_signal(r["signal_id"])
            if not sig or not sig.get("intent_id"):
                continue
            it = self.engine.executor.get(sig["intent_id"]) or {}
            ticket = it.get("ticket")
            if ticket is None:
                continue
            found = self.engine.find_position(int(ticket))
            if not found:
                continue
            src, pos = found[0], found[1]
            opened = (self.engine._paths.get((("PAPER" if src == "PAPER" else "MT5"), str(ticket))) or {}).get("opened_ts")
            if not opened or now - float(opened) < int(r["hold_minutes"]) * 60:
                continue
            try:
                self.engine.close_position(src, int(ticket), reason="AI_TIME")
                n += 1
            except Exception as e:
                self._warn_once(f"hold{ticket}", f"AI trader: închidere la termen #{ticket} eșuată: {e!r}")
        return n

    # ---- shadow labelling ------------------------------------------------------------------------------------------
    def label_pending(self, now: float | None = None, limit: int = 200) -> int:
        """Resolve BUY/SELL entry decisions with the shadow simulator: fill at the next bar, SL first, bar spread + 1
        point slippage on stops, time stop = hold_minutes. Returns rows labelled."""
        now = now or time.time()
        try:
            rows = self.db.query("SELECT id, symbol, tf, bar_time, side, sl, tp, hold_minutes FROM ai_trader_decisions "
                                 "WHERE kind='ENTRY' AND action IN ('BUY','SELL') AND status='VALID' AND outcome IS NULL "
                                 "ORDER BY ts LIMIT ?", (int(limit),))
        except sqlite3.Error:
            return 0
        n = 0
        points = self.engine.symbol_points() or {}
        for r in rows:
            tf = r["tf"] if r["tf"] in TF_SECONDS else "M15"
            sec = TF_SECONDS[tf]
            label_tf = "M1" if "M1" in self.rt.needed_tfs() else tf
            lsec = TF_SECONDS[label_tf]
            decision_ts = int(r["bar_time"]) + sec
            if now < decision_ts + lsec:
                continue
            hold = int(r["hold_minutes"] or 240)
            bars = self.engine.get_closed_bars(r["symbol"], label_tf, decision_ts - 2 * lsec, now)
            if not bars and label_tf != tf:  # no M1 archive for this period: label on the decision timeframe
                label_tf, lsec = tf, sec
                bars = self.engine.get_closed_bars(r["symbol"], tf, decision_ts - 2 * sec, now)
                cfg_sec = sec
            if not bars:
                continue
            point = float(points.get(r["symbol"]) or labeler.guess_point(r["symbol"], bars[-1]["c"]))
            cfg = {"_bar_sec": lsec, "max_hold_minutes": hold, "break_even_at_r": 0, "be_lock_r": 0, "_horizon_s": hold * 60}
            if now < decision_ts + lsec:
                continue
            try:
                out = labeler.simulate(r["side"], float(r["sl"]), float(r["tp"]), bars, point, decision_ts, cfg, now,
                                       slip_px=point)
            except Exception as e:
                self._warn_once("label", f"AI trader: etichetare eșuată: {e!r}")
                continue
            if not out:
                continue
            try:
                self.db.execute("UPDATE ai_trader_decisions SET outcome=?, outcome_r=?, mfe_r=?, mae_r=?, labeled_ts=? WHERE id=?",
                                (out.get("outcome"), out.get("outcome_r"), out.get("mfe_r"), out.get("mae_r"), now, r["id"]))
                n += 1
            except sqlite3.Error:
                pass
        return n
