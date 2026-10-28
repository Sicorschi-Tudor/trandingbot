"""Orchestrator: analysis on bar close, AI review, risk, intents, position management, notifications."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from datetime import datetime, timedelta

from . import indicators as ind
from . import risk as risk_mod
from . import scheduler, strategy
from .ai_claude import ClaudeReviewer
from .config import STRATEGY_VERSION, TF_SECONDS, VERSION, Settings, iso, parse_iso, utcnow
from .db import DB
from .executor import Executor
from .paper import PaperBroker
from .position_manager import PositionManager
from .simfeed import SimFeed
from .state import Runtime
from .telegram import (TelegramNotifier, fmt_daily_start, fmt_intent, fmt_modify, fmt_risk_alert, fmt_signal,
                       fmt_trade)

HORIZON_MINUTES = 60
SIGNAL_FRESH_S = 90
WARMUP_S = 30.0  # no new signals right after process start
CALENDAR_STALE_S = 1800.0
NEWS_BE_WINDOW_S = 300.0  # high-impact event this close -> move profitable positions to break-even
NEWS_BE_MIN_R = 0.3
NEUTRAL_R = 0.1  # |net| below this many R counts as break-even (neither loss nor win)
NEUTRAL_EQUITY_FRAC = 0.0005  # fallback when the initial risk is unknown: 0.05% of equity
DEAL_REASON = {4: "SL", 5: "TP", 3: "BOT", 0: "MANUAL", 1: "MANUAL", 2: "MANUAL", 6: "SO"}


class EngineError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def mask_login(login) -> str:
    s = str(login or "")
    return "****" + s[-4:] if s else "****"


class Engine:
    def __init__(self, settings: Settings, db: DB):
        self.settings = settings
        self.db = db
        self.rt = Runtime(settings, db)
        self.paper = PaperBroker(settings.PAPER_START_BALANCE, settings.MT5_MAGIC, on_close=self._on_paper_close)
        self.paper.on_change = self._mark_paper_dirty
        self.executor = Executor(self.rt, self.paper, deviation_points=settings.MAX_SLIPPAGE_POINTS)
        self.executor.on_update = self._on_intent_update
        self.ai = ClaudeReviewer(settings)
        self.telegram = TelegramNotifier(settings, db, log_fn=self.rt.log)
        self.pm = PositionManager(self)
        self.sim: SimFeed | None = SimFeed(self.rt) if settings.data_source == "sim" else None
        self.tasks: list[asyncio.Task] = []
        self.signal_tasks: set[asyncio.Task] = set()
        self.last_analyzed_bar: dict[str, int] = {}
        self.known_mt5_positions: dict[int, dict] = {}
        self._paper_dirty = False
        self._prev_session_open: bool | None = None
        self._prev_bridge_connected: bool | None = None
        self._positions_sig: str | None = None
        self._equity_cache: dict[str, dict] = {}
        self._warmup_done = False
        self._cal_warn_ts = 0.0
        self.rt.quote_hooks.append(self._on_quote)

    # ---- lifecycle -------------------------------------------------------
    def startup(self) -> None:
        self.rt.load_runtime_config()
        self.paper.load(self.db.get_setting("paper_state"))
        self.rt.load_bars_from_db()
        s = self.settings
        if s.TRADING_MODE.strip().upper() == "LIVE":
            self.rt.log("WARN", "engine", "TRADING_MODE=LIVE este refuzat în v1 → pornesc în PAPER")
        self.rt.log("INFO", "engine",
                    f"Pornire v{VERSION}: sursă={s.data_source} ({s.feed_label}), mod={self.rt.trading['mode']}, "
                    f"execuție={'armată' if self.rt.trading['execution_enabled'] else 'dezarmată'}, "
                    f"simboluri={','.join(self.rt.active_symbols())}, Claude={'da' if self.ai.key_set else 'fără cheie'}, "
                    f"Telegram={'activ' if self.telegram.active else 'inactiv'}")
        if not s.ADMIN_TOKEN:
            self.rt.log("WARN", "engine", "ADMIN_TOKEN nu este setat — acțiunile de administrare sunt blocate")

    async def start(self) -> None:
        if self.sim:
            self.sim.ensure_symbols()
            self.tasks.append(asyncio.create_task(self.sim.run(), name="simfeed"))
        self.tasks.append(asyncio.create_task(self._loop(), name="engine"))
        self.tasks.append(asyncio.create_task(self.telegram.worker(), name="telegram"))
        self.tasks.append(asyncio.create_task(self._broadcaster(), name="status"))

    async def stop(self) -> None:
        for t in self.tasks + list(self.signal_tasks):
            t.cancel()
        for t in self.tasks + list(self.signal_tasks):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self.rt.flush_bars()
        self._save_paper()
        await self.telegram.aclose()

    # ---- helpers ---------------------------------------------------------
    def _mark_paper_dirty(self) -> None:
        self._paper_dirty = True

    def _save_paper(self) -> None:
        self.db.set_setting("paper_state", self.paper.to_dict())
        self._paper_dirty = False

    def now_utc(self) -> datetime:
        return utcnow()

    def session(self) -> dict:
        return scheduler.session_status(self.rt.schedule, self.now_utc(), self.rt.session_override)

    def tz(self) -> str:
        return self.rt.schedule.get("tz") or self.settings.DEFAULT_TIMEZONE

    def mode(self) -> str:
        return self.rt.trading["mode"]

    def mt5_account_public(self) -> dict | None:
        a = self.rt.mt5_account
        if not a:
            return None
        return {
            "login_masked": mask_login(a.get("login")), "server": a.get("server"), "company": a.get("company"),
            "is_demo": a.get("trade_mode") == 0, "currency": a.get("currency"), "balance": a.get("balance"),
            "equity": a.get("equity"), "margin": a.get("margin"), "margin_free": a.get("margin_free"),
            "leverage": a.get("leverage"), "profit": a.get("profit"),
        }

    def risk_account(self, mode: str | None = None) -> tuple[str, dict | None]:
        mode = mode or self.mode()
        if mode == "DEMO":
            acc = self.mt5_account_public()
            login = (self.rt.mt5_account or {}).get("login")
            return f"DEMO:{login}", acc
        return "PAPER", self.paper.account()

    # ---- positions -------------------------------------------------------
    def mt5_positions_public(self) -> list[dict]:
        out = []
        for p in self.rt.mt5_positions:
            out.append({
                "ticket": int(p.get("ticket", 0)), "symbol": p.get("symbol"),
                "side": "BUY" if int(p.get("type", 0)) == 0 else "SELL", "volume": p.get("volume"),
                "price_open": p.get("price_open"), "price_current": p.get("price_current"), "sl": p.get("sl"),
                "tp": p.get("tp"), "profit": p.get("profit"), "swap": p.get("swap"),
                "opened_at_utc": iso(p.get("time")) if p.get("time") else None, "source": "MT5",
                "bot_owned": int(p.get("magic") or 0) == self.settings.MT5_MAGIC, "comment": p.get("comment") or "",
            })
        return out

    def positions_payload(self) -> dict:
        return {"open": self.paper.public_positions() + self.mt5_positions_public(), "pending_commands": self.executor.pending()}

    def bot_positions(self, mode: str | None = None) -> list[dict]:
        mode = mode or self.mode()
        paper = self.paper.public_positions()
        mt5 = [p for p in self.mt5_positions_public() if p["bot_owned"]]
        if mode == "PAPER":
            return paper
        if mode == "DEMO":
            return mt5
        return paper + mt5

    def open_exposures(self, mode: str | None = None) -> list[tuple[str, str]]:
        """mode="ALL": paper + MT5 bot positions and in-flight opens of both modes (mode-independent)."""
        mode = mode or self.mode()
        out = [(p["symbol"], p["side"]) for p in self.bot_positions(mode)]
        if mode in ("PAPER", "DEMO"):
            out += self.executor.pending_open_exposures(mode)
        elif mode == "ALL":
            out += self.executor.pending_open_exposures("PAPER") + self.executor.pending_open_exposures("DEMO")
        return out

    def open_risk_money(self, mode: str) -> float:
        """Money at risk on open bot positions + in-flight opens of this account; SL at/after BE counts 0."""
        _, acc = self.risk_account(mode)
        equity = float((acc or {}).get("equity") or 0)
        fallback = equity * float(self.rt.risk.get("max_risk_per_trade_pct", 0.25)) / 100.0
        items = [(p["symbol"], p["side"], p.get("price_open"), p.get("sl"), p.get("volume")) for p in self.bot_positions(mode)]
        for it in self.executor.pending():
            if it.get("kind") != "OPEN" or it.get("mode") != mode:
                continue
            q = self.rt.quotes.get(it.get("symbol") or "") or {}
            px = q.get("ask") if it.get("side") == "BUY" else q.get("bid")
            items.append((it.get("symbol"), it.get("side"), px, it.get("sl"), it.get("volume")))
        total = 0.0
        for sym, side, px, sl, vol in items:
            rm = None
            if px:
                rm = risk_mod.position_risk_money(side, float(px), sl, float(vol or 0), self.rt.quotes.get(sym) or {})
            total += fallback if rm is None else rm
        return total

    def has_bot_position(self, symbol: str, mode: str | None = None) -> bool:
        if any(p["symbol"] == symbol for p in self.bot_positions(mode)):
            return True
        return self.executor.has_pending_open(symbol)

    def set_mt5_positions(self, positions: list[dict]) -> None:
        self.rt.mt5_positions = positions
        self.rt.mt5_positions_ts = time.time()
        for p in positions:
            try:
                self.known_mt5_positions[int(p["ticket"])] = p
            except (KeyError, ValueError, TypeError):
                pass
        if len(self.known_mt5_positions) > 500:
            for k in list(self.known_mt5_positions)[:-200]:
                self.known_mt5_positions.pop(k, None)

    # ---- trades ----------------------------------------------------------
    def _insert_trade(self, deal_key: str, tr: dict) -> bool:
        try:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO trades(deal_key, ticket, symbol, side, volume, price_open, price_close, profit, "
                "commission, swap, opened_at_utc, closed_at_utc, close_reason, mode, signal_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (deal_key, tr.get("ticket"), tr.get("symbol"), tr.get("side"), tr.get("volume"), tr.get("price_open"),
                 tr.get("price_close"), tr.get("profit"), tr.get("commission"), tr.get("swap"), tr.get("opened_at_utc"),
                 tr.get("closed_at_utc"), tr.get("close_reason"), tr.get("mode"), tr.get("signal_id")),
            )
            if cur.rowcount:
                tr["id"] = cur.lastrowid
                return True
        except sqlite3.Error as e:
            self.rt.log("ERROR", "db", f"insert trade failed: {e!r}")
        return False

    def _on_paper_close(self, trade: dict, pos: dict) -> None:
        if self._insert_trade(f"PAPER:{trade['ticket']}", trade):
            self._announce_trade(trade)

    def _announce_trade(self, trade: dict, notify: bool = True) -> None:
        net = (trade.get("profit") or 0) + (trade.get("commission") or 0) + (trade.get("swap") or 0)
        self.rt.log("INFO", "trades", f"Poziție închisă {trade['mode']} {trade['symbol']} {trade['side']} {trade['volume']} "
                                      f"motiv={trade['close_reason']} net={net:+.2f}")
        self.rt.bus.publish("trade", trade)
        if trade.get("signal_id"):
            self.add_signal_event(trade["signal_id"], "POSITION_CLOSED", f"{trade['close_reason']} net {net:+.2f}")
        if notify:
            self.telegram.enqueue(f"{trade.get('mode')}:{trade.get('ticket')}", "POSITION_CLOSED", fmt_trade(trade, self.tz()))

    def ingest_deals(self, deals: list[dict]) -> None:
        magic = self.settings.MT5_MAGIC
        ins = {}
        bot_in = set()
        for d in deals:
            try:
                if int(d.get("entry", -1)) == 0:
                    pid = int(d.get("position_id") or 0)
                    ins[pid] = d
                    if int(d.get("magic") or 0) == magic:
                        bot_in.add(pid)
            except (ValueError, TypeError):
                continue
        for d in deals:
            try:
                entry = int(d.get("entry", -1))
                if entry not in (1, 2, 3):
                    continue
                pos_id = int(d.get("position_id") or 0)
                if int(d.get("magic") or 0) != magic and not self._is_bot_position_id(pos_id, bot_in):
                    continue  # manual/stop-out exits of bot positions carry magic 0 but are accepted above
                deal_type = int(d.get("type", 0))
                side = "BUY" if deal_type == 1 else "SELL"
                opened = ins.get(pos_id)
                known = self.known_mt5_positions.get(pos_id) or {}
                price_open = (opened or {}).get("price") or known.get("price_open")
                opened_t = (opened or {}).get("time") or known.get("time")
                oi = self.executor.open_intent_for_ticket(pos_id) or self.executor.open_intent_by_comment(known.get("comment") or "")
                tr = {
                    "ticket": pos_id, "symbol": d.get("symbol"), "side": side, "volume": d.get("volume"),
                    "price_open": price_open, "price_close": d.get("price"), "profit": float(d.get("profit") or 0),
                    "commission": float(d.get("commission") or 0), "swap": float(d.get("swap") or 0),
                    "opened_at_utc": iso(opened_t) if opened_t else None, "closed_at_utc": iso(d.get("time") or time.time()),
                    "close_reason": DEAL_REASON.get(int(d.get("reason", -1)), "UNKNOWN"), "mode": "DEMO",
                    "signal_id": (oi or {}).get("signal_id"),
                }
                if self._insert_trade(f"MT5:{int(d['ticket'])}", tr):
                    recent = (d.get("time") or 0) >= self.rt.started_ts - 120
                    self._announce_trade(tr, notify=recent)
            except (KeyError, ValueError, TypeError) as e:
                self.rt.log("WARN", "bridge", f"deal invalid ignorat: {e!r}")

    def _is_bot_position_id(self, pos_id: int, bot_in: set[int]) -> bool:
        if not pos_id:
            return False
        if pos_id in bot_in:
            return True
        known = self.known_mt5_positions.get(pos_id)
        if known and int(known.get("magic") or 0) == self.settings.MT5_MAGIC:
            return True
        return self.executor.open_intent_for_ticket(pos_id) is not None

    def list_trades(self, limit: int = 100) -> list[dict]:
        return self.db.query(
            "SELECT id, ticket, symbol, side, volume, price_open, price_close, profit, commission, swap, opened_at_utc, "
            "closed_at_utc, close_reason, mode, signal_id FROM trades ORDER BY closed_at_utc DESC, id DESC LIMIT ?", (limit,))

    # ---- day stats -------------------------------------------------------
    def day_start_iso(self) -> str:
        return iso(scheduler.local_day_start_utc(self.now_utc(), self.settings.DEFAULT_TIMEZONE))

    def trades_today(self, mode: str) -> int:
        r = self.db.query_one(
            "SELECT COUNT(*) AS n FROM intents WHERE kind='OPEN' AND mode=? AND created_at_utc >= ? "
            "AND state IN ('QUEUED','DELIVERED','FILLED','UNKNOWN','PAPER_FILLED')", (mode, self.day_start_iso()))
        return int(r["n"]) if r else 0

    def closed_today(self, mode: str | None = None) -> list[dict]:
        if mode:
            return self.db.query("SELECT profit, commission, swap FROM trades WHERE closed_at_utc >= ? AND mode=? "
                                 "ORDER BY closed_at_utc", (self.day_start_iso(), mode))
        return self.db.query("SELECT profit, commission, swap FROM trades WHERE closed_at_utc >= ?", (self.day_start_iso(),))

    def consecutive_losses(self, mode: str) -> int:
        return self.loss_streak(mode)[0]

    def _trade_outcome(self, r: dict, equity: float) -> int:
        """-1 loss, +1 win, 0 neutral (|net| < 0.1R; 0.05% of equity when the initial risk is unknown)."""
        net = (r["profit"] or 0) + (r["commission"] or 0) + (r["swap"] or 0)
        oi = self.executor.open_intent_for_ticket(int(r["ticket"])) if r["ticket"] is not None else None
        init_risk = None
        if oi and r["price_open"] and r["volume"]:
            init_risk = risk_mod.position_risk_money(r["side"], float(r["price_open"]), oi.get("sl"), float(r["volume"]),
                                                     self.rt.quotes.get(r["symbol"]) or {})
        if init_risk:
            neutral = abs(net) < NEUTRAL_R * init_risk
        elif equity > 0:
            neutral = abs(net) < NEUTRAL_EQUITY_FRAC * equity
        else:
            neutral = net == 0
        if neutral:
            return 0
        return -1 if net < 0 else 1

    def loss_streak(self, mode: str, now: float | None = None) -> tuple[int, float | None]:
        """(consecutive losses of this account, ts when the pause ends | None = rest of day).
        Keyed per account (PAPER / DEMO:<login>); the streak resets once the pause after the last loss is over."""
        now = now or time.time()
        key, acc = self.risk_account(mode)
        trade_mode = "DEMO" if mode == "DEMO" else "PAPER"
        login = (self.rt.mt5_account or {}).get("login") if trade_mode == "DEMO" else None
        equity = float((acc or {}).get("equity") or 0)
        max_cl = int(self.rt.risk.get("max_consecutive_losses", 3))
        pause_s = int(self.rt.risk.get("loss_streak_pause_minutes", 90) or 0) * 60
        since = self.day_start_iso() if pause_s <= 0 else iso(now - max(86400, pause_s + 3600))
        rows = self.db.query("SELECT ticket, symbol, side, volume, price_open, profit, commission, swap, closed_at_utc "
                             "FROM trades WHERE mode=? AND closed_at_utc >= ? ORDER BY closed_at_utc DESC, id DESC LIMIT 200",
                             (trade_mode, since))
        streak, last_loss = 0, None
        for r in reversed(rows):
            if login is not None and r["ticket"] is not None:
                oi = self.executor.open_intent_for_ticket(int(r["ticket"])) or {}
                acct = oi.get("account_login")
                if acct is not None and str(acct) != str(login):
                    continue  # trade of another MT5 account (unknown account = counted, conservative)
            closed = _ts(r["closed_at_utc"])
            if pause_s > 0 and streak >= max_cl and last_loss is not None and closed is not None and closed >= last_loss + pause_s:
                streak = 0
            out = self._trade_outcome(r, equity)
            if out < 0:
                streak += 1
                last_loss = closed if closed is not None else now
            elif out > 0:
                streak = 0
        if streak >= max_cl:
            if pause_s <= 0:
                return streak, None
            until = (last_loss or now) + pause_s
            if now >= until:
                return 0, None
            return streak, until
        return streak, None

    def update_equity_marks(self) -> None:
        for mode in ("PAPER", "DEMO"):
            key, acc = self.risk_account(mode)
            if not acc or acc.get("equity") is None or (mode == "DEMO" and not self.rt.bridge_connected()):
                continue
            if mode == "DEMO" and self.account_settling():
                continue  # account switch in progress: equity may still belong to the previous login
            eq = float(acc["equity"])
            day = scheduler.local_day(self.now_utc(), self.settings.DEFAULT_TIMEZONE)
            cur = self._equity_cache.get(key)
            if cur is None:
                cur = self.db.query_one("SELECT * FROM equity_marks WHERE account_key=?", (key,))
            changed = False
            if cur is None:
                cur = {"account_key": key, "day": day, "day_start_equity": eq, "peak_equity": eq}
                changed = True
            if cur["day"] != day:
                cur["day"], cur["day_start_equity"] = day, eq
                changed = True
            if eq > cur["peak_equity"]:
                cur["peak_equity"] = eq
                changed = True
            self._equity_cache[key] = cur
            if changed:
                self.db.execute(
                    "INSERT INTO equity_marks(account_key, day, day_start_equity, peak_equity, updated_at_utc) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(account_key) DO UPDATE SET day=excluded.day, day_start_equity=excluded.day_start_equity, "
                    "peak_equity=excluded.peak_equity, updated_at_utc=excluded.updated_at_utc",
                    (key, cur["day"], cur["day_start_equity"], cur["peak_equity"], iso(utcnow())))

    def account_settling(self) -> bool:
        """True during the settle window after an MT5 login switch (same 15 s as bridge.ACCOUNT_SETTLE_S)."""
        return time.time() - float(self.rt.bridge.get("account_changed_ts") or 0) < 15.0

    def equity_marks(self, key: str) -> dict | None:
        return self._equity_cache.get(key) or self.db.query_one("SELECT * FROM equity_marks WHERE account_key=?", (key,))

    # ---- risk ------------------------------------------------------------
    def risk_check(self, symbol: str, side: str, entry: float, sl: float, tp: float, test_order: bool = False,
                   mode: str | None = None) -> dict:
        mode = mode or self.mode()
        self.update_equity_marks()
        key, acc = self.risk_account(mode)
        streak, pause_until = self.loss_streak(mode)
        news_block, news_note = self.news_check(symbol)
        marks = self.equity_marks(key) or {}
        bots = self.bot_positions(mode)
        n_open = len(bots)
        if mode == "DEMO":
            n_open += self.executor.count_pending_open("DEMO", {int(p["ticket"]) for p in bots})
        ctx = risk_mod.RiskContext(
            mode=mode, execution_enabled=self.rt.trading["execution_enabled"],
            entries_paused=self.rt.trading["entries_paused"], side=side, entry=entry, sl=sl, tp=tp,
            symbol_info=dict(self.rt.quotes.get(symbol) or {}), account=acc, risk=self.rt.risk,
            bridge_connected=self.rt.bridge_connected(), terminal=self.rt.bridge.get("terminal"),
            mt5_account=self.rt.mt5_account, tick_age=self.rt.tick_age(symbol),
            symbol=symbol, open_exposures=self.open_exposures(mode),
            open_bot_positions=n_open, ea_magic_ok=self.rt.bridge.get("magic_ok") is True, has_bot_position_on_symbol=self.has_bot_position(symbol, mode),
            trades_today=self.trades_today(mode), day_start_equity=marks.get("day_start_equity"),
            peak_equity=marks.get("peak_equity"), consecutive_losses=streak, loss_pause_until=pause_until,
            open_risk_money=self.open_risk_money(mode), news_block=news_block, news_note=news_note, test_order=test_order,
            account_settling=mode == "DEMO" and self.account_settling(),
        )
        return risk_mod.evaluate(ctx)

    # ---- signals ---------------------------------------------------------
    def save_signal(self, sig: dict) -> None:
        self.db.execute("UPDATE signals SET status=?, data_json=? WHERE id=?", (sig["status"], json.dumps(sig), sig["id"]))
        self.rt.bus.publish("signal", sig)

    def get_signal(self, sid: str) -> dict | None:
        r = self.db.query_one("SELECT data_json FROM signals WHERE id=?", (sid,))
        return json.loads(r["data_json"]) if r else None

    def list_signals(self, limit: int = 100, symbol: str | None = None, direction: str | None = None) -> list[dict]:
        sql = "SELECT data_json FROM signals WHERE 1=1"
        params: list = []
        if symbol:
            sql += " AND symbol=?"
            params.append(symbol.upper())
        if direction:
            sql += " AND direction=?"
            params.append(direction.upper())
        sql += " ORDER BY created_at_utc DESC, rowid DESC LIMIT ?"
        params.append(limit)
        return [json.loads(r["data_json"]) for r in self.db.query(sql, params)]

    def signal_events(self, sid: str) -> list[dict]:
        return self.db.query("SELECT time_utc, event_type, detail FROM signal_events WHERE signal_id=? ORDER BY id", (sid,))

    def add_signal_event(self, sid: str, event_type: str, detail: str = "") -> None:
        self.db.execute("INSERT INTO signal_events(signal_id, time_utc, event_type, detail) VALUES(?,?,?,?)",
                        (sid, iso(utcnow()), event_type, detail))

    def signals_this_session(self, symbol: str) -> int:
        start = scheduler.session_start_utc(self.rt.schedule, self.now_utc(), self.settings.DEFAULT_TIMEZONE)
        r = self.db.query_one("SELECT COUNT(*) AS n FROM signals WHERE symbol=? AND created_at_utc >= ?", (symbol, iso(start)))
        return int(r["n"]) if r else 0

    def create_signal(self, a: dict) -> dict | None:
        now = utcnow()
        expiry_min = int(self.rt.strategy.get("signal_expiry_minutes", 15))
        expires = now + timedelta(minutes=expiry_min)
        expires_local = expires.astimezone(scheduler._tz(self.tz())).strftime("%H:%M")
        ai_on = self.ai.enabled and self.ai.key_set
        sig = {
            "id": str(uuid.uuid4()), "created_at_utc": iso(now), "symbol": a["symbol"], "timeframe": a["timeframe"],
            "bar_time": a["bar_time"], "direction": a["direction"], "horizon_minutes": HORIZON_MINUTES,
            "probabilities": a["probabilities"], "entry": a["entry"], "stop_loss": a["stop_loss"],
            "take_profit": a["take_profit"], "rr": a["rr"], "spread_points": a["spread_points"],
            "expires_at_utc": iso(expires), "invalidation": strategy.invalidation_text(a, expires_local),
            "reasons": a["reasons"], "indicators": a["indicators"],
            "ai": {"status": "PENDING" if ai_on else "DISABLED", "verdict": None,
                   "explanation": "" if ai_on else "Analiza Claude este dezactivată (lipsește ANTHROPIC_API_KEY sau CLAUDE_ENABLED=false).",
                   "risks": [], "model": self.settings.CLAUDE_MODEL},
            "status": "NEW", "status_reason": "", "mode": self.mode(), "feed_label": self.settings.feed_label,
            "strategy_version": STRATEGY_VERSION, "intent_id": None,
            # execution context at creation: a signal never executes after a later arm/mode change
            "created_ts": time.time(), "created_mode": self.mode(),
            "created_armed": bool(self.rt.trading["execution_enabled"]),
        }
        key = strategy.dedup_key(a["symbol"], a["timeframe"], a["bar_time"], a["direction"])
        try:
            self.db.execute(
                "INSERT INTO signals(id, created_at_utc, symbol, timeframe, bar_time, direction, status, dedup_key, data_json) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (sig["id"], sig["created_at_utc"], sig["symbol"], sig["timeframe"], sig["bar_time"], sig["direction"],
                 sig["status"], key, json.dumps(sig)))
        except sqlite3.IntegrityError:
            return None
        self.add_signal_event(sig["id"], "CREATED", f"{sig['direction']} {sig['symbol']} @ {sig['entry']} SL {sig['stop_loss']} TP {sig['take_profit']}")
        self.rt.log("INFO", "strategy", f"Semnal nou {sig['direction']} {sig['symbol']} {sig['timeframe']} "
                                        f"entry {sig['entry']} SL {sig['stop_loss']} TP {sig['take_profit']} ({sig['feed_label']})")
        self.rt.bus.publish("signal", sig)
        return sig

    def expire_signals(self) -> None:
        now = iso(utcnow())
        rows = self.db.query("SELECT data_json FROM signals WHERE status='NEW'")
        for r in rows:
            sig = json.loads(r["data_json"])
            if sig.get("ai", {}).get("status") == "PENDING":
                continue
            if sig["expires_at_utc"] and sig["expires_at_utc"] <= now:
                sig["status"] = "EXPIRED"
                sig["status_reason"] = (sig.get("status_reason") + " | " if sig.get("status_reason") else "") + "Semnal expirat"
                self.save_signal(sig)
                self.add_signal_event(sig["id"], "EXPIRED", "")

    # ---- warm-up / news ---------------------------------------------------
    def warmup_reason(self, symbol: str, now: float | None = None) -> str | None:
        """Why new signals are not allowed yet after a (re)start; None when ready."""
        now = now or time.time()
        rt = self.rt
        reasons = []
        left = WARMUP_S - (now - rt.started_ts)
        if left > 0:
            reasons.append(f"încă {left:.0f} s")
        if self.settings.data_source == "mt5_bridge" and int(rt.bridge.get("sync_count") or 0) < 1:
            reasons.append("aștept prima sincronizare cu EA")
        if not reasons and not self._warmup_done:
            self._warmup_done = True
            rt.log("INFO", "engine", "Încălzire după pornire încheiată — semnalele noi sunt permise")
        if symbol and any(k[0] == symbol and k[1] in (rt.strategy["signal_timeframe"], rt.strategy["htf_timeframe"]) for k in rt.hist_force):
            reasons.append("aștept reumplerea istoricului")
        return "; ".join(reasons) or None

    def calendar_fresh(self, now: float | None = None) -> bool:
        ts = getattr(self.rt, "calendar_ts", None)
        return ts is not None and (now or time.time()) - ts <= CALENDAR_STALE_S

    def news_check(self, symbol: str, now: float | None = None) -> tuple[str | None, str]:
        """(blackout reason | None, detail). A stale calendar does not block (warned in the log instead)."""
        now = now or time.time()
        minutes = int(self.rt.risk.get("news_blackout_minutes", 30) or 0)
        if minutes <= 0:
            return None, "blocare pe știri dezactivată"
        if self.settings.data_source == "mt5_bridge" and not self.calendar_fresh(now):
            return None, "calendar economic indisponibil — știrile nu au fost verificate"
        min_imp = int(self.rt.risk.get("news_min_importance", 3))
        block = risk_mod.news_blocker(symbol, getattr(self.rt, "calendar", None) or [], now, minutes, min_imp)
        return block, f"fără știri (impact ≥{min_imp}) în ±{minutes} min"

    def news_event_near(self, symbol: str, now: float | None = None, window_s: float = NEWS_BE_WINDOW_S) -> dict | None:
        """Nearest high-impact (3) event for one of the symbol's currencies within ±window_s."""
        now = now or time.time()
        ccys = set(risk_mod.symbol_currencies(symbol))
        best = None
        for ev in getattr(self.rt, "calendar", None) or []:
            try:
                t, imp, ccy = float(ev["t"]), int(ev.get("imp") or 0), str(ev.get("ccy") or "").upper()
            except (KeyError, TypeError, ValueError):
                continue
            if imp >= 3 and ccy in ccys and abs(t - now) <= window_s and (best is None or abs(t - now) < abs(best["t"] - now)):
                best = {**ev, "t": t, "ccy": ccy}
        return best

    def news_status(self) -> dict:
        now = time.time()
        rt = self.rt
        ts = getattr(rt, "calendar_ts", None)
        minutes = int(rt.risk.get("news_blackout_minutes", 30) or 0)
        min_imp = int(rt.risk.get("news_min_importance", 3))
        ccys = {c for sym in rt.active_symbols() for c in risk_mod.symbol_currencies(sym)}
        n24, nxt = 0, None
        for ev in getattr(rt, "calendar", None) or []:
            try:
                t, imp, ccy = float(ev["t"]), int(ev.get("imp") or 0), str(ev.get("ccy") or "").upper()
            except (KeyError, TypeError, ValueError):
                continue
            if ccy not in ccys:
                continue
            if imp >= min_imp and now <= t <= now + 86400:
                n24 += 1
            if imp >= 3 and t >= now - minutes * 60 and (nxt is None or t < nxt["t"]):
                nxt = {"t": int(t), "time_utc": iso(t), "ccy": ccy, "imp": imp, "name": str(ev.get("name") or ""),
                       "in_s": round(t - now)}
        blocked = []
        for sym in rt.active_symbols():
            b, _ = self.news_check(sym, now)
            if b:
                blocked.append({"symbol": sym, "reason": b})
        return {"ok": self.calendar_fresh(now), "events_next_24h": n24, "next_high": nxt,
                "age_s": round(now - ts) if ts is not None else None, "blackout_minutes": minutes,
                "min_importance": min_imp, "blocked": blocked}

    def _check_calendar(self, now: float) -> None:
        if (self.settings.data_source != "mt5_bridge" or not self._warmup_done
                or int(self.rt.risk.get("news_blackout_minutes", 30) or 0) <= 0 or self.calendar_fresh(now)):
            return
        if now - self._cal_warn_ts >= CALENDAR_STALE_S:
            self._cal_warn_ts = now
            self.rt.log("WARN", "news", "Calendar economic indisponibil — blocarea intrărilor pe știri nu se aplică "
                                        "(EA fără calendar sau date mai vechi de 30 min)")

    # ---- analysis --------------------------------------------------------
    def analyze_symbol(self, symbol: str, allow_signal: bool = True) -> dict:
        rt = self.rt
        tf, htf = rt.strategy["signal_timeframe"], rt.strategy["htf_timeframe"]
        now = time.time()
        sig_bars = rt.closed_bars(symbol, tf, now)[-400:]
        htf_bars = rt.closed_bars(symbol, htf, now)[-200:]
        news_block, _ = self.news_check(symbol, now)
        # positions of every mode count (after a restart/mode switch the other side's positions still exist)
        ctx = strategy.AnalysisContext(
            session_open=self.session()["open_now"], has_bot_position=self.has_bot_position(symbol, "ALL"),
            signals_this_session=self.signals_this_session(symbol), tick_age=rt.tick_age(symbol, now), now_ts=now,
            open_exposures=self.open_exposures("ALL"), corr_limit=int(rt.risk.get("max_same_currency_direction", 1)),
            news_block=news_block,
        )
        a = strategy.analyze(symbol, sig_bars, htf_bars, rt.quotes.get(symbol), rt.strategy, rt.risk, ctx)
        rt.last_analysis[symbol] = a
        rt.engine_info["last_analysis_utc"] = a["time_utc"]
        rt.bus.publish("analysis", a)
        if a["direction"] in ("BUY", "SELL"):
            warm = self.warmup_reason(symbol, now) if allow_signal else None
            if warm:
                a["reasons"].append(f"Încălzire după pornire ({warm}) — doar analiză, fără semnal nou.")
            elif allow_signal:
                sig = self.create_signal(a)
                if sig:
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = None
                    if loop is None:
                        asyncio.run(self.signal_pipeline(sig))
                    else:
                        task = loop.create_task(self.signal_pipeline(sig))
                        self.signal_tasks.add(task)
                        task.add_done_callback(self.signal_tasks.discard)
            else:
                a["reasons"].append("Bara este prea veche pentru a emite un semnal nou (doar analiză).")
        return a

    async def signal_pipeline(self, sig: dict) -> None:
        try:
            if sig["ai"]["status"] == "PENDING":
                ai = await self.ai.review(sig, self._ai_context(sig["symbol"]))
                sig = self.get_signal(sig["id"]) or sig
                sig["ai"] = ai
                self.add_signal_event(sig["id"], "AI_" + ai["status"], f"{ai.get('verdict')}: {ai.get('explanation', '')[:300]}")
                if ai["status"] == "ERROR":
                    self.rt.log("WARN", "ai", f"Claude indisponibil pentru semnalul {sig['id'][:8]}: {self.ai.last_error}")
                if ai.get("verdict") == "VETO" and self.settings.CLAUDE_VETO_ENABLED:
                    sig["status"] = "NO_TRADE"
                    sig["status_reason"] = "VETO Claude: " + (ai.get("explanation") or "")[:300]
                elif ai["status"] == "ERROR" and self.settings.CLAUDE_REQUIRED:
                    sig["status"] = "NO_TRADE"
                    sig["status_reason"] = "Claude este obligatoriu (CLAUDE_REQUIRED=true) și a eșuat"
                self.save_signal(sig)
            if sig["status"] == "NEW" and self.settings.CLAUDE_REQUIRED and sig["ai"]["status"] != "DONE":
                sig["status"] = "NO_TRADE"
                sig["status_reason"] = "Claude este obligatoriu (CLAUDE_REQUIRED=true) dar nu este disponibil/activat"
                self.save_signal(sig)
                self.add_signal_event(sig["id"], "NO_TRADE", sig["status_reason"])
            if sig["status"] == "NEW":
                self.try_execute(sig)
            sig = self.get_signal(sig["id"]) or sig
            self.telegram.enqueue(sig["id"], "NEW_SIGNAL", fmt_signal(sig, self.tz()), signal_id=sig["id"])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.rt.log("ERROR", "engine", f"pipeline semnal {sig.get('id', '')[:8]} eșuat: {e!r}")

    def _ai_context(self, symbol: str) -> dict:
        rt = self.rt
        q = rt.quotes.get(symbol) or {}
        bars = rt.closed_bars(symbol, rt.strategy["signal_timeframe"])[-12:]
        htf = rt.closed_bars(symbol, rt.strategy["htf_timeframe"])[-6:]
        a = rt.last_analysis.get(symbol) or {}
        return {
            "bid": q.get("bid"), "ask": q.get("ask"), "digits": q.get("digits"), "regime": a.get("regime"),
            "score": a.get("score"), "session_open": self.session()["open_now"],
            "recent_bars_signal_tf": [{"t": iso(b["t"]), "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]} for b in bars],
            "recent_bars_htf": [{"t": iso(b["t"]), "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]} for b in htf],
        }

    def try_execute(self, sig: dict) -> None:
        mode = self.mode()
        if mode not in ("PAPER", "DEMO") or not self.rt.trading["execution_enabled"]:
            sig["status_reason"] = (f"Doar semnal: mod {mode}, execuție "
                                    f"{'armată' if self.rt.trading['execution_enabled'] else 'dezarmată'}")
            self.save_signal(sig)
            self.add_signal_event(sig["id"], "NOT_EXECUTED", sig["status_reason"])
            return
        created = sig.get("created_ts") or _ts(sig.get("created_at_utc")) or 0
        # armed_ts: last disarmed->armed or mode change; process start counts as one
        armed_ts = float(self.rt.trading.get("armed_ts") or self.rt.started_ts)
        if (created < armed_ts or sig.get("created_mode") != mode
                or not sig.get("created_armed")):
            sig["status"], sig["status_reason"] = "NO_TRADE", "Semnal creat înainte de armare/schimbare de mod"
            self.save_signal(sig)
            self.add_signal_event(sig["id"], "NO_TRADE", sig["status_reason"])
            return
        if sig["expires_at_utc"] <= iso(utcnow()):
            sig["status"], sig["status_reason"] = "EXPIRED", "Semnal expirat înainte de execuție"
            self.save_signal(sig)
            return
        sym, side = sig["symbol"], sig["direction"]
        q = self.rt.quotes.get(sym) or {}
        if not q.get("bid"):
            sig["status"], sig["status_reason"] = "NO_TRADE", "Fără cotație la momentul execuției"
            self.save_signal(sig)
            return
        entry = q["ask"] if side == "BUY" else q["bid"]
        sl, tp = sig["stop_loss"], sig["take_profit"]
        sl_dist0 = abs(sig["entry"] - sl)
        sign = 1 if side == "BUY" else -1
        if (entry - sl) * sign <= 0 or (tp - entry) * sign <= 0 or abs(entry - sig["entry"]) > 0.5 * sl_dist0:
            sig["status"] = "NO_TRADE"
            sig["status_reason"] = f"Prețul s-a mutat prea mult față de intrarea semnalului ({sig['entry']} → {entry})"
            self.save_signal(sig)
            self.add_signal_event(sig["id"], "NO_TRADE", sig["status_reason"])
            return
        rc = self.risk_check(sym, side, entry, sl, tp)
        it = self.executor.submit_open(mode, sym, side, rc["volume"], sl, tp, q, rc, signal_id=sig["id"])
        sig = self.get_signal(sig["id"]) or sig
        sig["intent_id"] = it["id"]
        sig["mode"] = mode
        if it["state"] == "REJECTED_RISK":
            sig["status"] = "NO_TRADE"
            sig["status_reason"] = "Risk Engine: " + risk_mod.failed_summary(rc)
        elif it["state"] == "PAPER_FILLED":
            sig["status"] = "EXECUTED"
            sig["status_reason"] = f"PAPER executat @ {it['result']['price']} volum {it['volume']}"
        else:
            sig["status"] = "INTENT_CREATED"
            sig["status_reason"] = f"Ordin DEMO trimis către EA (volum {it['volume']})"
        self.save_signal(sig)
        self.add_signal_event(sig["id"], "INTENT_" + it["state"], sig["status_reason"])

    # ---- intents → signals / notifications -------------------------------
    def _on_intent_update(self, it: dict, prev: str | None) -> None:
        st = it["state"]
        if it["kind"] == "OPEN":
            if it.get("signal_id") and prev is not None:
                sig = self.get_signal(it["signal_id"])
                if sig:
                    if st == "FILLED":
                        sig["status"] = "EXECUTED"
                        sig["status_reason"] = f"Executat de broker @ {(it.get('result') or {}).get('price')}"
                    elif st == "FAILED":
                        sig["status"] = "REJECTED"
                        res = it.get("result") or {}
                        sig["status_reason"] = f"Respins: {res.get('retcode')} {res.get('retcode_desc')} {res.get('comment') or ''}".strip()
                    if st in ("FILLED", "FAILED"):
                        self.save_signal(sig)
                        self.add_signal_event(sig["id"], "INTENT_" + st, sig["status_reason"])
            if st in ("QUEUED", "FILLED", "PAPER_FILLED", "FAILED", "REJECTED_RISK", "UNKNOWN"):
                self.telegram.enqueue(it["id"], "INTENT_" + st, fmt_intent(it, self.tz()), signal_id=it.get("signal_id"))
        elif st in ("FAILED", "UNKNOWN"):
            self.telegram.enqueue(it["id"], "INTENT_" + st, fmt_intent(it, self.tz()))
        elif it["kind"] == "MODIFY" and st == "FILLED" and prev is not None:
            self.notify_modify(it, {"sl": it.get("sl"), "tp": it.get("tp"), "reason": it.get("reason") or "MODIFY"})
        if st == "UNKNOWN":
            self.risk_alert(f"Comanda {it['kind']} {it.get('symbol')} ({it['id'][:8]}) fără confirmare după 30 s. "
                            f"Starea se reconciliază prin poziții; nu se retrimite.", key=f"unknown:{it['id']}")

    def risk_alert(self, text: str, key: str) -> None:
        self.rt.log("WARN", "risk", text)
        self.telegram.enqueue(key, "RISK_ALERT", fmt_risk_alert(text))

    def notify_modify(self, pos: dict, action: dict) -> None:
        self.telegram.enqueue(f"{pos['ticket']}:{action['sl']}", "MODIFY", fmt_modify(pos, action))

    def fail_queued_commands(self, reason: str) -> None:
        self.executor.fail_queued(reason)

    # ---- manual controls -------------------------------------------------
    def find_position(self, ticket: int) -> tuple[str, dict] | None:
        p = self.paper.positions.get(int(ticket))
        if p:
            return "PAPER", {"ticket": p["ticket"], "symbol": p["symbol"], "side": p["side"], "volume": p["volume"],
                             "bot_owned": True, "signal_id": p.get("signal_id")}
        for mp in self.mt5_positions_public():
            if mp["ticket"] == int(ticket):
                return "MT5", mp
        return None

    def close_position(self, source: str, ticket: int, reason: str = "BOT") -> dict:
        found = self.find_position(ticket)
        if not found:
            raise EngineError(404, "Poziția nu există")
        src, p = found
        if not p.get("bot_owned"):
            raise EngineError(403, "Poziția nu aparține botului (magic diferit) — nu este atinsă")
        if src == "MT5":
            if not self.rt.bridge_connected():
                raise EngineError(409, "Bridge MT5 deconectat")
            if (self.rt.mt5_account or {}).get("trade_mode") != 0:
                raise EngineError(409, "Contul MT5 nu este DEMO")
            if self.executor.has_pending_for_ticket(int(ticket), kinds=("CLOSE",), states=("QUEUED", "DELIVERED")):
                raise EngineError(409, "Există deja o comandă de închidere în curs pentru această poziție")
        return self.executor.submit_close(src, int(ticket), p["symbol"], p["side"], p["volume"],
                                          self.rt.quotes.get(p["symbol"]), reason=reason)

    def close_all(self) -> list[dict]:
        out = []
        for p in self.paper.public_positions():
            out.append(self.close_position("PAPER", p["ticket"], reason="CLOSE_ALL"))
        if self.rt.bridge_connected() and (self.rt.mt5_account or {}).get("trade_mode") == 0:
            for p in self.mt5_positions_public():
                if not p["bot_owned"]:
                    continue
                if self.executor.has_pending_for_ticket(p["ticket"], kinds=("CLOSE",), states=("QUEUED", "DELIVERED")):
                    self.rt.log("WARN", "control", f"CLOSE ALL: ticket {p['ticket']} are deja o închidere în curs — sărit")
                    continue
                out.append(self.close_position("MT5", p["ticket"], reason="CLOSE_ALL"))
        elif any(p["bot_owned"] for p in self.mt5_positions_public()):
            self.rt.log("WARN", "control", "CLOSE ALL: pozițiile MT5 ale botului NU au fost închise (bridge deconectat sau cont non-demo)")
        self.rt.log("WARN", "control", f"CLOSE ALL: {len(out)} comenzi de închidere (doar poziții ale botului)")
        return out

    def set_mode(self, mode: str) -> None:
        mode = (mode or "").upper()
        if mode == "LIVE":
            raise EngineError(403, "LIVE disabled in v1")
        if mode not in ("SIGNAL_ONLY", "PAPER", "DEMO"):
            raise EngineError(400, "Mod invalid")
        if mode == "DEMO":
            if self.settings.data_source == "sim":
                raise EngineError(409, "DEMO indisponibil: DATA_SOURCE=sim (feed simulat)")
            if not self.rt.bridge_connected():
                raise EngineError(409, "DEMO indisponibil: bridge-ul MT5 nu este conectat")
            if (self.rt.mt5_account or {}).get("trade_mode") != 0:
                raise EngineError(409, "DEMO indisponibil: contul MT5 nu este de tip DEMO")
            if self.rt.bridge.get("magic_ok") is not True:
                raise EngineError(409, f"DEMO indisponibil: magic EA ({self.rt.bridge.get('magic')}) diferit de MT5_MAGIC ({self.settings.MT5_MAGIC})")
        prev = self.rt.trading["mode"]
        self.rt.trading["mode"] = mode
        if mode != prev:
            self.rt.trading["execution_enabled"] = False
            self.rt.trading["armed_ts"] = time.time()
        self.rt.save_trading()
        self.rt.log("WARN", "control", f"Mod schimbat {prev} → {mode} (execuția a fost dezarmată, armează explicit)")

    def set_execution(self, enabled: bool) -> None:
        if enabled and self.mode() == "DEMO":
            if not self.rt.bridge_connected() or (self.rt.mt5_account or {}).get("trade_mode") != 0:
                raise EngineError(409, "Nu pot arma execuția DEMO: bridge deconectat sau cont non-demo")
            if self.rt.bridge.get("magic_ok") is not True:
                raise EngineError(409, "Nu pot arma execuția DEMO: magic EA diferit de MT5_MAGIC")
        if enabled and not self.rt.trading["execution_enabled"]:
            self.rt.trading["armed_ts"] = time.time()
        self.rt.trading["execution_enabled"] = bool(enabled)
        self.rt.save_trading()
        self.rt.log("WARN", "control", f"Execuție {'ARMATĂ' if enabled else 'dezarmată'} (mod {self.mode()})")

    def set_paused(self, paused: bool) -> None:
        self.rt.trading["entries_paused"] = bool(paused)
        self.rt.save_trading()
        self.rt.log("WARN", "control", "Intrări noi OPRITE (kill switch)" if paused else "Intrări noi reluate")
        if paused:
            self.telegram.enqueue(f"pause:{int(time.time())}", "RISK_ALERT", fmt_risk_alert("Intrările noi au fost oprite manual (kill switch). Pozițiile existente rămân gestionate."))

    def test_order(self, symbol: str, side: str) -> dict:
        mode = self.mode()
        if mode not in ("PAPER", "DEMO"):
            raise EngineError(409, "Ordinul de test este permis doar în PAPER sau DEMO")
        side = (side or "").upper()
        if side not in ("BUY", "SELL"):
            raise EngineError(400, "side trebuie să fie BUY sau SELL")
        symbol = (symbol or "").upper()
        q = self.rt.quotes.get(symbol)
        if not q or not q.get("bid"):
            raise EngineError(409, f"Fără cotație pentru {symbol}")
        bars = self.rt.closed_bars(symbol, self.rt.strategy["signal_timeframe"])[-200:]
        atr = ind.last(ind.atr([b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars], 14)) if len(bars) >= 15 else None
        if not atr:
            raise EngineError(409, "ATR indisponibil (bare insuficiente)")
        digits = int(q.get("digits") or 5)
        entry = q["ask"] if side == "BUY" else q["bid"]
        sign = 1 if side == "BUY" else -1
        sl = round(entry - sign * float(self.rt.strategy["atr_sl_mult"]) * atr, digits)
        tp = round(entry + sign * float(self.rt.strategy["atr_tp_mult"]) * atr, digits)
        rc = self.risk_check(symbol, side, entry, sl, tp, test_order=True)
        self.rt.log("INFO", "control", f"Ordin de test {side} {symbol} ({mode})")
        return self.executor.submit_open(mode, symbol, side, rc["volume"], sl, tp, q, rc)

    # ---- status ----------------------------------------------------------
    def status(self) -> dict:
        rt, s = self.rt, self.settings
        term = rt.bridge.get("terminal") or {}
        age = rt.bridge_age()
        connected = rt.bridge_connected()
        mt5_acc = self.mt5_account_public() if connected else None
        paper_acc = self.paper.account()
        mode = self.mode()
        # account = real MT5 account only; paper account is reported separately
        account = mt5_acc
        closed = self.closed_today(mode if mode in ("PAPER", "DEMO") else None)
        sig_today = self.db.query_one("SELECT COUNT(*) AS n FROM signals WHERE created_at_utc >= ?", (self.day_start_iso(),))
        return {
            "app_env": s.APP_ENV, "version": VERSION, "data_source": s.data_source, "feed_label": s.feed_label,
            "bridge": {
                "connected": connected, "last_sync_utc": iso(rt.bridge["last_sync_ts"]) if rt.bridge.get("last_sync_ts") else None,
                "age_s": round(age, 1) if age is not None else None,
                "terminal_connected": bool(term.get("connected")) if term else False,
                "trade_allowed": bool(term.get("trade_allowed")) if term else False,
                "ea_version": rt.bridge.get("ea_version"),
                "clock_skew_s": rt.bridge.get("clock_skew_s", 0),
                "clock_raw_skew_s": rt.bridge.get("clock_raw_skew_s"),
            },
            "account": account,
            "paper_account": paper_acc,
            "session": self.session(),
            "trading": {"mode": mode, "execution_enabled": rt.trading["execution_enabled"],
                        "entries_paused": rt.trading["entries_paused"], "live_allowed": False},
            "ai": self.ai.status(),
            "telegram": self.telegram.status(),
            "counts": {
                "signals_today": int(sig_today["n"]) if sig_today else 0,
                "trades_today": self.trades_today(mode) if mode in ("PAPER", "DEMO") else 0,
                "open_positions": len(self.bot_positions()),
                "closed_today": len(closed),
                "realized_pnl_today": round(sum((r["profit"] or 0) + (r["commission"] or 0) + (r["swap"] or 0) for r in closed), 2),
            },
            "engine": {**rt.engine_info, "warmup_done": self._warmup_done},
            "news": self.news_status(),
        }

    def symbols_payload(self) -> list[dict]:
        rt = self.rt
        out = []
        max_age = float(rt.risk.get("max_tick_age_seconds", 10))
        for sym in rt.active_symbols():
            q = rt.quotes.get(sym)
            age = rt.tick_age(sym)
            quality = "NO_DATA" if not q else ("STALE" if age is None or age > max_age else "OK")
            if q and q.get("bid") and q.get("ask") and q["bid"] > q["ask"]:
                quality = "CROSSED"
            out.append({
                "symbol": sym, "enabled": True, "bid": (q or {}).get("bid"), "ask": (q or {}).get("ask"),
                "spread_points": (q or {}).get("spread_points"), "digits": (q or {}).get("digits"),
                "point": (q or {}).get("point"), "tick_age_s": round(age, 1) if age is not None else None,
                "quality": quality, "max_spread_points": rt.risk.get("max_spread_points"),
                "analysis": rt.last_analysis.get(sym),
            })
        return out

    # ---- loops -----------------------------------------------------------
    def _on_quote(self, symbol: str) -> None:
        if self.paper.positions:
            self.paper.on_quote(symbol, self.rt.quotes[symbol])

    def loop_once(self) -> None:
        rt = self.rt
        now = time.time()
        rt.engine_info["last_loop_utc"] = iso(now)
        rt.flush_bars()
        if self._paper_dirty:
            self._save_paper()
        self.update_equity_marks()
        sess = self.session()
        if self._prev_session_open is not None and sess["open_now"] and not self._prev_session_open:
            day = scheduler.local_day(self.now_utc(), self.tz())
            self.rt.log("INFO", "scheduler", "Sesiunea de tranzacționare a început")
            self.telegram.enqueue(f"daily:{day}:{sess['start']}", "SESSION_START", fmt_daily_start({
                **sess, "mode": self.mode(), "execution_enabled": rt.trading["execution_enabled"],
                "symbols": rt.active_symbols(), "feed_label": self.settings.feed_label}))
        elif self._prev_session_open and not sess["open_now"]:
            self.rt.log("INFO", "scheduler", "Sesiunea s-a închis — fără intrări noi; pozițiile rămân gestionate")
        self._prev_session_open = sess["open_now"]
        if self.settings.data_source == "mt5_bridge":
            connected = rt.bridge_connected()
            if self._prev_bridge_connected and not connected:
                msg = "Bridge-ul MT5 s-a deconectat — intrările noi sunt blocate."
                if self.mode() == "DEMO":
                    self.risk_alert(msg, key=f"bridge_down:{int(now // 60)}")
                else:
                    rt.log("WARN", "bridge", msg)
            self._prev_bridge_connected = connected
        self.warmup_reason("", now)
        self._check_calendar(now)
        tf = rt.strategy["signal_timeframe"]
        htf = rt.strategy["htf_timeframe"]
        sec = TF_SECONDS.get(tf, 300)
        for sym in rt.active_symbols():
            if (sym, tf) in rt.hist_force or (sym, htf) in rt.hist_force:
                continue  # waiting for a history refill after a gap
            closed = rt.closed_bars(sym, tf, now)
            if not closed:
                continue
            t = closed[-1]["t"]
            if self.last_analyzed_bar.get(sym) != t:
                self.last_analyzed_bar[sym] = t
                fresh = now - (t + sec) < SIGNAL_FRESH_S
                try:
                    self.analyze_symbol(sym, allow_signal=fresh)
                except Exception as e:
                    rt.log("ERROR", "strategy", f"analiză {sym} eșuată: {e!r}")
        try:
            self.pm.run(now)
        except Exception as e:
            rt.log("ERROR", "positions", f"position manager: {e!r}")
        self.executor.check_timeouts(now)
        self.expire_signals()

    async def _loop(self) -> None:
        while True:
            try:
                self.loop_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.rt.log("ERROR", "engine", f"loop error: {e!r}")
            await asyncio.sleep(1.0)

    async def _broadcaster(self) -> None:
        while True:
            try:
                self.rt.bus.publish("status", self.status())
                pos = self.positions_payload()
                sig = json.dumps(pos, sort_keys=True, default=str)
                if sig != self._positions_sig:
                    self._positions_sig = sig
                    self.rt.bus.publish("positions", pos)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.rt.log("ERROR", "engine", f"broadcaster: {e!r}")
            await asyncio.sleep(2.0)



def _ts(s: str | None) -> float | None:
    try:
        d = parse_iso(s)
    except (TypeError, ValueError):
        return None
    return d.timestamp() if d else None
