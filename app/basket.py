"""Claude basket manager on gold (owner's choice): once per closed M1 bar Claude (app.ai_claude.decide_basket) gets the
whole state - account, positions, M1/M5/M15/H1 charts, price-action patterns, its last decisions - and answers 0-4
actions: OPEN / ARM (an entry executed in code at the price) / CLOSE / CLOSE_ALL / PROTECT / TARGET.
Scalp exits (owner, 2026-11-03): every new entry that is not a hedge carries a fixed broker TP of BASKET_SCALP_TP_PRICE
and SL of BASKET_SCALP_SL_PRICE (price distance = USD per 0.01 lot); Claude's job is the direction.
Hard rules enforced in code, whatever Claude answers: the basket loss limit closes everything (CLAUDE_LIMIT); CLAUDE_MAX_LOT
per position, BASKET_MAX_POSITIONS_CLAUDE positions and the broker's free margin; a hedge (OPEN against a losing side) is
at least the losing lot x BASKET_ZR_MULT, carries no TP and the hedged group only closes whole (at +BASKET_RECOVERY_EXIT_USD
in code, or CLOSE_ALL); a CLOSE at a loss on a position younger than BASKET_CLAUDE_LOSS_CLOSE_MIN_MINUTES is refused;
no re-entry on a side inside the zone where its last BASKET_ZONE_BLOCK_LOSSES trades lost; abnormal spread, rollover and
the Risk Engine (bridge, armed execution, margin) block entries. One call in flight at a time; routine bars go to the
cheaper model (BASKET_CLAUDE_FLAT_MODEL), entries / exits / investigations to CLAUDE_MODEL."""
from __future__ import annotations

import asyncio

from . import indicators as ind
from . import pattern_context
from .config import spread_cap
from .risk import rollover_blocker

import math
import time

ACTION_GAP_S = 2.0  # at most one open/close round per symbol every few seconds (orders in flight settle first)


class BasketTrader:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.last_action: dict[str, float] = {}
        self.state: dict[str, dict] = {}
        self.claude_last: dict | None = None  # last Claude decision (shown on the dashboard)
        self.claude_history: list[dict] = []  # last decisions, fed back to Claude
        self._claude_task = None
        self.group_lot: dict[str, float] = {}  # symbol -> lot of the open group's first position (scales cfg USD)
        self.armed: dict[str, list[dict]] = {}  # symbol -> Claude's armed entries (ARM), fired in code at the price
        self.breakout: dict[str, dict] = {}  # symbol -> {"side", "level", "since"} of the armed BREAKOUT that opened the group

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.engine.settings, "BASKET_ENABLED", False))

    def cfg(self) -> dict:
        s = self.engine.settings
        out = {"lot_per": float(s.BASKET_LOT_PER_USD),
               "rec_trig": float(s.BASKET_RECOVERY_TRIGGER_USD), "rec_exit": float(s.BASKET_RECOVERY_EXIT_USD),
               "limit": float(s.BASKET_MAX_LOSS_USD),
               "sl_dist": float(s.BASKET_SL_DISTANCE), "zr_mult": float(s.BASKET_ZR_MULT),
               "claude_max_lot": float(s.CLAUDE_MAX_LOT), "claude_max_pos": int(s.BASKET_MAX_POSITIONS_CLAUDE),
               "abn_mult": float(s.BASKET_ABNORMAL_SPREAD_MULT), "roll_min": int(s.BASKET_ROLLOVER_BLACKOUT_MINUTES),
               "loss_close_min": float(s.BASKET_CLAUDE_LOSS_CLOSE_MIN_MINUTES),
               "scalp_tp": float(s.BASKET_SCALP_TP_PRICE), "scalp_sl": float(s.BASKET_SCALP_SL_PRICE),
               "m5_gate": bool(s.BASKET_M5_GATE), "impulse_atr": float(s.BASKET_PULLBACK_IMPULSE_ATR),
               "max_run": int(s.BASKET_MAX_RUN_BARS), "sure_conf": float(s.BASKET_SURE_MIN_CONFIDENCE),
               "time_stop": int(s.BASKET_SCALP_TIME_STOP_MINUTES), "max_stretch": float(s.BASKET_MAX_STRETCH_EMA50_ATR),
               "claude_hedge": bool(s.BASKET_CLAUDE_HEDGE), "needs_pullback": bool(s.BASKET_ENTRY_NEEDS_PULLBACK),
               "max_overshoot": float(s.BASKET_PULLBACK_MAX_OVERSHOOT_ATR),
               "no_entry_hours": {int(h) for h in str(s.BASKET_NO_ENTRY_UTC_HOURS or "").split(",") if h.strip().isdigit()},
               "reentry_atr": float(s.BASKET_REENTRY_MIN_ATR), "reentry_min": int(s.BASKET_REENTRY_MINUTES),
               "max_atr": float(s.BASKET_MAX_ATR_M1),
               "tp_atr": float(s.BASKET_SCALP_TP_ATR_MULT), "sl_atr": float(s.BASKET_SCALP_SL_ATR_MULT),
               "armed_skip": bool(s.BASKET_CLAUDE_ARMED_SKIP), "armed_move": float(s.BASKET_CLAUDE_ARMED_MOVE_ATR),
               "open_every": int(s.BASKET_CLAUDE_OPEN_EVERY_BARS),
               "flat_every": int(s.BASKET_CLAUDE_FLAT_WAIT_EVERY_BARS), "flat_move": float(s.BASKET_CLAUDE_FLAT_WAIT_MOVE_ATR),
               "inv_actionable": bool(s.BASKET_INVESTIGATE_ONLY_WHEN_ACTIONABLE),
               "margin_frac": float(s.BASKET_MARGIN_USE_FRAC), "risk_pct": float(s.BASKET_MAX_RISK_PCT_PER_ENTRY),
               "hedge_at": float(s.BASKET_HEDGE_AT_PRICE), "hedge_on_close": bool(s.BASKET_HEDGE_ON_CLOSE),
               "reopen_min": float(s.BASKET_REOPEN_BLACKOUT_MINUTES), "reopen_gap": float(s.BASKET_REOPEN_GAP_MINUTES)}
        limit = self.loss_limit_override()
        if limit:
            # owner's value from the Positions page: the group closes at -limit; the broker SL of a hedge is never
            # tighter than it, so a hedged leg cannot stop out first
            out.update(limit=limit, sl_dist=max(out["sl_dist"], limit))
        # start lot from the Positions page (owner's rule 2026-11-02): every USD amount above is meant for 0.01 lot and
        # grows with the start lot (loss limit, investigation step, group exit); price distances (SL, TP) stay as they
        # are - a price move is worth more USD on a bigger lot by itself
        start = self.start_lot()
        # Claude sizes entries itself, so the USD amounts follow the lot of the group's FIRST position when it is bigger
        # than the start lot (the dashboard limit is "USD at 0.01 lot")
        base = max(start, max(self.group_lot.values(), default=0.0))
        k = base / 0.01 if base else 1.0
        out.update(start_lot=start, lot_scale=k)
        if base:
            for key in ("limit", "rec_trig", "rec_exit"):
                out[key] = round(out[key] * k, 2)
            # the hedge chain 1 -> 2 -> 4 -> 8 x the start lot must fit under Claude's lot cap
            cap = float(self.engine.rt.risk.get("max_volume_lots") or 0) or float(s.MAX_VOLUME_LOTS)
            out["claude_max_lot"] = round(min(max(out["claude_max_lot"], base * 8), cap), 2)
        return out

    def start_lot(self) -> float:
        """Start lot saved from the Positions page (0 = the lot follows the balance, BASKET_LOT_PER_USD)."""
        try:
            return round(float((self.engine.db.get_setting("basket") or {}).get("start_lot") or 0), 2)
        except (TypeError, ValueError, AttributeError):
            return 0.0

    def set_start_lot(self, lot) -> dict:
        try:
            lot = round(float(lot), 2)
        except (TypeError, ValueError):
            raise ValueError("lot invalid")
        cap = float(self.engine.rt.risk.get("max_volume_lots") or 0) or float(self.engine.settings.MAX_VOLUME_LOTS)
        if lot != 0 and not 0.01 <= lot <= cap:
            raise ValueError(f"lotul de start trebuie să fie între 0.01 și {cap:g} (0 = după sold)")
        old = self.start_lot()
        cur = dict(self.engine.db.get_setting("basket") or {})
        cur["start_lot"] = lot
        self.engine.db.set_setting("basket", cur)
        self.engine.rt.log("WARN", "control", f"Lotul de start schimbat din dashboard: {old or 'după sold'} → "
                                              f"{lot or 'după sold'} (sumele în USD ale coșului x{(lot or 0.01) / 0.01:g})")
        return self.settings_public()

    def running(self) -> bool:
        tr = self.engine.rt.trading
        return bool(tr.get("execution_enabled")) and not tr.get("entries_paused")

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
        old = self.cfg()["limit"]
        cur = dict(self.engine.db.get_setting("basket") or {})
        cur["max_loss_usd"] = usd
        self.engine.db.set_setting("basket", cur)
        self.engine.rt.log("WARN", "control", f"Limita coșului schimbată din dashboard: −{old:g} → −{usd:g} USD")
        return self.settings_public()

    def settings_public(self) -> dict:
        c = self.cfg()
        return {"enabled": self.enabled, "max_loss_usd": c["limit"],
                "source": "dashboard" if self.loss_limit_override() else "profil",
                "running": self.running(), "claude_max_lot": c["claude_max_lot"], "claude_last": self.claude_last,
                "armed": {k: [{f: x[f] for f in ("side", "trigger", "price", "expires_utc", "why")} for x in v]
                          for k, v in self.armed.items() if v},
                "start_lot": c["start_lot"], "lot_scale": c["lot_scale"],
                "max_loss_per_001_usd": round(c["limit"] / c["lot_scale"], 2),
                "scalp": {"tp_price": c["scalp_tp"], "sl_price": c["scalp_sl"]}}

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
        eng = self.engine
        if now - self.last_action.get(sym, 0) < ACTION_GAP_S or eng.executor.has_pending_open(sym):
            return
        q = eng.rt.quotes.get(sym) or {}
        if not q.get("bid") or not q.get("ask"):
            return
        pos = self.positions(sym)
        self._set_group_lot(sym, pos)
        st = self.state.setdefault(sym, {})
        self._track_reopen(sym, q, st, now)
        abn = self.abnormal_spread(sym, q)
        if abn:
            # P/L on such a quote is not real (30.10, weekly close: SELL shown at -42 on a 58 USD spread): no open,
            # no hedge, no close (loss limit / group exit) until the spread is back to normal
            if not st.get("abnormal"):
                self.engine.rt.log("WARN", "basket", f"Coș {sym}: {abn} — nu deschid, nu acopăr și nu închid până la "
                                                     "normalizarea spread-ului")
            st["abnormal"] = abn
            st["analysis"] = abn
            return
        st.pop("abnormal", None)
        if pos and any(eng.executor.has_pending_for_ticket(int(p["ticket"]), kinds=("CLOSE",)) for p in pos):
            return  # basket closing: wait until the broker confirms
        self._claude_tick(sym, pos, q, mode, now)

    def _track_reopen(self, sym: str, q: dict, st: dict, now: float) -> None:
        """Remember when ticks resumed after a gap of BASKET_REOPEN_GAP_MINUTES (the broker's daily break)."""
        c = self.cfg()
        try:
            t = float(q.get("time_msc") or 0) / 1000 or now
        except (TypeError, ValueError):
            t = now
        prev = st.get("last_tick_t")
        if prev is not None and t - prev > c["reopen_gap"] * 60 and c["reopen_min"] > 0:
            st["reopen_at"] = now
            self.engine.rt.log("WARN", "basket", f"Coș {sym}: piața s-a redeschis după {(t - prev) / 60:.0f} min fără cotații — "
                                                 f"fără intrări noi {c['reopen_min']:g} min (lichiditate subțire)")
        if t != prev:
            st["last_tick_t"] = t

    def reopen_note(self, sym: str, now: float) -> str | None:
        st = self.state.get(sym) or {}
        at = st.get("reopen_at")
        if at is None:
            return None
        left = self.cfg()["reopen_min"] * 60 - (now - at)
        if left <= 0:
            return None
        return f"piața s-a redeschis acum {(now - at) / 60:.0f} min — fără intrări noi încă {left / 60:.0f} min"

    def _set_group_lot(self, sym: str, pos: list[dict]) -> None:
        if not pos:
            self.group_lot.pop(sym, None)
            return
        first = min(pos, key=lambda p: (str(p.get("opened_at_utc") or ""), int(p.get("ticket") or 0)))
        self.group_lot[sym] = round(float(first.get("volume") or 0.01), 2)

    # ---- Claude manager ---------------------------------------------------------------------------------------------
    def _claude_tick(self, sym: str, pos: list[dict], q: dict, mode: str, now: float) -> None:
        """Hard limit first (basket loss limit closes everything, in code), then one Claude decision per closed M1 bar."""
        c, st = self.cfg(), self.state.setdefault(sym, {})
        eng = self.engine
        total = round(sum(self.net(p) for p in pos), 2)
        limit = c["limit"]
        st.update(n=len(pos), total=total, claude=True, stop_usd=limit, side=" + ".join(
            f"{p['side']} {p['volume']}" for p in pos))
        if pos and total <= -limit:
            self._close_all(sym, pos, "CLAUDE_LIMIT", total, now)
            return
        hedged = len({p["side"] for p in pos}) > 1
        if hedged and total >= c["rec_exit"]:
            # owner's rule 2026-10-30: a hedged group closes all at once the moment its sum is back to >= 0 (in code,
            # without waiting for Claude's next bar)
            self._close_all(sym, pos, "BASKET_RECOVERED", total, now)
            return
        if hedged and self._strip_group_tps(sym, pos, q):
            return  # no leg of a hedged group may close alone on its broker TP
        if not hedged and self._failed_breakout(sym, pos, total, now):
            return
        if not hedged and self._time_stop(sym, pos, total, now):
            return
        if not hedged and self._auto_hedge(sym, pos, q, mode, now):
            return
        if self._fire_armed(sym, pos, q, mode, now):
            return
        # investigation: every time the group sinks another BASKET_RECOVERY_TRIGGER_USD, Claude is called at once
        # (not at the next M1 bar) with the recovery brief
        # a hedged group carries (1 + mult) x the lot, so its P/L swings that much faster: the investigation step scales
        # with it (21:19-21:28: five paid calls while a 0.05-lot group wobbled between -4 and -6.6)
        step = c["rec_trig"] * ((1 + c["zr_mult"]) if hedged else 1)
        inv_n = int(-total // step) if pos and total < 0 and step > 0 else 0
        prev_n = int(st.get("inv_n") or 0)
        investigate = inv_n > prev_n
        if investigate and c["inv_actionable"] and not hedged and not c["claude_hedge"] and pos:
            # nothing Claude answers could be executed yet: CLOSE at a loss is refused on a young position and hedging
            # is off - the paid call would only say "hold" (04.11: 3-4 such calls per losing trade)
            from datetime import datetime
            try:
                age = min((now - datetime.fromisoformat(str(p["opened_at_utc"]).replace("Z", "+00:00")).timestamp()) / 60
                          for p in pos)
            except (KeyError, TypeError, ValueError):
                age = None
            if age is not None and age < c["loss_close_min"]:
                investigate = False
                st["inv_n"] = inv_n  # counted as seen: the next step, not this one, may re-trigger once it is actionable
        # the level is re-armed only after the group won back half a step: on 2026-11-02 the total wobbling around -4.00
        # re-triggered the investigation 6 times in a minute (6 paid calls, same answer)
        if not pos or (inv_n < prev_n and -total < (prev_n - 0.5) * step):
            st["inv_n"] = inv_n
        if not eng.ai.enabled or not eng.ai.key_set:
            st["analysis"] = "Claude nu este disponibil (CLAUDE_ENABLED / ANTHROPIC_API_KEY)"
            return
        bars = eng.rt.closed_bars(sym, "M1", now)
        if not bars or (st.get("claude_bar") == bars[-1]["t"] and not investigate):
            return
        if not investigate and (skip := self._skip_bar(sym, pos, hedged, q, st, bars[-1]["t"])):
            st["claude_bar"] = bars[-1]["t"]
            st["analysis"] = skip
            return
        if self._claude_task is not None and not self._claude_task.done():
            return  # previous decision still thinking
        st["claude_bar"] = bars[-1]["t"]
        if investigate:
            st["inv_n"] = inv_n
            eng.rt.log("WARN", "basket", f"Coș {sym}: grupul a ajuns la {total:+.2f} USD — trimit la Claude pentru "
                                         "investigație (recuperare)")
        payload = self._claude_payload(sym, pos, q, now)
        if investigate:
            payload["investigation_now"] = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        # API cost (owner, 2026-11-03: 8.79 USD of API against +7.68 USD of trading since the morning restart): routine bars
        # go to the cheaper model, the important decisions to CLAUDE_MODEL (see _route_model)
        model, st["model_route"] = self._route_model(sym, pos, hedged, investigate, now)
        coro = self.claude_decide_and_apply(sym, payload, mode, model)
        if loop is None:
            asyncio.run(coro)
        else:
            self._claude_task = loop.create_task(coro)

    def _skip_bar(self, sym: str, pos: list[dict], hedged: bool, q: dict, st: dict, bar_t: int) -> str | None:
        """Why this M1 bar does not call Claude (API cost), or None: a live armed entry the price is still close to (the
        code fills it itself), or an open position on its fixed SL/TP between two check-ins (first bar, then every
        BASKET_CLAUDE_OPEN_EVERY_BARS)."""
        c = self.cfg()
        if not pos:
            st["open_bars"] = 0
            roll = (self.rollover_note(bar_t + 60) or self.reopen_note(sym, bar_t + 60) or self.hour_block_note(bar_t + 60)
                    or self.atr_block_note(sym, bar_t + 60))
            if roll:
                # 03.11 21:44-22:20: 28 Opus calls that all answered "wait for the rollover" - the Risk Engine refuses
                # every entry in the blackout anyway
                self.armed[sym] = []
                st["waited_px"] = None  # after the blackout the first bar asks Claude again
                return f"{roll} — Claude nu e chemat cât ține blocajul (nicio intrare nu ar fi acceptată)"
            arms = [x for x in self.armed.get(sym, []) if x["expires"] > bar_t]
            atr = self.atr_m1(sym, bar_t + 60) or 0.0
            px = float(q.get("bid") or 0)
            if c["armed_skip"] and arms and atr and all(abs(float(x["price"]) - px) <= c["armed_move"] * atr for x in arms):
                return ("intrare armată activă: " + "; ".join(f"{x['side']} la {x['price']:g} până la {x['expires_utc']}"
                                                              for x in arms) + " — codul o execută singur, Claude nu e rechemat")
            if c["flat_every"] <= 1 or not st.get("waited_px"):
                return None
            # the last decision was "wait" (or an arm the price has run away from, 16:40-17:00 on 04.11: 12 Opus calls in
            # 20 minutes re-arming under a rally): Claude is asked again on the M5 close or once the price has moved
            # FLAT_WAIT_MOVE_ATR x ATR from where it decided - a bar that changed nothing costs a call for nothing
            m5_close = ((bar_t + 60) // 60) % c["flat_every"] == 0
            moved = atr and abs(px - float(st["waited_px"])) >= c["flat_move"] * atr
            if m5_close or moved:
                return None
            left = c["flat_every"] - ((bar_t + 60) // 60) % c["flat_every"]
            return (f"Claude a spus „aștept” la {float(st['waited_px']):g}: e rechemat la închiderea M5 (peste {left} bare) "
                    f"sau dacă prețul se mișcă {c['flat_move']:g}×ATR")
        if hedged or c["open_every"] <= 1:
            return None
        n = int(st.get("open_bars") or 0) + 1
        st["open_bars"] = n
        if n == 1 or n % c["open_every"] == 0:
            return None
        return f"poziție deschisă cu SL/TP fixe — următoarea decizie Claude peste {c['open_every'] - n % c['open_every']} bare"

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
                              "tp": p.get("tp"), "sl": p.get("sl"), "minutes_open": mins,
                              "profit": self._profit_brief(sym, p, q, now)})
        recent = eng.db.query("SELECT side, volume, price_open, price_close, profit, close_reason, closed_at_utc FROM trades "
                              "WHERE symbol=? ORDER BY closed_at_utc DESC LIMIT 10", (sym,))
        try:
            charts = eng._scalp_context(sym)
        except Exception:
            charts = {}
        return {
            "time_utc": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": sym,
            "account": {k: (acc or {}).get(k) for k in ("balance", "equity", "margin", "margin_free", "leverage")},
            "quote": {"bid": q.get("bid"), "ask": q.get("ask"), "spread_usd": round(float(q["ask"]) - float(q["bid"]), 2)},
            "positions": positions,
            "group_total_usd": round(sum(x["pl_usd"] for x in positions), 2),
            "flat_for_minutes": self._flat_minutes(sym, pos, now),
            "constraints": {"max_lot": self._entry_max_lot(c, q), "max_positions": c["claude_max_pos"],
                            "lot_tiers": self._lot_tiers(c, q),
                            "big_lot_needs": {"confidence_min": c["sure_conf"], "m5_and_m15_with_entry": True,
                                              "this_utc_hour_7d_not_negative": True},
                            "basket_loss_limit_usd": c["limit"],
                            "hedge_fits_margin": self.hedge_fits(q, pos) if pos else
                            self.hedge_fits(q, [], c["start_lot"] or 0.01),
                            "margin_per_001_lot_usd_approx": 40,
                            "group_exit_usd": c["rec_exit"], "hedge_lot_mult": c["zr_mult"],
                            "auto_hedge_at_price": c["hedge_at"] or None,
                            "investigation_every_usd": c["rec_trig"],
                            "loss_close_min_minutes": c["loss_close_min"], "m5_gate": c["m5_gate"],
                            "pullback_waits_on_impulse_atr": c["impulse_atr"],
                            "no_entry_after_run_of_m1_bars": c["max_run"] or None,
                            "no_entry_beyond_ema50_m1_atr": c["max_stretch"] or None,
                            "entry_needs_counter_m1_bar": c["needs_pullback"],
                            "pullback_dropped_past_level_atr": c["max_overshoot"] or None,
                            "reentry_needs_new_level": {"min_atr_from_last_entry": c["reentry_atr"],
                                                        "within_minutes": c["reentry_min"]} if c["reentry_atr"] > 0 else None,
                            "time_stop_minutes": c["time_stop"] or None, "claude_may_hedge": c["claude_hedge"],
                            "news_blackout_minutes": int(
                                eng.rt.risk.get("news_blackout_minutes", 0) or 0),
                            "scalp": {"on": True, "tp_price": c["scalp_tp"], "sl_price": c["scalp_sl"],
                                      "tp_atr_mult": c["tp_atr"] or None, "sl_atr_mult": c["sl_atr"] or None,
                                      "tp_now": round(self.scalp_exits(sym, now)[0], 2),
                                      "sl_now": round(self.scalp_exits(sym, now)[1], 2)},
                            "start_lot": c["start_lot"] or None},
            "charts": charts,
            "session_now": self._session_now(now),
            "trend_now": self._trend_now(sym, q, now),
            "bot_stats": self._bot_stats(sym, now),
            "armed_entries": [{k: x[k] for k in ("side", "trigger", "price", "expires_utc", "why")}
                              for x in self.armed.get(sym, [])],
            "price_action": pattern_context.build(eng.rt, sym, now),
            "recovery": self._recovery_brief(sym, pos, q, now),
            "rollover_or_close": self.rollover_note(now),
            "no_entry_utc_hours": sorted(c["no_entry_hours"]) or None,
            "no_entry_above_atr_m1_usd": c["max_atr"] or None,
            "recent_closed_trades": recent,
            "losing_streak": self._losing_streak(recent),
            "last_decisions": self.claude_history[-5:],
        }

    SESSIONS = ((0, 7, "Asia", "liniște, range de 1–2 USD, mișcări mici; botul a câștigat aici 19 din 22 cu ținte mici"),
                (7, 9, "Londra deschidere", "prima direcție a zilei, des falsă la 08:00; așteaptă retestul"),
                (9, 12, "Londra înainte de NY", "cea mai proastă fereastră a botului (7 din 18 pe 02–03.11): doar confluență clară"),
                (12, 16, "New York dimineață", "știrile US la 12:30 / 14:00 / 15:00, cele mai mari mișcări; evită 5 min în jur"),
                (16, 21.5, "New York după-amiază", "volum în scădere, range-uri înguste de 4–7 USD care se întorc des; pe 03.11 "
                                                   "toate cele 3 pierderi au fost aici, cu intrări la marginea range-ului"),
                # the broker's day ends at 17:00 New York = 22:00 UTC in winter / 21:00 in summer (risk.nearest_rollover_ts
                # is DST-safe; this label is only the lesson text)
                (21.5, 22.5, "rollover", "închiderea zilei NY (17:00 New York): spread uriaș, nimic"),
                (22.5, 24, "Asia deschidere", "subțire, gap-uri; prudență"))

    def _session_now(self, now: float) -> dict:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        d = datetime.fromtimestamp(now, timezone.utc)
        h = d.hour + d.minute / 60
        name, note = next(((n, t) for a, b, n, t in self.SESSIONS if a <= h < b), ("?", ""))
        local = d.astimezone(ZoneInfo(self.engine.settings.DEFAULT_TIMEZONE)).strftime("%H:%M")
        return {"utc": d.strftime("%H:%M"), "chisinau": local, "weekday": d.strftime("%a"), "session": name, "lesson": note}

    def _trend_now(self, sym: str, q: dict, now: float) -> dict:
        """Direction read in code so Claude never has to infer it: EMA9/21 on M1 and M5, EMA20/50 on M1..H1, the M1
        20-bar range and where the price sits in it, last 3 M1 bars' direction."""
        fast_side, fast = self.fast_vote(sym, now)
        mtf_side, mtf = self.mtf_vote(sym, now)
        arrow = lambda v: "sus" if v > 0 else "jos" if v < 0 else "neutru"  # noqa: E731
        bars = self.engine.rt.closed_bars(sym, "M1", now)[-20:]
        out = {"m1_ema9_21": arrow(fast.get("M1", 0)), "m5_ema9_21": arrow(fast.get("M5", 0)),
               "m5_ema20_50": arrow(mtf.get("M5", 0)), "m15_ema20_50": arrow(mtf.get("M15", 0)),
               "h1_ema20_50": arrow(mtf.get("H1", 0)),
               "short_term_consensus": fast_side, "mtf_consensus": mtf_side}
        if bars and q.get("bid"):
            hi, lo = max(b["h"] for b in bars), min(b["l"] for b in bars)
            px = float(q["bid"])
            out.update(range20_usd=round(hi - lo, 2), range20_high=round(hi, 3), range20_low=round(lo, 3),
                       price_in_range_pct=round((px - lo) / (hi - lo) * 100) if hi > lo else 50,
                       last3_m1=[("sus" if b["c"] > b["o"] else "jos" if b["c"] < b["o"] else "=") for b in bars[-3:]],
                       range_regime="îngust" if hi - lo < 5 else "normal" if hi - lo < 10 else "larg")
        return out

    def _bot_stats(self, sym: str, now: float) -> dict:
        """The bot's own record: this UTC hour over the last 7 days, today, and the last 10 closes - so Claude sees
        when its entries work and when they do not."""
        from datetime import datetime, timezone
        d = datetime.fromtimestamp(now, timezone.utc)
        since = datetime.fromtimestamp(now - 7 * 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self.engine.db.query("SELECT profit, closed_at_utc FROM trades WHERE closed_at_utc >= ? ORDER BY closed_at_utc",
                                    (since,))
        def agg(rs):
            n = len(rs)
            return {"n": n, "wins": sum(1 for r in rs if float(r["profit"] or 0) > 0),
                    "pl_usd": round(sum(float(r["profit"] or 0) for r in rs), 2)} if n else {"n": 0}
        hour = [r for r in rows if str(r["closed_at_utc"])[11:13] == d.strftime("%H")]
        today = [r for r in rows if str(r["closed_at_utc"])[:10] == d.strftime("%Y-%m-%d")]
        return {"this_utc_hour_7d": agg(hour), "today": agg(today), "last10": agg(rows[-10:])}

    def _profit_brief(self, sym: str, p: dict, q: dict, now: float) -> dict | None:
        """USD view of one position for Claude's hold / protect / take decision (per its own lot)."""
        if not p.get("price_open"):
            return None
        sign = 1 if p["side"] == "BUY" else -1
        per_usd = float(q.get("contract_size") or 100) * float(p.get("volume") or 0.01)
        open_px = float(p["price_open"])
        atr = self.atr_m1(sym, now)
        sl, tp = float(p.get("sl") or 0) or None, float(p.get("tp") or 0) or None
        return {"pl_usd": round(self.net(p), 2),
                "pl_at_sl_usd": round((sl - open_px) * sign * per_usd, 2) if sl else None,
                "pl_at_tp_usd": round((tp - open_px) * sign * per_usd, 2) if tp else None,
                "atr_m1_usd": round(atr * per_usd, 2) if atr else None}

    def _manage(self, sym: str, parts: dict[str, dict], pos: list[dict], q: dict) -> str:
        """PROTECT (price = new SL: locks profit, or on a loss an invalidation stop tighter than the current SL) and/or
        TARGET (price = new TP) on one position that is not part of a hedged group, sent as
        ONE broker MODIFY (03.11 10:01: a PROTECT + TARGET pair in one decision had its TARGET refused because the
        PROTECT's MODIFY was still in flight). Both execute at the broker, at the price, without waiting for a decision."""
        eng = self.engine
        kinds = "+".join(k for k in ("PROTECT", "TARGET") if k in parts)
        tk = next(iter(parts.values())).get("ticket")
        p = next((x for x in pos if tk is not None and int(x["ticket"]) == int(tk)), None)
        if p is None:
            return f"{kinds} {tk} refuzat: tichet necunoscut"
        if len({x["side"] for x in pos}) > 1:
            return f"{kinds} {tk} refuzat: grup acoperit — se gestionează doar ca întreg"
        if not q.get("bid") or not p.get("price_open"):
            return f"{kinds} {tk} refuzat: cotație lipsă"
        tk, sign = int(tk), (1 if p["side"] == "BUY" else -1)
        digits, point = int(q.get("digits") or 2), float(q.get("point") or 0.01)
        cur = float(q["bid"]) if sign == 1 else float(q["ask"])
        gap = (float(q.get("stops_level") or 0) + 2) * point + (float(q["ask"]) - float(q["bid"]))
        open_px = float(p["price_open"])
        per_usd = float(q.get("contract_size") or 100) * float(p.get("volume") or 0.01)
        sl, tp = float(p.get("sl") or 0) or None, float(p.get("tp") or 0) or None
        if eng.executor.has_pending_for_ticket(tk):
            return f"{kinds} #{tk} refuzat: o modificare e deja în curs"
        new_sl, new_tp, out = sl, tp, []
        for kind in ("PROTECT", "TARGET"):
            if kind not in parts:
                continue
            try:
                px = round(float(parts[kind].get("price")), digits)
            except (TypeError, ValueError):
                out.append(f"{kind} #{tk} refuzat: preț lipsă")
                continue
            if kind == "PROTECT" and (px - open_px) * sign < 0:
                out.append(f"PROTECT #{tk} refuzat: SL-ul fix al intrării ({sl:g}) nu se strânge pe pierdere")
            elif kind == "PROTECT":
                # 03.11 09:52: Claude asked for an SL just above the last M1 high on a losing SELL (an invalidation stop
                # at about -3.4 USD instead of the -10 USD basket limit) and it was refused - the SL may sit on the loss
                # side, it only ever gets tighter
                if sl is not None and (px - sl) * sign <= 0:
                    out.append(f"PROTECT #{tk} refuzat: SL-ul e deja mai strâns ({sl:g})")
                elif (cur - px) * sign <= gap:
                    out.append(f"PROTECT #{tk} refuzat: {px:g} e prea aproape de preț ({cur:g}) — pentru ieșire acum "
                               "folosește CLOSE")
                else:
                    new_sl = px
                    at_sl = (px - open_px) * sign * per_usd
                    out.append(f"SL #{tk} mutat la {px:g} (" + (f"asigurat {at_sl:+.2f} USD" if at_sl >= 0 else
                                                                 f"stop de invalidare: pierdere maximă {at_sl:+.2f} USD")
                               + ")")
            else:
                if (px - cur) * sign <= gap:
                    out.append(f"TARGET #{tk} refuzat: {px:g} e prea aproape de preț ({cur:g}) — pentru încasare acum "
                               "folosește CLOSE")
                else:
                    new_tp = px
                    out.append(f"TP #{tk} fixat la {px:g} ({(px - open_px) * sign * per_usd:+.2f} USD la atingere)")
        if (new_sl, new_tp) == (sl, tp):
            return "; ".join(out)
        if new_sl is None:
            return f"{kinds} #{tk} refuzat: poziția nu are SL"
        src = "PAPER" if p.get("source") == "PAPER" else "MT5"
        eng.executor.submit_modify(src, tk, sym, p["side"], new_sl, new_tp, reason=f"CLAUDE_{kinds.replace('+', '_')}")
        return "; ".join(out)

    @staticmethod
    def _losing_streak(recent: list[dict]) -> dict | None:
        """The latest run of losing closes on the same side (newest first in `recent`): count, side, the entry zone and
        the money lost. 03.11 10:42-11:00: three SELLs in a row at 3904.3-3905.2 under the same resistance, stopped at
        -1.34, -2.30, -4.00. None unless the last two closes are losses on the same side."""
        run = []
        for t in recent or []:
            if run and t.get("side") != run[0].get("side"):
                break
            if float(t.get("profit") or 0) >= 0:
                break
            run.append(t)
        if len(run) < 2:
            return None
        entries = [float(t["price_open"]) for t in run if t.get("price_open")]
        return {"side": run[0]["side"], "losses_in_row": len(run), "lost_usd": round(sum(float(t["profit"]) for t in run), 2),
                "entry_zone": [min(entries), max(entries)] if entries else None,
                "first_close_utc": run[-1].get("closed_at_utc")}

    def _entry_max_lot(self, c: dict, q: dict) -> float:
        """Biggest lot of a new entry: CLAUDE_MAX_LOT, the free margin (margin_max_lot) and the risk cap (risk_max_lot)."""
        mx = min(x for x in (c["claude_max_lot"], self.margin_max_lot(q), self.risk_max_lot(q)) if x is not None)
        return round(mx, 2)

    def risk_max_lot(self, q: dict) -> float | None:
        """Biggest lot whose fixed scalp SL loses at most BASKET_MAX_RISK_PCT_PER_ENTRY % of the balance; None when the
        balance is unknown. 200 USD, 5 %, SL 4 USD per 0.01 lot -> 0.02."""
        c = self.cfg()
        bal = self.balance()
        if not bal or c["risk_pct"] <= 0 or c["scalp_sl"] <= 0:
            return None
        per001 = c["scalp_sl"] * float(q.get("contract_size") or 100) * 0.01
        return max(0.01, round(math.floor(bal * c["risk_pct"] / 100 / per001) * 0.01, 2))

    def _lot_tiers(self, c: dict, q: dict) -> dict:
        """Lots for Claude's three confidence levels, inside [start lot or 0.01, max lot by margin and CLAUDE_MAX_LOT]."""
        lo = c["start_lot"] or 0.01
        hi = max(self._entry_max_lot(c, q), lo)
        mid = round(max(lo, math.floor((lo + hi) / 2 * 100) / 100), 2)
        return {"unsure": lo, "good": mid, "very_sure": round(hi, 2)}

    def _flat_minutes(self, sym: str, pos: list[dict], now: float) -> float | None:
        """Minutes since the basket was last flat-and-trading (last close), None with open positions."""
        if pos:
            return None
        row = self.engine.db.query("SELECT closed_at_utc FROM trades WHERE symbol=? ORDER BY closed_at_utc DESC LIMIT 1",
                                   (sym,))
        from datetime import datetime
        try:
            last = datetime.fromisoformat(str(row[0]["closed_at_utc"]).replace("Z", "+00:00")).timestamp()
        except (IndexError, KeyError, TypeError, ValueError):
            return None
        return round((now - last) / 60, 1)

    ESCALATE_ACTIONS = ("OPEN", "ARM", "CLOSE", "CLOSE_ALL")

    def _route_model(self, sym: str, pos: list[dict], hedged: bool, investigate: bool,
                     now: float) -> tuple[str | None, str]:
        """(model, why) for this call: the light model (BASKET_CLAUDE_FLAT_MODEL) on routine bars, None = CLAUDE_MODEL on
        the decisions that move money."""
        s = self.engine.settings
        light = s.BASKET_CLAUDE_FLAT_MODEL or None
        if light is None:
            return None, "un singur model"
        if investigate:
            return None, "investigație (grupul pe minus)"
        if not pos:
            if s.BASKET_CLAUDE_SMART_ROUTE and self.fast_vote(sym, now)[0]:
                return None, "M1 și M5 aliniate — decizie de direcție"
            return light, "fără poziție, rutină"
        if not s.BASKET_CLAUDE_SMART_ROUTE:
            return None, "poziție deschisă"
        if hedged:
            return None, "grup acoperit"
        from datetime import datetime
        try:
            age = min((now - datetime.fromisoformat(str(p["opened_at_utc"]).replace("Z", "+00:00")).timestamp()) / 60
                      for p in pos)
        except (KeyError, TypeError, ValueError):
            return None, "vârsta poziției necunoscută"
        if age < float(s.BASKET_CLAUDE_HEAVY_FIRST_MINUTES):
            return None, "poziție nouă (stopul de invalidare)"
        return light, "poziție deschisă, bară de rutină"

    async def claude_decide_and_apply(self, sym: str, payload: dict, mode: str, model: str | None = None) -> dict:
        eng = self.engine
        res = await eng.ai.decide_basket(payload, model=model)
        if (model and eng.settings.BASKET_CLAUDE_SMART_ROUTE and res.get("status") == "DONE"
                and any(str(a.get("type") or "").upper() in self.ESCALATE_ACTIONS for a in res.get("actions") or [])):
            # the light model wants to enter or exit: the same bar goes to CLAUDE_MODEL, whose answer is the one executed
            kinds = ", ".join(sorted({str(a.get("type")).upper() for a in res["actions"]}))
            eng.rt.log("INFO", "basket", f"Claude {sym}: modelul ușor propune {kinds} — confirm cu modelul principal")
            res = await eng.ai.decide_basket(payload, model=None)
        now = time.time()
        if res.get("status") == "DONE":
            self.armed[sym] = []  # every decision replaces the armed entries: Claude re-arms what it still wants
            if not payload.get("positions") and self.positions(sym):
                # an armed entry filled while Claude was thinking on a flat basket: its new entries are stale
                res["actions"] = [a for a in res.get("actions") or []
                                  if str(a.get("type") or "").upper() not in ("OPEN", "ARM")]
        rec = {"time_utc": payload.get("time_utc"), "status": res.get("status"),
               "market_view": res.get("market_view") or res.get("error"), "plan": res.get("plan"),
               "confidence": res.get("confidence"), "actions": res.get("actions") or [], "done": []}
        if res.get("status") == "DONE":
            actions = [a for a in res.get("actions") or [] if isinstance(a, dict)]
            gates = str(res.get("gates") or "")
            if "✗" in gates and any(str(a.get("type") or "").upper() in ("OPEN", "ARM") for a in actions):
                # Claude's own entry checklist failed a gate yet it still proposed an entry (kojott/LLM-trader-test
                # pattern: the model echoes numbered gates and the code holds it to them)
                kinds = ", ".join(str(a.get("type")).upper() for a in actions
                                  if str(a.get("type") or "").upper() in ("OPEN", "ARM"))
                actions = [a for a in actions if str(a.get("type") or "").upper() not in ("OPEN", "ARM")]
                rec["done"].append(f"{kinds} refuzat: porțile tale de intrare au ✗ ({gates.strip()[:60]})")
            for a in actions:
                if str(a.get("type") or "").upper() in ("OPEN", "ARM"):
                    a["confidence"] = res.get("confidence")  # the lot above the start lot is tied to it (_big_lot_block)
            rec["done"] += self.apply_claude_actions(sym, actions, mode, now)
        self.claude_last = rec
        if res.get("status") == "DONE":
            # "wait" with nothing armed and no position: remembered for the flat-call throttle (_skip_bar)
            st = self.state.setdefault(sym, {})
            q = eng.rt.quotes.get(sym) or {}
            waited = not self.positions(sym) and q.get("bid")
            st["waited_px"] = float(q["bid"]) if waited else None
        self.claude_history = (self.claude_history + [{k: rec[k] for k in ("time_utc", "plan", "done")}])[-20:]
        eng.rt.log("INFO", "basket", f"Claude {sym}: " + ("; ".join(rec["done"]) or "aștept") + f" · {str(rec['plan'])[:200]}")
        return rec

    def apply_claude_actions(self, sym: str, actions: list[dict], mode: str, now: float) -> list[str]:
        """Execute Claude's actions under the hard limits; returns what was done (or refused) per action."""
        eng, c = self.engine, self.cfg()
        done = []
        q = eng.rt.quotes.get(sym) or {}
        managed = set()  # tickets whose PROTECT/TARGET were already sent (as one MODIFY)
        for a in actions[:4]:
            kind = str(a.get("type") or "").upper()
            pos = self.positions(sym)
            if kind == "CLOSE_ALL":
                if pos and (why := self._young_loss_close(pos, pos, c, now)):
                    done.append(f"CLOSE_ALL refuzat: {why}")
                    break
                if pos:
                    self._close_all(sym, pos, "CLAUDE_CLOSE_ALL", round(sum(self.net(p) for p in pos), 2), now)
                    done.append(f"închid toate ({len(pos)})")
                break
            if kind == "ARM":
                done.append(self._arm(sym, a, q, now))
                continue
            if kind in ("PROTECT", "TARGET"):
                tk = a.get("ticket")
                if tk in managed:
                    continue  # already sent together with its pair
                managed.add(tk)
                parts = {str(x.get("type") or "").upper(): x for x in actions[:4]
                         if str(x.get("type") or "").upper() in ("PROTECT", "TARGET") and x.get("ticket") == tk}
                done.append(self._manage(sym, parts, pos, q))
                continue
            if kind == "CLOSE":
                tk = a.get("ticket")
                p = next((x for x in pos if tk is not None and int(x["ticket"]) == int(tk)), None)
                if p is None:
                    done.append(f"CLOSE {tk} refuzat: tichet necunoscut")
                    continue
                if len({x["side"] for x in pos}) > 1:
                    # closing one leg of a hedged group realises its loss and leaves the other leg naked (30.10: the
                    # SELL 0.02 hedge closed at -3.30, then the BUY ran alone to -15)
                    done.append(f"CLOSE {tk} refuzat: grup acoperit — se închide doar tot (CLOSE_ALL sau automat la "
                                f"total ≥ +{c['rec_exit']:g})")
                    continue
                if why := self._young_loss_close([p], pos, c, now):
                    done.append(f"CLOSE {tk} refuzat: {why}")
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
                note = ""
                other = [x for x in pos if x["side"] != side]
                hedge = bool(other) and sum(self.net(x) for x in other) < 0
                if hedge and not c["claude_hedge"]:
                    done.append(f"OPEN {side} refuzat: acoperirea e oprită (04.11 live: 4 grupuri acoperite −21,98 USD față "
                                "de −16 cu SL-ul simplu) — poziția iese pe SL/TP-ul fix")
                    continue
                if not hedge:
                    # owner's rule 2026-11-02 evening: Claude sizes each entry by how sure it is (constraints.lot_tiers);
                    # the dashboard start lot is the floor, the free margin the ceiling (the broker refuses more)
                    if c["start_lot"]:
                        lot = max(lot, c["start_lot"])
                    mx = self.margin_max_lot(q)
                    if mx is not None and lot > mx:
                        if mx < 0.01:
                            done.append(f"OPEN {side} refuzat: marjă liberă insuficientă pentru 0.01 lot")
                            continue
                        note = f" (lot redus de la {lot:g} la {mx:g}: atât permite marja liberă)"
                        lot = mx
                    rx = self.risk_max_lot(q)
                    if rx is not None and lot > rx:
                        note = f" (lot redus de la {lot:g} la {rx:g}: SL-ul fix nu poate risca peste {c['risk_pct']:g} % din sold)"
                        lot = rx
                    lo = c["start_lot"] or 0.01
                    if lot > lo and (why := self._big_lot_block(sym, side, a.get("confidence"), now)):
                        note = f" (lot redus de la {lot:g} la {lo:g}: {why})"
                        lot = lo
                if hedge:
                    # recovery hedge: the opposite side must end up BIGGER than the losing side (lot x hedge_lot_mult),
                    # and it carries no TP of its own - the group closes together
                    lose_vol = sum(float(x.get("volume") or 0.01) for x in other)
                    same_vol = sum(float(x.get("volume") or 0.01) for x in pos if x["side"] == side)
                    need = round(max(lose_vol * c["zr_mult"] - same_vol, 0.01), 2)
                    if lot < need:
                        note = f" (lot ridicat de la {lot:g} la {min(need, c['claude_max_lot']):g}: acoperirea trebuie să fie mai mare)"
                        lot = round(min(need, c["claude_max_lot"]), 2)
                elif not pos and (why := self._zone_block(sym, side, float(q["ask"] if side == "BUY" else q["bid"]), now)):
                    done.append(f"OPEN {side} refuzat: {why}")
                    continue
                elif not pos and (why := self.rollover_note(now) or self.hour_block_note(now) or self.atr_block_note(sym, now)):
                    done.append(f"OPEN {side} refuzat: {why}")
                    continue
                elif not pos and (why := self.reopen_note(sym, now)):
                    done.append(f"OPEN {side} refuzat: {why}")
                    continue
                elif not pos and (why := self._against_m5(sym, side, now)):
                    done.append(f"OPEN {side} refuzat: {why}")
                    continue
                elif not pos and (why := self._chasing_run(sym, side, now) or self._stretched(sym, side, q, now)
                                  or self._no_pullback_yet(sym, side, now)
                                  or self._same_level_reentry(sym, side, float(q["ask"] if side == "BUY" else q["bid"]), now)):
                    done.append(f"OPEN {side} refuzat: {why}")
                    continue
                self.last_action[sym] = 0  # Claude's actions are not throttled by the open gap
                why_txt = f"Claude: {str(a.get('why') or '')[:160]}"
                if not hedge and isinstance(a.get("confidence"), (int, float)):
                    why_txt += f" · încredere {float(a['confidence']):.2f}"  # kept on the intent: lot vs outcome study
                self._open(sym, side, q, mode, now, why_txt, volume=lot, tp_far=hedge)
                done.append(("acoperire " if hedge else "") + f"deschid {side} {lot}" + note)
        return done

    def _young_loss_close(self, closing: list[dict], pos: list[dict], c: dict, now: float) -> str | None:
        """Why a Claude CLOSE / CLOSE_ALL of `closing` is refused, or None: a loss on a group that is not hedged, while its
        newest position is younger than loss_close_min minutes. 30.10-03.11: 16 such closes realised -103.67 USD, the same
        positions held 30 more minutes on their own SL -14.57 - the exit came at the bottom of the M1 noise. The SL (or a
        PROTECT invalidation stop) still cuts a real reversal, and CLAUDE_LIMIT stays in force."""
        mins = c.get("loss_close_min") or 0
        if mins <= 0 or len({x["side"] for x in pos}) > 1 or sum(self.net(x) for x in closing) >= 0:
            return None
        from datetime import datetime
        ages = []
        for x in closing:
            try:
                ages.append((now - datetime.fromisoformat(str(x["opened_at_utc"]).replace("Z", "+00:00")).timestamp()) / 60)
            except (KeyError, TypeError, ValueError):
                return None
        if not ages or min(ages) >= mins:
            return None
        return (f"pierdere pe o poziție deschisă de {min(ages):.1f} min (< {mins:g}) — lasă SL-ul să lucreze; pentru o "
                "ieșire mai strânsă pune PROTECT (stop de invalidare)")

    ARM_MAX_MIN = 15

    def _arm(self, sym: str, a: dict, q: dict, now: float) -> str:
        """ARM (2026-11-02): Claude's conditional entry ("SELL if the price breaks 3953.4", "BUY on a pullback to 3957")
        is executed in code at the tick the price gets there, instead of waiting for the next M1 bar (by then the
        move is gone and Claude waits again). BREAKOUT = a closed M1 bar closes beyond the price (BUY above, SELL below),
        then entry at market; PULLBACK = BUY when ask <= price / SELL when bid >= price, at the tick. Lives until it
        fires, expires, or the next Claude decision. 2026-11-02 20:00: BREAKOUT on a tick touch lost 3 times in 40
        minutes (-3.83, -3.48, -6.75) on wicks that went back into the range inside the same bar."""
        side, trig = str(a.get("side") or "").upper(), str(a.get("trigger") or "").upper()
        try:
            price = float(a.get("price"))
        except (TypeError, ValueError):
            price = 0.0
        if side not in ("BUY", "SELL") or trig not in ("BREAKOUT", "PULLBACK") or price <= 0 or not q.get("bid"):
            return "ARM refuzat: direcție/declanșator/preț lipsă"
        ref = float(q["ask"]) if side == "BUY" else float(q["bid"])
        if why := self._zone_block(sym, side, price, now) or self.hour_block_note(now) or self.atr_block_note(sym, now):
            return f"ARM {side} {price:g} refuzat: {why}"
        last = self._last_close(sym, now)
        if self._arm_hit(side, trig, price, last if trig == "BREAKOUT" and last is not None else ref):
            return f"ARM {side} {price:g} refuzat: prețul ({ref:g}) e deja acolo — pentru intrare acum folosește OPEN"
        atr = self.atr_m1(sym, now) or 0.0
        if atr and abs(price - ref) > 10 * atr:
            return f"ARM {side} {price:g} refuzat: prea departe de preț ({abs(price - ref):.2f} USD, peste 10×ATR M1)"
        try:
            mins = min(max(float(a.get("valid_minutes") or 5), 1), self.ARM_MAX_MIN)
        except (TypeError, ValueError):
            mins = 5.0
        from datetime import datetime, timezone
        exp = now + mins * 60
        self.armed.setdefault(sym, []).append({
            "side": side, "trigger": trig, "price": price, "expires": exp, "lot": a.get("lot"), "armed_at": now,
            "confidence": a.get("confidence"),
            "expires_utc": datetime.fromtimestamp(exp, timezone.utc).strftime("%H:%M:%S"),
            "why": str(a.get("why") or "")[:160]})
        return f"armez {side} {'la spargerea' if trig == 'BREAKOUT' else 'la pullback'} {price:g} ({mins:g} min)"

    def _against_m5(self, sym: str, side: str, now: float) -> str | None:
        """Why a new entry is refused by the M5 gate (BASKET_M5_GATE): the M5 trend (EMA9 vs EMA21) points against it."""
        if not self.cfg()["m5_gate"]:
            return None
        _, votes = self.fast_vote(sym, now)
        v = votes.get("M5", 0)
        if v == 0 or v == (1 if side == "BUY" else -1):
            return None
        return (f"contra trendului M5 (EMA9 {'sub' if v < 0 else 'peste'} EMA21) — pe 02–03.11 intrările contra M5 au "
                "câștigat 5 din 12; intră doar în sensul M5")

    def _impulse_against(self, sym: str, side: str, q: dict, now: float) -> str | None:
        """Why a PULLBACK fill waits (BASKET_PULLBACK_IMPULSE_ATR): the forming M1 bar, or the last closed one, is an
        impulse against the entry of at least that many ATR14 M1 - buying into a falling knife."""
        k = self.cfg()["impulse_atr"]
        atr = self.atr_m1(sym, now) if k > 0 else None
        if not atr:
            return None
        sign = 1 if side == "BUY" else -1
        px = float(q["ask"]) if side == "BUY" else float(q["bid"])
        live = self.engine.rt.bars.get((sym, "M1")) or []
        closed = self.engine.rt.closed_bars(sym, "M1", now)
        moves = []
        if live and (not closed or live[-1]["t"] > closed[-1]["t"]):
            moves.append((float(live[-1]["o"]) - px) * sign)  # the forming bar, open -> now
        if closed:
            b = closed[-1]
            moves.append((float(b["o"]) - float(b["c"])) * sign)  # the last closed bar, open -> close
        worst = max(moves, default=0.0)
        if worst < k * atr:
            return None
        return f"impuls contra de {worst:.2f} USD (≥ {k:g}×ATR M1 {atr:.2f}) — aștept să se oprească înainte să intru"

    def _time_stop(self, sym: str, pos: list[dict], total: float, now: float) -> bool:
        """BASKET_SCALP_TIME_STOP_MINUTES: a scalp position that has hit neither TP nor SL after that long is closed at
        market (04.11 on 53 entries: the winners came in the first minutes; what still sits after 20 ended worse).
        True when the close was sent."""
        mins = self.cfg()["time_stop"]
        if mins <= 0 or not pos:
            return False
        from datetime import datetime
        ages = []
        for p in pos:
            try:
                ages.append((now - datetime.fromisoformat(str(p["opened_at_utc"]).replace("Z", "+00:00")).timestamp()) / 60)
            except (KeyError, TypeError, ValueError):
                return False
        if min(ages) < mins:
            return False
        self._close_all(sym, pos, "BASKET_TIME_STOP", total, now)
        return True

    def _overshoot(self, sym: str, side: str, level: float, px: float, now: float) -> tuple[float, float] | None:
        """(USD, ATR) by which the price ran past an armed PULLBACK level when that exceeds
        BASKET_PULLBACK_MAX_OVERSHOOT_ATR, else None. 04.11: fills more than 0.3 ATR past the level lost 4 of 4."""
        k = self.cfg()["max_overshoot"]
        atr = self.atr_m1(sym, now) if k > 0 else None
        if not atr:
            return None
        over = (level - px) if side == "BUY" else (px - level)
        return (over, over / atr) if over > k * atr else None

    def _same_level_reentry(self, sym: str, side: str, px: float, now: float) -> str | None:
        """Why an entry waits (BASKET_REENTRY_MIN_ATR / _MINUTES): the last trade on this side closed less than
        REENTRY_MINUTES ago and the price is within REENTRY_MIN_ATR x ATR14 M1 of its entry - the same level again.
        02-04.11: such re-entries won 24 of 36 (-1.1 USD); entries at a new level > 1.5 ATR away won 19 of 24 (+21.2)."""
        c = self.cfg()
        if c["reentry_atr"] <= 0 or c["reentry_min"] <= 0:
            return None
        atr = self.atr_m1(sym, now)
        if not atr:
            return None
        row = self.engine.db.query("SELECT price_open, closed_at_utc FROM trades WHERE symbol=? AND side=? "
                                   "ORDER BY closed_at_utc DESC LIMIT 1", (sym, side))
        if not row or not row[0]["price_open"] or not row[0]["closed_at_utc"]:
            return None
        from datetime import datetime
        try:
            closed = datetime.fromisoformat(str(row[0]["closed_at_utc"]).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
        mins = (now - closed) / 60
        dist = abs(px - float(row[0]["price_open"])) / atr
        if mins > c["reentry_min"] or dist >= c["reentry_atr"]:
            return None
        return (f"același nivel ca intrarea {side} precedentă ({float(row[0]['price_open']):g}, închisă acum {mins:.0f} min, "
                f"la {dist:.2f}×ATR) — reintrările la același nivel au câștigat 24 din 36 (−1,1 USD); aștept un nivel nou")

    def _no_pullback_yet(self, sym: str, side: str, now: float) -> str | None:
        """Why an entry waits (BASKET_ENTRY_NEEDS_PULLBACK): neither the last closed M1 bar nor the last 10 minutes net
        moved against the trade - the price has not pulled back at all, the entry would chase. 04.11, 57 real entries:
        after a counter move 31 of 35 won (+41.6), straight after a bar in the own direction 13 of 22 (-15.4)."""
        if not self.cfg()["needs_pullback"]:
            return None
        bars = self.engine.rt.closed_bars(sym, "M1", now)
        if len(bars) < 11:
            return None
        sign = 1 if side == "BUY" else -1
        last = (float(bars[-1]["c"]) - float(bars[-1]["o"])) * sign
        net10 = (float(bars[-1]["c"]) - float(bars[-11]["c"])) * sign
        if last < 0 or net10 < 0:
            return None
        return ("nicio revenire încă: ultima bară M1 și ultimele 10 minute merg în sensul intrării — aștept o bară contra "
                "(intrările fără revenire au câștigat 13 din 22, cele după revenire 31 din 35)")

    def _stretched(self, sym: str, side: str, q: dict, now: float) -> str | None:
        """Why an entry waits (BASKET_MAX_STRETCH_EMA50_ATR): the price sits that many ATR14 M1 beyond the EMA50 M1 in
        the trade direction - the move is extended and the entry buys its exhaustion (5 of the 11 losses up to 04.11)."""
        k = self.cfg()["max_stretch"]
        atr = self.atr_m1(sym, now) if k > 0 else None
        if not atr or not q.get("bid"):
            return None
        closes = [float(b["c"]) for b in self.engine.rt.closed_bars(sym, "M1", now)[-200:]]
        if len(closes) < 60:
            return None
        e50 = ind.last(ind.ema(closes, 50))
        if e50 is None:
            return None
        sign = 1 if side == "BUY" else -1
        px = float(q["ask"]) if side == "BUY" else float(q["bid"])
        stretch = (px - e50) * sign / atr
        if stretch < k:
            return None
        return (f"prețul e la {stretch:.1f}×ATR M1 {'peste' if side == 'BUY' else 'sub'} EMA50 M1 (limita {k:g}) — "
                "mișcare întinsă, aștept revenirea spre medie")

    def _big_lot_block(self, sym: str, side: str, confidence, now: float) -> str | None:
        """Why an entry lot above the start lot falls back to it (BASKET_SURE_MIN_CONFIDENCE): Claude's confidence on the
        decision is missing or too low, M5 (EMA9/21) or M15 (EMA20/50) does not point with the entry, or this UTC hour
        lost money over the last 7 days. The owner (04.11) wants the bigger volume only on the entries Claude is sure of."""
        c = self.cfg()
        if c["sure_conf"] <= 0:
            return None
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            conf = None
        if conf is None or conf < c["sure_conf"]:
            return (f"încredere {conf:.2f}" if conf is not None else "încredere lipsă") + f" sub {c['sure_conf']:g}"
        sign = 1 if side == "BUY" else -1
        _, fast = self.fast_vote(sym, now)
        _, mtf = self.mtf_vote(sym, now)
        if fast.get("M5", 0) * sign <= 0:
            return "M5 (EMA9/21) nu e în sensul intrării"
        if mtf.get("M15", 0) * sign <= 0:
            return "M15 (EMA20/50) nu e în sensul intrării"
        hour = self._bot_stats(sym, now).get("this_utc_hour_7d") or {}
        if hour.get("n", 0) >= 3 and float(hour.get("pl_usd") or 0) < 0:
            return f"ora asta UTC a pierdut în ultimele 7 zile ({hour['pl_usd']:+.2f} USD din {hour['n']})"
        return None

    def _chasing_run(self, sym: str, side: str, now: float) -> str | None:
        """Why an entry waits (BASKET_MAX_RUN_BARS): the last N closed M1 bars all closed in the trade direction - the
        move is spent and the entry chases it (16:59, 18:10 on 03.11 and 09:28 on 04.11 were stopped within minutes)."""
        n = self.cfg()["max_run"]
        if n <= 0:
            return None
        bars = self.engine.rt.closed_bars(sym, "M1", now)
        if len(bars) < n:
            return None
        sign = 1 if side == "BUY" else -1
        if all((float(b["c"]) - float(b["o"])) * sign > 0 for b in bars[-n:]):
            return f"{n} bare M1 la rând în sensul {side} — mișcarea e consumată, aștept o bară contra înainte să intru"
        return None

    def _zone_block(self, sym: str, side: str, price: float, now: float) -> str | None:
        """Why a new `side` entry at `price` is refused, or None: the last BASKET_ZONE_BLOCK_LOSSES closes on that side
        were all losses (closes on the other side do not count), the newest of them less than BASKET_ZONE_BLOCK_MINUTES
        ago, and the price is inside their entry zone +- 1 x ATR M1 - the level stopped holding (03.11 10:42-11:40)."""
        s = self.engine.settings
        n, mins = int(s.BASKET_ZONE_BLOCK_LOSSES or 0), float(s.BASKET_ZONE_BLOCK_MINUTES or 0)
        if n <= 0 or mins <= 0:
            return None
        rows = self.engine.db.query("SELECT price_open, profit, closed_at_utc FROM trades WHERE symbol=? AND side=? AND "
                                    "closed_at_utc IS NOT NULL ORDER BY closed_at_utc DESC LIMIT ?", (sym, side, n))
        if len(rows) < n or any(float(r.get("profit") or 0) >= 0 or not r.get("price_open") for r in rows):
            return None
        from datetime import datetime
        try:
            closed = [datetime.fromisoformat(str(r["closed_at_utc"]).replace("Z", "+00:00")).timestamp() for r in rows]
        except ValueError:
            return None
        # every loss of the run inside the window: 03.11 12:15 a BUY loss from the day before (3963.23) stretched the
        # zone to 3904.35-3963.23
        if now - min(closed) >= mins * 60:
            return None
        last = max(closed)
        pad = self.atr_m1(sym, now) or 0.0
        entries = [float(r["price_open"]) for r in rows]
        # each losing entry blocks only its own +- 1 x ATR (14:52: two BUY losses 11 USD apart had closed the whole span)
        near = [e for e in entries if abs(price - e) <= pad]
        if not near:
            return None
        return (f"ultimele {n} {side} au pierdut, una la {near[0]:g} (±1×ATR M1 de preț) — nicio reintrare {side} acolo "
                f"până la {time.strftime('%H:%M', time.gmtime(last + mins * 60))} UTC; intră mai departe de nivel")

    def _last_close(self, sym: str, now: float) -> float | None:
        bars = self.engine.rt.closed_bars(sym, "M1", now)
        return float(bars[-1]["c"]) if bars else None

    @staticmethod
    def _arm_hit(side: str, trigger: str, price: float, px: float) -> bool:
        """px = ask for BUY, bid for SELL. BREAKOUT goes with the side (BUY up through, SELL down through), PULLBACK
        comes back against it (BUY down to, SELL up to)."""
        if trigger == "BREAKOUT":
            return px >= price if side == "BUY" else px <= price
        return px <= price if side == "BUY" else px >= price

    def _fire_armed(self, sym: str, pos: list[dict], q: dict, mode: str, now: float) -> bool:
        """Open the first armed entry whose price was reached (only while the basket is flat); True when it fired."""
        arms = [x for x in self.armed.get(sym, []) if x["expires"] > now]
        self.armed[sym] = arms
        if not arms or pos or not q.get("bid") or not q.get("ask"):
            return False
        bars = self.engine.rt.closed_bars(sym, "M1", now)
        for x in arms:
            px = float(q["ask"]) if x["side"] == "BUY" else float(q["bid"])
            if x["trigger"] == "BREAKOUT":
                # only an M1 bar that CLOSED beyond the level after the arm counts, not a wick through it
                if not bars or bars[-1]["t"] + 60 <= x.get("armed_at", 0) or \
                        not self._arm_hit(x["side"], "BREAKOUT", x["price"], float(bars[-1]["c"])):
                    continue
            elif not self._arm_hit(x["side"], x["trigger"], x["price"], px):
                continue
            elif (over := self._overshoot(sym, x["side"], x["price"], px, now)) is not None:
                # the price ran through the level: it failed, and a fill here would sit inside the reversal
                self.armed[sym] = [a for a in arms if a is not x]
                self.engine.rt.log("INFO", "basket", f"Coș {sym}: {x['side']} la {x['price']:g} anulat — prețul a trecut de "
                                                     f"nivel cu {over[0]:.2f} USD ({over[1]:.2f}×ATR M1, limita "
                                                     f"{self.cfg()['max_overshoot']:g}); nivelul a cedat (0 din 4 câștigate așa)")
                continue
            elif (why := self._impulse_against(sym, x["side"], q, now) or self._chasing_run(sym, x["side"], now)
                  or self._stretched(sym, x["side"], q, now) or self._no_pullback_yet(sym, x["side"], now)
                  or self._same_level_reentry(sym, x["side"], px, now)):
                st = self.state.setdefault(sym, {})
                if st.get("impulse_note") != x["price"]:
                    st["impulse_note"] = x["price"]
                    self.engine.rt.log("INFO", "basket", f"Coș {sym}: {x['side']} la {x['price']:g} atins, dar {why}")
                continue
            self.armed[sym] = []
            if x["trigger"] == "BREAKOUT":
                self.breakout[sym] = {"side": x["side"], "level": float(x["price"]), "since": now}
            else:
                self.breakout.pop(sym, None)
            done = self.apply_claude_actions(sym, [{"type": "OPEN", "side": x["side"], "lot": x.get("lot"),
                                                    "confidence": x.get("confidence"),
                                                    "why": f"armat: {x['why']}"}], mode, now)
            msg = (f"Claude {sym}: intrare armată atinsă ({x['side']} "
                   f"{'închidere M1 dincolo de' if x['trigger'] == 'BREAKOUT' else 'pullback'} {x['price']:g}, preț {px:g}) — "
                   + "; ".join(done))
            self.engine.rt.log("INFO", "basket", msg)
            self.claude_history = (self.claude_history + [{"time_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
                                                           "plan": "intrare armată executată", "done": done}])[-20:]
            return True
        return False

    FAILED_BREAKOUT_ATR = 0.3

    def _failed_breakout(self, sym: str, pos: list[dict], total: float, now: float) -> bool:
        """A group opened by an armed BREAKOUT closes at once (in code) when a closed M1 bar ends back through the broken
        level by FAILED_BREAKOUT_ATR x ATR M1 while the group is on a loss. 03.11 09:02: SELL 0.02 on a close under
        3896.1, the 09:04 bar closed back at 3897.9 (-3.6 USD) and Claude held it to -16.92 waiting for a rejection; of the
        16 armed entries of 02-03.11 no winning breakout ever closed back like that. True when the group was closed."""
        b = self.breakout.get(sym)
        if not b:
            return False
        if not pos:
            if now - b["since"] > 120:  # the OPEN never filled, or the group is gone
                self.breakout.pop(sym, None)
            return False
        if any(p["side"] != b["side"] for p in pos):
            self.breakout.pop(sym, None)
            return False
        atr = self.atr_m1(sym, now)
        bars = self.engine.rt.closed_bars(sym, "M1", now)
        if not atr or not bars or total >= 0:
            return False
        sign = 1 if b["side"] == "BUY" else -1
        last = bars[-1]
        if last["t"] < int(b["since"] // 60 * 60) or (b["level"] - float(last["c"])) * sign < self.FAILED_BREAKOUT_ATR * atr:
            return False
        self.breakout.pop(sym, None)
        self.engine.rt.log("WARN", "basket", f"Coș {sym}: spargere eșuată — bara M1 a închis la {float(last['c']):g}, înapoi "
                                             f"dincolo de nivelul spart {b['level']:g}; închid grupul la {total:+.2f} USD")
        self._close_all(sym, pos, "FAILED_BREAKOUT", total, now)
        return True

    def hedge_fits(self, q: dict, pos: list[dict], volume: float | None = None) -> bool:
        """Whether the free margin still takes the recovery hedge (losing lot x BASKET_ZR_MULT) - for a new entry of
        `volume`, the entry itself plus its hedge. Unknown margin = True (the broker decides)."""
        c, mx = self.cfg(), self.margin_max_lot(q)
        if mx is None:
            return True
        if volume is not None:
            need = float(volume) * (1 + c["zr_mult"])
        else:
            need = sum(float(p.get("volume") or 0.01) for p in pos) * c["zr_mult"]
        return mx + 1e-9 >= round(need, 2)

    def abnormal_spread(self, sym: str, q: dict) -> str | None:
        """Reason text when the spread is far above normal (rollover / weekly close quote), else None."""
        c = self.cfg()
        sp, point = q.get("spread_points"), float(q.get("point") or 0)
        if sp is None and q.get("bid") and q.get("ask") and point > 0:
            sp = (float(q["ask"]) - float(q["bid"])) / point
        cap = spread_cap(self.engine.rt.risk.get("max_spread_points", 30), sym, point) * c["abn_mult"]
        if sp is None or c["abn_mult"] <= 0 or cap <= 0 or float(sp) <= cap:
            return None
        usd = round((float(q["ask"]) - float(q["bid"])), 2) if q.get("bid") and q.get("ask") else None
        return f"spread anormal {float(sp):.0f} puncte ({usd} USD, normal sub {cap / c['abn_mult']:.0f})"

    def rollover_note(self, now: float) -> str | None:
        return rollover_blocker(now, self.cfg()["roll_min"])

    def scalp_exits(self, sym: str, now: float) -> tuple[float, float]:
        """(TP, SL) price distances of a scalp entry: the fixed BASKET_SCALP_TP/SL_PRICE, widened to TP_ATR_MULT / SL_ATR_MULT
        x ATR14 M1 when the volatility is high (05.11: once ATR passes 2 USD the fixed 4 USD stop sits inside one bar's
        noise - ATR >= 3 entries -8.0 with fixed exits vs +14.9 with 1xATR / 2xATR)."""
        c = self.cfg()
        tp, sl = c["scalp_tp"], c["scalp_sl"]
        atr = self.atr_m1(sym, now) if (c["tp_atr"] > 0 or c["sl_atr"] > 0) else None
        if atr:
            if c["tp_atr"] > 0:
                tp = max(tp, c["tp_atr"] * atr)
            if c["sl_atr"] > 0:
                sl = max(sl, c["sl_atr"] * atr)
        return tp, sl

    def atr_block_note(self, sym: str, now: float) -> str | None:
        """Why a new entry is refused by BASKET_MAX_ATR_M1: the 1-minute ATR is so large that the fixed scalp stop is
        inside one bar's noise (05.11 01:26: BUY at ATR 6.2 stopped in 60 s; ATR >= 3.5 entries won 0 of 2)."""
        k = self.cfg()["max_atr"]
        if k <= 0:
            return None
        atr = self.atr_m1(sym, now)
        if not atr or atr < k:
            return None
        return (f"volatilitate extremă: ATR M1 {atr:.2f} USD ≥ {k:g} — SL-ul fix de {self.cfg()['scalp_sl']:g} e în zgomotul "
                "unei singure bare (0 din 2 câștigate la ATR ≥ 3,5); fără intrări noi până se calmează")

    def hour_block_note(self, now: float) -> str | None:
        """Why a new entry is refused by BASKET_NO_ENTRY_UTC_HOURS (the London/NY overlap: 12-16 UTC lost 16 of 22 real
        trades over 02-04.11, -33.73 USD; the 1-minute range there is bigger than half the stop)."""
        hours = self.cfg()["no_entry_hours"]
        h = time.gmtime(now).tm_hour
        if not hours or h not in hours:
            return None
        end = h
        while (end + 1) % 24 in hours and end + 1 < h + 24:
            end += 1
        return (f"fereastra {min(hours):02d}:00–{(end + 1) % 24:02d}:00 UTC e fără intrări noi (suprapunerea Londra/NY: "
                "6 din 16 câștigate, −33,73 USD pe 02–04.11)")

    def margin_max_lot(self, q: dict) -> float | None:
        """Biggest lot BASKET_MARGIN_USE_FRAC of the free margin pays for, from price x contract / leverage; None when unknown.
        2026-11-02: 0.01 lot of gold at 3950 and 1:100 = ~39.5 USD margin, so a 123 USD account carries ~0.03 lot."""
        _, acc = self.engine.risk_account(self.engine.mode())
        try:
            free, lev = float((acc or {}).get("margin_free")), float((acc or {}).get("leverage"))
            px = float(q.get("ask") or 0)
        except (TypeError, ValueError):
            return None
        per001 = px * float(q.get("contract_size") or 100) * 0.01 / lev if lev > 0 and px > 0 else 0
        if per001 <= 0:
            return None
        return round(math.floor(free * self.cfg()["margin_frac"] / per001) * 0.01, 2)

    def atr_m1(self, sym: str, now: float) -> float | None:
        bars = self.engine.rt.closed_bars(sym, "M1", now)[-120:]
        if len(bars) < 30:
            return None
        return ind.last(ind.atr([b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars], 14))

    def _auto_hedge(self, sym: str, pos: list[dict], q: dict, mode: str, now: float) -> bool:
        """Owner's rule 2026-11-03: a lone position BASKET_HEDGE_AT_PRICE against its entry gets the opposite side opened
        in code with BASKET_ZR_MULT x its lot (no TP of its own); from then on the group is managed as a whole - closed at
        +rec_exit or at -limit. True when the hedge order was sent."""
        c = self.cfg()
        if c["hedge_at"] <= 0 or not pos or not q.get("bid") or not q.get("ask"):
            return False
        side = pos[0]["side"]
        sign = 1 if side == "BUY" else -1
        cur = float(q["bid"]) if sign == 1 else float(q["ask"])
        worst = max((float(p["price_open"]) - cur) * sign for p in pos if p.get("price_open"))
        if worst < c["hedge_at"]:
            return False
        st = self.state.setdefault(sym, {})
        if c["hedge_on_close"]:
            # confirmation: the last CLOSED M1 bar must have closed beyond the trigger too, not just a wick
            bars = self.engine.rt.closed_bars(sym, "M1", now)
            if not bars:
                return False
            closed_adverse = max((float(p["price_open"]) - float(bars[-1]["c"])) * sign for p in pos if p.get("price_open"))
            if closed_adverse < c["hedge_at"]:
                if st.get("hedge_wait") != bars[-1]["t"]:
                    st["hedge_wait"] = bars[-1]["t"]
                    self.engine.rt.log("INFO", "basket", f"Coș {sym}: {side} la {worst:.2f} USD contra pe fitil, dar bara M1 a "
                                                         f"închis la {closed_adverse:.2f} — acoperirea așteaptă o închidere dincolo de {c['hedge_at']:g}")
                return False
        lot = round(sum(float(p.get("volume") or 0.01) for p in pos) * c["zr_mult"], 2)
        if not self.hedge_fits(q, pos):
            if st.get("hedge_note") != "margin":
                st["hedge_note"] = "margin"
                self.engine.rt.log("WARN", "basket", f"Coș {sym}: {side} la {worst:.2f} USD contra, dar acoperirea x{c['zr_mult']:g} "
                                                     f"({lot:g} lot) nu încape în marjă — rămâne SL-ul fix")
            return False
        hedge = "SELL" if side == "BUY" else "BUY"
        self.engine.rt.log("WARN", "basket", f"Coș {sym}: {side} a ajuns la {worst:.2f} USD contra — deschid acoperirea "
                                             f"{hedge} {lot:g} (x{c['zr_mult']:g}); grupul se închide la +{c['rec_exit']:g} sau la "
                                             f"−{c['limit']:g} USD")
        self.last_action[sym] = 0
        self._open(sym, hedge, q, mode, now, f"acoperire automată la {worst:.2f} USD contra", tp_far=True, volume=lot)
        st.pop("hedge_note", None)
        return True

    def _strip_group_tps(self, sym: str, pos: list[dict], q: dict) -> bool:
        """Hedged group: move every leg's broker TP and SL out to BASKET_SL_DISTANCE so no leg closes alone (the code
        closes the group at +rec_exit / -limit); True when a MODIFY was sent (one per round)."""
        eng, c = self.engine, self.cfg()
        digits = int(q.get("digits") or 2)
        for p in pos:
            if not p.get("price_open"):
                continue
            sign = 1 if p["side"] == "BUY" else -1
            far_tp = round(float(p["price_open"]) + sign * c["sl_dist"], digits)
            far_sl = round(float(p["price_open"]) - sign * c["sl_dist"], digits)
            tp_ok = not p.get("tp") or abs(float(p["tp"]) - far_tp) <= 0.05
            sl_ok = not p.get("sl") or abs(float(p["sl"]) - far_sl) <= 0.05
            if tp_ok and sl_ok:
                continue
            if eng.executor.has_pending_for_ticket(int(p["ticket"])):
                return True
            src = "PAPER" if p.get("source") == "PAPER" else "MT5"
            eng.executor.submit_modify(src, int(p["ticket"]), sym, p["side"], far_sl if p.get("sl") else None,
                                       far_tp if p.get("tp") else None, reason="GROUP_EXIT")
            return True
        return False

    def _recovery_brief(self, sym: str, pos: list[dict], q: dict, now: float) -> dict | None:
        """What Claude needs to investigate a losing position/group: how deep, how far the price went, how fast,
        the trend on every timeframe, and the hedge that would turn the group around (lot and break-even price)."""
        if not pos:
            return None
        c = self.cfg()
        by: dict[str, dict] = {}
        for p in pos:
            b = by.setdefault(p["side"], {"lot": 0.0, "pl_usd": 0.0, "n": 0})
            b["lot"] = round(b["lot"] + float(p.get("volume") or 0.01), 2)
            b["pl_usd"] = round(b["pl_usd"] + self.net(p), 2)
            b["n"] += 1
        losing = min(by, key=lambda s: by[s]["pl_usd"])
        if by[losing]["pl_usd"] >= 0:
            return None
        hedge_side = "SELL" if losing == "BUY" else "BUY"
        bid, ask = float(q["bid"]), float(q["ask"])
        contract = float(q.get("contract_size") or 100)

        def zero_price(legs):  # price where the group's raw P/L is 0 (spread/commission not included)
            sv = sum((1 if s == "BUY" else -1) * v for s, v, _ in legs)
            if abs(sv) < 1e-9:
                return None
            return round(sum((1 if s == "BUY" else -1) * v * o for s, v, o in legs) / sv, 2)

        legs = [(p["side"], float(p.get("volume") or 0.01), float(p["price_open"])) for p in pos if p.get("price_open")]
        hedge_vol = by.get(hedge_side, {}).get("lot", 0.0)
        lot = round(max(by[losing]["lot"] * c["zr_mult"] - hedge_vol, 0.01), 2)
        lot = round(min(lot, c["claude_max_lot"]), 2)
        hedge_px = bid if hedge_side == "SELL" else ask
        after = legs + [(hedge_side, lot, hedge_px)]
        net_after = round(sum((1 if s == "BUY" else -1) * v for s, v, _ in after), 2)
        total = round(sum(self.net(p) for p in pos), 2)
        # worst excursion against the losing side since its first entry (closed M1 bars + the live quote)
        from datetime import datetime
        opened = [p.get("opened_at_utc") for p in pos if p["side"] == losing and p.get("opened_at_utc")]
        t0 = None
        if opened:
            try:
                t0 = min(datetime.fromisoformat(o.replace("Z", "+00:00")).timestamp() for o in opened)
            except ValueError:
                t0 = None
        bars = [b for b in self.engine.rt.closed_bars(sym, "M1", now) if t0 is None or b["t"] >= t0 - 60]
        entries = [o for s, _, o in legs if s == losing]
        if losing == "BUY":
            worst = min([b["l"] for b in bars] + [bid])
            adverse = round(max(entries) - worst, 2) if entries else None
        else:
            worst = max([b["h"] for b in bars] + [ask])
            adverse = round(worst - min(entries), 2) if entries else None
        side_v, votes = self.mtf_vote(sym, now)
        _, fast = self.fast_vote(sym, now)
        return {
            "losing_side": losing, "losing_lot": by[losing]["lot"], "losing_pl_usd": by[losing]["pl_usd"],
            "group_total_usd": total, "by_side": by, "hedged": len(by) > 1,
            "hedge_levels_open": by.get(hedge_side, {}).get("n", 0),
            "minutes_since_first_entry": round((now - t0) / 60, 1) if t0 else None,
            "max_adverse_move_usd": adverse, "price_now": {"bid": bid, "ask": ask},
            "group_zero_price_now": zero_price(legs),
            "trend_ema20_50": votes, "trend_short_ema9_21": fast, "trend_consensus": side_v,
            "suggested_hedge": {"side": hedge_side, "lot": lot, "group_zero_price_after": zero_price(after),
                                "net_lot_after": net_after,
                                "usd_per_1usd_move_after": round(abs(net_after) * contract, 2)},
            "group_exit_usd": c["rec_exit"],
            "usd_until_loss_limit": round(c["limit"] + total, 2),
        }

    def _open(self, sym: str, side: str, q: dict, mode: str, now: float, why: str, tp_far: bool = False,
              volume: float | None = None) -> None:
        """Entry at market through the Risk Engine: fixed scalp TP / SL (price distance), or for a hedge (tp_far) the TP and
        SL BASKET_SL_DISTANCE away - the hedged group closes as a whole."""
        eng, c = self.engine, self.cfg()
        self.last_action[sym] = now
        sign = 1 if side == "BUY" else -1
        entry = float(q["ask"]) if side == "BUY" else float(q["bid"])
        digits = int(q.get("digits") or 2)
        tp_dist, sl_dist = (c["sl_dist"], c["sl_dist"]) if tp_far else self.scalp_exits(sym, now)
        contract = float(q.get("contract_size") or 0)
        if not tp_far and contract > 0 and not self.hedge_fits(q, [], volume or c["start_lot"] or 0.01):
            # no margin for a hedge (03.11, lot 0.02 on ~130 USD): the broker SL is never further than just past the
            # basket loss limit, so the position is capped even when the bot or MT5 is not running (USD limit per 0.01
            # lot -> price distance, the same for every lot)
            limit_001 = c["limit"] / (c["lot_scale"] or 1.0)
            sl_dist = min(sl_dist, round(limit_001 / (contract * 0.01) * 1.1, digits))
        sl, tp = round(entry - sign * sl_dist, digits), round(entry + sign * tp_dist, digits)
        rc = eng.risk_check(sym, side, entry, sl, tp, mode=mode)
        volume = volume or self.lot(q) or rc["volume"]
        it = eng.executor.submit_open(mode, sym, side, volume, sl, tp, q, rc)
        eng.rt.log("INFO", "basket", f"Coș {sym}: {why} → {side} {volume} ({it['state']})")
        if it["state"] == "REJECTED_RISK":
            eng.rt.log("WARN", "basket", f"Coș {sym}: Risk Engine a refuzat poziția (de obicei marjă liberă)")

    def mtf_vote(self, sym: str, now: float) -> tuple[str | None, dict]:
        """Trend of M1/M5/M15/H1 (EMA20 vs EMA50 on closed bars) for the recovery brief: (side when >= 3 of 4 agree,
        {tf: +1/-1/0})."""
        votes = {}
        for tf in ("M1", "M5", "M15", "H1"):
            closes = [b["c"] for b in self.engine.rt.closed_bars(sym, tf, now)[-200:]]
            if len(closes) < 60:
                votes[tf] = 0
                continue
            e20, e50 = ind.last(ind.ema(closes, 20)), ind.last(ind.ema(closes, 50))
            votes[tf] = 0 if e20 is None or e50 is None else (1 if e20 > e50 else -1 if e20 < e50 else 0)
        need = 3
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

    def on_open_failed(self, it: dict) -> None:
        """The broker refused a basket OPEN (e.g. 10019 NO_MONEY): logged once per refusal."""
        if not self.enabled or not it.get("symbol"):
            return
        res = it.get("result") or {}
        self.engine.rt.log("WARN", "basket", f"Coș {it['symbol']}: brokerul a refuzat poziția "
                                             f"({res.get('retcode_desc') or res.get('retcode') or it['state']})")

    def balance(self) -> float | None:
        _, acc = self.engine.risk_account(self.engine.mode())
        try:
            return float((acc or {}).get("balance") or (acc or {}).get("equity") or 0) or None
        except (TypeError, ValueError):
            return None

    def lot(self, q: dict) -> float | None:
        """Lot scaled with the balance (BASKET_LOT_PER_USD per 0.01), None = use the Risk Engine's fixed lot."""
        c = self.cfg()
        if c["start_lot"]:
            return c["start_lot"]
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

    def _close_all(self, sym: str, pos: list[dict], reason: str, total: float, now: float) -> None:
        eng = self.engine
        self.last_action[sym] = now
        eng.rt.log("INFO" if reason in ("BASKET_TP", "BASKET_TIME_STOP") else "WARN", "basket",
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
