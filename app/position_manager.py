"""Break-even / trailing / time-stop / session-end close for bot-owned positions. Never widens SL."""
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


def plan(pos: dict, info: dict, cfg: dict, now_ts: float, session_open: bool,
         initial_sl: float | None = None, atr: float | None = None, news_event: dict | None = None) -> list[dict]:
    """pos keys: side, price_open, sl, tp, opened_ts. Returns list of actions.
    news_event: imminent high-impact event for the position's currencies -> break-even from +0.3R (NEWS_BE)."""
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
    if be_r > 0 and risk_dist > 0 and profit_dist >= be_r * risk_dist - point / 2:
        be_sl = round(open_px + sign * 2 * point, digits)
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
            actions = plan(pos, info, cfg, now_ts, session_open, init_sl, atr, news_ev)
            if not stale and not any(a["type"] == "CLOSE" for a in actions) and (ticket, "CLOSE") not in self.gave_up:
                ml_a = self._ml_exit(source, pos, now_ts)
                # an early close supersedes this round's SL move only when it can actually be sent: while a failed
                # CLOSE is backing off (or was abandoned) the break-even / trailing / NEWS_BE MODIFY still goes out
                if ml_a is not None and not self._backing_off(ticket, ml_a, now_ts):
                    actions = [ml_a]
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
