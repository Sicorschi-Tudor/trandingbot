"""Basket / grid trader (owner's EXPERIMENT): the scheme from the owner's video. Several bot positions on one
symbol in the same direction, closed together:

- no basket open: BASKET_START_POSITIONS positions at market, one every ACTION_GAP_S seconds (each OPEN must be filled
  before the next), direction = momentum of the last BASKET_MOMENTUM_BARS closed M1 bars (close now >= then -> BUY);
- price BASKET_STEP_USD further against the last entry: one more position (up to BASKET_MAX_POSITIONS);
- net profit of the whole basket >= BASKET_TP_PER_POSITION_USD x positions (per 0.01 lot) when that is set, else
  >= BASKET_TP_USD: close every position (reason BASKET_TP);
- net loss <= -BASKET_MAX_LOSS_USD (or BASKET_MAX_LOSS_PCT % of the balance at the basket's first entry, when set):
  close every position (reason BASKET_STOP).
Entry mode "mtf" (BASKET_ENTRY_MODE, owner's rule 2026-10-30): no burst, no averaging. One decision per closed M1 bar:
the trend of M1, M5, M15 and H1 (EMA20 vs EMA50 on each) must agree on at least BASKET_MTF_MIN_VOTES of 4, and then ONE
position opens in that direction (up to BASKET_MAX_POSITIONS, all on the same side); after any position closes the
bot waits for the next closed M1 bar and re-analyses everything before the next one. Measured (scratch model on 105 days
of M1, out of sample): the 4-timeframe consensus wins 74-76 % at +0.8 / -4, the same as without it (breakeven 83 %).
Entry vote (BASKET_ENTRY_VOTE, owner's rule 2026-10-30): "short" = the trades last minutes and close at +0.80, so the
direction comes from the short term only - M1 and M5 (EMA9 vs EMA21) must both point the same way; M15/H1 are shown for
context but never decide (a falling H1 no longer means "always SELL"). "mtf" = the 4-timeframe consensus above.
Continuous recovery (BASKET_RECOVERY_ENABLED, owner's rule 2026-10-30): once a basket is down
BASKET_RECOVERY_TRIGGER_USD the bot keeps hedging it - see _recovery_step: up to BASKET_RECOVERY_POSITIONS positions
(0.01, normal +0.80 TP) against the losing side, but only at the moment the losers turn against again: their best
level since the last round is tracked, and a round opens once they worsened by BASKET_RECOVERY_TURN_USD from it with M1
pointing against them (or by BASKET_RECOVERY_STEP_USD without M1). While they come back towards their TP nothing opens.
Hedges that take their TP are replaced at the next turn, round after round, no lot doubling. The whole group closes at +BASKET_RECOVERY_EXIT_USD or at -BASKET_RECOVERY_MAX_LOSS_USD.
Strategy 2 "zone recovery" (owner's choice from the Positions page, settings key basket.strategy = s2): one position
in the short-term direction with the normal +0.80 TP; if the price crosses the zone (BASKET_ZR_ZONE_USD against it),
the opposite side opens with the lot x BASKET_ZR_MULT; every further cross of the opposite zone line adds the next side
with the lot x MULT again, up to BASKET_ZR_MAX_LEVELS. From the second level on the individual TPs are removed and the
whole group closes at a net of +BASKET_ZR_EXIT_USD, or at the basket loss limit. The lots grow geometrically (0.01,
0.02, 0.04, 0.08 ...): a long two-way market ends at the limit or at the broker's margin.
Claude manager (settings key basket.claude = true, owner's choice 2026-10-30): instead of the rules above, once per
closed M1 bar Claude (app.ai_claude.decide_basket) gets the whole state and answers 0-4 actions (OPEN side/lot/TP,
CLOSE ticket, CLOSE_ALL); the code still enforces CLAUDE_MAX_LOT per position, BASKET_MAX_POSITIONS_CLAUDE positions,
the basket loss limit (closes everything) and the broker's margin. One call in flight at a time; a failed call = no
action this bar.
Scaling with the balance (as in the video): BASKET_LOT_PER_USD > 0 -> lot = 0.01 per that much balance (min 0.01, capped
by risk.max_volume_lots); the per-position profit target is per 0.01 lot, so it grows with the lot.
Every position carries a broker-side SL BASKET_SL_DISTANCE away; its broker TP is BASKET_POSITION_TP_USD of profit per
0.01 lot (each position closes on its own the moment it gets there, as in the video), or BASKET_SL_DISTANCE when 0.
Each OPEN goes through the normal Risk Engine (bridge, armed execution, fixed lot).
Scratch backtest on 15 weeks of M1 gold (1 start position, step 3, max 5, TP 10, stop 60): 5 weeks doubled, 7 weeks
lost the account (stop hit repeatedly), median x1.27 — a high-risk experiment, not a measured edge."""
from __future__ import annotations

import asyncio

from . import indicators as ind

import math
import time

ACTION_GAP_S = 2.0  # at most one open/close round per symbol every few seconds (orders in flight settle first)
REJECT_PAUSE_S = 60.0  # after the Risk Engine refuses an OPEN (e.g. free margin), no further OPEN for a minute


class BasketTrader:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.last_action: dict[str, float] = {}
        self.state: dict[str, dict] = {}
        self.claude_last: dict | None = None  # last Claude decision (shown on the dashboard)
        self.claude_history: list[dict] = []  # last decisions, fed back to Claude
        self._claude_task = None

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.engine.settings, "BASKET_ENABLED", False))

    def cfg(self) -> dict:
        s = self.engine.settings
        out = {"start": int(s.BASKET_START_POSITIONS), "step": float(s.BASKET_STEP_USD),
                "tp_pos": float(s.BASKET_TP_PER_POSITION_USD), "lot_per": float(s.BASKET_LOT_PER_USD),
                "stop_pct": float(s.BASKET_MAX_LOSS_PCT), "pos_tp": float(s.BASKET_POSITION_TP_USD),
                "entry": str(s.BASKET_ENTRY_MODE).lower(), "min_votes": int(s.BASKET_MTF_MIN_VOTES),
                "vote": str(s.BASKET_ENTRY_VOTE).lower(),
                "rec": bool(s.BASKET_RECOVERY_ENABLED), "rec_trig": float(s.BASKET_RECOVERY_TRIGGER_USD),
                "rec_n": int(s.BASKET_RECOVERY_POSITIONS), "rec_exit": float(s.BASKET_RECOVERY_EXIT_USD),
                "rec_cap": float(s.BASKET_RECOVERY_MAX_LOSS_USD), "rec_flip": str(s.BASKET_RECOVERY_FLIP).lower(),
                "rec_step": float(s.BASKET_RECOVERY_STEP_USD), "rec_turn": float(s.BASKET_RECOVERY_TURN_USD),
                "max": int(s.BASKET_MAX_POSITIONS), "tp": float(s.BASKET_TP_USD), "stop": float(s.BASKET_MAX_LOSS_USD),
                "sl_dist": float(s.BASKET_SL_DISTANCE), "mom": int(s.BASKET_MOMENTUM_BARS)}
        out.update(claude_max_lot=float(s.CLAUDE_MAX_LOT), claude_max_pos=int(s.BASKET_MAX_POSITIONS_CLAUDE))
        out.update(strategy=self.strategy(), zr_zone=float(s.BASKET_ZR_ZONE_USD), zr_mult=float(s.BASKET_ZR_MULT),
                   zr_levels=int(s.BASKET_ZR_MAX_LEVELS), zr_exit=float(s.BASKET_ZR_EXIT_USD))
        limit = self.loss_limit_override()
        if limit:
            # owner's value from the Positions page: the group closes at -limit (recovery cap and plain stop); the
            # broker SL of each position is never tighter than it, so a single position cannot stop out first
            out.update(rec_cap=limit, stop=limit, sl_dist=max(out["sl_dist"], limit))
        return out

    STRATEGIES = {"s1": "Strategia 1 — coș + acoperire cu lot egal",
                  "s2": "Strategia 2 — acoperire cu lot dublu (zone recovery)"}

    def claude_on(self) -> bool:
        return bool((self.engine.db.get_setting("basket") or {}).get("claude"))

    def set_claude(self, on: bool) -> dict:
        if self.running():
            raise ValueError("Oprește întâi botul, apoi schimbă modul Claude")
        cur = dict(self.engine.db.get_setting("basket") or {})
        cur["claude"] = bool(on)
        self.engine.db.set_setting("basket", cur)
        self.engine.rt.log("WARN", "control", "Claude ia deciziile botului: " + ("DA" if on else "NU (reguli fixe)"))
        return self.settings_public()

    def strategy(self) -> str:
        s = str((self.engine.db.get_setting("basket") or {}).get("strategy") or "s1")
        return s if s in self.STRATEGIES else "s1"

    def running(self) -> bool:
        tr = self.engine.rt.trading
        return bool(tr.get("execution_enabled")) and not tr.get("entries_paused")

    def set_strategy(self, name: str) -> dict:
        if name not in self.STRATEGIES:
            raise ValueError(f"strategie necunoscută: {name}")
        if self.running():
            raise ValueError("Oprește întâi botul, apoi schimbă strategia")
        cur = dict(self.engine.db.get_setting("basket") or {})
        old = self.strategy()
        cur["strategy"] = name
        self.engine.db.set_setting("basket", cur)
        for st in self.state.values():
            st.clear()
        self.engine.rt.log("WARN", "control", f"Strategia botului schimbată din dashboard: {self.STRATEGIES[old]} → "
                                              f"{self.STRATEGIES[name]}")
        return self.settings_public()

    def loss_limit_override(self) -> float | None:
        """Basket loss limit saved from the dashboard (settings key 'basket', max_loss_usd), None = profile value."""
        try:
            v = float((self.engine.db.get_setting("basket") or {}).get("max_loss_usd") or 0)
        except (TypeError, ValueError, AttributeError):
            return None
        return v if v > 0 else None

    def set_loss_limit(self, usd: float) -> dict:
        try:
            usd = round(float(usd), 2)
        except (TypeError, ValueError):
            raise ValueError("valoare invalidă")
        if not 1 <= usd <= 1000:
            raise ValueError("limita coșului trebuie să fie între 1 și 1000 USD")
        old = self.cfg()["rec_cap"] if self.cfg()["rec"] else self.cfg()["stop"]
        cur = dict(self.engine.db.get_setting("basket") or {})
        cur["max_loss_usd"] = usd
        self.engine.db.set_setting("basket", cur)
        self.engine.rt.log("WARN", "control", f"Limita coșului schimbată din dashboard: −{old:g} → −{usd:g} USD")
        return self.settings_public()

    def settings_public(self) -> dict:
        c = self.cfg()
        return {"enabled": self.enabled, "max_loss_usd": c["rec_cap"] if c["rec"] else c["stop"],
                "position_sl_usd": c["sl_dist"], "position_tp_usd": c["pos_tp"], "recovery": c["rec"],
                "recovery_trigger_usd": c["rec_trig"], "source": "dashboard" if self.loss_limit_override() else "profil",
                "strategy": c["strategy"], "strategies": [{"id": k, "name": v} for k, v in self.STRATEGIES.items()],
                "running": self.running(), "claude": self.claude_on(), "claude_max_lot": c["claude_max_lot"],
                "claude_last": self.claude_last,
                "zr": {"zone_usd": c["zr_zone"], "mult": c["zr_mult"], "levels": c["zr_levels"], "exit_usd": c["zr_exit"]}}

    def positions(self, symbol: str) -> list[dict]:
        return [p for p in self.engine.bot_positions(self.engine.mode()) if p.get("symbol") == symbol]

    @staticmethod
    def net(p: dict) -> float:
        return float(p.get("profit") or 0) + float(p.get("swap") or 0) + float(p.get("commission") or 0)

    def tick(self, now: float | None = None) -> None:
        if not self.enabled:
            return
        eng = self.engine
        now = now or time.time()
        mode = eng.mode()
        if mode not in ("PAPER", "DEMO") or not eng.rt.trading["execution_enabled"] or eng.rt.trading["entries_paused"]:
            return
        for sym in eng.trade_symbols():
            try:
                self._tick_symbol(sym, now, mode)
            except Exception as e:
                eng._perf_warn_once(f"basket:{sym}", f"coș {sym}: {e!r}")

    def _tick_symbol(self, sym: str, now: float, mode: str) -> None:
        eng, c = self.engine, self.cfg()
        if now - self.last_action.get(sym, 0) < ACTION_GAP_S or eng.executor.has_pending_open(sym):
            return
        opens_paused = now < self.state.get(sym, {}).get("open_pause_until", 0)
        q = eng.rt.quotes.get(sym) or {}
        if not q.get("bid") or not q.get("ask"):
            return
        pos = self.positions(sym)
        st = self.state.setdefault(sym, {})
        if pos and any(eng.executor.has_pending_for_ticket(int(p["ticket"]), kinds=("CLOSE",)) for p in pos):
            return  # basket closing: wait until the broker confirms
        if self.claude_on():
            self._claude_tick(sym, pos, q, mode, now)
            return
        if c["strategy"] == "s2":
            self._zr_tick(sym, pos, q, mode, now, opens_paused)
            return
        mixed = len({p["side"] for p in pos}) > 1
        if pos and self._fix_tp(sym, pos, q):
            return  # a TP correction is in flight
        if pos and c["rec"] and (mixed or st.get("recovery")):
            self._recovery_step(sym, pos, q, mode, now)
            return
        if not pos:
            for k in ("recovery", "round_px", "rounds", "best_loss"):
                st.pop(k, None)
        if pos:
            total = round(sum(self.net(p) for p in pos), 2)
            target = (round(c["tp_pos"] * sum(float(p.get("volume") or 0.01) for p in pos) / 0.01, 2)
                      if c["tp_pos"] > 0 else c["tp"])
            stop = c["rec_cap"] if c["rec"] else self.stop_usd(st)
            st.update(n=len(pos), total=total, side=pos[0]["side"], target=target, stop_usd=stop, recovery=False)
            reason = "BASKET_TP" if total >= target else "BASKET_STOP" if total <= -stop else None
            if reason:
                self._close_all(sym, pos, reason, total, now)
                return
            if c["rec"] and total <= -c["rec_trig"]:
                st["recovery"] = True
                self.engine.rt.log("WARN", "basket", f"Coș {sym}: {pos[0]['side']} pe {total:+.2f} USD — pornesc "
                                                     "acoperirea continuă")
                self._recovery_step(sym, pos, q, mode, now)
                return
            if c["entry"] == "mtf":
                self._mtf_step(sym, pos, q, mode, now, opens_paused)
                return
            side = pos[0]["side"]
            sign = 1 if side == "BUY" else -1
            entries = [float(p["price_open"]) for p in pos if p.get("price_open")]
            last = min(entries) if side == "BUY" else max(entries)
            cur = float(q["ask"]) if side == "BUY" else float(q["bid"])
            st["next_add"] = round(last - sign * c["step"], 2) if len(pos) < c["max"] else None
            if opens_paused:
                return
            if st.get("filling") and len(pos) < c["start"]:
                self._open(sym, side, q, mode, now, f"umplu coșul ({len(pos) + 1}/{c['start']})")
                return
            st["filling"] = False
            if len(pos) < c["max"] and (last - cur) * sign >= c["step"]:
                self._open(sym, side, q, mode, now, f"adaug poziția {len(pos) + 1}/{c['max']} (prețul a mers "
                                                     f"{(last - cur) * sign:.2f} USD contra)")
            return
        if c["entry"] == "mtf":
            st.update(n=0, total=0.0)
            self._mtf_step(sym, pos, q, mode, now, opens_paused)
            return
        if opens_paused:
            return
        st.clear()
        bars = eng.rt.closed_bars(sym, "M1", now)
        if len(bars) <= c["mom"]:
            return
        side = "BUY" if bars[-1]["c"] >= bars[-1 - c["mom"]]["c"] else "SELL"
        st["filling"] = c["start"] > 1
        st["balance0"] = self.balance()
        self._open(sym, side, q, mode, now, f"coș nou {side} (1/{c['start']}), impuls {c['mom']} bare M1")

    def _recovery_step(self, sym: str, pos: list[dict], q: dict, mode: str, now: float) -> None:
        """Continuous recovery: every tick, find the losing side; while it recovers wait, and when it turns against
        again (TURN from its best level with M1 against, or STEP without) open up to BASKET_RECOVERY_POSITIONS
        positions against it (normal +0.80 TP). The whole group closes at +EXIT (out at about zero) or -CAP."""
        c, st = self.cfg(), self.state.setdefault(sym, {})
        total = round(sum(self.net(p) for p in pos), 2)
        by = {}
        for p in pos:
            by[p["side"]] = by.get(p["side"], 0.0) + self.net(p)
        st.update(n=len(pos), total=total, recovery=True, target=c["rec_exit"], stop_usd=c["rec_cap"],
                  side="/".join(f"{s} {sum(1 for p in pos if p['side'] == s)}" for s in sorted(by)))
        if total >= c["rec_exit"]:
            self._close_all(sym, pos, "BASKET_RECOVERED", total, now)
            return
        if total <= -c["rec_cap"]:
            self._close_all(sym, pos, "BASKET_REC_STOP", total, now)
            return
        losing = min(by, key=by.get)
        loss_now = by[losing]
        # owner's rule 2026-10-30: while the losing positions come back towards their TP, do not hedge - track their
        # best (least negative) level since the last round and hedge only once they turn against again by TURN
        best = max(st.get("best_loss", loss_now), loss_now)
        st["best_loss"] = best
        drop = best - loss_now  # how much the losers worsened again from their best level
        if loss_now >= 0 or now < st.get("open_pause_until", 0):
            return
        hedge = "SELL" if losing == "BUY" else "BUY"
        n_hedge = sum(1 for p in pos if p["side"] == hedge)
        if n_hedge >= c["rec_n"]:
            return
        vote, votes = self.m1_vote(sym, now)  # the turn is read on M1 alone (fastest; M5 lagged live)
        txt = ", ".join(f"{k} {'↑' if v > 0 else '↓' if v < 0 else '–'}" for k, v in votes.items())
        st["analysis"] = (f"{losing} pe {loss_now:+.2f} (cel mai bun {best:+.2f}, s-a adâncit {drop:.2f}); "
                          f"termen scurt {txt}")
        if drop < c["rec_turn"]:
            st["analysis"] += f" · revine sau stă — aștept (acopăr când se adâncește cu {c['rec_turn']:g})"
            return
        if vote != hedge and drop < c["rec_step"]:
            st["analysis"] += f" · M1 încă nu confirmă — acopăr la M1 întors sau adâncire {c['rec_step']:g}"
            return
        if n_hedge + 1 >= c["rec_n"]:
            st["rounds"] = int(st.get("rounds") or 0) + 1
            st["best_loss"] = loss_now  # the next round waits for a new turn from here
        why = (f"pierderea s-a adâncit din nou cu {drop:.2f} față de {best:+.2f}"
               + (", M1 confirmă" if vote == hedge else ""))
        self._open(sym, hedge, q, mode, now, f"acoperire {n_hedge + 1}/{c['rec_n']} {hedge} ({why}; {losing} pe "
                                             f"{loss_now:+.2f}, grup {total:+.2f})")

    # ---- Claude manager ---------------------------------------------------------------------------------------------
    def _claude_tick(self, sym: str, pos: list[dict], q: dict, mode: str, now: float) -> None:
        """Hard limit first (basket loss limit closes everything, in code), then one Claude decision per closed M1 bar."""
        c, st = self.cfg(), self.state.setdefault(sym, {})
        eng = self.engine
        total = round(sum(self.net(p) for p in pos), 2)
        limit = c["rec_cap"] if c["rec"] else c["stop"]
        st.update(n=len(pos), total=total, claude=True, stop_usd=limit, side=" + ".join(
            f"{p['side']} {p['volume']}" for p in pos))
        if pos and total <= -limit:
            self._close_all(sym, pos, "CLAUDE_LIMIT", total, now)
            return
        if not eng.ai.enabled or not eng.ai.key_set:
            st["analysis"] = "Claude nu este disponibil (CLAUDE_ENABLED / ANTHROPIC_API_KEY)"
            return
        bars = eng.rt.closed_bars(sym, "M1", now)
        if not bars or st.get("claude_bar") == bars[-1]["t"]:
            return
        if self._claude_task is not None and not self._claude_task.done():
            return  # previous decision still thinking
        st["claude_bar"] = bars[-1]["t"]
        payload = self._claude_payload(sym, pos, q, now)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        coro = self.claude_decide_and_apply(sym, payload, mode)
        if loop is None:
            asyncio.run(coro)
        else:
            self._claude_task = loop.create_task(coro)

    def _claude_payload(self, sym: str, pos: list[dict], q: dict, now: float) -> dict:
        eng, c = self.engine, self.cfg()
        _, acc = eng.risk_account(eng.mode())
        from datetime import datetime, timezone
        positions = []
        for p in pos:
            opened = p.get("opened_at_utc")
            mins = None
            if opened:
                try:
                    mins = round((now - datetime.fromisoformat(opened.replace("Z", "+00:00")).timestamp()) / 60, 1)
                except ValueError:
                    mins = None
            positions.append({"ticket": int(p["ticket"]), "side": p["side"], "lot": p.get("volume"),
                              "open": p.get("price_open"), "now": p.get("price_current"), "pl_usd": round(self.net(p), 2),
                              "tp": p.get("tp"), "sl": p.get("sl"), "minutes_open": mins})
        recent = eng.db.query("SELECT side, volume, price_open, price_close, profit, close_reason, closed_at_utc FROM trades "
                              "WHERE symbol=? ORDER BY closed_at_utc DESC LIMIT 10", (sym,))
        try:
            charts = eng._scalp_context(sym)
        except Exception:
            charts = {}
        return {
            "time_utc": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": sym, "strategy": c["strategy"],
            "account": {k: (acc or {}).get(k) for k in ("balance", "equity", "margin", "margin_free", "leverage")},
            "quote": {"bid": q.get("bid"), "ask": q.get("ask"), "spread_usd": round(float(q["ask"]) - float(q["bid"]), 2)},
            "positions": positions,
            "group_total_usd": round(sum(x["pl_usd"] for x in positions), 2),
            "constraints": {"max_lot": c["claude_max_lot"], "max_positions": c["claude_max_pos"],
                            "basket_loss_limit_usd": c["rec_cap"] if c["rec"] else c["stop"],
                            "tp_per_001_lot_usd": c["pos_tp"], "margin_per_001_lot_usd_approx": 40},
            "charts": charts,
            "recent_closed_trades": recent,
            "last_decisions": self.claude_history[-5:],
        }

    async def claude_decide_and_apply(self, sym: str, payload: dict, mode: str) -> dict:
        eng = self.engine
        res = await eng.ai.decide_basket(payload)
        now = time.time()
        rec = {"time_utc": payload.get("time_utc"), "status": res.get("status"),
               "market_view": res.get("market_view") or res.get("error"), "plan": res.get("plan"),
               "confidence": res.get("confidence"), "actions": res.get("actions") or [], "done": []}
        if res.get("status") == "DONE":
            rec["done"] = self.apply_claude_actions(sym, res.get("actions") or [], mode, now)
        self.claude_last = rec
        self.claude_history = (self.claude_history + [{k: rec[k] for k in ("time_utc", "plan", "done")}])[-20:]
        eng.rt.log("INFO", "basket", f"Claude {sym}: " + ("; ".join(rec["done"]) or "aștept") + f" · {str(rec['plan'])[:200]}")
        return rec

    def apply_claude_actions(self, sym: str, actions: list[dict], mode: str, now: float) -> list[str]:
        """Execute Claude's actions under the hard limits; returns what was done (or refused) per action."""
        eng, c = self.engine, self.cfg()
        done = []
        q = eng.rt.quotes.get(sym) or {}
        for a in actions[:4]:
            kind = str(a.get("type") or "").upper()
            pos = self.positions(sym)
            if kind == "CLOSE_ALL":
                if pos:
                    self._close_all(sym, pos, "CLAUDE_CLOSE_ALL", round(sum(self.net(p) for p in pos), 2), now)
                    done.append(f"închid toate ({len(pos)})")
                break
            if kind == "CLOSE":
                tk = a.get("ticket")
                p = next((x for x in pos if tk is not None and int(x["ticket"]) == int(tk)), None)
                if p is None:
                    done.append(f"CLOSE {tk} refuzat: tichet necunoscut")
                    continue
                try:
                    eng.close_position("PAPER" if p.get("source") == "PAPER" else "MT5", int(p["ticket"]), reason="CLAUDE")
                    done.append(f"închid {p['side']} {p['volume']} #{tk} ({self.net(p):+.2f})")
                except Exception as e:
                    done.append(f"CLOSE {tk} eșuat: {getattr(e, 'detail', e)}")
                continue
            if kind == "OPEN":
                side = str(a.get("side") or "").upper()
                if side not in ("BUY", "SELL") or not q.get("bid"):
                    done.append("OPEN refuzat: direcție/cotație lipsă")
                    continue
                if len(pos) >= c["claude_max_pos"]:
                    done.append(f"OPEN refuzat: maxim {c['claude_max_pos']} poziții")
                    continue
                try:
                    lot = float(a.get("lot") or 0.01)
                except (TypeError, ValueError):
                    lot = 0.01
                lot = round(min(max(lot, 0.01), c["claude_max_lot"]), 2)
                tp_usd = a.get("tp_usd")
                self.last_action[sym] = 0  # Claude's actions are not throttled by the rule-based gap
                self._open(sym, side, q, mode, now, f"Claude: {str(a.get('why') or '')[:160]}", volume=lot,
                           tp_usd=float(tp_usd) if isinstance(tp_usd, (int, float)) and tp_usd > 0 else None)
                done.append(f"deschid {side} {lot}" + (f" TP +{float(tp_usd):g}" if isinstance(tp_usd, (int, float)) and tp_usd > 0 else ""))
        return done

    def _zr_tick(self, sym: str, pos: list[dict], q: dict, mode: str, now: float, opens_paused: bool) -> None:
        """Strategy 2, zone recovery with lot multiplication (see the module docstring)."""
        c, st = self.cfg(), self.state.setdefault(sym, {})
        eng = self.engine
        if not pos:
            if st.get("n"):
                st["wait_after_bar"] = (eng.rt.closed_bars(sym, "M1", now) or [{"t": 0}])[-1]["t"]
            for k in ("n", "total", "zr", "levels_line"):
                st.pop(k, None)
            if opens_paused:
                return
            bars = eng.rt.closed_bars(sym, "M1", now)
            if not bars or st.get("decided_bar") == bars[-1]["t"] or bars[-1]["t"] <= st.get("wait_after_bar", 0):
                return
            st["decided_bar"] = bars[-1]["t"]
            side, votes = self.fast_vote(sym, now)
            txt = ", ".join(f"{k} {'↑' if v > 0 else '↓' if v < 0 else '–'}" for k, v in votes.items())
            st["analysis"] = f"termen scurt {txt} → " + (side or "M1 și M5 nu sunt de acord, aștept")
            if side:
                st["balance0"] = self.balance()
                self._open(sym, side, q, mode, now, f"Strategia 2: prima poziție {side} ({txt})")
            return
        pos = sorted(pos, key=lambda p: (p.get("opened_at_utc") or "", int(p["ticket"])))
        first, last = pos[0], pos[-1]
        p0 = float(first["price_open"])
        zone = c["zr_zone"]
        top, bot = (p0, p0 - zone) if first["side"] == "BUY" else (p0 + zone, p0)
        total = round(sum(self.net(p) for p in pos), 2)
        n = len(pos)
        limit = c["rec_cap"] if c["rec"] else c["stop"]
        target = c["pos_tp"] if n == 1 else c["zr_exit"]
        st.update(n=n, total=total, zr=True, target=target, stop_usd=limit, recovery=False,
                  side=" + ".join(f"{p['side']} {p['volume']}" for p in pos), levels_line=f"{bot:.2f} / {top:.2f}")
        if n >= 2 and total >= c["zr_exit"]:
            self._close_all(sym, pos, "ZR_TP", total, now)
            return
        if total <= -limit:
            self._close_all(sym, pos, "ZR_STOP", total, now)
            return
        if n >= 2:  # the group exit replaces the individual TPs from the second level on
            digits = int(q.get("digits") or 2)
            for p in pos:
                sign = 1 if p["side"] == "BUY" else -1
                far = round(float(p["price_open"]) + sign * c["sl_dist"], digits)
                if p.get("tp") and abs(float(p["tp"]) - far) > 0.05:
                    if not eng.executor.has_pending_for_ticket(int(p["ticket"])):
                        src = "PAPER" if p.get("source") == "PAPER" else "MT5"
                        eng.executor.submit_modify(src, int(p["ticket"]), sym, p["side"], p.get("sl"), far,
                                                   reason="ZR_GROUP_EXIT")
                    return
        if opens_paused or n >= c["zr_levels"]:
            return
        bid, ask = float(q["bid"]), float(q["ask"])
        if last["side"] == "BUY" and bid <= bot:
            nxt = "SELL"
        elif last["side"] == "SELL" and ask >= top:
            nxt = "BUY"
        else:
            return
        lot = round(float(last.get("volume") or 0.01) * c["zr_mult"], 2)
        self._open(sym, nxt, q, mode, now, f"Strategia 2: nivel {n + 1}/{c['zr_levels']} {nxt} {lot} (zona "
                                           f"{bot:.2f}–{top:.2f} traversată, grup {total:+.2f})",
                   tp_far=True, volume=lot)

    def _open(self, sym: str, side: str, q: dict, mode: str, now: float, why: str, tp_far: bool = False,
              volume: float | None = None, tp_usd: float | None = None) -> None:
        eng, c = self.engine, self.cfg()
        self.last_action[sym] = now
        sign = 1 if side == "BUY" else -1
        entry = float(q["ask"]) if side == "BUY" else float(q["bid"])
        digits = int(q.get("digits") or 2)
        tp_dist = c["sl_dist"] if tp_far else (self.tp_distance(q) or c["sl_dist"])
        if tp_usd is not None:  # Claude's TP in USD of profit for this position's lot
            contract = float(q.get("contract_size") or 0)
            per = contract * float(volume or 0.01) if contract > 0 else 0.0
            tp_dist = tp_usd / per if per > 0 else tp_dist
        sl, tp = round(entry - sign * c["sl_dist"], digits), round(entry + sign * tp_dist, digits)
        rc = eng.risk_check(sym, side, entry, sl, tp, mode=mode)
        volume = volume or self.lot(q) or rc["volume"]
        it = eng.executor.submit_open(mode, sym, side, volume, sl, tp, q, rc)
        eng.rt.log("INFO", "basket", f"Coș {sym}: {why} → {side} {volume} ({it['state']})")
        if it["state"] == "REJECTED_RISK":
            st = self.state.setdefault(sym, {})
            st["filling"] = False
            st["open_pause_until"] = now + REJECT_PAUSE_S
            eng.rt.log("WARN", "basket", f"Coș {sym}: Risk Engine a refuzat poziția (de obicei marjă liberă) — coșul "
                                         f"rămâne cu pozițiile existente, reîncerc peste {REJECT_PAUSE_S:.0f} s")

    def mtf_vote(self, sym: str, now: float) -> tuple[str | None, dict]:
        """Trend of M1/M5/M15/H1 (EMA20 vs EMA50 on closed bars): (side when >= min_votes agree, {tf: +1/-1/0})."""
        votes = {}
        for tf in ("M1", "M5", "M15", "H1"):
            closes = [b["c"] for b in self.engine.rt.closed_bars(sym, tf, now)[-200:]]
            if len(closes) < 60:
                votes[tf] = 0
                continue
            e20, e50 = ind.last(ind.ema(closes, 20)), ind.last(ind.ema(closes, 50))
            votes[tf] = 0 if e20 is None or e50 is None else (1 if e20 > e50 else -1 if e20 < e50 else 0)
        need = self.cfg()["min_votes"]
        ups, downs = list(votes.values()).count(1), list(votes.values()).count(-1)
        side = "BUY" if ups >= need and ups > downs else "SELL" if downs >= need and downs > ups else None
        return side, votes

    def fast_vote(self, sym: str, now: float) -> tuple[str | None, dict]:
        """Short-term direction for the recovery hedge: M1 and M5 (EMA9 vs EMA21) must both point the same way."""
        votes = {}
        for tf in ("M1", "M5"):
            closes = [b["c"] for b in self.engine.rt.closed_bars(sym, tf, now)[-120:]]
            if len(closes) < 30:
                votes[tf] = 0
                continue
            f, s_ = ind.last(ind.ema(closes, 9)), ind.last(ind.ema(closes, 21))
            votes[tf] = 0 if f is None or s_ is None else (1 if f > s_ else -1 if f < s_ else 0)
        vals = list(votes.values())
        return ("BUY" if vals.count(1) == len(vals) else "SELL" if vals.count(-1) == len(vals) else None), votes

    def m1_vote(self, sym: str, now: float) -> tuple[str | None, dict]:
        """Fastest read of the turn for the recovery hedge: M1 EMA9 vs EMA21 (M5 shown, does not decide)."""
        side, votes = self.fast_vote(sym, now)
        m1 = votes.get("M1", 0)
        return ("BUY" if m1 > 0 else "SELL" if m1 < 0 else None), votes

    def _mtf_step(self, sym: str, pos: list[dict], q: dict, mode: str, now: float, opens_paused: bool) -> None:
        """Entry mode "mtf": one decision per closed M1 bar, one position per decision, re-analysis after a close."""
        c, st = self.cfg(), self.state.setdefault(sym, {})
        bars = self.engine.rt.closed_bars(sym, "M1", now)
        if not bars:
            return
        bar_t = bars[-1]["t"]
        n = len(pos)
        if n < st.get("last_n", 0):
            st["wait_after_bar"] = bar_t  # a position closed: wait for a new closed M1 bar, then re-analyse
            self.engine.rt.log("INFO", "basket", f"Coș {sym}: o poziție s-a închis — reanalizez la următoarea bară M1")
        st["last_n"] = n
        if opens_paused or n >= c["max"] or st.get("decided_bar") == bar_t or bar_t <= st.get("wait_after_bar", 0):
            return
        st["decided_bar"] = bar_t
        arrow = lambda v: '↑' if v > 0 else '↓' if v < 0 else '–'  # noqa: E731
        if c["vote"] == "short":
            side, votes = self.fast_vote(sym, now)
            _, ctx_votes = self.mtf_vote(sym, now)
            txt = ", ".join(f"{k} {arrow(v)}" for k, v in votes.items())
            ctx_txt = ", ".join(f"{k} {arrow(ctx_votes[k])}" for k in ("M15", "H1"))
            st["analysis"] = (f"termen scurt {txt} → " + (side or "M1 și M5 nu sunt de acord, aștept")
                              + f" · context (nu decide): {ctx_txt}")
        else:
            side, votes = self.mtf_vote(sym, now)
            txt = ", ".join(f"{k} {arrow(v)}" for k, v in votes.items())
            st["analysis"] = f"{txt} → " + (side or f"fără acord (minim {c['min_votes']} din 4)")
        if side is None:
            return
        if pos and pos[0]["side"] != side:
            st["analysis"] += f" · contra coșului {pos[0]['side']}: aștept"
            return
        if not pos:
            st["balance0"] = self.balance()
        label = "termen scurt M1/M5" if c["vote"] == "short" else "analiză M1/M5/M15/H1"
        self._open(sym, side, q, mode, now, f"{label} ({txt}) → {side} {n + 1}/{c['max']}")

    def tp_distance(self, q: dict) -> float | None:
        """Price distance of BASKET_POSITION_TP_USD per 0.01 lot (contract size first, tick value otherwise)."""
        c = self.cfg()
        if c["pos_tp"] <= 0:
            return None
        contract = float(q.get("contract_size") or 0)
        ts, tv = float(q.get("tick_size") or q.get("point") or 0), float(q.get("tick_value") or 0)
        per_001 = contract * 0.01 if contract > 0 else (tv / ts * 0.01 if ts > 0 and tv > 0 else 0.0)
        return c["pos_tp"] / per_001 if per_001 > 0 else None

    def _fix_tp(self, sym: str, pos: list[dict], q: dict) -> bool:
        """Re-anchor each position's broker TP (BASKET_POSITION_TP_USD) and SL (BASKET_SL_DISTANCE) on its real fill
        price: the order was priced on the quote and slippage moved the fill, and a changed BASKET_SL_DISTANCE also
        reaches positions opened before it. One MODIFY per position per round; True when one was sent."""
        dist = self.tp_distance(q)
        sl_dist = self.cfg()["sl_dist"]
        eng = self.engine
        digits = int(q.get("digits") or 2)
        point = float(q.get("point") or 0.01)
        gap = (float(q.get("stops_level") or 0) + 2) * point
        for p in pos:
            if not p.get("price_open") or not p.get("tp"):
                continue
            sign = 1 if p["side"] == "BUY" else -1
            open_px = float(p["price_open"])
            cur = float(q["bid"]) if sign == 1 else float(q["ask"])
            tp, sl = float(p["tp"]), float(p.get("sl") or 0)
            want_tp = round(open_px + sign * dist, digits) if dist else tp
            want_sl = round(open_px - sign * sl_dist, digits)
            fix_tp = abs(tp - want_tp) > 2 * point and (want_tp - cur) * sign > gap
            fix_sl = sl_dist > 0 and abs(sl - want_sl) > 2 * point and (cur - want_sl) * sign > gap
            if not fix_tp and not fix_sl:
                continue
            ticket = int(p["ticket"])
            if eng.executor.has_pending_for_ticket(ticket):
                return True
            src = "PAPER" if p.get("source") == "PAPER" else "MT5"
            eng.executor.submit_modify(src, ticket, sym, p["side"], want_sl if fix_sl else (sl or None),
                                       want_tp if fix_tp else tp, reason="BASKET_TP_FIX")
            return True
        return False

    def on_open_failed(self, it: dict) -> None:
        """The broker refused a basket OPEN (e.g. 10019 NO_MONEY): stop filling, pause opens like a risk rejection."""
        if not self.enabled or not it.get("symbol"):
            return
        st = self.state.setdefault(it["symbol"], {})
        if time.time() < st.get("open_pause_until", 0):
            return
        st["filling"] = False
        st["open_pause_until"] = time.time() + REJECT_PAUSE_S
        res = it.get("result") or {}
        self.engine.rt.log("WARN", "basket", f"Coș {it['symbol']}: brokerul a refuzat poziția "
                                             f"({res.get('retcode_desc') or res.get('retcode') or it['state']}) — "
                                             f"coșul rămâne cu pozițiile existente, reîncerc peste {REJECT_PAUSE_S:.0f} s")

    def balance(self) -> float | None:
        _, acc = self.engine.risk_account(self.engine.mode())
        try:
            return float((acc or {}).get("balance") or (acc or {}).get("equity") or 0) or None
        except (TypeError, ValueError):
            return None

    def lot(self, q: dict) -> float | None:
        """Lot scaled with the balance (BASKET_LOT_PER_USD per 0.01), None = use the Risk Engine's fixed lot."""
        c = self.cfg()
        bal = self.balance()
        if c["lot_per"] <= 0 or not bal:
            return None
        step = float(q.get("volume_step") or 0.01)
        vmin = float(q.get("volume_min") or 0.01)
        lot = max(vmin, math.floor(bal / c["lot_per"]) * 0.01)
        cap = float(self.engine.rt.risk.get("max_volume_lots") or 0)
        if cap:
            lot = min(lot, cap)
        return round(math.floor(lot / step + 1e-9) * step, 2)

    def stop_usd(self, st: dict) -> float:
        c = self.cfg()
        bal = st.get("balance0") or self.balance()
        if c["stop_pct"] > 0 and bal:
            return round(bal * c["stop_pct"] / 100.0, 2)
        return c["stop"]

    def _close_all(self, sym: str, pos: list[dict], reason: str, total: float, now: float) -> None:
        eng = self.engine
        self.last_action[sym] = now
        eng.rt.log("INFO" if reason == "BASKET_TP" else "WARN", "basket",
                   f"Coș {sym}: închid {len(pos)} poziții, total {total:+.2f} USD ({reason})")
        for p in pos:
            src = "PAPER" if p.get("source") == "PAPER" else "MT5"
            try:
                eng.close_position(src, int(p["ticket"]), reason=reason)
            except Exception as e:
                eng.rt.log("WARN", "basket", f"Închidere ticket {p['ticket']} eșuată: {getattr(e, 'detail', e)!r}")

    def status(self, symbol: str) -> dict | None:
        if not self.enabled:
            return None
        return {**self.cfg(), **(self.state.get(symbol) or {})}
