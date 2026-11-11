"""Orchestrator: MT5 bridge state, the Claude basket manager (app.basket), risk checks, intents, trade history."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime

from . import indicators as ind
from . import risk as risk_mod
from . import scheduler
from .ai_claude import ClaudeReviewer
from .news_feed import NewsFeed
from .config import VERSION, Settings, iso, parse_iso, spread_cap, utcnow
from .db import DB
from .executor import Executor
from .paper import PaperBroker
from .risk import pyramid_blocker
from .state import Runtime
from .basket import BasketTrader
from .session_breakout import SessionBreakout

WARMUP_S = 30.0  # no new signals right after process start
CALENDAR_STALE_S = 1800.0

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
        self.basket = BasketTrader(self)
        self.session_bo = SessionBreakout(self)
        self.tasks: list[asyncio.Task] = []
        self.known_mt5_positions: dict[int, dict] = {}
        self._paper_dirty = False
        self._prev_session_open: bool | None = None
        self._prev_bridge_connected: bool | None = None
        self._positions_sig: str | None = None
        self._equity_cache: dict[str, dict] = {}
        self._warmup_done = False
        self._bridge_down_ts: float | None = None
        self._cal_warn_ts = 0.0
        self.news_feed = NewsFeed(str(getattr(self.settings, "NEWS_FEED_URL", "") or ""), self.rt.log)
        self._no_quote_warn_ts = 0.0
        self._perf_warned: set[str] = set()
        self.ai.on_usage = self.record_claude_usage
        self.rt.quote_hooks.append(self._on_quote)

    # ---- lifecycle -------------------------------------------------------
    def startup(self) -> None:
        self.rt.load_runtime_config()
        self.paper.load(self.db.get_setting("paper_state"))
        self.rt.load_bars_from_db()
        s = self.settings
        self.rt.log("INFO", "engine",
                    f"Pornire v{VERSION}: sursă={s.data_source} ({s.feed_label}), mod={self.rt.trading['mode']}, "
                    f"execuție={'armată' if self.rt.trading['execution_enabled'] else 'dezarmată'}, "
                    f"simboluri={','.join(self.rt.active_symbols())}, Claude={'da' if self.ai.key_set else 'fără cheie'}")
        if not s.ADMIN_TOKEN:
            self.rt.log("WARN", "engine", "ADMIN_TOKEN nu este setat — acțiunile de administrare sunt blocate")

    async def start(self) -> None:
        self.tasks.append(asyncio.create_task(self._loop(), name="engine"))
        self.tasks.append(asyncio.create_task(self._broadcaster(), name="status"))

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self.rt.flush_bars()
        self._save_paper()

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

    def symbol_positions(self, symbol: str, mode: str | None = None) -> list[dict]:
        """Open bot positions on `symbol` as [{"side", "open_r", "ticket"}] for the pyramiding rule (open_r = open
        profit in R of the initial risk; None when the initial SL is unknown or an OPEN is still in flight)."""
        out = []
        for p in self.bot_positions(mode):
            if p["symbol"] != symbol:
                continue
            src = "PAPER" if p.get("source") == "PAPER" else "MT5"
            risk = None
            if risk is None:
                sl0 = None
                if src == "PAPER":
                    pos = self.paper.positions.get(int(p["ticket"])) or {}
                    sl0 = pos.get("initial_sl") or pos.get("sl")
                else:
                    oi = self.executor.open_intent_for_ticket(int(p["ticket"])) or \
                        self.executor.open_intent_by_comment(p.get("comment") or "")
                    sl0 = (oi or {}).get("sl")
                if sl0 and p.get("price_open"):
                    risk = abs(float(p["price_open"]) - float(sl0))
            open_r = None
            dist_atr = None
            if risk and p.get("price_current") and p.get("price_open"):
                sign = 1 if p["side"] == "BUY" else -1
                open_r = round((float(p["price_current"]) - float(p["price_open"])) * sign / risk, 3)
            atr = self._atr14(symbol)
            if atr and p.get("price_current") and p.get("price_open"):
                dist_atr = round(abs(float(p["price_current"]) - float(p["price_open"])) / atr, 3)
            out.append({"side": p["side"], "open_r": open_r, "ticket": p["ticket"], "entry": p.get("price_open"),
                        "distance_atr": dist_atr})
        if self.executor.has_pending_open(symbol):
            out.append({"side": None, "open_r": None, "ticket": None})
        return out

    def _atr14(self, symbol: str) -> float | None:
        bars = self.rt.closed_bars(symbol, self.rt.strategy["signal_timeframe"])[-120:]
        if len(bars) < 16:
            return None
        return ind.last(ind.atr([b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars], 14))

    def resolve_symbol(self, name: str | None) -> str:
        """The active symbol matching `name` (exact, else case-insensitive), else `name` stripped. API inputs are typed
        by people; the EA and the broker use the exact spelling."""
        name = (name or "").strip()
        act = self.rt.active_symbols()
        if name in act:
            return name
        for s in act:
            if s.upper() == name.upper():
                return s
        return name

    def is_tradable(self, symbol: str) -> bool:
        """False only when TRADE_SYMBOLS is configured and excludes the symbol (the active list already governs analysis)."""
        act = self.rt.active_symbols()
        ts = [s for s in (self.rt.strategy.get("trade_symbols") or []) if s in act]
        return not ts or symbol in ts

    def trade_symbols(self) -> list[str]:
        """Symbols that may receive signals/orders: strategy.trade_symbols (settings/.env TRADE_SYMBOLS) ∩ active;
        empty = every active symbol. The rest are context only (quoted, analysed, shadow-labelled)."""
        act = self.rt.active_symbols()
        ts = [s for s in (self.rt.strategy.get("trade_symbols") or []) if s in act]
        return ts or act

    def pyramid_status(self, symbol: str, side: str, mode: str | None = None) -> tuple[bool, str]:
        """(one more position allowed, note) under risk.max_positions_per_symbol / pyramid_min_r."""
        rk = self.rt.risk
        max_per = int(rk.get("max_positions_per_symbol", 1) or 1)
        if max_per <= 1:
            return False, "o singură poziție pe simbol"
        positions = self.symbol_positions(symbol, mode)
        reason = pyramid_blocker(side, positions, max_per, float(rk.get("pyramid_min_r", 0.5) or 0),
                                          float(rk.get("pyramid_min_distance_atr", 0) or 0),
                                          allow_opposite=bool(rk.get("allow_hedge")))
        if reason:
            return False, reason.rstrip(".")
        return True, f"piramidare: {len(positions)}/{max_per} poziții, toate ≥ {float(rk.get('pyramid_min_r', 0.5)):g}R"

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
            sig_entry = None
            rm = None
            if sl0 and tr.get("price_open") and tr.get("volume"):
                rm = risk_mod.position_risk_money(tr.get("side") or "BUY", float(tr["price_open"]), float(sl0),
                                                  float(tr["volume"]), self.rt.quotes.get(tr.get("symbol") or "") or {})
                if rm is None:
                    rm = risk_money_from_fill(tr, float(sl0))
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

    def history_reset_ts(self) -> float:
        """Owner's 'start from zero' point (settings key history_reset): MT5 deals closed before it are never recorded
        again, although the EA keeps resending the account's last 24 h of deals."""
        r = self.db.get_setting("history_reset")
        try:
            return float((r or {}).get("ts") or 0)
        except (TypeError, ValueError, AttributeError):
            return 0.0

    def clear_history(self, what: str) -> dict:
        """Owner's 'clean page' buttons (Positions page): delete the finished execution intents ('intents') or the closed
        trades ('trades'). Rows still needed live are kept: intents in flight and the intents of open positions (their
        initial SL / signal), trade paths of open positions. The deleted rows are exported first to
        data/backup/history-<what>-<time>.json. Clearing trades also moves the history_reset point to now, so the EA's
        resent last-24h deals do not bring them back."""
        if what not in ("intents", "trades"):
            raise EngineError(400, "what = intents | trades")
        from .config import ROOT_DIR
        from .executor import PENDING_STATES
        open_tickets = [int(p["ticket"]) for p in self.bot_positions("ALL") if p.get("ticket") is not None]
        now = time.time()
        if what == "intents":
            ph_s = ",".join("?" * len(PENDING_STATES))
            ph_t = ",".join("?" * len(open_tickets)) or "NULL"
            where = f"state NOT IN ({ph_s}) AND (ticket IS NULL OR ticket NOT IN ({ph_t}))"
            params = tuple(PENDING_STATES) + tuple(open_tickets)
            table = "intents"
        else:
            where, params, table = "1=1", (), "trades"
        rows = self.db.query(f"SELECT * FROM {table} WHERE {where}", params)
        out_dir = ROOT_DIR / "data" / "backup"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"history-{what}-{datetime.fromtimestamp(now).strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(rows, ensure_ascii=False, default=str))
        self.db.execute(f"DELETE FROM {table} WHERE {where}", params)
        if what == "trades":
            self.db.set_setting("history_reset", {"ts": now, "note": "dashboard: Tranzacții închise → Șterge istoricul"})
        label = "intențiile de execuție" if what == "intents" else "tranzacțiile închise"
        self.rt.log("WARN", "control", f"Istoric șters din dashboard: {len(rows)} {label} (copie {path.name})")
        return {"ok": True, "deleted": len(rows), "backup": str(path.relative_to(ROOT_DIR))}

    def ingest_deals(self, deals: list[dict]) -> None:
        magic = self.settings.MT5_MAGIC
        reset_ts = self.history_reset_ts()
        if reset_ts:
            deals = [d for d in deals if float(d.get("time") or 0) >= reset_ts]
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
                    "close_reason": self._deal_close_reason(pos_id, int(d.get("reason", -1))), "mode": "DEMO",
                    "signal_id": (oi or {}).get("signal_id"),
                }
                if self._insert_trade(f"MT5:{int(d['ticket'])}", tr, open_intent=oi):
                    recent = (d.get("time") or 0) >= self.rt.started_ts - 120
                    self._announce_trade(tr, notify=recent)
            except (KeyError, ValueError, TypeError) as e:
                self.rt.log("WARN", "bridge", f"deal invalid ignorat: {e!r}")

    def _deal_close_reason(self, pos_id: int, deal_reason: int) -> str:
        """Broker deal reason (SL/TP/SO/MANUAL); an EA-sent close (DEAL_REASON_EXPERT = "BOT") is named after the
        bot's latest (non-failed) CLOSE intent for the position (RESCUE, TIME_STOP, ML_EXIT, SESSION_END, MANUAL, ...)."""
        reason = DEAL_REASON.get(deal_reason, "UNKNOWN")
        if reason != "BOT" or not pos_id:
            return reason
        try:
            # the deal can arrive in the same sync as (and before) the intent result: any non-failed CLOSE counts
            row = self.db.query_one("SELECT reason FROM intents WHERE kind='CLOSE' AND ticket=? AND mode='DEMO' AND "
                                    "state IN ('FILLED','QUEUED','DELIVERED','UNKNOWN') "
                                    "ORDER BY created_at_utc DESC LIMIT 1", (int(pos_id),))
        except sqlite3.Error:
            return reason
        return str(row["reason"]) if row and row["reason"] else reason

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
        pyr_ok, pyr_note = self.pyramid_status(symbol, side, mode)
        ctx = risk_mod.RiskContext(
            mode=mode, execution_enabled=self.rt.trading["execution_enabled"],
            entries_paused=self.rt.trading["entries_paused"], side=side, entry=entry, sl=sl, tp=tp,
            symbol_info=dict(self.rt.quotes.get(symbol) or {}), account=acc, risk=self.rt.risk,
            bridge_connected=self.rt.bridge_connected(), terminal=self.rt.bridge.get("terminal"),
            mt5_account=self.rt.mt5_account, tick_age=self.rt.tick_age(symbol),
            symbol=symbol, symbol_tradable=self.is_tradable(symbol),
            open_exposures=self.open_exposures(mode) + [(r["symbol"], r["side"]) for r in reserved],
            open_bot_positions=n_open + len(reserved), ea_magic_ok=self.rt.bridge.get("magic_ok") is True,
            has_bot_position_on_symbol=self.has_bot_position(symbol, mode) or any(r["symbol"] == symbol for r in reserved),
            pyramid_ok=pyr_ok and not any(r["symbol"] == symbol for r in reserved), pyramid_note=pyr_note,
            trades_today=self.trades_today(mode) + len(reserved), day_start_equity=marks.get("day_start_equity"),
            peak_equity=marks.get("peak_equity"), consecutive_losses=streak, loss_pause_until=pause_until,
            open_risk_money=self.open_risk_money(mode) + res_risk, news_block=news_block, news_note=news_note, test_order=test_order,
            account_settling=mode == "DEMO" and self.account_settling(),
            size_factor=size_factor,
        )
        return risk_mod.evaluate(ctx)

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

    def _check_symbol_feed(self, now: float) -> None:
        """An active symbol that never got a quote although the EA has been syncing for 30 s almost always means the
        broker's symbol name differs (XAUUSD.a, GOLD, XAUUSDm). Warn every 10 min, and keep the list in the status."""
        rt = self.rt
        if self.settings.data_source != "mt5_bridge" or not rt.bridge_connected():
            rt.bridge["no_quote_symbols"] = []
            return
        first = rt.bridge.get("first_sync_ts")
        if not first or now - float(first) < 30:
            rt.bridge.setdefault("no_quote_symbols", [])
            return
        missing = [s for s in rt.active_symbols() if not (rt.quotes.get(s) or {}).get("bid")]
        rt.bridge["no_quote_symbols"] = missing
        if not missing:
            return
        if now - self._no_quote_warn_ts >= 600:
            self._no_quote_warn_ts = now
            unknown = rt.bridge.get("unknown_symbols")
            hint = (f"EA-ul raportează că brokerul nu le cunoaște: {', '.join(unknown)}" if unknown else
                    "EA-ul nu trimite cotații pentru ele — numele la broker e probabil diferit (XAUUSD.a, GOLD, XAUUSDm): "
                    "deschide Market Watch în MT5, copiază numele exact în ACTIVE_SYMBOLS și repornește")
            rt.log("WARN", "bridge", f"Fără cotații pentru {', '.join(missing)}. {hint}")

    def _check_calendar(self, now: float) -> None:
        if (self.settings.data_source != "mt5_bridge" or not self._warmup_done
                or int(self.rt.risk.get("news_blackout_minutes", 30) or 0) <= 0):
            return
        if not self.calendar_fresh(now) or self.rt.bridge.get("calendar_source") == "forexfactory":
            # no (fresh) EA calendar: the ForexFactory fallback feed takes its place (fetched in a background thread)
            self.news_feed.poll(now)
            items, fetched = self.news_feed.snapshot()
            if fetched is not None and now - fetched < 24 * 3600:
                if self.rt.bridge.get("calendar_source") != "forexfactory":
                    self.rt.log("INFO", "news", "Calendarul EA lipsește — blocarea pe știri folosește calendarul de rezervă "
                                                "(ForexFactory)")
                self.rt.calendar, self.rt.calendar_ts = items, now
                self.rt.bridge["calendar_source"] = "forexfactory"
                return
        if self.calendar_fresh(now):
            return
        if now - self._cal_warn_ts >= CALENDAR_STALE_S:
            self._cal_warn_ts = now
            self.rt.log("WARN", "news", "Calendar economic indisponibil — blocarea intrărilor pe știri nu se aplică "
                                        "(EA fără calendar sau date mai vechi de 30 min)")

    def _scalp_context(self, symbol: str) -> dict:
        """Multi-timeframe picture for the scalp entry analyst: compact bars + indicators on M1/M5/M15/H1 and the votes of
        the bot's other strategies on M5 (H1 confirmation), so Claude weighs the RSI fade against the bigger trend."""
        rt, now = self.rt, time.time()
        q = rt.quotes.get(symbol) or {}
        digits = int(q.get("digits") or 2)
        r = lambda x, nd=digits: None if x is None else round(float(x), nd)  # noqa: E731
        tfs = {}
        for tf, n_show in (("M1", 30), ("M5", 24), ("M15", 16), ("H1", 24)):
            bars = rt.closed_bars(symbol, tf, now)[-200:]
            if len(bars) < 30:
                continue
            c = [b["c"] for b in bars]
            h = [b["h"] for b in bars]
            lo = [b["l"] for b in bars]
            atr_s = ind.atr(h, lo, c, 14)
            _, _, hist = ind.macd(c, 12, 26, 9)
            mid, up, dn = ind.bollinger(c[-20:], 20, 2.0)
            atr_hist = [x for x in atr_s[-100:] if x is not None]
            tfs[tf] = {
                "close": r(c[-1]), "rsi14": r(ind.last(ind.rsi(c, 14)), 1), "ema20": r(ind.last(ind.ema(c, 20))),
                "ema50": r(ind.last(ind.ema(c, 50))), "atr14": r(ind.last(atr_s)),
                "atr_vs_median": r((ind.last(atr_s) or 0) / (ind.median(atr_hist) or 1), 2) if atr_hist else None,
                "macd_hist_last3": [r(x, digits + 1) for x in hist[-3:]],
                "bollinger": {"lower": r(ind.last(dn)), "mid": r(ind.last(mid)), "upper": r(ind.last(up))},
                "high20": r(max(h[-20:])), "low20": r(min(lo[-20:])), "high100": r(max(h[-100:])), "low100": r(min(lo[-100:])),
                "bars": [[iso(b["t"])[11:16], r(b["o"]), r(b["h"]), r(b["l"]), r(b["c"])] for b in bars[-n_show:]],
            }
        return {"bars_format": "[ora UTC, open, high, low, close]", "timeframes": tfs,
                "spread_usd": r((q.get("ask") or 0) - (q.get("bid") or 0)),
                "point": q.get("point")}

    # ---- intents ---------------------------------------------------------
    def _on_intent_update(self, it: dict, prev: str | None) -> None:
        st = it["state"]
        if it.get("kind") == "OPEN" and st in ("FAILED", "REJECTED"):
            self.basket.on_open_failed(it)
        if prev is None:
            key = "PAPER" if it.get("mode") == "PAPER" else (
                f"DEMO:{it['account_login']}" if it.get("account_login") is not None else self.account_key("DEMO"))
            self._perf_exec("UPDATE intents SET account_key=? WHERE id=? AND account_key IS NULL", (key, it["id"]), "intent_account")
        if st == "UNKNOWN":
            self.risk_alert(f"Comanda {it['kind']} {it.get('symbol')} ({it['id'][:8]}) fără confirmare după 30 s. "
                            f"Starea se reconciliază prin poziții; nu se retrimite.", key=f"unknown:{it['id']}")

    def risk_alert(self, text: str, key: str) -> None:
        self.rt.log("WARN", "risk", text)

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
            if self.executor.has_pending_for_ticket(int(ticket), kinds=("CLOSE",), states=("QUEUED", "DELIVERED")):
                raise EngineError(409, "Există deja o comandă de închidere în curs pentru această poziție")
        return self.executor.submit_close(src, int(ticket), p["symbol"], p["side"], p["volume"],
                                          self.rt.quotes.get(p["symbol"]), reason=reason)

    def close_all(self) -> list[dict]:
        out = []
        for p in self.paper.public_positions():
            out.append(self.close_position("PAPER", p["ticket"], reason="CLOSE_ALL"))
        if self.rt.bridge_connected():
            for p in self.mt5_positions_public():
                if not p["bot_owned"]:
                    continue
                if self.executor.has_pending_for_ticket(p["ticket"], kinds=("CLOSE",), states=("QUEUED", "DELIVERED")):
                    self.rt.log("WARN", "control", f"CLOSE ALL: ticket {p['ticket']} are deja o închidere în curs — sărit")
                    continue
                out.append(self.close_position("MT5", p["ticket"], reason="CLOSE_ALL"))
        elif any(p["bot_owned"] for p in self.mt5_positions_public()):
            self.rt.log("WARN", "control", "CLOSE ALL: pozițiile MT5 ale botului NU au fost închise (bridge deconectat)")
        self.rt.log("WARN", "control", f"CLOSE ALL: {len(out)} comenzi de închidere (doar poziții ale botului)")
        return out

    def set_mode(self, mode: str) -> None:
        mode = (mode or "").upper()
        if mode == "LIVE":
            mode = "DEMO"
        if mode not in ("SIGNAL_ONLY", "PAPER", "DEMO"):
            raise EngineError(400, "Mod invalid")
        if mode == "DEMO":
            if self.settings.data_source == "sim":
                raise EngineError(409, "DEMO indisponibil: DATA_SOURCE=sim (feed simulat)")
            if not self.rt.bridge_connected():
                raise EngineError(409, "DEMO indisponibil: bridge-ul MT5 nu este conectat")
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
            if not self.rt.bridge_connected():
                raise EngineError(409, "Nu pot arma execuția MT5: bridge deconectat")
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

    # ---- live pipeline status (what the bot is doing right now, step by step) ---------------------------------------
    def live_status(self) -> dict:
        """One snapshot per trade symbol for the Overview: MT5 data -> Claude's last decision -> armed entries ->
        position, each step with state ok | wait | bad | idle and a Romanian line, plus a headline."""
        rt, now = self.rt, time.time()
        mode = self.mode()
        bridge_ok = rt.bridge_connected()
        armed = mode in ("PAPER", "DEMO") and rt.trading["execution_enabled"] and not rt.trading["entries_paused"]
        out = []
        for sym in self.trade_symbols() or rt.active_symbols():
            q = rt.quotes.get(sym) or {}
            age = rt.tick_age(sym, now)
            spread, max_sp = q.get("spread_points"), spread_cap(rt.risk.get("max_spread_points", 30), sym, q.get("point"))
            steps = []

            def step(name, state, text):
                steps.append({"step": name, "state": state, "text": text})

            if not bridge_ok:
                step("Date MT5", "bad", "Bridge MT5 deconectat — pornește MT5 cu EA-ul AIBridgeEA pe grafic.")
            elif age is None or age > float(rt.risk.get("max_tick_age_seconds", 10)):
                step("Date MT5", "bad", f"Fără cotații proaspete (ultimul tick acum {age if age is None else round(age)} s) — "
                                        "piață închisă (weekend / pauza zilnică) sau EA oprit.")
            else:
                sp_txt = f"spread {spread} puncte (max {max_sp:g})" if spread is not None else "spread necunoscut"
                step("Date MT5", "ok" if spread is None or spread <= max_sp else "bad",
                     f"Cotații live, tick acum {age:.1f} s, {sp_txt}.")
            warm = self.warmup_reason(sym, now)
            if warm:
                step("Pornire", "wait", f"Încălzire după pornire: {warm}.")
            bs = self.basket.status(sym) or {}
            cl = self.basket.claude_last
            if not self.ai.enabled:
                step("Claude", "bad", "Claude nu este disponibil (CLAUDE_ENABLED / ANTHROPIC_API_KEY).")
            elif bs.get("analysis") and not cl:
                step("Claude", "wait", bs["analysis"])
            else:
                step("Claude", "ok" if (cl or {}).get("status") == "DONE" else "wait",
                     (f"{cl.get('time_utc')}: " + ("; ".join(cl.get("done") or []) or "aștept")
                      + f" · {str(cl.get('market_view') or '')[:200]} · plan: {str(cl.get('plan') or '')[:200]}")
                     if cl else "Claude decide la fiecare bară M1 închisă — prima decizie vine imediat.")
            arm = self.basket.armed.get(sym) or []
            if arm:
                step("Intrare armată", "wait", "; ".join(f"{x['side']} {'spargere' if x['trigger'] == 'BREAKOUT' else 'revenire'} "
                                                        f"la {x['price']:g} (până la {x['expires_utc']})" for x in arm))
            if not armed:
                step("Execuție", "bad", f"Botul e oprit (mod {mode}, "
                                        f"{'pauză intrări' if rt.trading['entries_paused'] else 'execuție dezarmată'}).")
            pos = [p for p in self.bot_positions(mode) if p.get("symbol") == sym]
            pos_txt = None
            if pos:
                pos_txt = "; ".join(f"{p.get('side')} {p.get('volume')} @ {p.get('price_open')}: profit {p.get('profit')} · "
                                    f"SL {p.get('sl')} · TP {p.get('tp')}" for p in pos)
                step("Poziție", "ok", pos_txt)
            else:
                step("Poziție", "idle", "Nicio poziție deschisă.")
            bad = next((s_ for s_ in steps if s_["state"] == "bad"), None)
            if bad:
                headline, level = f"Oprit: {bad['text']}", "bad"
            elif pos:
                headline, level = f"Poziție deschisă — {pos_txt}", "ok"
            elif warm:
                headline, level = f"Pornire: {warm}", "wait"
            elif arm:
                headline, level = "Intrare armată — se execută singură când prețul ajunge la nivel.", "wait"
            else:
                headline, level = "Lucrează normal — Claude analizează fiecare bară M1.", "ok"
            out.append({"symbol": sym, "level": level, "headline": headline, "steps": steps, "time_utc": iso(now)})
        return {"symbols": out, "mode": mode, "armed": armed,
                "today": {"trades_closed": len(self.closed_today(mode if mode in ("PAPER", "DEMO") else None))}}

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
        return {
            "app_env": s.APP_ENV, "version": VERSION, "data_source": s.data_source, "feed_label": s.feed_label,
            "bridge": {
                "connected": connected, "last_sync_utc": iso(rt.bridge["last_sync_ts"]) if rt.bridge.get("last_sync_ts") else None,
                "age_s": round(age, 1) if age is not None else None,
                "terminal_connected": bool(term.get("connected")) if term else False,
                "trade_allowed": bool(term.get("trade_allowed")) if term else False,
                "ea_version": rt.bridge.get("ea_version"),
                "unknown_symbols": rt.bridge.get("unknown_symbols") or [],
                "no_quote_symbols": rt.bridge.get("no_quote_symbols") or [],
                "clock_skew_s": rt.bridge.get("clock_skew_s", 0),
                "clock_raw_skew_s": rt.bridge.get("clock_raw_skew_s"),
            },
            "account": account,
            "paper_account": paper_acc,
            "session": self.session(),
            "trading": {"mode": mode, "execution_enabled": rt.trading["execution_enabled"],
                        "trade_symbols": self.trade_symbols(), "profile": s.profile_name or None,
                        "entries_paused": rt.trading["entries_paused"], "live_allowed": bool(s.LIVE_TRADING_ALLOWED)},
            "ai": self.ai.status(),
            "claude_cost": self.claude_cost(),
            "counts": {
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
                "quality": quality, "max_spread_points": spread_cap(rt.risk.get("max_spread_points", 30), sym, (q or {}).get("point")),
                "analysis": rt.last_analysis.get(sym),
            })
        return out

    # ---- accounts / Claude usage --------------------------------------------
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


    def record_claude_usage(self, rec: dict) -> None:
        self._perf_exec(
            "INSERT INTO claude_usage(ts, signal_id, model, input_tokens, output_tokens, cache_read_tokens, cost_usd) "
            "VALUES(?,?,?,?,?,?,?)",
            (rec["ts"], rec.get("signal_id"), rec.get("model"), rec.get("input_tokens"), rec.get("output_tokens"),
             rec.get("cache_read_tokens"), rec.get("cost_usd")), "claude_usage")

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
            self.rt.log("INFO", "scheduler", "Sesiunea de tranzacționare a început")
        elif self._prev_session_open and not sess["open_now"]:
            self.rt.log("INFO", "scheduler", "Sesiunea s-a închis — fără intrări noi; pozițiile rămân gestionate")
        self._prev_session_open = sess["open_now"]
        if self.settings.data_source == "mt5_bridge":
            connected = rt.bridge_connected()
            if self._prev_bridge_connected and not connected:
                self._bridge_down_ts = now
                msg = "Bridge-ul MT5 s-a deconectat — intrările noi sunt blocate."
                if self.mode() == "DEMO":
                    self.risk_alert(msg, key=f"bridge_down:{int(now // 60)}")
                else:
                    rt.log("WARN", "bridge", msg)
            elif connected and self._prev_bridge_connected is False and self._bridge_down_ts is not None:
                gap = now - self._bridge_down_ts
                self._bridge_down_ts = None
                rt.log("INFO", "bridge", f"Bridge-ul MT5 s-a reconectat după {gap:.0f} s fără sincronizări "
                                         "(EA-ul trimite de 2 ori pe secundă; pauzele lungi înseamnă terminal MT5 ocupat "
                                         "cu descărcări de istoric, mai multe EA-uri active sau server încărcat)")
            self._prev_bridge_connected = connected
        self.warmup_reason("", now)
        self._check_calendar(now)
        self._check_symbol_feed(now)
        if not self.warmup_reason("", now):
            try:
                self.basket.tick(now)
            except Exception as e:
                rt.log("ERROR", "basket", f"coș: {e!r}")
            try:
                self.session_bo.tick(now)
            except Exception as e:
                rt.log("ERROR", "session", f"spargere sesiune: {e!r}")
        self.executor.check_timeouts(now)

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



# ---- performance math (shared by /stats and /shadow) ------------------------
SHADOW_MIN_N = 30
Z90 = 1.645


def risk_money_from_fill(tr: dict, sl0: float) -> float | None:
    """Money at risk of a closed trade from its own fill when no quote/tick value is known (e.g. deals ingested on the
    first sync after a restart, before the EA sent symbol specs): |profit| × |entry − initial SL| / |close − entry|.
    The tick value cancels out. None when the trade closed at its entry price or without profit."""
    try:
        po, pc, profit = float(tr["price_open"]), float(tr["price_close"]), float(tr.get("profit") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    move = abs(pc - po)
    if move <= 0 or not profit or not sl0:
        return None
    sign = 1 if (tr.get("side") or "BUY") == "BUY" else -1
    if (float(sl0) - po) * sign >= 0:
        return 0.0
    return abs(profit) * abs(po - float(sl0)) / move


def _ts(s: str | None) -> float | None:
    try:
        d = parse_iso(s)
    except (TypeError, ValueError):
        return None
    return d.timestamp() if d else None
