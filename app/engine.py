"""Orchestrator: analysis on bar close, AI review, risk, intents, position management, notifications."""
from __future__ import annotations

import asyncio
import importlib
import json
import math
import re
import sqlite3
import threading
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
from .forward import ForwardRunner
from .ml_live import MLLive
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
EQUITY_SNAPSHOT_S = 60.0
LABEL_EVERY_S = 60.0
DEFAULT_STRATEGY = "trend-v1"
PATH_EVERY_S = 60  # live trade path rows: one per position per minute
PATH_SOURCE = "live"
# pre-AI arbitration: risk checks that may clear while Claude reviews (not a reason to skip the review)
PRE_AI_TRANSIENT_CHECKS = ("quote_fresh", "account_settled")
PRE_AI_PREFIX = "Respins înainte de AI: "


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
        # performance tracking (analyses / equity snapshots / labeler / backtests)
        self._last_equity_snap = 0.0
        self._last_label_ts = 0.0
        self._labeler_busy = False
        self._perf_warned: set[str] = set()
        # live trade paths: (source, ticket) -> {"entry","risk","side","symbol","opened_ts","mfe","mae"}
        self._paths: dict[tuple[str, str], dict] = {}
        # pre-AI arbitration: signals admitted and still waiting for Claude / execution -> reserved exposure
        self._pending_ai: dict[str, dict] = {}
        self._bar_batch: list[tuple[dict, dict]] | None = None
        self._last_path_ts = 0
        self.backtests: dict[str, dict] = {}  # run id -> {"progress": 0..1}
        self._bt_lock = threading.Lock()
        self.ai.on_usage = self.record_claude_usage
        self.rt.quote_hooks.append(self._on_quote)
        # forward tests (PAPER, simulated only): own book, never intents/bridge commands
        self.forward = ForwardRunner(self)
        # ML (spec §6.4): shadow logging always harmless; only an explicitly approved model may gate/close
        self.ml = MLLive(self)

    # ---- lifecycle -------------------------------------------------------
    def startup(self) -> None:
        self.rt.load_runtime_config()
        self._load_strategy_name()
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
        self._perf_startup()
        try:
            self.forward.startup()
        except Exception as e:
            self.rt.log("ERROR", "forward", f"teste forward neîncărcate: {e!r}")
        try:
            self.ml.startup()
        except Exception as e:
            self.rt.log("ERROR", "ml", f"ML neinițializat: {e!r}")

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
        try:
            self.ml.stop()
        except Exception:
            pass
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
    def _insert_trade(self, deal_key: str, tr: dict, open_intent: dict | None = None, initial_sl: float | None = None) -> bool:
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
                self._trade_perf_fields(tr, open_intent, initial_sl)
                return True
        except sqlite3.Error as e:
            self.rt.log("ERROR", "db", f"insert trade failed: {e!r}")
        return False

    def _trade_perf_fields(self, tr: dict, oi: dict | None, initial_sl: float | None) -> None:
        """account_key / signal entry / initial SL / money at risk / R multiple of a recorded trade (best effort)."""
        try:
            mode = tr.get("mode")
            if mode == "PAPER":
                key = "PAPER"
            else:
                login = (self.rt.mt5_account or {}).get("login")  # deals always come from the connected account
                if login is None and oi:
                    login = oi.get("account_login")
                key = f"DEMO:{login}" if login is not None else None
            if oi is None and tr.get("ticket") is not None:
                oi = self._open_intent(int(tr["ticket"]), mode)
            sl0 = initial_sl if initial_sl is not None else (oi or {}).get("sl")
            sig = self.get_signal(tr["signal_id"]) if tr.get("signal_id") else None
            sig_entry = (sig or {}).get("entry")
            rm = None
            if sl0 and tr.get("price_open") and tr.get("volume"):
                rm = risk_mod.position_risk_money(tr.get("side") or "BUY", float(tr["price_open"]), float(sl0),
                                                  float(tr["volume"]), self.rt.quotes.get(tr.get("symbol") or "") or {})
            net = (tr.get("profit") or 0) + (tr.get("commission") or 0) + (tr.get("swap") or 0)
            r_mult = round(net / rm, 3) if rm else None
            tr.update({"account_key": key, "signal_entry": sig_entry, "initial_sl": sl0,
                       "risk_money": round(rm, 4) if rm is not None else None, "r_multiple": r_mult})
            self.db.execute("UPDATE trades SET account_key=?, signal_entry=?, initial_sl=?, risk_money=?, r_multiple=? "
                            "WHERE id=?", (key, sig_entry, sl0, tr["risk_money"], r_mult, tr["id"]))
        except (sqlite3.Error, TypeError, ValueError) as e:
            self._perf_warn_once("trade_fields", f"câmpuri de performanță pentru tranzacție nesalvate: {e!r}")

    def _open_intent(self, ticket: int, mode: str | None) -> dict | None:
        row = self.db.query_one("SELECT data_json FROM intents WHERE kind='OPEN' AND ticket=? AND mode=? "
                                "ORDER BY created_at_utc DESC LIMIT 1", (int(ticket), mode or "DEMO"))
        return json.loads(row["data_json"]) if row else None

    def _on_paper_close(self, trade: dict, pos: dict) -> None:
        oi = self.executor.get(pos["intent_id"]) if pos.get("intent_id") else None
        if self._insert_trade(f"PAPER:{trade['ticket']}", trade, open_intent=oi, initial_sl=pos.get("initial_sl")):
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
                if self._insert_trade(f"MT5:{int(d['ticket'])}", tr, open_intent=oi):
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

    def last_loss_ts(self, symbol: str, now: float | None = None) -> float | None:
        """Close time of the latest bot trade on `symbol` (any mode) closed at a loss within the
        cooldown_after_loss_minutes window; wins and break-evens (|net| < 0.1R) do not count. None = no cooldown."""
        minutes = int(self.rt.risk.get("cooldown_after_loss_minutes", 0) or 0)
        if minutes <= 0:
            return None
        now = now or time.time()
        try:
            rows = self.db.query("SELECT ticket, symbol, side, volume, price_open, profit, commission, swap, mode, "
                                 "closed_at_utc FROM trades WHERE symbol=? AND closed_at_utc >= ? "
                                 "ORDER BY closed_at_utc DESC, id DESC LIMIT 50", (symbol, iso(now - minutes * 60)))
        except sqlite3.Error:
            return None
        equity = {}
        for r in rows:
            m = "DEMO" if r["mode"] == "DEMO" else "PAPER"
            if m not in equity:
                equity[m] = float((self.risk_account(m)[1] or {}).get("equity") or 0)
            if self._trade_outcome(r, equity[m]) < 0:
                return _ts(r["closed_at_utc"])
        return None

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
                   mode: str | None = None, size_factor: float = 1.0, reserved: list[dict] | None = None) -> dict:
        """reserved: [{"symbol","side","risk_money"}] of candidates not yet in the book (pre-AI arbitration dry run);
        they count as open positions / exposures / trades today / open risk."""
        mode = mode or self.mode()
        reserved = list(reserved or [])
        self.update_equity_marks()
        key, acc = self.risk_account(mode)
        streak, pause_until = self.loss_streak(mode)
        news_block, news_note = self.news_check(symbol)
        marks = self.equity_marks(key) or {}
        bots = self.bot_positions(mode)
        n_open = len(bots)
        if mode == "DEMO":
            n_open += self.executor.count_pending_open("DEMO", {int(p["ticket"]) for p in bots})
        fallback = float((acc or {}).get("equity") or 0) * float(self.rt.risk.get("max_risk_per_trade_pct", 0.25)) / 100.0
        res_risk = sum(fallback if r.get("risk_money") is None else float(r["risk_money"]) for r in reserved)
        ctx = risk_mod.RiskContext(
            mode=mode, execution_enabled=self.rt.trading["execution_enabled"],
            entries_paused=self.rt.trading["entries_paused"], side=side, entry=entry, sl=sl, tp=tp,
            symbol_info=dict(self.rt.quotes.get(symbol) or {}), account=acc, risk=self.rt.risk,
            bridge_connected=self.rt.bridge_connected(), terminal=self.rt.bridge.get("terminal"),
            mt5_account=self.rt.mt5_account, tick_age=self.rt.tick_age(symbol),
            symbol=symbol, open_exposures=self.open_exposures(mode) + [(r["symbol"], r["side"]) for r in reserved],
            open_bot_positions=n_open + len(reserved), ea_magic_ok=self.rt.bridge.get("magic_ok") is True,
            has_bot_position_on_symbol=self.has_bot_position(symbol, mode) or any(r["symbol"] == symbol for r in reserved),
            trades_today=self.trades_today(mode) + len(reserved), day_start_equity=marks.get("day_start_equity"),
            peak_equity=marks.get("peak_equity"), consecutive_losses=streak, loss_pause_until=pause_until,
            open_risk_money=self.open_risk_money(mode) + res_risk, news_block=news_block, news_note=news_note, test_order=test_order,
            account_settling=mode == "DEMO" and self.account_settling(),
            size_factor=size_factor, size_cap_pct=float(self.settings.ML_MAX_RISK_PCT),
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
            "strategy_version": a.get("strategy_version") or STRATEGY_VERSION, "intent_id": None,
            # execution context at creation: a signal never executes after a later arm/mode change
            "created_ts": time.time(), "created_mode": self.mode(),
            "created_armed": bool(self.rt.trading["execution_enabled"]),
            "account_key": self.account_key(),
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
        self._perf_exec("UPDATE signals SET account_key=? WHERE id=?", (sig["account_key"], sig["id"]), "signal_account")
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

    # ---- strategy registry -----------------------------------------------
    @staticmethod
    def strategy_registry():
        """app.strategies (plugins) when present; None -> only the built-in trend-v1."""
        try:
            return importlib.import_module("app.strategies")
        except ImportError:
            return None

    def strategy_names(self) -> list[str]:
        reg = self.strategy_registry()
        names = []
        if reg is not None:
            try:
                names = [str(n) for n in reg.names()]
            except Exception:
                names = []
        return names if DEFAULT_STRATEGY in names else [DEFAULT_STRATEGY] + names

    def strategy_catalog(self) -> list[dict]:
        out = []
        reg = self.strategy_registry()
        for n in self.strategy_names():
            mod = None
            if reg is not None:
                try:
                    mod = reg.get(n)
                except Exception:
                    mod = None
            if mod is None:
                out.append({"name": n, "version": STRATEGY_VERSION if n == DEFAULT_STRATEGY else "?",
                            "description": "Trend EMA20/EMA50 + MACD + RSI, confirmat de trendul EMA50 pe HTF."
                            if n == DEFAULT_STRATEGY else "", "params": {}})
                continue
            out.append({"name": str(getattr(mod, "NAME", n)), "version": str(getattr(mod, "VERSION", "")),
                        "description": str(getattr(mod, "DESCRIPTION_RO", "")),
                        "params": dict(getattr(mod, "PARAMS", {}) or {})})
        return out

    def _load_strategy_name(self) -> None:
        saved = self.db.get_setting("strategy")
        name = saved.get("name") if isinstance(saved, dict) else None
        if not name:
            return
        if name in self.strategy_names():
            self.rt.strategy["name"] = name
            if name != DEFAULT_STRATEGY:
                self.rt.log("WARN", "strategy", f"Strategie live activă: {name} (nu {DEFAULT_STRATEGY})")
        else:
            self.rt.log("WARN", "strategy", f"Strategia salvată {name} nu există — folosesc {DEFAULT_STRATEGY}")

    def active_strategy_name(self) -> str:
        return str(self.rt.strategy.get("name", DEFAULT_STRATEGY) or DEFAULT_STRATEGY)

    def active_strategy(self):
        """(name, analyze fn, cfg). trend-v1 always runs app.strategy.analyze on the live settings, unchanged."""
        name = self.active_strategy_name()
        if name != DEFAULT_STRATEGY:
            reg = self.strategy_registry()
            try:
                mod = reg.get(name) if reg is not None else None
            except Exception:
                mod = None
            if mod is not None and callable(getattr(mod, "analyze", None)):
                # plugin defaults < live settings (same precedence as the backtest)
                merge = getattr(reg, "merged_cfg", None)
                cfg = (merge(name, self.rt.strategy) if callable(merge)
                       else {**dict(getattr(mod, "PARAMS", {}) or {}), **self.rt.strategy})
                return name, mod.analyze, cfg
            self._perf_warn_once(f"strategy_missing:{name}", f"strategia {name} indisponibilă — folosesc {DEFAULT_STRATEGY}")
        return DEFAULT_STRATEGY, strategy.analyze, self.rt.strategy

    # ---- live trade paths -------------------------------------------------
    def _path_positions(self) -> list[tuple[str, dict]]:
        """Open bot-owned positions (PAPER always, DEMO when the bridge serves a demo account)."""
        rt = self.rt
        items = [("PAPER", p) for p in list(self.paper.positions.values())]
        acc = rt.mt5_account or {}
        if rt.bridge_connected() and acc.get("trade_mode") == 0:
            for p in rt.mt5_positions:
                if int(p.get("magic") or 0) != self.settings.MT5_MAGIC:
                    continue
                items.append(("MT5", {"ticket": int(p["ticket"]), "symbol": p["symbol"],
                                      "side": "BUY" if int(p.get("type", 0)) == 0 else "SELL",
                                      "price_open": p.get("price_open"), "sl": p.get("sl"), "opened_ts": p.get("time"),
                                      "comment": p.get("comment")}))
        return items

    def _path_state(self, source: str, pos: dict) -> dict | None:
        """Initial risk = |fill price - SL of the OPEN intent|; MFE/MAE resumed from rows already written."""
        ticket = int(pos["ticket"])
        if source == "PAPER":
            init_sl = pos.get("initial_sl")
            oi = self.executor.get(pos["intent_id"]) if pos.get("intent_id") else None
            if oi and oi.get("sl"):
                init_sl = oi["sl"]
        else:
            oi = self.executor.open_intent_for_ticket(ticket) or self.executor.open_intent_by_comment(pos.get("comment") or "")
            init_sl = (oi or {}).get("sl")
        entry = pos.get("price_open")
        if not init_sl or not entry:
            return None
        risk = abs(float(entry) - float(init_sl))
        if risk <= 0:
            return None
        # n / neg: 1-min samples written and those with r < 0 (rescue exit); last_ts: latest sample minute
        st = {"entry": float(entry), "risk": risk, "side": pos["side"], "symbol": pos["symbol"],
              "opened_ts": pos.get("opened_ts"), "mfe": 0.0, "mae": 0.0, "n": 0, "neg": 0, "last_ts": 0}
        try:
            r = self.db.query_one("SELECT MAX(mfe_r) AS mfe, MAX(mae_r) AS mae, COUNT(*) AS n, "
                                  "SUM(CASE WHEN r_now < 0 THEN 1 ELSE 0 END) AS neg, MAX(ts) AS last_ts FROM trade_paths "
                                  "WHERE source=? AND trade_id=? AND symbol=?", (PATH_SOURCE, str(ticket), pos["symbol"]))
            if r and r["mfe"] is not None:
                st["mfe"], st["mae"] = float(r["mfe"]), float(r["mae"] or 0)
                st["n"], st["neg"], st["last_ts"] = int(r["n"] or 0), int(r["neg"] or 0), int(r["last_ts"] or 0)
        except sqlite3.Error:
            pass
        return st

    def _path_indicators(self, symbol: str) -> tuple[float | None, float | None]:
        bars = self.rt.closed_bars(symbol, self.rt.strategy["signal_timeframe"])[-200:]
        if len(bars) < 21:
            return None, None
        closes = [b["c"] for b in bars]
        return (ind.last(ind.atr([b["h"] for b in bars], [b["l"] for b in bars], closes, 14)),
                ind.last(ind.ema(closes, 20)))

    def track_trade_paths(self, now: float) -> int:
        """Every loop: MFE/MAE from the closing price (bid for BUY, ask for SELL); once per minute: one trade_paths row
        per open bot position. R is relative to the initial risk. Never influences trading."""
        rt = self.rt
        ts = int(now // PATH_EVERY_S * PATH_EVERY_S)
        write = ts != self._last_path_ts
        max_age = float(rt.risk.get("max_tick_age_seconds", 10)) * 3
        seen: set[tuple[str, str]] = set()
        rows = []
        ind_cache: dict[str, tuple] = {}
        for source, pos in self._path_positions():
            key = (source, str(pos["ticket"]))
            seen.add(key)
            q = rt.quotes.get(pos["symbol"]) or {}
            if not q.get("bid") or not q.get("ask"):
                continue
            age = rt.tick_age(pos["symbol"], now)
            if age is None or age > max_age:
                continue
            st = self._paths.get(key)
            if st is None:
                st = self._path_state(source, pos)
                if st is None:
                    continue
                self._paths[key] = st
            sign = 1 if st["side"] == "BUY" else -1
            price = float(q["bid"] if sign > 0 else q["ask"])
            r_now = (price - st["entry"]) * sign / st["risk"]
            st["mfe"] = max(st["mfe"], r_now)
            st["mae"] = max(st["mae"], -r_now)
            if not write:
                continue
            if ts > int(st.get("last_ts") or 0):
                st["last_ts"] = ts
                st["n"] = int(st.get("n") or 0) + 1
                st["neg"] = int(st.get("neg") or 0) + (1 if r_now < 0 else 0)
            if st["symbol"] not in ind_cache:
                ind_cache[st["symbol"]] = self._path_indicators(st["symbol"])
            atr, ema = ind_cache[st["symbol"]]
            ema_dist = round((price - ema) * sign / atr, 4) if atr and ema is not None else None
            minutes = round((now - float(st["opened_ts"])) / 60.0, 2) if st.get("opened_ts") else None
            rows.append((PATH_SOURCE, key[1], st["symbol"], st["side"], ts, minutes, round(r_now, 4), round(st["mfe"], 4),
                         round(st["mae"], 4), price, atr, ema_dist, q.get("spread_points")))
        for k in list(self._paths):
            if k not in seen:
                self._paths.pop(k, None)
        if not write:
            return 0
        self._last_path_ts = ts
        if rows:
            try:
                self.db.executemany("INSERT OR IGNORE INTO trade_paths(source, trade_id, symbol, side, ts, minutes, r_now, "
                                    "mfe_r, mae_r, price, atr, ema_dist_atr, spread_pts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    rows)
            except sqlite3.Error as e:
                self._perf_warn_once("trade_paths_db", f"traiectorii tranzacții nesalvate: {e!r}")
                return 0
        return len(rows)

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
            news_block=news_block, last_loss_ts=self.last_loss_ts(symbol, now),
        )
        name, fn, cfg = self.active_strategy()
        a = fn(symbol, sig_bars, htf_bars, rt.quotes.get(symbol), cfg, rt.risk, ctx)
        if name != DEFAULT_STRATEGY:
            a["strategy_version"] = name
        self.record_analysis(a)
        try:
            self.ml.on_analysis(a)  # shadow logging in the ML thread; never changes `a` or the decision
        except Exception as e:
            self._perf_warn_once("ml_analysis", f"ML umbră (analiză): {e!r}")
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
                    self._perf_exec("UPDATE analyses SET signal_id=? WHERE symbol=? AND tf=? AND bar_time=? AND signal_id IS NULL",
                                    (sig["id"], a["symbol"], a["timeframe"], a["bar_time"]), "analysis_signal")
                    if self._bar_batch is not None:
                        self._bar_batch.append((sig, a))  # loop_once arbitrates every signal of this pass together
                    else:
                        self.dispatch_signals([(sig, a)])
            else:
                a["reasons"].append("Bara este prea veche pentru a emite un semnal nou (doar analiză).")
        return a

    def _spawn_pipeline(self, sig: dict) -> None:
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

    # ---- pre-AI arbitration ------------------------------------------------
    @staticmethod
    def _arb_key(item: tuple[dict, dict], quotes: dict) -> tuple:
        """HTF-aligned first, then the lowest spread / SL-distance cost ratio, then the symbol (deterministic)."""
        sig, a = item
        side = sig["direction"]
        htf = (a.get("indicators") or {}).get("htf_trend")
        aligned = htf == ("UP" if side == "BUY" else "DOWN")
        q = quotes.get(sig["symbol"]) or {}
        sl_dist = abs(float(sig.get("entry") or 0) - float(sig.get("stop_loss") or 0))
        spread = None
        if q.get("bid") and q.get("ask"):
            spread = float(q["ask"]) - float(q["bid"])
        elif sig.get("spread_points") is not None and q.get("point"):
            spread = float(sig["spread_points"]) * float(q["point"])
        cost = spread / sl_dist if spread is not None and sl_dist > 0 else float("inf")
        return (0 if aligned else 1, cost, sig["symbol"])

    def reserved_exposures(self) -> list[dict]:
        return [dict(r) for r in self._pending_ai.values()]

    def pre_ai_check(self, sig: dict, mode: str, reserved: list[dict]) -> tuple[bool, dict, dict | None]:
        """Deterministic dry run of the Risk Engine (+ correlation) for a candidate, counting `reserved` candidates
        as already open. Returns (admitted, risk check, reservation). Checks that may clear during the review
        (PRE_AI_TRANSIENT_CHECKS) never reject."""
        sym, side = sig["symbol"], sig["direction"]
        q = self.rt.quotes.get(sym) or {}
        entry = (q.get("ask") if side == "BUY" else q.get("bid")) or sig.get("entry")
        rc = self.risk_check(sym, side, float(entry), sig["stop_loss"], sig["take_profit"], mode=mode, reserved=reserved)
        bad = [c for c in rc["checks"] if not c["ok"] and c["name"] not in PRE_AI_TRANSIENT_CHECKS]
        if bad:
            return False, {**rc, "checks": bad, "passed": False}, None
        rm = None
        if rc.get("volume"):
            rm = risk_mod.position_risk_money(side, float(entry), sig["stop_loss"], float(rc["volume"]), q)
        return True, rc, {"signal_id": sig["id"], "symbol": sym, "side": side, "risk_money": rm}

    def arbitrate(self, items: list[tuple[dict, dict]]) -> list[dict]:
        """Order this bar's new signals and admit them one by one with a risk dry run that reserves the exposure of
        candidates admitted before them and of signals still waiting for Claude. Only admitted signals go on to
        Claude; the others become NO_TRADE with ai.status SKIPPED. Applies only when an entry could actually be
        taken (PAPER/DEMO armed) and the signals are waiting for Claude; otherwise only the order changes."""
        quotes = self.rt.quotes
        items = sorted(items, key=lambda it: self._arb_key(it, quotes))
        mode = self.mode()
        if (mode not in ("PAPER", "DEMO") or not self.rt.trading["execution_enabled"]
                or not any(sig["ai"].get("status") == "PENDING" for sig, _ in items)):
            return [sig for sig, _ in items]
        reserved = self.reserved_exposures()
        out = []
        for sig, _ in items:
            if sig["ai"].get("status") != "PENDING":
                out.append(sig)
                continue
            try:
                ok, rc, res = self.pre_ai_check(sig, mode, reserved)
            except Exception as e:  # a dry-run failure never blocks the normal path (final risk check stays)
                self._perf_warn_once("pre_ai", f"arbitraj pre-AI eșuat — semnalul continuă normal: {e!r}")
                out.append(sig)
                continue
            if ok:
                reserved.append(res)
                self._pending_ai[sig["id"]] = res
                out.append(sig)
            else:
                self._reject_pre_ai(sig, rc)
        return out

    def _reject_pre_ai(self, sig: dict, rc: dict) -> None:
        reason = PRE_AI_PREFIX + risk_mod.failed_summary(rc)
        sig["status"], sig["status_reason"] = "NO_TRADE", reason[:600]
        sig["ai"] = {**sig["ai"], "status": "SKIPPED", "verdict": None,
                     "explanation": "Claude nu a fost apelat: semnalul a fost respins de verificarea de risc dinaintea AI."}
        self.save_signal(sig)
        self.add_signal_event(sig["id"], "PRE_AI_REJECTED", sig["status_reason"])
        self._perf_exec("UPDATE analyses SET ai_verdict=? WHERE signal_id=?", ("SKIPPED", sig["id"]), "analysis_verdict")
        self.rt.log("INFO", "strategy", f"Semnal {sig['direction']} {sig['symbol']} respins înainte de AI: "
                                        f"{risk_mod.failed_summary(rc)[:200]}")

    def dispatch_signals(self, items: list[tuple[dict, dict]]) -> None:
        try:
            admitted = self.arbitrate(items)
        except Exception as e:  # never strand a PENDING signal: fall back to the old path (final risk check)
            self.rt.log("ERROR", "engine", f"arbitraj pre-AI eșuat: {e!r}")
            admitted = [sig for sig, _ in items if sig.get("status") == "NEW"]
        for sig in admitted:
            self._spawn_pipeline(sig)

    async def signal_pipeline(self, sig: dict) -> None:
        try:
            await self._signal_pipeline(sig)
        finally:
            self._pending_ai.pop(sig.get("id"), None)  # reservation ends: executed (now in the book) or dropped

    async def _signal_pipeline(self, sig: dict) -> None:
        try:
            if sig["ai"]["status"] == "PENDING":
                ai = await self.ai.review(sig, self._ai_context(sig["symbol"]))
                sig = self.get_signal(sig["id"]) or sig
                sig["ai"] = ai
                self._perf_exec("UPDATE analyses SET ai_verdict=? WHERE signal_id=?",
                                (ai.get("verdict") or ai.get("status"), sig["id"]), "analysis_verdict")
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
        # approved ML entry model only (none by default -> no change): veto below its threshold or scale the risk
        gate = None
        try:
            gate = self.ml.entry_gate(sig)
        except Exception as e:
            self._perf_warn_once("ml_gate", f"ML gate eșuat — intrarea continuă fără ML: {e!r}")
        if gate and gate["veto"]:
            sig["status"] = "NO_TRADE"
            sig["status_reason"] = (f"ML: P(câștig)={gate['p']:.2f} sub pragul aprobat {gate['threshold']:.2f} "
                                    f"(model {gate['model_id']})")
            sig["ml"] = gate
            self.save_signal(sig)
            self.add_signal_event(sig["id"], "ML_VETO", sig["status_reason"])
            return
        size_factor = float(gate["size_factor"]) if gate else 1.0
        rc = self.risk_check(sym, side, entry, sl, tp, size_factor=size_factor)
        it = self.executor.submit_open(mode, sym, side, rc["volume"], sl, tp, q, rc, signal_id=sig["id"])
        sig = self.get_signal(sig["id"]) or sig
        sig["intent_id"] = it["id"]
        sig["mode"] = mode
        if gate:
            sig["ml"] = gate
            self.add_signal_event(sig["id"], "ML_SIZE", f"P(câștig)={gate['p']:.2f}, factor risc {gate['size_factor']:g} "
                                                        f"(model {gate['model_id']})")
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
        if prev is None:
            key = "PAPER" if it.get("mode") == "PAPER" else (
                f"DEMO:{it['account_login']}" if it.get("account_login") is not None else self.account_key("DEMO"))
            self._perf_exec("UPDATE intents SET account_key=? WHERE id=? AND account_key IS NULL", (key, it["id"]), "intent_account")
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

    # ---- performance: analyses / equity / Claude usage / labeler ----------
    def account_key(self, mode: str | None = None) -> str | None:
        mode = mode or self.mode()
        if mode == "DEMO":
            login = (self.rt.mt5_account or {}).get("login")
            return f"DEMO:{login}" if login is not None else None
        return "PAPER"

    def _perf_warn_once(self, key: str, msg: str) -> None:
        if key not in self._perf_warned:
            self._perf_warned.add(key)
            self.rt.log("WARN", "perf", msg)

    def _perf_exec(self, sql: str, params: tuple, key: str) -> int:
        """Write for analytics only: a failure is logged once and never breaks trading."""
        try:
            return self.db.execute(sql, params).rowcount
        except sqlite3.Error as e:
            self._perf_warn_once(key, f"scriere analitică eșuată ({key}): {e!r}")
            return 0

    def record_analysis(self, a: dict) -> None:
        if a.get("bar_time") is None or not a.get("indicators"):
            return  # no data / incomplete indicators: nothing to evaluate
        cand = a.get("candidate")
        if cand is None and a.get("direction") in ("BUY", "SELL") and a.get("entry") is not None:
            cand = {"side": a["direction"], "entry": a["entry"], "stop_loss": a["stop_loss"],
                    "take_profit": a["take_profit"], "score": a.get("score"), "blockers": []}
        cand = cand or {}
        self._perf_exec(
            "INSERT OR IGNORE INTO analyses(symbol, tf, bar_time, created_ts, account_key, direction, candidate_side, "
            "entry, sl, tp, score, blockers_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (a["symbol"], a.get("timeframe"), int(a["bar_time"]), time.time(), self.account_key(), a.get("direction"),
             cand.get("side"), cand.get("entry"), cand.get("stop_loss"), cand.get("take_profit"),
             cand.get("score", a.get("score")), json.dumps(cand.get("blockers") or [], ensure_ascii=False)),
            "analysis_insert")

    def record_claude_usage(self, rec: dict) -> None:
        self._perf_exec(
            "INSERT INTO claude_usage(ts, signal_id, model, input_tokens, output_tokens, cache_read_tokens, cost_usd) "
            "VALUES(?,?,?,?,?,?,?)",
            (rec["ts"], rec.get("signal_id"), rec.get("model"), rec.get("input_tokens"), rec.get("output_tokens"),
             rec.get("cache_read_tokens"), rec.get("cost_usd")), "claude_usage")

    def snapshot_accounts(self) -> list[tuple[str, dict]]:
        out = []
        if self.mode() == "PAPER" or self.paper.positions:
            out.append(("PAPER", self.paper.account()))
        a = self.rt.mt5_account
        if a and a.get("login") is not None and self.rt.bridge_connected() and not self.account_settling():
            out.append((f"DEMO:{a['login']}", a))
        return out

    def snapshot_equity(self, now: float) -> int:
        ts = int(now // 60 * 60)
        n = 0
        for key, acc in self.snapshot_accounts():
            if not acc or acc.get("equity") is None:
                continue
            n += self._perf_exec("INSERT OR IGNORE INTO equity_snapshots(account_key, ts, balance, equity) VALUES(?,?,?,?)",
                                 (key, ts, acc.get("balance"), acc.get("equity")), "equity_snapshot")
        return n

    def labeler_cfg(self) -> dict:
        st = self.rt.strategy
        cfg = {"break_even_at_r": float(st.get("break_even_at_r", 1.0) or 0),
               # 0 = no time stop (as live); the labeler then uses its own 24 h HORIZON cap
               "max_hold_minutes": int(st.get("max_hold_minutes", 240) or 0),
               "label_tf": "M1" if "M1" in self.rt.needed_tfs() else st["signal_timeframe"]}
        points = self.symbol_points()
        if points:
            cfg["points"] = points  # broker point size instead of the labeler's guess from the symbol name
        return cfg

    def symbol_points(self) -> dict:
        return {s: float(q["point"]) for s, q in list(self.rt.quotes.items()) if q and q.get("point")}

    def symbol_specs(self) -> dict:
        """Broker symbol specs from the live quotes (backtest sizing); missing ones are estimated there."""
        keys = ("point", "digits", "tick_size", "tick_value", "contract_size", "volume_min", "volume_step",
                "volume_max", "stops_level")
        return {s: {k: q[k] for k in keys if q.get(k) is not None}
                for s, q in list(self.rt.quotes.items()) if q and q.get("point")}

    def get_closed_bars(self, symbol: str, tf: str, from_ts: float, to_ts: float) -> list[dict]:
        """Closed bars from bars_archive + bars (live rows win), ascending. Safe to call from a worker thread."""
        sec = TF_SECONDS.get(tf, 60)
        now = time.time()
        rows: dict[int, dict] = {}
        for table in ("bars_archive", "bars"):
            try:
                for r in self.db.query(f"SELECT t,o,h,l,c,sp FROM {table} WHERE symbol=? AND tf=? AND t>=? AND t<=? ORDER BY t",
                                       (symbol, tf, int(from_ts), int(to_ts))):
                    rows[int(r["t"])] = r
            except sqlite3.Error:
                continue
        return [rows[t] for t in sorted(rows) if t + sec <= now]

    def run_labeler(self, now: float) -> int:
        try:
            labeler = importlib.import_module("app.labeler")
        except ImportError:
            self._perf_warn_once("labeler_missing", "modulul labeler lipsește — analizele nu sunt etichetate")
            return 0
        return int(labeler.label_pending(self.db, self.get_closed_bars, self.labeler_cfg(), now) or 0)

    def _labeler_job(self, now: float) -> None:
        try:
            self.run_labeler(now)
        except Exception as e:
            self._perf_warn_once("labeler_error", f"etichetare analize eșuată: {e!r}")
        finally:
            self._labeler_busy = False

    def perf_tick(self, now: float) -> None:
        """Called every loop second from the async loop: 60 s equity snapshots + labeler in a worker thread."""
        if now - self._last_equity_snap >= EQUITY_SNAPSHOT_S:
            self._last_equity_snap = now
            self.snapshot_equity(now)
        if now - self._last_label_ts >= LABEL_EVERY_S and not self._labeler_busy:
            self._last_label_ts = now
            self._labeler_busy = True
            try:
                asyncio.get_running_loop().run_in_executor(None, self._labeler_job, now)
            except RuntimeError:
                self._labeler_busy = False

    def _perf_startup(self) -> None:
        # backtests interrupted by a restart
        self._perf_exec("UPDATE backtest_runs SET status='ERROR', error=? WHERE status IN ('RUNNING','QUEUED')",
                        ("Întrerupt de repornirea serverului",), "backtest_cleanup")
        # trades recorded before the account columns existed
        try:
            rows = self.db.query("SELECT id, ticket, mode FROM trades WHERE account_key IS NULL ORDER BY id DESC LIMIT 5000")
        except sqlite3.Error:
            return
        for r in rows:
            key = None
            if r["mode"] == "PAPER":
                key = "PAPER"
            elif r["ticket"] is not None:
                oi = self._open_intent(int(r["ticket"]), "DEMO") or {}
                if oi.get("account_login") is not None:
                    key = f"DEMO:{oi['account_login']}"
                if oi.get("sl") is not None:
                    self._perf_exec("UPDATE trades SET initial_sl=? WHERE id=? AND initial_sl IS NULL",
                                    (oi["sl"], r["id"]), "trade_backfill")
            if key:
                self._perf_exec("UPDATE trades SET account_key=? WHERE id=? AND account_key IS NULL", (key, r["id"]),
                                "trade_backfill")

    # ---- performance: read side (API) -----------------------------------
    def perf_accounts(self) -> list[str]:
        keys: set[str] = set()
        for sql in ("SELECT DISTINCT account_key AS k FROM trades WHERE account_key IS NOT NULL",
                    "SELECT DISTINCT account_key AS k FROM equity_snapshots"):
            try:
                keys.update(r["k"] for r in self.db.query(sql) if r["k"])
            except sqlite3.Error:
                pass
        cur = self.account_key()
        return sorted(keys, key=lambda k: (k != cur, k))

    def claude_cost(self) -> dict:
        day0 = scheduler.local_day_start_utc(self.now_utc(), self.settings.DEFAULT_TIMEZONE).timestamp()
        out = {"today_usd": 0.0, "total_usd": 0.0, "calls_today": 0, "calls_total": 0, "unpriced_calls": 0,
               "input_tokens": 0, "output_tokens": 0}
        try:
            r = self.db.query_one(
                "SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd),0) AS c, SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS u, "
                "COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o, "
                "COALESCE(SUM(CASE WHEN ts>=? THEN cost_usd END),0) AS ct, SUM(CASE WHEN ts>=? THEN 1 ELSE 0 END) AS nt "
                "FROM claude_usage", (day0, day0))
        except sqlite3.Error:
            return out
        if r:
            out.update({"today_usd": round(r["ct"] or 0, 4), "total_usd": round(r["c"] or 0, 4),
                        "calls_today": int(r["nt"] or 0), "calls_total": int(r["n"] or 0), "unpriced_calls": int(r["u"] or 0),
                        "input_tokens": int(r["i"] or 0), "output_tokens": int(r["o"] or 0)})
        return out

    def perf_stats(self, account_key: str | None = None, days: int = 30, now: float | None = None) -> dict:
        now = now or time.time()
        since = now - days * 86400 if days and days > 0 else None
        sql = ("SELECT t.id, t.ticket, t.symbol, t.side, t.volume, t.profit, t.commission, t.swap, t.opened_at_utc, "
               "t.closed_at_utc, t.close_reason, t.mode, t.signal_id, t.account_key, t.r_multiple, t.risk_money, "
               "t.signal_entry, t.price_open, s.data_json AS sig_json FROM trades t LEFT JOIN signals s ON s.id = t.signal_id WHERE 1=1")
        params: list = []
        if account_key:
            sql += " AND t.account_key=?"
            params.append(account_key)
        if since is not None:
            sql += " AND t.closed_at_utc >= ?"
            params.append(iso(since))
        sql += " ORDER BY t.closed_at_utc, t.id"
        try:
            rows = self.db.query(sql, params)
        except sqlite3.Error as e:
            raise EngineError(503, f"Statistici indisponibile (schema bazei de date): {e}")
        tz = scheduler._tz(self.settings.DEFAULT_TIMEZONE)
        trades = []
        for r in rows:
            verdict = None
            if r["sig_json"]:
                try:
                    ai = (json.loads(r["sig_json"]) or {}).get("ai") or {}
                    verdict = ai.get("verdict") or ai.get("status")
                except (TypeError, ValueError):
                    pass
            opened, closed = _ts(r["opened_at_utc"]), _ts(r["closed_at_utc"])
            ref = opened if opened is not None else closed
            net = (r["profit"] or 0) + (r["commission"] or 0) + (r["swap"] or 0)
            slip = None
            if r["signal_entry"] and r["price_open"]:
                q = self.rt.quotes.get(r["symbol"]) or {}
                pt = q.get("point")
                if pt:
                    sign = 1 if r["side"] == "BUY" else -1
                    slip = round((float(r["price_open"]) - float(r["signal_entry"])) * sign / float(pt), 1)
            trades.append({
                "t": int(closed) if closed is not None else None, "symbol": r["symbol"], "side": r["side"],
                "net": round(net, 2), "r": r["r_multiple"], "reason": r["close_reason"], "mode": r["mode"],
                "account_key": r["account_key"], "ai_verdict": verdict or "FĂRĂ SEMNAL",
                "hour": datetime.fromtimestamp(ref, tz).hour if ref is not None else None, "slippage_pts": slip,
                "opened_at_utc": r["opened_at_utc"], "closed_at_utc": r["closed_at_utc"], "signal_id": r["signal_id"],
            })
        out = {
            "account_key": account_key, "days": days, "accounts": self.perf_accounts(),
            "summary": trade_summary(trades), "by_symbol": group_summary(trades, "symbol"),
            "by_hour": group_summary(trades, "hour"), "by_ai_verdict": group_summary(trades, "ai_verdict"),
            "equity": [], "pnl_curve": [], "claude": self.claude_cost(), "warnings": [],
            "trades": [{k: v for k, v in t.items() if k != "hour"} for t in trades[-100:]][::-1],
            "hour_tz": self.settings.DEFAULT_TIMEZONE,
        }
        cum = 0.0
        for t in trades:
            if t["t"] is not None:
                cum += t["net"]
                out["pnl_curve"].append([t["t"], round(cum, 2)])
        slips = [t["slippage_pts"] for t in trades if t["slippage_pts"] is not None]
        out["summary"]["avg_slippage_pts"] = round(sum(slips) / len(slips), 2) if slips else None
        if account_key:
            esql = "SELECT ts, balance, equity FROM equity_snapshots WHERE account_key=?"
            eparams: list = [account_key]
            if since is not None:
                esql += " AND ts >= ?"
                eparams.append(int(since))
            try:
                snaps = self.db.query(esql + " ORDER BY ts", eparams)
            except sqlite3.Error:
                snaps = []
            step = max(1, math.ceil(len(snaps) / 1000))
            pts = snaps[::step]
            if snaps and pts[-1] is not snaps[-1]:
                pts.append(snaps[-1])
            out["equity"] = [[int(s["ts"]), s["equity"]] for s in pts if s["equity"] is not None]
            eq = [s["equity"] for s in snaps if s["equity"] is not None]
            if eq:
                peak, dd, dd_pct = eq[0], 0.0, 0.0
                for v in eq:
                    peak = max(peak, v)
                    if peak - v > dd:
                        dd, dd_pct = peak - v, (peak - v) / peak * 100 if peak > 0 else 0.0
                out["summary"]["max_dd_usd"] = round(dd, 2)
                out["summary"]["max_dd_pct"] = round(dd_pct, 2)
        else:
            out["warnings"].append("Toate conturile combinate — alege un cont pentru curba de equity.")
        n_r = sum(1 for t in trades if t["r"] is not None)
        if trades and n_r < len(trades):
            out["warnings"].append(f"{len(trades) - n_r} tranzacții fără R (risc inițial necunoscut) — excluse din mediile R.")
        if 0 < len(trades) < 30:
            out["warnings"].append(f"Eșantion mic: {len(trades)} tranzacții (< 30) — rezultatele nu sunt concludente.")
        return out

    def shadow_stats(self, days: int = 30, now: float | None = None) -> dict:
        now = now or time.time()
        sql = ("SELECT a.symbol, a.direction, a.candidate_side, a.blockers_json, a.signal_id, a.ai_verdict, a.outcome, "
               "a.outcome_r, a.mfe_r, a.mae_r, s.status AS sig_status FROM analyses a LEFT JOIN signals s ON s.id = a.signal_id "
               "WHERE a.candidate_side IN ('BUY','SELL')")
        params: list = []
        if days and days > 0:
            sql += " AND a.bar_time >= ?"
            params.append(int(now - days * 86400))
        try:
            rows = self.db.query(sql, params)
        except sqlite3.Error as e:
            raise EngineError(503, f"Analiza în umbră indisponibilă (schema bazei de date): {e}")
        total = len(rows)
        labeled = [r for r in rows if r["outcome"]]
        by_cat: dict[str, list] = {}
        by_blocker: dict[str, list] = {}
        by_verdict: dict[str, list] = {}
        by_symbol: dict[str, list] = {}
        for r in labeled:
            try:
                blockers = json.loads(r["blockers_json"] or "[]") or []
            except (TypeError, ValueError):
                blockers = []
            by_cat.setdefault(shadow_category(r, blockers), []).append(r)
            for b in {blocker_type(x) for x in blockers}:
                by_blocker.setdefault(b, []).append(r)
            if r["signal_id"]:
                by_verdict.setdefault(r["ai_verdict"] or "PENDING", []).append(r)
            by_symbol.setdefault(r["symbol"], []).append(r)
        agg = lambda d: {k: shadow_group(v) for k, v in sorted(d.items(), key=lambda kv: -len(kv[1]))}  # noqa: E731
        out = {
            "days": days, "candidates": total, "labeled": len(labeled), "pending": total - len(labeled),
            "all": shadow_group(labeled), "by_category": agg(by_cat), "by_blocker": agg(by_blocker),
            "by_ai_verdict": agg(by_verdict), "by_symbol": agg(by_symbol), "min_sample": SHADOW_MIN_N, "warnings": [],
        }
        if len(labeled) < SHADOW_MIN_N:
            out["warnings"].append(f"Eșantion mic: {len(labeled)} candidați etichetați (< {SHADOW_MIN_N}) — nu trage concluzii.")
        return out

    def archive_status(self) -> dict:
        from . import archive  # per-TF days_target (ARCHIVE_EXTRA), phase, exhausted / history_start_ts
        return archive.status_view(self.rt)

    # ---- backtests --------------------------------------------------------
    def backtest_running(self) -> bool:
        with self._bt_lock:
            return any(not b.get("done") for b in self.backtests.values())

    def start_backtest(self, params: dict) -> dict:
        with self._bt_lock:
            if any(not b.get("done") for b in self.backtests.values()):
                raise EngineError(409, "Un backtest rulează deja — așteaptă să se termine")
            run_id = str(uuid.uuid4())
            self.backtests[run_id] = {"progress": 0.0, "done": False}
        created = time.time()
        try:
            self.db.execute("INSERT INTO backtest_runs(id, created_ts, status, params_json) VALUES(?,?,?,?)",
                            (run_id, created, "RUNNING", json.dumps(params)))
        except sqlite3.Error as e:
            with self._bt_lock:
                self.backtests.pop(run_id, None)
            raise EngineError(503, f"Backtest indisponibil (schema bazei de date): {e}")
        th = threading.Thread(target=self._backtest_job, args=(run_id, params), name=f"backtest-{run_id[:8]}", daemon=True)
        th.start()
        self.rt.log("INFO", "backtest", f"Backtest {run_id[:8]} pornit: {','.join(params.get('symbols') or [])} "
                                        f"{params.get('days')} zile")
        return {"id": run_id, "status": "RUNNING", "created_ts": created}

    def _backtest_job(self, run_id: str, params: dict) -> None:
        def progress(p) -> None:
            try:
                self.backtests[run_id]["progress"] = max(0.0, min(1.0, float(p)))
            except (KeyError, TypeError, ValueError):
                pass
        status, metrics, err = "ERROR", None, None
        try:
            bt = importlib.import_module("app.backtest")
            run_params = dict(params)
            if not run_params.get("specs"):
                run_params["specs"] = self.symbol_specs()
            # own read-only connection: the bar loads never hold the shared DB.lock the event loop uses
            ro = None
            ro_cls = getattr(bt, "ReadOnlyDB", None)
            if ro_cls is not None and str(self.settings.DATABASE_PATH) != ":memory:":
                try:
                    ro = ro_cls(str(self.settings.db_path))
                except sqlite3.Error:
                    ro = None
            try:
                res = bt.run(ro or self.db, run_params, progress=progress)
            finally:
                if ro is not None:
                    ro.close()
            metrics, status = json.dumps(res, default=str), "DONE"
        except Exception as e:
            err = repr(e)[:500]
        try:
            self.db.execute("UPDATE backtest_runs SET status=?, metrics_json=?, error=? WHERE id=?", (status, metrics, err, run_id))
        except sqlite3.Error as e:
            err = f"{err or ''} | salvare eșuată: {e!r}"
        with self._bt_lock:
            b = self.backtests.get(run_id)
            if b is not None:
                b["done"] = True
                if status == "DONE":
                    b["progress"] = 1.0
        if status == "DONE":
            self.rt.log("INFO", "backtest", f"Backtest {run_id[:8]} terminat")
        else:
            self.rt.log("WARN", "backtest", f"Backtest {run_id[:8]} eșuat: {err}")

    def _backtest_row(self, r: dict, full: bool) -> dict:
        try:
            params = json.loads(r["params_json"]) if r.get("params_json") else None
        except ValueError:
            params = None
        metrics = None
        if r.get("metrics_json"):
            try:
                metrics = json.loads(r["metrics_json"])
            except ValueError:
                metrics = None
        out = {"id": r["id"], "created_ts": r["created_ts"], "status": r["status"], "params": params, "error": r.get("error"),
               "progress": (self.backtests.get(r["id"]) or {}).get("progress", 1.0 if r["status"] == "DONE" else None)}
        if full:
            out["metrics"] = metrics
        else:
            out["summary"] = (metrics or {}).get("summary") if isinstance(metrics, dict) else None
        return out

    def list_backtests(self, limit: int = 30) -> list[dict]:
        try:
            rows = self.db.query("SELECT * FROM backtest_runs ORDER BY created_ts DESC LIMIT ?", (limit,))
        except sqlite3.Error:
            return []
        return [self._backtest_row(r, full=False) for r in rows]

    def get_backtest(self, run_id: str) -> dict | None:
        try:
            r = self.db.query_one("SELECT * FROM backtest_runs WHERE id=?", (run_id,))
        except sqlite3.Error:
            return None
        return self._backtest_row(r, full=True) if r else None

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
        # new signals of this pass (same bar close) are collected, then arbitrated together before Claude
        self._bar_batch = []
        try:
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
        finally:
            batch, self._bar_batch = self._bar_batch, None
        if batch:
            try:
                self.dispatch_signals(batch)
            except Exception as e:
                rt.log("ERROR", "engine", f"arbitraj semnale eșuat: {e!r}")
        try:
            self.pm.run(now)
        except Exception as e:
            rt.log("ERROR", "positions", f"position manager: {e!r}")
        try:
            self.track_trade_paths(now)
        except Exception as e:
            self._perf_warn_once("trade_paths", f"traiectorii tranzacții: {e!r}")
        self.executor.check_timeouts(now)
        self.expire_signals()
        try:
            self.forward.tick(now)
        except Exception as e:
            self._perf_warn_once("forward", f"teste forward: {e!r}")
        try:
            self.ml.tick(now)
        except Exception as e:
            self._perf_warn_once("ml_tick", f"ML tick: {e!r}")

    async def _loop(self) -> None:
        while True:
            try:
                self.loop_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.rt.log("ERROR", "engine", f"loop error: {e!r}")
            try:
                self.perf_tick(time.time())
            except Exception as e:
                self._perf_warn_once("perf_tick", f"perf tick: {e!r}")
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



# ---- performance math (shared by /stats and /shadow) ------------------------
SHADOW_MIN_N = 30
Z90 = 1.645


def mean_ci90(xs: list[float]) -> tuple[float | None, list[float] | None]:
    n = len(xs)
    if not n:
        return None, None
    m = sum(xs) / n
    if n < 2:
        return m, None
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    h = Z90 * sd / math.sqrt(n)
    return m, [round(m - h, 3), round(m + h, 3)]


def trade_summary(trades: list[dict]) -> dict:
    """trades: dicts with net (USD), r (R multiple | None), t (close ts)."""
    n = len(trades)
    nets = [t["net"] for t in trades]
    rs = [t["r"] for t in trades if t.get("r") is not None]
    wins = sum(1 for x in nets if x > 0)
    gross_win = sum(x for x in nets if x > 0)
    gross_loss = -sum(x for x in nets if x < 0)
    avg_r, ci = mean_ci90(rs)
    cum = peak = dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    streak = best = 0
    for x in nets:
        streak = streak + 1 if x < 0 else 0
        best = max(best, streak)
    ts = [t["t"] for t in trades if t.get("t") is not None]
    return {
        "trades": n, "wins": wins, "losses": sum(1 for x in nets if x < 0),
        "win_rate": round(wins / n, 4) if n else None,
        "avg_r": round(avg_r, 3) if avg_r is not None else None,
        "expectancy_r": round(avg_r, 3) if avg_r is not None else None,
        "trades_with_r": len(rs),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,  # None = no losing trade
        "max_dd_r": round(dd, 3), "max_consec_losses": best, "net_usd": round(sum(nets), 2),
        "gross_win_usd": round(gross_win, 2), "gross_loss_usd": round(gross_loss, 2),
        "sample_days": round((max(ts) - min(ts)) / 86400, 1) if len(ts) >= 2 else 0.0,
        "ci90_avg_r": ci,
    }


def group_summary(trades: list[dict], key: str) -> dict:
    groups: dict = {}
    for t in trades:
        k = t.get(key)
        groups.setdefault("—" if k is None else k, []).append(t)
    out = {}
    for k in sorted(groups, key=lambda x: (isinstance(x, str), x)):
        s = trade_summary(groups[k])
        out[str(k)] = {f: s[f] for f in ("trades", "wins", "win_rate", "avg_r", "net_usd", "profit_factor", "ci90_avg_r")}
    return out


def blocker_type(text: str) -> str:
    """Stable group name of a free-text blocker: text up to the first ':', '(' or digit."""
    m = re.match(r"[^:(0-9]*", str(text or ""))
    head = (m.group(0) if m else "").strip(" .,;-—>")
    return (head or str(text or "?").strip())[:60]


def shadow_category(r: dict, blockers: list) -> str:
    if r.get("signal_id"):
        if r.get("ai_verdict") == "VETO":
            return "Semnal → VETO Claude"
        st = r.get("sig_status")
        if st in ("EXECUTED", "INTENT_CREATED"):
            return "Semnal → executat"
        if st in ("NO_TRADE", "REJECTED"):
            return "Semnal → respins (risc/execuție)"
        return "Semnal → neexecutat (doar semnal/expirat)"
    if blockers:
        return "Candidat blocat de filtre"
    if r.get("direction") in ("BUY", "SELL"):
        return "Fără semnal (încălzire/bară veche)"
    return "HOLD fără blocaj explicit"


def shadow_group(rows: list[dict]) -> dict:
    rs = [float(r["outcome_r"]) for r in rows if r.get("outcome_r") is not None]
    avg, ci = mean_ci90(rs)
    outcomes: dict[str, int] = {}
    for r in rows:
        outcomes[r.get("outcome") or "?"] = outcomes.get(r.get("outcome") or "?", 0) + 1
    mfe = [float(r["mfe_r"]) for r in rows if r.get("mfe_r") is not None]
    mae = [float(r["mae_r"]) for r in rows if r.get("mae_r") is not None]
    n = len(rows)
    return {
        "n": n, "win_rate": round(sum(1 for x in rs if x > 0) / len(rs), 4) if rs else None,
        "avg_r": round(avg, 3) if avg is not None else None, "sum_r": round(sum(rs), 2), "ci90_avg_r": ci,
        "avg_mfe_r": round(sum(mfe) / len(mfe), 3) if mfe else None, "avg_mae_r": round(sum(mae) / len(mae), 3) if mae else None,
        "outcomes": outcomes, "small_sample": n < SHADOW_MIN_N,
    }


def _ts(s: str | None) -> float | None:
    try:
        d = parse_iso(s)
    except (TypeError, ValueError):
        return None
    return d.timestamp() if d else None
