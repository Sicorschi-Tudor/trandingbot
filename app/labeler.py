"""Shadow labels: what every analysed setup (signal, blocked candidate, vetoed signal) would have done.

Shared bar-by-bar trade simulation, also used by app/backtest.py:
- entry at the open of the first bar after the decision bar (+ spread for BUY; SELL fills at bid),
- BUY exits on bid (bar l/h), SELL exits on ask (bar l/h + spread),
- SL and TP touched in the same bar -> SL first (conservative),
- break-even at `break_even_at_r`: SL locked at entry ± 2 points from the NEXT bar (same level as
  position_manager.plan), only when the bar's favourable price is ≥ spread/stops level + 1 point beyond it;
  with `be_lock_r` > 0 (smart BE lock) the level is entry ± max(spread + 3 points, be_lock_r × initial risk) —
  be_lock_r = 0 is the old 2-point lock exactly,
- time stop after `max_hold_minutes` at the close of the bar containing the deadline; with max_hold_minutes=0
  (no live time stop) labels stop at a 24 h horizon with outcome HORIZON (not a live exit).
Shadow labels (label_pending) price entries/ask exits with the live spread statistics (bar `sp` is the bar's MINIMUM
spread) and stop exits with DEFAULT slippage; outcome_r is GROSS of commission (not expressible in R without volume).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import TF_SECONDS

BE_LOCK_MIN_POINTS = 2  # position_manager.plan: open ± 2 points (be_lock_r = 0)
BE_LOCK_SPREAD_POINTS = 3  # smart BE lock (be_lock_r > 0): at least spread + 3 points beyond the entry
NODATA_GRACE_S = 6 * 3600  # deadline passed this long ago and still no bars -> NODATA (keeps the queue moving)
LABEL_HORIZON_S = 24 * 3600  # labeling horizon when there is no time stop (max_hold_minutes=0)
LABEL_SLIPPAGE_PTS = 1.0  # = backtest.DEFAULT_SLIPPAGE


def guess_point(symbol: str, price: float | None = None) -> float:
    """Point size when the symbol spec is not known (FX: 5/3 digits, metals 2/3)."""
    s = "".join(ch for ch in (symbol or "").upper() if ch.isalpha())
    if s.startswith("XAU"):
        return 0.01
    if s.startswith("XAG"):
        return 0.001
    if len(s) >= 6 and s[3:6] in ("JPY", "HUF"):
        return 0.001
    if price is not None and price > 500:
        return 0.01
    return 0.00001


def be_lock_level(entry: float, sign: int, point: float, r_dist: float, spread_px: float, be_lock_r: float = 0.0) -> float:
    """Break-even SL level (same rule as position_manager.plan): be_lock_r = 0 -> entry ± 2 points (old behaviour);
    be_lock_r > 0 -> entry ± max(spread now + 3 points, be_lock_r × initial risk distance)."""
    if be_lock_r and be_lock_r > 0:
        return entry + sign * max(spread_px + BE_LOCK_SPREAD_POINTS * point, be_lock_r * r_dist)
    return entry + sign * BE_LOCK_MIN_POINTS * point


@dataclass
class SimPosition:
    side: str
    entry: float
    sl: float
    tp: float
    point: float
    opened_t: int
    be_at_r: float = 1.0
    slip_px: float = 0.0  # adverse slippage applied to stop exits
    stops_px: float = 0.0  # broker stops level (price): BE needs this much room, like position_manager.plan
    r_dist: float = 0.0
    be_done: bool = False
    be_lock_r: float = 0.0  # smart BE lock (strategy be_lock_r); 0 = entry ± 2 points
    mfe: float = 0.0  # price distance, favourable
    mae: float = 0.0  # price distance, adverse
    extra: dict = field(default_factory=dict)
    # Stage C exit policies (app/exits.py): None = base behaviour (code path below, unchanged)
    exit: object = None
    ind: object = None  # exits.IncIndicators updated with every bar (policies / trade paths); never changes base exits
    xs: dict = field(default_factory=dict)
    bar_sec: int = 60
    closed_frac: float = 0.0  # fraction already closed (partial exit)
    realized_r: float = 0.0  # R booked by partial closes (weighted by their fraction)

    def __post_init__(self) -> None:
        self.sign = 1 if self.side == "BUY" else -1
        self.r_dist = (self.entry - self.sl) * self.sign

    @property
    def valid(self) -> bool:
        return self.r_dist > 0

    def r_of(self, price: float) -> float:
        return (price - self.entry) * self.sign / self.r_dist if self.r_dist > 0 else 0.0

    def total_r(self, price: float) -> float:
        """R of the whole position when the rest closes at `price` (partial closes included)."""
        if not self.closed_frac:
            return self.r_of(price)
        return self.realized_r + (1.0 - self.closed_frac) * self.r_of(price)

    def book_partial(self, frac: float, price: float) -> None:
        frac = min(frac, 1.0 - self.closed_frac)
        self.realized_r += frac * self.r_of(price)
        self.closed_frac += frac

    def step(self, bar: dict, spread_px: float) -> tuple[str, float] | None:
        """Process one bar (t,o,h,l,c). Returns (reason, exit_price) when the position closes in this bar."""
        if self.ind is not None:
            self.ind.update(bar)
        if self.exit is not None:
            from .exits import step as exit_step
            return exit_step(self, bar, spread_px)
        return self._step_base(bar, spread_px)

    def _step_base(self, bar: dict, spread_px: float) -> tuple[str, float] | None:
        sg = self.sign
        if sg > 0:
            lo, hi, op = bar["l"], bar["h"], bar["o"]
        else:  # SELL is closed at ask
            lo, hi, op = bar["l"] + spread_px, bar["h"] + spread_px, bar["o"] + spread_px
        adverse = (self.entry - lo) if sg > 0 else (hi - self.entry)
        favour = (hi - self.entry) if sg > 0 else (self.entry - lo)
        hit_sl = lo <= self.sl if sg > 0 else hi >= self.sl
        hit_tp = hi >= self.tp if sg > 0 else lo <= self.tp
        if hit_sl:
            self.mae = max(self.mae, adverse)
            # gap through the stop at the open fills at the open; stops slip against us
            px = min(self.sl, op) if sg > 0 else max(self.sl, op)
            px -= sg * self.slip_px
            return ("BE" if self.be_done else "SL"), px
        self.mae = max(self.mae, adverse)
        self.mfe = max(self.mfe, favour)
        if hit_tp:
            return "TP", self.tp
        if self.be_at_r > 0 and not self.be_done and self.r_dist > 0 and favour >= self.be_at_r * self.r_dist - self.point / 2:
            lock = be_lock_level(self.entry, sg, self.point, self.r_dist, spread_px, self.be_lock_r)
            if (lock - self.sl) * sg <= 0:
                self.be_done = True  # stop already at/after the lock: nothing to move
            elif ((hi if sg > 0 else lo) - lock) * sg >= max(spread_px, self.stops_px) + self.point:
                # live valid(): the broker needs spread/stops level + 1 point between price and the new SL
                self.sl = lock
                self.be_done = True
        return None

    def close_price(self, bar_close: float, spread_px: float) -> float:
        return bar_close if self.sign > 0 else bar_close + spread_px


def simulate(side: str, sl: float, tp: float, bars: list[dict], point: float, decision_ts: float, cfg: dict,
             now_ts: float, slip_px: float = 0.0, spread_fn=None, exit_policy=None) -> dict | None:
    """Label one setup from bars after the decision. Returns dict(outcome, outcome_r, mfe_r, mae_r, entry, exit, ...)
    or None while unresolved. spread_fn(bar) -> spread in price (default: bar sp × point).
    exit_policy: exits.ExitPolicy / {mode, params} (default base); its indicators are seeded with the bars before
    the decision that `bars` contains (closed bars only)."""
    spread_fn = spread_fn or (lambda b: float(b.get("sp") or 0) * point)
    tf_sec = int(cfg.get("_bar_sec") or 60)
    max_hold = int(cfg.get("max_hold_minutes") or 0) * 60
    horizon = int(cfg.get("_horizon_s") or 0)  # labeling cap when there is no time stop -> outcome HORIZON
    be_r = float(cfg.get("break_even_at_r") or 0)
    be_lock_r = float(cfg.get("be_lock_r") or 0)
    after = [b for b in bars if b["t"] >= decision_ts]
    if not after:
        return None
    eb = after[0]
    sp0 = spread_fn(eb)
    entry = eb["o"] + (sp0 if side == "BUY" else 0.0)
    pos = SimPosition(side, entry, sl, tp, point, int(eb["t"]), be_at_r=be_r, slip_px=slip_px,
                      stops_px=float(cfg.get("_stops_px") or 0), be_lock_r=be_lock_r)
    if exit_policy:
        from .exits import WARMUP_BARS, ExitPolicy, attach
        before = [b for b in bars if b["t"] < decision_ts][-WARMUP_BARS:]
        attach(pos, ExitPolicy.from_spec(exit_policy), history=before, bar_sec=tf_sec)
    if not pos.valid or (tp - entry) * pos.sign <= 0:
        # the market opened beyond the stop (or the target): nothing tradeable at these levels
        return {"outcome": "SL" if not pos.valid else "TP", "outcome_r": -1.0 if not pos.valid else 0.0,
                "mfe_r": 0.0, "mae_r": 0.0, "entry": entry, "exit": entry, "entry_t": eb["t"], "exit_t": eb["t"],
                "gap": True}
    deadline = eb["t"] + max_hold if max_hold > 0 else (eb["t"] + horizon if horizon > 0 else None)
    t_reason = "TIME" if max_hold > 0 else "HORIZON"
    last, reached = None, False
    for b in after:
        if deadline is not None and b["t"] >= deadline:
            reached = True  # data gap across the deadline (weekend): close at the last price before it
            break
        sp = spread_fn(b)
        res = pos.step(b, sp)
        last = (b, sp)
        if res:
            reason, px = res
            return _result(pos, reason, px, b["t"])
        if deadline is not None and b["t"] + tf_sec >= deadline:
            return _result(pos, t_reason, pos.close_price(b["c"], sp), b["t"])
    if deadline is not None and last is not None and (reached or now_ts >= deadline + NODATA_GRACE_S):
        b, sp = last
        return _result(pos, t_reason, pos.close_price(b["c"], sp), b["t"])
    return None


def _result(pos: SimPosition, reason: str, px: float, t: int) -> dict:
    return {"outcome": reason, "outcome_r": round(pos.total_r(px), 4), "mfe_r": round(pos.mfe / pos.r_dist, 4),
            "mae_r": round(pos.mae / pos.r_dist, 4), "entry": pos.entry, "exit": px, "entry_t": pos.opened_t,
            "exit_t": t, "gap": False}


def _spread_stats(db) -> dict[tuple[str, int], float]:
    from .backtest import load_spread_stats  # local import: backtest imports this module
    try:
        return load_spread_stats(db)
    except Exception:
        return {}


def label_pending(db, get_bars, cfg: dict, now_ts: float, limit: int = 500) -> int:
    """Resolve analyses rows with a BUY/SELL candidate and no outcome yet. Returns the number of rows labeled.
    get_bars(symbol, tf, from_ts, to_ts) -> closed bars [{t,o,h,l,c,sp}] ascending.
    Costs: cfg spread_mode "stats" (default: live spread per UTC hour, else max(bar sp, 1)) | "bar" (bar sp as is),
    cfg slippage_pts (default LABEL_SLIPPAGE_PTS) on stop exits."""
    rows = db.query(
        "SELECT id, symbol, tf, bar_time, candidate_side, entry, sl, tp FROM analyses "
        "WHERE outcome IS NULL AND candidate_side IN ('BUY','SELL') AND sl IS NOT NULL AND tp IS NOT NULL "
        "ORDER BY bar_time ASC LIMIT ?", (int(limit),))
    if not rows:
        return 0
    max_hold = int(cfg.get("max_hold_minutes") or 0) * 60
    horizon = max_hold if max_hold > 0 else LABEL_HORIZON_S
    stats = _spread_stats(db) if str(cfg.get("spread_mode") or "stats") == "stats" else None
    slip_pts = float(cfg["slippage_pts"]) if cfg.get("slippage_pts") is not None else LABEL_SLIPPAGE_PTS
    pref_tf = str(cfg.get("label_tf") or "M1").upper()
    by_sym: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        by_sym.setdefault((r["symbol"], r["tf"]), []).append(r)
    updates = []
    for (sym, tf), items in by_sym.items():
        sig_sec = TF_SECONDS.get(tf, 300)
        t_from = min(int(r["bar_time"]) + sig_sec for r in items)
        t_to = max(int(r["bar_time"]) + sig_sec for r in items) + horizon + sig_sec
        lbl_tf, bars = pref_tf, []
        if pref_tf in TF_SECONDS:
            bars = get_bars(sym, pref_tf, t_from, t_to) or []
        if not bars:
            lbl_tf, bars = tf, get_bars(sym, tf, t_from, t_to) or []
        lsec = TF_SECONDS.get(lbl_tf, sig_sec)
        bars = [b for b in bars if b["t"] + lsec <= now_ts]
        point, spread_fn = None, None
        lcfg = {"max_hold_minutes": cfg.get("max_hold_minutes"), "break_even_at_r": cfg.get("break_even_at_r"),
                "be_lock_r": cfg.get("be_lock_r"), "_bar_sec": lsec, "_horizon_s": LABEL_HORIZON_S if max_hold <= 0 else 0}
        points = cfg.get("points") or {}
        for r in items:
            decision = int(r["bar_time"]) + sig_sec
            if point is None:
                ref = r["entry"] or (bars[0]["c"] if bars else None)
                point = float(points.get(sym) or guess_point(sym, ref))
                spread_fn = _spread_fn(stats, sym, point)
            res = simulate(r["candidate_side"], float(r["sl"]), float(r["tp"]), bars, point, decision, lcfg, now_ts,
                           slip_px=slip_pts * point, spread_fn=spread_fn)
            if res is None:
                if now_ts >= decision + horizon + NODATA_GRACE_S and not any(b["t"] >= decision for b in bars):
                    updates.append(("NODATA", None, None, None, now_ts, r["id"]))
                continue
            updates.append((res["outcome"], res["outcome_r"], res["mfe_r"], res["mae_r"], now_ts, r["id"]))
    if updates:
        db.executemany("UPDATE analyses SET outcome=?, outcome_r=?, mfe_r=?, mae_r=?, labeled_ts=? WHERE id=?", updates)
    return len(updates)


def _spread_fn(stats: dict | None, sym: str, point: float):
    if stats is None:
        return lambda b: float(b.get("sp") or 0) * point
    # same as the backtest's "stats" cost mode: bar sp is the bar's MINIMUM spread, never below 1 point
    return lambda b: stats.get((sym, int(b["t"] % 86400 // 3600)), max(float(b.get("sp") or 0), 1.0)) * point


__all__ = ["SimPosition", "simulate", "label_pending", "guess_point", "be_lock_level"]
