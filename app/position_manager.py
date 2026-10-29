"""Break-even / trailing / time-stop / session-end / rescue close for bot-owned positions. Never widens SL."""
from __future__ import annotations

import time

from . import indicators as ind

MODIFY_THROTTLE_S = 10.0
FAIL_BACKOFF_BASE_S = 30.0
FAIL_BACKOFF_MAX_S = 900.0
FAIL_MAX_ATTEMPTS = 6
# closes that must happen even on a stale tick (EA closes at market) and are never abandoned
FORCED_CLOSE_REASONS = ("SESSION_END", "TIME_STOP")
NEWS_BE_MIN_R = 0.3
BE_LOCK_MIN_POINTS = 2  # break-even lock with be_lock_r = 0 (old behaviour): entry ± 2 points
BE_LOCK_SPREAD_POINTS = 3  # smart lock: at least the current spread + 3 points beyond the entry


def be_lock_distance(be_lock_r: float, spread_px: float, risk_dist: float, point: float) -> float:
    """Distance of the break-even SL beyond the entry: 2 points when be_lock_r <= 0 (old behaviour), else
    max(spread now + 3 points, be_lock_r × initial risk distance). Shared with exits/labeler for parity."""
    if not be_lock_r or be_lock_r <= 0:
        return BE_LOCK_MIN_POINTS * point
    return max(spread_px + BE_LOCK_SPREAD_POINTS * point, be_lock_r * risk_dist)


def rescue_due(cfg: dict, st: dict | None, r_now: float, now_ts: float) -> bool:
    """Rescue exit (same semantics as exits 'rescue'): armed after rescue_minutes open, MAE ≥ rescue_mae_r and at
    least rescue_neg_frac of the 1-min samples with r < 0; due when the current r ≥ rescue_close_r.
    st: {"opened_ts", "mae_r", "n", "neg"} from the live trade-path tracker (None -> never)."""
    if not cfg.get("rescue_enabled") or not st:
        return False
    opened, n = st.get("opened_ts"), int(st.get("n") or 0)
    if not opened or n <= 0:
        return False
    if now_ts - float(opened) < float(cfg.get("rescue_minutes", 30)) * 60:
        return False
    if float(st.get("mae_r") or 0) < float(cfg.get("rescue_mae_r", 0.3)):
        return False
    if int(st.get("neg") or 0) / n < float(cfg.get("rescue_neg_frac", 0.6)):
        return False
    return r_now >= float(cfg.get("rescue_close_r", 0.1))


def rescue_close(pos: dict, info: dict, cfg: dict, now_ts: float, initial_sl: float | None,
                 rescue_state: dict | None) -> dict | None:
    """The rescue market CLOSE when rescue_due at the current bid/ask (SL never touched), else None."""
    bid, ask = info.get("bid"), info.get("ask")
    if rescue_state is None or not bid or not ask or not initial_sl:
        return None
    open_px = float(pos["price_open"])
    init_risk = abs(open_px - float(initial_sl))
    if init_risk <= 0:
        return None
    sign = 1 if pos["side"] == "BUY" else -1
    cur = bid if pos["side"] == "BUY" else ask
    r_now = (cur - open_px) * sign / init_risk
    # accrued swap/commission count too: "in profit" must mean net money, not just price
    tick_size, tick_value = float(info.get("tick_size") or 0), float(info.get("tick_value") or 0)
    volume = float(pos.get("volume") or 0)
    costs = float(pos.get("swap") or 0) + float(pos.get("commission") or 0)
    if costs and tick_size > 0 and tick_value > 0 and volume > 0:
        r_now += costs / (init_risk / tick_size * tick_value * volume)
    if rescue_due(cfg, rescue_state, r_now, now_ts):
        return {"type": "CLOSE", "reason": "RESCUE"}
    return None


def plan(pos: dict, info: dict, cfg: dict, now_ts: float, session_open: bool,
         initial_sl: float | None = None, atr: float | None = None, news_event: dict | None = None,
         rescue_state: dict | None = None) -> list[dict]:
    """pos keys: side, price_open, sl, tp, opened_ts. Returns list of actions.
    news_event: imminent high-impact event for the position's currencies -> break-even from +0.3R (NEWS_BE).
    rescue_state: trade-path stats for the rescue exit (see rescue_due); the caller passes it only on a fresh quote."""
    bid, ask = info.get("bid"), info.get("ask")
    if not bid or not ask:
        return []
    side = pos["side"]
    sign = 1 if side == "BUY" else -1
    point = float(info.get("point") or 0.00001)
    digits = int(info.get("digits") or 5)
    open_px = float(pos["price_open"])
    sl = float(pos.get("sl") or 0)
    tp = float(pos.get("tp") or 0)
    cur = bid if side == "BUY" else ask

    max_hold = int(cfg.get("max_hold_minutes") or 0)
    opened = pos.get("opened_ts")
    if max_hold > 0 and opened and now_ts - opened >= max_hold * 60:
        return [{"type": "CLOSE", "reason": "TIME_STOP"}]
    if cfg.get("close_at_session_end") and not session_open:
        return [{"type": "CLOSE", "reason": "SESSION_END"}]
    rescue_a = rescue_close(pos, info, cfg, now_ts, initial_sl, rescue_state)
    if rescue_a is not None:
        return [rescue_a]

    freeze = float(info.get("freeze_level") or 0) * point
    if freeze > 0 and ((sl and abs(cur - sl) <= freeze) or (tp and abs(cur - tp) <= freeze)):
        return []  # inside broker freeze zone: modification would be rejected

    profit_dist = (cur - open_px) * sign
    ref_sl = initial_sl if initial_sl else sl
    risk_dist = abs(open_px - ref_sl) if ref_sl else 0.0
    spread = ask - bid
    min_gap = max(spread, float(info.get("stops_level") or 0) * point, freeze) + point

    def improves(new_sl: float) -> bool:
        if not sl:
            return True
        return (new_sl - sl) * sign > point / 2

    def valid(new_sl: float) -> bool:
        return (cur - new_sl) * sign >= min_gap

    best: tuple[float, str] | None = None
    be_r = float(cfg.get("break_even_at_r") or 0)
    be_done = bool(sl) and (sl - open_px) * sign >= 0  # SL already at/above entry: break-even applied once
    if be_r > 0 and risk_dist > 0 and not be_done and profit_dist >= be_r * risk_dist - point / 2:
        lock = be_lock_distance(float(cfg.get("be_lock_r") or 0), spread, risk_dist, point)
        be_sl = round(open_px + sign * lock, digits)
        if improves(be_sl) and valid(be_sl):
            best = (be_sl, "BREAK_EVEN")
    if cfg.get("trailing_enabled") and atr and atr > 0 and profit_dist > 0:
        tr_sl = round(cur - sign * atr * float(cfg.get("atr_sl_mult") or 1.5), digits)
        floor = best[0] if best else sl
        step_ok = (not floor) or (tr_sl - floor) * sign >= 0.1 * atr
        if improves(tr_sl) and valid(tr_sl) and step_ok and (best is None or (tr_sl - best[0]) * sign > 0):
            best = (tr_sl, "TRAILING")
    if news_event and risk_dist > 0 and profit_dist >= NEWS_BE_MIN_R * risk_dist - point / 2:
        nb_sl = round(open_px + sign * 2 * point, digits)
        if improves(nb_sl) and valid(nb_sl) and (best is None or (nb_sl - best[0]) * sign > 0):
            best = (nb_sl, "NEWS_BE")
    if best:
        return [{"type": "MODIFY", "sl": best[0], "tp": tp, "reason": best[1]}]
    return []


class PositionManager:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.last_action: dict[int, float] = {}
        self.gave_up: set[tuple[int, str]] = set()

    def _backing_off(self, ticket: int, action: dict, now_ts: float) -> bool:
        """Exponential backoff after FAILED MODIFY/CLOSE intents; stop after FAIL_MAX_ATTEMPTS with one alert."""
        kind = action["type"]
        n, last = self.engine.executor.consecutive_failures(ticket, kind)
        if n == 0:
            return False
        forced = kind == "CLOSE" and action.get("reason") in FORCED_CLOSE_REASONS
        if n >= FAIL_MAX_ATTEMPTS:
            if (ticket, kind) not in self.gave_up:
                self.gave_up.add((ticket, kind))
                tail = (f"reîncerc la cel mult {FAIL_BACKOFF_MAX_S / 60:g} min." if forced
                        else "opresc reîncercările automate.")
                self.engine.risk_alert(f"{kind} ({action.get('reason')}) pe ticket {ticket} a eșuat de {n} ori — "
                                       f"{tail} Verifică poziția manual.",
                                       key=f"pm_giveup:{ticket}:{kind}")
            if not forced:
                return True
        wait = min(FAIL_BACKOFF_BASE_S * 2 ** (n - 1), FAIL_BACKOFF_MAX_S)
        return last is not None and now_ts - last < wait

    def _atr(self, symbol: str) -> float | None:
        rt = self.engine.rt
        bars = rt.closed_bars(symbol, rt.strategy["signal_timeframe"])[-200:]
        if len(bars) < 20:
            return None
        return ind.last(ind.atr([b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars], 14))

    def _ml_exit(self, source: str, pos: dict, now_ts: float) -> dict | None:
        """Approved ML exit model only (none by default): an early CLOSE with reason ML_EXIT, nothing else — it can
        never move SL/TP or add to a position. Any error = no action."""
        ml = getattr(self.engine, "ml", None)
        if ml is None:
            return None
        try:
            a = ml.exit_action(source, pos, now_ts)
        except Exception as e:
            self.engine.rt.log("WARN", "ml", f"ML exit pe ticket {pos.get('ticket')}: {e!r}")
            return None
        if isinstance(a, dict) and a.get("type") == "CLOSE":
            return {"type": "CLOSE", "reason": "ML_EXIT"}
        return None

    def _rescue_state(self, source: str, pos: dict) -> dict | None:
        """Minutes open / MAE in R / 1-min samples under water of a bot position, from the engine's live trade-path
        tracker (resumed from trade_paths after a restart). None while the position is not tracked yet."""
        paths = getattr(self.engine, "_paths", None) or {}
        st = paths.get((source, str(pos["ticket"])))
        if not st or not st.get("risk"):
            return None
        return {"opened_ts": st.get("opened_ts") or pos.get("opened_ts"), "mae_r": float(st.get("mae") or 0),
                "n": int(st.get("n") or 0), "neg": int(st.get("neg") or 0)}

    def run(self, now_ts: float | None = None) -> None:
        eng = self.engine
        rt = eng.rt
        now_ts = now_ts or time.time()
        cfg = dict(rt.strategy)
        session_open = eng.session()["open_now"]
        items: list[tuple[str, dict, float | None]] = []
        for p in list(eng.paper.positions.values()):
            items.append(("PAPER", p, p.get("initial_sl")))
        acc = rt.mt5_account or {}
        if rt.bridge_connected() and acc.get("trade_mode") == 0:
            for p in rt.mt5_positions:
                if int(p.get("magic") or 0) != rt.settings.MT5_MAGIC:
                    continue
                pos = {"ticket": int(p["ticket"]), "symbol": p["symbol"], "side": "BUY" if int(p.get("type", 0)) == 0 else "SELL",
                       "price_open": p.get("price_open"), "sl": p.get("sl"), "tp": p.get("tp"), "opened_ts": p.get("time"),
                       "volume": p.get("volume")}
                oi = eng.executor.open_intent_for_ticket(pos["ticket"]) or eng.executor.open_intent_by_comment(p.get("comment") or "")
                items.append(("MT5", pos, (oi or {}).get("sl")))
        for source, pos, init_sl in items:
            ticket = int(pos["ticket"])
            if now_ts - self.last_action.get(ticket, 0) < MODIFY_THROTTLE_S:
                continue
            if source == "MT5" and eng.executor.has_pending_for_ticket(ticket):
                continue
            info = rt.quotes.get(pos["symbol"])
            if not info:
                continue
            age = rt.tick_age(pos["symbol"], now_ts)
            stale = age is None or age > float(rt.risk.get("max_tick_age_seconds", 10)) * 3
            atr = self._atr(pos["symbol"]) if cfg.get("trailing_enabled") and not stale else None
            news_ev = eng.news_event_near(pos["symbol"], now_ts) if hasattr(eng, "news_event_near") else None
            rescue_st = None
            if cfg.get("rescue_enabled") and age is not None and age <= float(rt.risk.get("max_tick_age_seconds", 10)):
                rescue_st = self._rescue_state(source, pos)
            # plan() without the rescue state: the rescue CLOSE is arbitrated below like ML_EXIT
            actions = plan(pos, info, cfg, now_ts, session_open, init_sl, atr, news_ev)
            if not stale and not any(a["type"] == "CLOSE" for a in actions) and (ticket, "CLOSE") not in self.gave_up:
                # an early close (RESCUE first, then ML_EXIT) supersedes this round's SL move only when it can actually
                # be sent: while a failed CLOSE is backing off (or was abandoned) the break-even / trailing / NEWS_BE
                # MODIFY still goes out
                early = rescue_close(pos, info, cfg, now_ts, init_sl, rescue_st) or self._ml_exit(source, pos, now_ts)
                if early is not None and not self._backing_off(ticket, early, now_ts):
                    actions = [early]
            if stale:
                # no SL moves on stale prices, but session-end / time-stop closes still go out (EA closes at market)
                actions = [a for a in actions if a["type"] == "CLOSE" and a.get("reason") in FORCED_CLOSE_REASONS]
            for a in actions:
                if self._backing_off(ticket, a, now_ts):
                    continue
                self.last_action[ticket] = now_ts
                try:
                    if a["type"] == "CLOSE":
                        rt.log("INFO", "positions", f"Închidere automată ticket {ticket} ({a['reason']})")
                        eng.close_position(source, ticket, reason=a["reason"])
                    elif a["type"] == "MODIFY":
                        it = eng.executor.submit_modify(source, ticket, pos["symbol"], pos["side"], a["sl"], a["tp"], reason=a["reason"])
                        if it["state"] == "PAPER_FILLED":
                            eng.notify_modify(pos, a)  # DEMO: notified when the intent is FILLED
                except Exception as e:
                    rt.log("WARN", "positions", f"Acțiune {a['type']} pe ticket {ticket} eșuată: {getattr(e, 'detail', e)!r}")
        live = {int(p["ticket"]) for _, p, _ in items}
        for k in list(self.gave_up):
            if k[0] not in live:
                self.gave_up.discard(k)
        for t in list(self.last_action):
            if t not in live:
                self.last_action.pop(t, None)
