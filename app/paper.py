"""Paper broker simulator using live bid/ask."""
from __future__ import annotations

import time
from typing import Callable

from .config import iso

PAPER_TICKET_START = 900_000_001
PAPER_LEVERAGE = 100


def position_pnl(side: str, price_open: float, bid: float, ask: float, volume: float, info: dict) -> tuple[float, float]:
    """returns (current close price, profit in account currency)"""
    tick_size = float(info.get("tick_size") or info.get("point") or 0.00001)
    tick_value = float(info.get("tick_value") or 1.0)
    if side == "BUY":
        px = bid
        diff = bid - price_open
    else:
        px = ask
        diff = price_open - ask
    return px, diff / tick_size * tick_value * volume


class PaperBroker:
    def __init__(self, start_balance: float, magic: int, on_close: Callable[[dict, dict], None] | None = None):
        self.start_balance = float(start_balance)
        self.balance = float(start_balance)
        self.magic = magic
        self.positions: dict[int, dict] = {}
        self.next_ticket = PAPER_TICKET_START
        self.on_close = on_close
        self.on_change: Callable[[], None] | None = None

    # ---- persistence -----------------------------------------------------
    def to_dict(self) -> dict:
        return {"balance": self.balance, "next_ticket": self.next_ticket, "positions": list(self.positions.values()),
                "start_balance": self.start_balance}

    def load(self, d: dict | None) -> None:
        if not d:
            return
        self.balance = float(d.get("balance", self.balance))
        self.next_ticket = int(d.get("next_ticket", self.next_ticket))
        self.positions = {int(p["ticket"]): p for p in d.get("positions", [])}

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    # ---- trading ---------------------------------------------------------
    def open(self, symbol: str, side: str, volume: float, sl: float, tp: float, info: dict,
             comment: str = "", intent_id: str | None = None, signal_id: str | None = None,
             now: float | None = None) -> dict:
        now = now or time.time()
        bid, ask = float(info["bid"]), float(info["ask"])
        price = ask if side == "BUY" else bid
        ticket = self.next_ticket
        self.next_ticket += 1
        pos = {
            "ticket": ticket, "symbol": symbol, "side": side, "volume": float(volume),
            "price_open": price, "price_current": bid if side == "BUY" else ask,
            "sl": float(sl), "tp": float(tp), "initial_sl": float(sl), "profit": 0.0, "swap": 0.0,
            "opened_ts": now, "comment": comment, "magic": self.magic,
            "intent_id": intent_id, "signal_id": signal_id,
            "contract_size": float(info.get("contract_size") or 100000),
            "tick_value": float(info.get("tick_value") or 0), "tick_size": float(info.get("tick_size") or info.get("point") or 0),
        }
        pos["price_current"], profit = position_pnl(side, price, bid, ask, volume, info)
        pos["profit"] = round(profit, 2)
        self.positions[ticket] = pos
        self._changed()
        return pos

    def modify(self, ticket: int, sl: float | None, tp: float | None) -> bool:
        p = self.positions.get(int(ticket))
        if not p:
            return False
        if sl is not None:
            p["sl"] = float(sl)
        if tp is not None:
            p["tp"] = float(tp)
        self._changed()
        return True

    def close(self, ticket: int, info: dict, reason: str = "BOT", price: float | None = None,
              now: float | None = None) -> dict | None:
        p = self.positions.pop(int(ticket), None)
        if not p:
            return None
        now = now or time.time()
        bid, ask = float(info["bid"]), float(info["ask"])
        px, profit = position_pnl(p["side"], p["price_open"], bid, ask, p["volume"], info)
        if price is not None:
            px = price
            fake_bid = price if p["side"] == "BUY" else bid
            fake_ask = price if p["side"] == "SELL" else ask
            _, profit = position_pnl(p["side"], p["price_open"], fake_bid, fake_ask, p["volume"], info)
        profit = round(profit, 2)
        self.balance = round(self.balance + profit, 2)
        trade = {
            "ticket": p["ticket"], "symbol": p["symbol"], "side": p["side"], "volume": p["volume"],
            "price_open": p["price_open"], "price_close": px, "profit": profit, "commission": 0.0, "swap": 0.0,
            "opened_at_utc": iso(p["opened_ts"]), "closed_at_utc": iso(now), "close_reason": reason,
            "mode": "PAPER", "signal_id": p.get("signal_id"),
        }
        if self.on_close:
            self.on_close(trade, p)
        self._changed()
        return trade

    def on_quote(self, symbol: str, info: dict, now: float | None = None) -> list[dict]:
        """update floating P&L; conservative SL/TP simulation (SL checked first)."""
        closed = []
        bid, ask = info.get("bid"), info.get("ask")
        if not bid or not ask:
            return closed
        for p in list(self.positions.values()):
            if p["symbol"] != symbol:
                continue
            p["price_current"], p["profit"] = position_pnl(p["side"], p["price_open"], bid, ask, p["volume"], info)
            p["profit"] = round(p["profit"], 2)
            sl, tp = p.get("sl") or 0, p.get("tp") or 0
            if p["side"] == "BUY":
                if sl and bid <= sl:
                    closed.append(self.close(p["ticket"], info, "SL", price=min(bid, sl), now=now))
                elif tp and bid >= tp:
                    closed.append(self.close(p["ticket"], info, "TP", price=tp, now=now))
            else:
                if sl and ask >= sl:
                    closed.append(self.close(p["ticket"], info, "SL", price=max(ask, sl), now=now))
                elif tp and ask <= tp:
                    closed.append(self.close(p["ticket"], info, "TP", price=tp, now=now))
        return [c for c in closed if c]

    # ---- views -----------------------------------------------------------
    def margin(self) -> float:
        m = 0.0
        for p in self.positions.values():
            cs = float(p.get("contract_size") or 100000)
            tv, ts = float(p.get("tick_value") or 0), float(p.get("tick_size") or 0)
            conv = tv / (ts * cs) if tv > 0 and ts > 0 and cs > 0 else 1.0  # quote -> account currency
            m += p["volume"] * cs * p["price_open"] * conv / PAPER_LEVERAGE
        return round(m, 2)

    def account(self, currency: str = "USD") -> dict:
        floating = round(sum(p.get("profit", 0.0) for p in self.positions.values()), 2)
        equity = round(self.balance + floating, 2)
        margin = self.margin()
        return {
            "login_masked": "PAPER", "server": "PAPER", "company": "Simulator hârtie", "is_demo": True,
            "currency": currency, "balance": round(self.balance, 2), "equity": equity, "margin": margin,
            "margin_free": round(equity - margin, 2), "leverage": PAPER_LEVERAGE, "profit": floating,
        }

    def public_positions(self) -> list[dict]:
        return [{
            "ticket": p["ticket"], "symbol": p["symbol"], "side": p["side"], "volume": p["volume"],
            "price_open": p["price_open"], "price_current": p["price_current"], "sl": p["sl"], "tp": p["tp"],
            "profit": round(p.get("profit", 0.0), 2), "swap": 0.0, "opened_at_utc": iso(p["opened_ts"]),
            "source": "PAPER", "bot_owned": True, "comment": p.get("comment", ""),
        } for p in self.positions.values()]
