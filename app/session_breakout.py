"""Asian-range breakout (owner, 09.11: "more trades, long ones too if they bring big income, or mixed").

A second strategy that runs next to the Claude basket, fully mechanical, at most one trade per symbol per weekday:
the high/low of the M1 bars between SESSION_BREAKOUT_RANGE_START_UTC and _RANGE_END_UTC (00:00-05:00) is the range;
after the range closes the first move through its high (BUY) or low (SELL) opens a position at market with
SL = the range height capped at SESSION_BREAKOUT_SL_CAP_PCT % of the price, TP = SESSION_BREAKOUT_TP_R x that risk,
the SL moved to entry once the trade is SESSION_BREAKOUT_BE_R x risk in profit, and anything still open closed at
SESSION_BREAKOUT_FLAT_UTC (21:00). Days whose range is under _MIN_RANGE_PCT or over _MAX_RANGE_PCT % of the price
are skipped.

Measured 09.11 on 104,642 XAUUSDm M1 bars (21.07-09.11.2026, spread 0.24, 0.01 lot): 70 trades in 79 weekdays,
47 % wins, avg win +39 / avg loss -21 USD, PF 1.69, +510 USD, max drawdown -126; next to the M15 pullback (A2) the
mix made +11.2 USD/day on 4.2 trades/day. Caveat: the 00-05 window was the best of five tested (01-06 PF 1.27,
03-08 PF 0.97); an independent 3.7-year test of the same rules (MQL5 blog 777540) gives PF 1.37.

Positions are tagged by their open comment ("ai:<intent>") kept in the DB setting "session_breakout", so the basket
(BasketTrader.positions) never sees, manages, time-stops or closes them, and Claude is never asked about them.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from .executor import open_comment

KEY = "session_breakout"


class SessionBreakout:
    def __init__(self, engine):
        self.engine = engine
        st = engine.db.get_setting(KEY) or {}
        self.trades: dict[str, dict] = dict(st.get("trades") or {})   # comment -> {symbol, side, entry, risk, be, day}
        self.days: dict[str, dict] = dict(st.get("days") or {})       # symbol -> {day, status, note}
        self._retry_at: dict[str, float] = {}
        self._be_sent: set[str] = set()

    # ---- config / state -------------------------------------------------------------------------------------------
    def cfg(self) -> dict:
        s = self.engine.settings
        return {"on": bool(getattr(s, "SESSION_BREAKOUT_ENABLED", False)),
                "r0": int(getattr(s, "SESSION_BREAKOUT_RANGE_START_UTC", 0)),
                "r1": int(getattr(s, "SESSION_BREAKOUT_RANGE_END_UTC", 5)),
                "flat": int(getattr(s, "SESSION_BREAKOUT_FLAT_UTC", 21)),
                "tp_r": float(getattr(s, "SESSION_BREAKOUT_TP_R", 2.5)),
                "be_r": float(getattr(s, "SESSION_BREAKOUT_BE_R", 1.5)),
                "min_pct": float(getattr(s, "SESSION_BREAKOUT_MIN_RANGE_PCT", 0.30)),
                "max_pct": float(getattr(s, "SESSION_BREAKOUT_MAX_RANGE_PCT", 1.60)),
                "cap_pct": float(getattr(s, "SESSION_BREAKOUT_SL_CAP_PCT", 0.6))}

    def _save(self) -> None:
        # keep the last 200 trade tags (a few months) so the setting stays small
        trades = dict(list(self.trades.items())[-200:])
        self.trades = trades
        self.engine.db.set_setting(KEY, {"trades": trades, "days": self.days})

    def owns(self, p: dict) -> bool:
        return str(p.get("comment") or "") in self.trades

    def positions(self, symbol: str) -> list[dict]:
        return [p for p in self.engine.bot_positions(self.engine.mode()) if p.get("symbol") == symbol and self.owns(p)]

    def status(self) -> dict:
        return {**self.cfg(), "days": self.days}

    # ---- loop ------------------------------------------------------------------------------------------------------
    def tick(self, now: float | None = None) -> None:
        c = self.cfg()
        if not c["on"]:
            return
        eng = self.engine
        now = now or time.time()
        mode = eng.mode()
        if mode not in ("PAPER", "DEMO") or not eng.rt.trading["execution_enabled"]:
            return
        for sym in eng.trade_symbols():
            try:
                self._tick_symbol(sym, now, mode, c)
            except Exception as e:
                eng._perf_warn_once(f"session_breakout:{sym}", f"spargere sesiune {sym}: {e!r}")

    def _log(self, level: str, msg: str) -> None:
        self.engine.rt.log(level, "session", msg)

    def _tick_symbol(self, sym: str, now: float, mode: str, c: dict) -> None:
        eng = self.engine
        utc = datetime.fromtimestamp(now, timezone.utc)
        day, hour = utc.strftime("%Y-%m-%d"), utc.hour
        q = eng.rt.quotes.get(sym) or {}
        if not q.get("bid") or not q.get("ask"):
            return
        pos = self.positions(sym)
        if pos:
            self._manage(sym, pos, q, hour, c)
            return
        if utc.weekday() >= 5 or hour < c["r1"] or hour >= c["flat"]:
            return
        d = self.days.get(sym) or {}
        if d.get("day") == day and d.get("status") in ("traded", "skipped"):
            return
        if eng.executor.has_pending_open(sym) or now < self._retry_at.get(sym, 0):
            return
        t0 = int(datetime(utc.year, utc.month, utc.day, c["r0"], tzinfo=timezone.utc).timestamp())
        t1 = int(datetime(utc.year, utc.month, utc.day, c["r1"], tzinfo=timezone.utc).timestamp())
        # the range from M15 bars: same high/low as the M1 bars of the window, and the runtime keeps 300 of them (3 days)
        # while the 300 M1 bars cover only the last 5 hours - after an afternoon restart the 00-05 M1 bars are gone
        need = max(1, (t1 - t0) // 900)
        bars = [b for b in eng.rt.closed_bars(sym, "M15", now) if t0 <= int(b["t"]) < t1]
        if len(bars) < need - 1:
            if d.get("note") != f"doar {len(bars)} bare M15 în range" or d.get("day") != day:
                self.days[sym] = {"day": day, "status": "waiting", "note": f"doar {len(bars)} bare M15 în range"}
                self._save()
            return
        hi, lo = max(float(b["h"]) for b in bars), min(float(b["l"]) for b in bars)
        ref = float(bars[-1]["c"])
        pct = (hi - lo) / ref * 100 if ref else 0.0
        if d.get("day") != day or d.get("status") == "waiting":
            if pct < c["min_pct"] or pct > c["max_pct"]:
                self.days[sym] = {"day": day, "status": "skipped", "hi": hi, "lo": lo,
                                  "note": f"range {hi - lo:.2f} USD = {pct:.2f} % (în afara {c['min_pct']:g}–{c['max_pct']:g} %)"}
                self._save()
                self._log("INFO", f"Spargere sesiune {sym}: range-ul asiatic {lo:g}–{hi:g} ({pct:.2f} %) e în afara "
                                  f"limitelor {c['min_pct']:g}–{c['max_pct']:g} % — azi nu se tranzacționează")
                return
            self.days[sym] = {"day": day, "status": "armed", "hi": hi, "lo": lo,
                              "note": f"range {lo:g}–{hi:g} ({pct:.2f} %)"}
            self._save()
            self._log("INFO", f"Spargere sesiune {sym}: range-ul asiatic {c['r0']:02d}–{c['r1']:02d} UTC = {lo:g}–{hi:g} "
                              f"({hi - lo:.2f} USD, {pct:.2f} %) — intru la prima spargere până la {c['flat']:02d}:00 UTC")
        ask, bid = float(q["ask"]), float(q["bid"])
        side = "BUY" if ask > hi else "SELL" if bid < lo else None
        if side is None:
            return
        self._open(sym, side, q, mode, now, day, hi, lo, ref, c)

    def _open(self, sym, side, q, mode, now, day, hi, lo, ref, c) -> None:
        eng = self.engine
        sign = 1 if side == "BUY" else -1
        entry = float(q["ask"]) if side == "BUY" else float(q["bid"])
        digits = int(q.get("digits") or 2)
        risk = min(hi - lo, ref * c["cap_pct"] / 100)
        sl, tp = round(entry - sign * risk, digits), round(entry + sign * c["tp_r"] * risk, digits)
        rc = eng.risk_check(sym, side, entry, sl, tp, mode=mode)
        volume = eng.basket.lot(q) or rc.get("volume") or 0.01
        it = eng.executor.submit_open(mode, sym, side, volume, sl, tp, q, rc)
        if it["state"] == "REJECTED_RISK":
            # news window, rollover, margin...: try again in a minute while the day is still open
            self._retry_at[sym] = now + 60
            failed = "; ".join(str(x.get("name")) for x in (rc.get("checks") or []) if not x.get("ok"))[:160]
            self._log("WARN", f"Spargere sesiune {sym}: {side} la {entry:g} refuzat de Risk Engine ({failed}) — reîncerc")
            return
        comment = open_comment(it["id"])
        self.trades[comment] = {"symbol": sym, "side": side, "entry": entry, "risk": round(risk, digits), "be": False,
                                "day": day, "tp": tp, "sl": sl}
        self.days[sym] = {**(self.days.get(sym) or {}), "day": day, "status": "traded",
                          "note": f"{side} {volume} la {entry:g}, SL {sl:g}, TP {tp:g}"}
        self._save()
        self._log("INFO", f"Spargere sesiune {sym}: prețul a spart {'maximul' if side == 'BUY' else 'minimul'} range-ului "
                          f"asiatic ({hi if side == 'BUY' else lo:g}) → {side} {volume} la {entry:g}, SL {sl:g} "
                          f"(−{risk:.2f}), TP {tp:g} (+{c['tp_r'] * risk:.2f}), ține până la TP/SL sau {c['flat']:02d}:00 UTC "
                          f"({it['state']})")

    def _manage(self, sym: str, pos: list[dict], q: dict, hour: int, c: dict) -> None:
        eng = self.engine
        for p in pos:
            tag = self.trades.get(str(p.get("comment") or "")) or {}
            src = "PAPER" if p.get("source") == "PAPER" else "MT5"
            tk = int(p["ticket"])
            if eng.executor.has_pending_for_ticket(tk):
                continue
            if hour >= c["flat"]:
                self._log("INFO", f"Spargere sesiune {sym}: {c['flat']:02d}:00 UTC — închid #{tk} la piață "
                                  f"({float(p.get('profit') or 0):+.2f} USD)")
                try:
                    eng.close_position(src, tk, reason="SESSION_FLAT")
                except Exception as e:
                    self._log("WARN", f"Spargere sesiune: închiderea #{tk} a eșuat: {getattr(e, 'detail', e)!r}")
                continue
            risk = float(tag.get("risk") or 0)
            if not risk or tag.get("be") or c["be_r"] <= 0 or str(p.get("comment")) in self._be_sent:
                continue
            sign = 1 if p["side"] == "BUY" else -1
            open_px = float(p.get("price_open") or tag.get("entry") or 0)
            cur = float(q["bid"]) if sign > 0 else float(q["ask"])
            if (cur - open_px) * sign < c["be_r"] * risk:
                continue
            spread = max(0.0, float(q["ask"]) - float(q["bid"]))
            new_sl = round(open_px + sign * spread, int(q.get("digits") or 2))
            tp = float(p.get("tp") or tag.get("tp") or 0)
            eng.executor.submit_modify(src, tk, sym, p["side"], new_sl, tp, reason="SESSION_BE")
            self._be_sent.add(str(p.get("comment")))
            tag["be"] = True
            self._save()
            self._log("INFO", f"Spargere sesiune {sym}: #{tk} e la +{c['be_r']:g}R — mut SL-ul la intrare ({new_sl:g}), "
                              "tranzacția nu mai poate pierde")
