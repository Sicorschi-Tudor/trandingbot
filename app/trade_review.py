"""Claude as trade analyst: post-trade review of every closed bot trade + a daily review at session end.

Analysis only. Nothing here changes a setting, a level, a volume or an order: the output is text (summary, lessons,
patterns, hypotheses to test in the backtest) stored in `trade_reviews`, pushed to Telegram and shown in the dashboard.
Every call is best effort — a failure is logged and never reaches the trading path.

Payloads are built from what the bot already stores: the trade row, the signal that produced it (indicators, reasons,
the AI verdict at entry), the 1-minute trade path (MFE / MAE in R), the recent trades of the account and of the symbol,
and — for the daily review — the shadow labels (what the blocked / vetoed / unexecuted setups would have done) and the
previous day's review, so lessons can be carried forward.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timezone

from . import scheduler
from .config import iso, parse_iso
from .telegram import fmt_daily_review, fmt_trade_review

RECENT_TRADES = 20
SYMBOL_TRADES = 6
MAX_PAYLOAD_TRADES = 40


def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except (TypeError, ValueError):
        return default


def _ts(s: str | None) -> float | None:
    d = parse_iso(s)
    return d.timestamp() if d else None


def trade_brief(tr: dict) -> dict:
    """The compact per-trade record used in every payload."""
    net = float(tr.get("profit") or 0) + float(tr.get("commission") or 0) + float(tr.get("swap") or 0)
    o, c = _ts(tr.get("opened_at_utc")), _ts(tr.get("closed_at_utc"))
    return {
        "id": tr.get("id"), "ticket": tr.get("ticket"), "symbol": tr.get("symbol"), "side": tr.get("side"),
        "volume": tr.get("volume"), "price_open": tr.get("price_open"), "price_close": tr.get("price_close"),
        "initial_sl": tr.get("initial_sl"), "opened_at_utc": tr.get("opened_at_utc"),
        "closed_at_utc": tr.get("closed_at_utc"),
        "minutes_open": round((c - o) / 60, 1) if o and c else None,
        "close_reason": tr.get("close_reason"), "mode": tr.get("mode"),
        "net_usd": round(net, 2), "profit": tr.get("profit"), "commission": tr.get("commission"), "swap": tr.get("swap"),
        "risk_money": tr.get("risk_money"), "r_multiple": tr.get("r_multiple"), "signal_id": tr.get("signal_id"),
    }


def trades_summary(briefs: list[dict]) -> dict:
    nets = [float(b.get("net_usd") or 0) for b in briefs]
    rs = [float(b["r_multiple"]) for b in briefs if b.get("r_multiple") is not None]
    gw = sum(x for x in nets if x > 0)
    gl = -sum(x for x in nets if x < 0)
    reasons: dict[str, int] = {}
    for b in briefs:
        k = str(b.get("close_reason") or "?")
        reasons[k] = reasons.get(k, 0) + 1
    return {
        "trades": len(briefs), "wins": sum(1 for x in nets if x > 0), "losses": sum(1 for x in nets if x < 0),
        "net_usd": round(sum(nets), 2), "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
        "sum_r": round(sum(rs), 3) if rs else None,
        "profit_factor": round(gw / gl, 3) if gl > 0 else None, "by_close_reason": reasons,
    }


class TradeReviewer:
    def __init__(self, engine):
        self.engine = engine
        self.db = engine.db
        self.settings = engine.settings
        self.tasks: set[asyncio.Task] = set()
        self._warned: set[str] = set()
        self._daily_running = False

    # ---- configuration ---------------------------------------------------------------------------------------------
    @property
    def trade_enabled(self) -> bool:
        return bool(getattr(self.settings, "CLAUDE_TRADE_REVIEW_ENABLED", True)) and self.engine.ai.enabled \
            and self.engine.ai.key_set

    @property
    def daily_enabled(self) -> bool:
        return bool(getattr(self.settings, "CLAUDE_DAILY_REVIEW_ENABLED", True)) and self.engine.ai.enabled \
            and self.engine.ai.key_set

    def status(self) -> dict:
        counts = {"TRADE": 0, "DAILY": 0}
        last = None
        try:
            for r in self.db.query("SELECT kind, COUNT(*) AS n FROM trade_reviews WHERE status='DONE' GROUP BY kind"):
                counts[r["kind"]] = int(r["n"])
            row = self.db.query_one("SELECT MAX(created_ts) AS ts FROM trade_reviews WHERE status='DONE'")
            last = iso(row["ts"]) if row and row["ts"] else None
        except sqlite3.Error:
            pass
        return {"trade_review_enabled": self.trade_enabled, "daily_review_enabled": self.daily_enabled,
                "done": counts, "last_utc": last, "pending": len(self.tasks)}

    def _warn_once(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self.engine.rt.log("WARN", "ai", msg)

    # ---- hooks from the engine -------------------------------------------------------------------------------------
    def on_trade_closed(self, trade: dict) -> bool:
        """Schedule the post-trade analysis of a just-recorded trade (needs the trades.id). Never raises."""
        try:
            if not self.trade_enabled or trade.get("id") is None:
                return False
            return self._spawn(self.review_trade(int(trade["id"])))
        except Exception as e:  # analysis must never break the trading path
            self._warn_once("trade_review_spawn", f"analiza tranzacției nu a pornit: {e!r}")
            return False

    def on_session_closed(self, now: float | None = None) -> bool:
        """Daily review when the trading session closes (one per local day; re-runs are ignored)."""
        try:
            if not self.daily_enabled:
                return False
            day = scheduler.local_day(datetime.fromtimestamp(now or time.time(), timezone.utc), self.engine.tz())
            return self._spawn(self.review_day(day))
        except Exception as e:
            self._warn_once("daily_review_spawn", f"bilanțul zilnic nu a pornit: {e!r}")
            return False

    def _spawn(self, coro) -> bool:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            asyncio.run(coro)
            return True
        task = loop.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return True

    # ---- storage ---------------------------------------------------------------------------------------------------
    def _claim(self, kind: str, ref_id: str, account_key: str | None, symbol: str | None, payload: dict) -> int | None:
        """Insert the PENDING row; None when a review of this (kind, ref_id) already exists (idempotent)."""
        try:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO trade_reviews(kind, ref_id, created_ts, account_key, symbol, status, model, "
                "review_json, payload_json) VALUES(?,?,?,?,?,'PENDING',?,NULL,?)",
                (kind, ref_id, time.time(), account_key, symbol, self.settings.CLAUDE_MODEL,
                 json.dumps(payload, ensure_ascii=False, default=str)))
            return int(cur.lastrowid) if cur.rowcount else None
        except sqlite3.Error as e:
            self._warn_once("trade_review_db", f"trade_reviews nescris: {e!r}")
            return None

    def _finish(self, row_id: int, res: dict) -> None:
        try:
            self.db.execute("UPDATE trade_reviews SET status=?, model=?, review_json=?, created_ts=? WHERE id=?",
                            (res.get("status") or "ERROR", res.get("model"),
                             json.dumps(res, ensure_ascii=False, default=str), time.time(), row_id))
        except sqlite3.Error as e:
            self._warn_once("trade_review_db", f"trade_reviews nescris: {e!r}")

    def _row(self, row_id: int) -> dict | None:
        try:
            r = self.db.query_one("SELECT * FROM trade_reviews WHERE id=?", (row_id,))
        except sqlite3.Error:
            return None
        return self._public(r) if r else None

    @staticmethod
    def _public(r: dict) -> dict:
        return {"id": r["id"], "kind": r["kind"], "ref_id": r["ref_id"], "created_at_utc": iso(r["created_ts"]),
                "account_key": r["account_key"], "symbol": r["symbol"], "status": r["status"], "model": r["model"],
                "review": _loads(r["review_json"], None), "payload": _loads(r["payload_json"], None)}

    def list_payload(self, limit: int = 50, kind: str | None = None, with_payload: bool = False) -> list[dict]:
        sql = "SELECT * FROM trade_reviews"
        params: list = []
        if kind:
            sql += " WHERE kind=?"
            params.append(kind)
        sql += " ORDER BY created_ts DESC, id DESC LIMIT ?"
        params.append(int(limit))
        try:
            rows = self.db.query(sql, params)
        except sqlite3.Error:
            return []
        out = []
        for r in rows:
            d = self._public(r)
            if not with_payload:
                d.pop("payload", None)
            out.append(d)
        return out

    # ---- payloads --------------------------------------------------------------------------------------------------
    def _trade_row(self, trade_id: int) -> dict | None:
        return self.db.query_one("SELECT * FROM trades WHERE id=?", (int(trade_id),))

    def _recent_trades(self, account_key: str | None, before_id: int | None, symbol: str | None = None,
                       limit: int = RECENT_TRADES) -> list[dict]:
        sql = "SELECT * FROM trades WHERE 1=1"
        params: list = []
        if account_key:
            sql += " AND account_key=?"
            params.append(account_key)
        if before_id is not None:
            sql += " AND id<?"
            params.append(int(before_id))
        if symbol:
            sql += " AND symbol=?"
            params.append(symbol)
        sql += " ORDER BY closed_at_utc DESC, id DESC LIMIT ?"
        params.append(int(limit))
        try:
            return [trade_brief(r) for r in self.db.query(sql, params)]
        except sqlite3.Error:
            return []

    def _path_stats(self, trade: dict) -> dict | None:
        """MFE / MAE / minutes under water from the 1-minute trade path (live tracker), when it exists."""
        if trade.get("ticket") is None:
            return None
        try:
            r = self.db.query_one(
                "SELECT COUNT(*) AS n, MAX(mfe_r) AS mfe, MAX(mae_r) AS mae, MAX(minutes) AS minutes, "
                "SUM(CASE WHEN r_now < 0 THEN 1 ELSE 0 END) AS neg, AVG(spread_pts) AS spread FROM trade_paths "
                "WHERE trade_id=? AND symbol=?", (str(trade["ticket"]), trade.get("symbol")))
        except sqlite3.Error:
            return None
        if not r or not r["n"]:
            return None
        n = int(r["n"])
        return {"samples": n, "mfe_r": round(float(r["mfe"] or 0), 3), "mae_r": round(float(r["mae"] or 0), 3),
                "minutes_tracked": r["minutes"], "minutes_under_water": int(r["neg"] or 0),
                "avg_spread_points": round(float(r["spread"]), 1) if r["spread"] is not None else None}

    def _signal_brief(self, signal_id: str | None) -> dict | None:
        if not signal_id:
            return None
        sig = self.engine.get_signal(signal_id)
        if not sig:
            return None
        ai = sig.get("ai") or {}
        keys = ("created_at_utc", "timeframe", "direction", "entry", "stop_loss", "take_profit", "rr", "spread_points",
                "probabilities", "reasons", "indicators", "strategy_version", "status", "status_reason")
        out = {k: sig.get(k) for k in keys}
        out["ai_at_entry"] = {"verdict": ai.get("verdict"), "status": ai.get("status"),
                              "explanation": ai.get("explanation"), "risks": ai.get("risks")}
        if sig.get("ml"):
            out["ml_gate"] = sig["ml"]
        try:
            out["events"] = [{"time_utc": ev.get("time_utc"), "type": ev.get("event_type"),
                              "detail": str(ev.get("detail") or "")[:200]}
                             for ev in self.engine.signal_events(signal_id)][-12:]
        except Exception:
            pass
        return out

    def _risk_brief(self) -> dict:
        rk = self.engine.rt.risk or {}
        keys = ("max_risk_per_trade_pct", "max_daily_loss_pct", "max_equity_drawdown_pct", "max_open_positions",
                "max_trades_per_day", "max_consecutive_losses", "max_spread_points", "news_blackout_minutes")
        st = self.engine.rt.strategy or {}
        return {k: rk.get(k) for k in keys if k in rk} | {
            "strategy": self.engine.active_strategy_name(), "signal_timeframe": st.get("signal_timeframe"),
            "htf_timeframe": st.get("htf_timeframe"), "break_even_at_r": st.get("break_even_at_r"),
            "max_hold_minutes": st.get("max_hold_minutes"), "rescue_enabled": st.get("rescue_enabled")}

    def build_trade_payload(self, trade_id: int) -> dict | None:
        tr = self._trade_row(trade_id)
        if not tr:
            return None
        brief = trade_brief(tr)
        recent = self._recent_trades(tr.get("account_key"), tr["id"])
        same = self._recent_trades(tr.get("account_key"), tr["id"], symbol=tr.get("symbol"), limit=SYMBOL_TRADES)
        return {
            "trade": brief,
            "signal": self._signal_brief(tr.get("signal_id")),
            "path": self._path_stats(tr),
            "recent_trades_summary": trades_summary(recent) if recent else None,
            "recent_trades_same_symbol": same or None,
            "settings": self._risk_brief(),
            "session": {k: self.engine.session().get(k) for k in ("start", "end", "tz")},
            "note": "Rezultat simulat (PAPER), nu bani reali." if tr.get("mode") == "PAPER" else None,
        }

    def _day_bounds(self, day: str) -> tuple[str, str] | None:
        try:
            d = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            return None
        tz = scheduler._tz(self.engine.tz())
        start = datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(timezone.utc)
        end = datetime.fromtimestamp(start.timestamp() + 86400 + 3600, timezone.utc).astimezone(tz)
        end = datetime(end.year, end.month, end.day, tzinfo=tz).astimezone(timezone.utc)
        return iso(start), iso(end)

    def _previous_daily(self, day: str, account_key: str | None) -> dict | None:
        """The latest DONE daily review of an earlier day (same account), for continuity of lessons."""
        ref = f"{account_key}:{day}" if account_key else day
        sql = "SELECT ref_id, review_json FROM trade_reviews WHERE kind='DAILY' AND status='DONE' AND ref_id<?"
        params: list = [ref]
        if account_key:
            sql += " AND account_key=?"
            params.append(account_key)
        try:
            r = self.db.query_one(sql + " ORDER BY ref_id DESC LIMIT 1", params)
        except sqlite3.Error:
            return None
        if not r:
            return None
        rv = _loads(r["review_json"], {}) or {}
        return {"day": r["ref_id"].split(":")[-1], "headline": rv.get("headline"), "patterns": rv.get("patterns"),
                "hypotheses_to_test": rv.get("hypotheses_to_test")}

    def build_daily_payload(self, day: str, account_key: str | None) -> dict | None:
        bounds = self._day_bounds(day)
        if not bounds:
            return None
        start, end = bounds
        sql = "SELECT * FROM trades WHERE closed_at_utc >= ? AND closed_at_utc < ?"
        params: list = [start, end]
        if account_key:
            sql += " AND account_key=?"
            params.append(account_key)
        sql += " ORDER BY closed_at_utc, id"
        try:
            trades = [trade_brief(r) for r in self.db.query(sql, params)]
        except sqlite3.Error:
            trades = []
        for b in trades:
            b["path"] = self._path_stats(b)
            b["ai_verdict_at_entry"] = ((self.engine.get_signal(b["signal_id"]) or {}).get("ai") or {}).get("verdict") \
                if b.get("signal_id") else None
            b["trade_review"] = self._trade_review_brief(b.get("id"))
        try:
            shadow = self.engine.shadow_stats(days=1, now=_ts(end))
            shadow = {k: shadow.get(k) for k in ("total", "labeled", "by_category", "by_verdict", "by_blocker")}
        except Exception as e:
            shadow = {"error": str(e)[:200]}
        try:
            perf30 = self.engine.perf_stats(account_key, days=30, now=_ts(end)).get("summary")
        except Exception:
            perf30 = None
        if not trades and not (shadow.get("labeled") or shadow.get("total")):
            return None
        return {
            "day": day, "tz": self.engine.tz(), "account_key": account_key,
            "mode": self.engine.mode(), "symbols": self.engine.rt.active_symbols(),
            "trades": trades[:MAX_PAYLOAD_TRADES], "summary": trades_summary(trades),
            "last_30_days": perf30, "shadow_labels": shadow,
            "settings": self._risk_brief(), "previous_review": self._previous_daily(day, account_key),
            "note": "Rezultate simulate (PAPER), nu bani reali." if self.engine.mode() == "PAPER" else None,
        }

    def _trade_review_brief(self, trade_id) -> dict | None:
        if trade_id is None:
            return None
        try:
            r = self.db.query_one("SELECT review_json FROM trade_reviews WHERE kind='TRADE' AND ref_id=? AND status='DONE'",
                                  (str(trade_id),))
        except sqlite3.Error:
            return None
        rv = _loads(r["review_json"], None) if r else None
        if not rv:
            return None
        return {k: rv.get(k) for k in ("process_quality", "outcome_class", "exit_assessment", "tags")}

    # ---- jobs ------------------------------------------------------------------------------------------------------
    async def review_trade(self, trade_id: int) -> dict | None:
        """Analyse one closed trade; returns the public review row (None when skipped / duplicate)."""
        try:
            payload = await asyncio.to_thread(self.build_trade_payload, trade_id)
            if not payload:
                return None
            tr = payload["trade"]
            row_id = self._claim("TRADE", str(trade_id), None if not tr.get("mode") else
                                 ("PAPER" if tr["mode"] == "PAPER" else self.engine.account_key("DEMO")),
                                 tr.get("symbol"), payload)
            if row_id is None:
                return None
            res = await self.engine.ai.review_trade(payload)
            self._finish(row_id, res)
            if res.get("status") == "DONE":
                self.engine.rt.log("INFO", "ai", f"Analiză tranzacție #{trade_id} {tr.get('symbol')}: proces "
                                                 f"{res.get('process_quality')} · {res.get('outcome_class')}")
                if tr.get("signal_id"):
                    self.engine.add_signal_event(tr["signal_id"], "AI_TRADE_REVIEW",
                                                 f"{res.get('process_quality')}: {str(res.get('summary') or '')[:300]}")
                self.engine.telegram.enqueue(f"{tr.get('mode')}:{tr.get('ticket')}", "TRADE_REVIEW",
                                             fmt_trade_review(tr, res), signal_id=tr.get("signal_id"))
            elif res.get("status") == "ERROR":
                self.engine.rt.log("WARN", "ai", f"Analiza tranzacției #{trade_id} a eșuat: {res.get('error')}")
            row = self._row(row_id)
            self.engine.rt.bus.publish("trade_review", row)
            return row
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._warn_once("trade_review_job", f"analiza tranzacției #{trade_id} a eșuat: {e!r}")
            return None

    async def review_day(self, day: str, account_key: str | None = None, force: bool = False) -> dict | None:
        """Daily review of one local day (YYYY-MM-DD). One per (account, day); `force` re-runs it."""
        if self._daily_running:
            return None
        self._daily_running = True
        try:
            key = account_key or self.engine.account_key()
            ref = f"{key}:{day}" if key else day
            if force:
                try:
                    self.db.execute("DELETE FROM trade_reviews WHERE kind='DAILY' AND ref_id=?", (ref,))
                except sqlite3.Error:
                    pass
            payload = await asyncio.to_thread(self.build_daily_payload, day, key)
            if not payload:
                return None
            row_id = self._claim("DAILY", ref, key, None, payload)
            if row_id is None:
                return None
            res = await self.engine.ai.review_day(payload)
            self._finish(row_id, res)
            if res.get("status") == "DONE":
                self.engine.rt.log("INFO", "ai", f"Bilanț zilnic {day}: {str(res.get('headline') or '')[:160]}")
                self.engine.telegram.enqueue(ref, "DAILY_REVIEW", fmt_daily_review(day, res, payload.get("summary")))
            elif res.get("status") == "ERROR":
                self.engine.rt.log("WARN", "ai", f"Bilanțul zilnic {day} a eșuat: {res.get('error')}")
            row = self._row(row_id)
            self.engine.rt.bus.publish("trade_review", row)
            return row
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._warn_once("daily_review_job", f"bilanțul zilnic {day} a eșuat: {e!r}")
            return None
        finally:
            self._daily_running = False
