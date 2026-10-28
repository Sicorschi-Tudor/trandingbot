"""Exit policies for simulated trades (labeler.SimPosition, backtest) — Stage C research.

ExitPolicy(mode, params) + step(pos, bar, spread_px): one bar of a SimPosition under the policy.
Bar semantics (same conventions as the base simulation in app/labeler.py):
- intrabar first: SL (gap/slippage rules of the base), then partial close, then TP, then break-even
  (`break_even_at_r` of the strategy applies in every mode; 0 disables it),
- then bar-close decisions on the CLOSED bar (close = bid for BUY, ask for SELL): invalidation, stall, giveback
  close at that price; trailing moves the SL from the next bar on (like a live MODIFY after the bar closed).
Indicators used by exits (EMA20, MACD(12,26,9) histogram, ATR14) are updated incrementally from the bars fed to
step (seeded with closed bars before the entry), on the timeframe of those bars — no lookahead.

Modes:
- base: SL/TP, BE at break_even_at_r (lock entry ± 2 pts), time stop (handled by the caller) = live behaviour.
- trailing: once the best close is ≥ trail_start_r, SL = best close ∓ max(trail_atr_mult×ATR, trail_min_r×R);
  only tightens, needs spread/stops level + 1 pt of room like a live MODIFY.
- partial: close partial_frac at +partial_r (level price), move the rest to BE, keep TP. Result in R is
  partial_frac×partial_r + rest×R(exit). In USD sizing the caller disables it when the volume cannot be split
  (volume < 2×volume_min); in R mode it is always simulated.
- invalidation: after min_bars closed bars, close when the close crosses EMA20 against the trade
  or the MACD histogram flips sign against it.
- stall: at the first closed bar ≥ stall_minutes after the entry, close if -stall_band_r ≤ R ≤ +stall_band_r.
- giveback: once MFE ≥ gb_arm_r, close at a bar close where R ≤ MFE×(1-gb_frac).
- combo: params.modes = list of the modes above, applied together (shared params).
Stage C2 (higher timeframes, long holds):
- channel: Turtle-style exit at the bar close: BUY closes when the close is below the lowest low of the previous
  channel_n bars (the bar itself excluded), SELL when it is above the highest high. Buffer seeded from the
  warm-up history, so the first bars of the trade already see a full channel.
- signal_flip: at every closed bar asks the strategy for its direction (`bias_fn(bar)` given to attach, computed
  only from closed bars up to that bar); closes when it is the opposite of the trade (flip_on_flat=1: also when
  it is 0). Without a bias_fn (e.g. shadow labels) the mode does nothing.
- time stop for long holds: params max_hold_minutes / max_hold_hours / max_hold_days (any mode, also base; up
  to 60 days, 0 = none) override the strategy's max_hold_minutes in the backtest (hold_minutes()).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

MODES = ("base", "trailing", "partial", "invalidation", "stall", "giveback", "channel", "signal_flip", "combo")
DEFAULT_PARAMS: dict[str, dict] = {
    "base": {},
    "trailing": {"trail_start_r": 1.0, "trail_atr_mult": 1.5, "trail_min_r": 0.5},
    "partial": {"partial_frac": 0.5, "partial_r": 1.0},
    "invalidation": {"min_bars": 3},
    "stall": {"stall_minutes": 60, "stall_band_r": 0.3},
    "giveback": {"gb_arm_r": 1.0, "gb_frac": 0.5},
    "channel": {"channel_n": 20},
    "signal_flip": {"flip_on_flat": 0},
    "combo": {"modes": ["trailing", "stall"]},
}
AT_CLOSE_REASONS = ("INVALIDATION", "STALL", "GIVEBACK", "CHANNEL", "FLIP")  # exits at the bar close (time = bar end)
HOLD_KEYS = {"max_hold_minutes": 1.0, "max_hold_hours": 60.0, "max_hold_days": 1440.0}  # -> minutes
MAX_HOLD_MINUTES = 60 * 1440  # 60 days
WARMUP_BARS = 200  # closed bars before the entry used to seed the exit indicators
BE_LOCK_POINTS = 2  # = labeler.BE_LOCK_MIN_POINTS (position_manager.plan)


class _Ema:
    """Incremental twin of indicators.ema (SMA seed over the first `period` values)."""
    __slots__ = ("period", "k", "n", "acc", "value")

    def __init__(self, period: int):
        self.period, self.k = period, 2.0 / (period + 1)
        self.n, self.acc, self.value = 0, 0, None

    def update(self, v: float) -> float | None:
        self.n += 1
        if self.n < self.period:
            self.acc += v
        elif self.n == self.period:
            self.acc += v
            self.value = self.acc / self.period
        else:
            self.value = v * self.k + self.value * (1 - self.k)
        return self.value


class IncIndicators:
    """EMA20, MACD(12,26,9) histogram and ATR14, bar by bar; same values as app.indicators on the same series."""

    def __init__(self) -> None:
        self.e20, self.e12, self.e26, self.sig = _Ema(20), _Ema(12), _Ema(26), _Ema(9)
        self.n = 0
        self._tr_n, self._tr_acc = 0, 0
        self.close = self.prev_close = None
        self.ema20 = self.prev_ema20 = None
        self.hist = self.prev_hist = None
        self.atr = None

    def update(self, bar: dict) -> None:
        h, l, c = bar["h"], bar["l"], bar["c"]
        tr = h - l if self.close is None else max(h - l, abs(h - self.close), abs(l - self.close))
        self._tr_n += 1
        if self._tr_n < 14:
            self._tr_acc += tr
        elif self._tr_n == 14:
            self._tr_acc += tr
            self.atr = self._tr_acc / 14
        else:
            self.atr = (self.atr * 13 + tr) / 14
        self.prev_close, self.close = self.close, c
        self.prev_ema20, self.ema20 = self.ema20, self.e20.update(c)
        f, s = self.e12.update(c), self.e26.update(c)
        self.prev_hist = self.hist
        if f is not None and s is not None:
            line = f - s
            sv = self.sig.update(line)
            self.hist = None if sv is None else line - sv
        self.n += 1

    def seed(self, bars: list[dict]) -> "IncIndicators":
        for b in bars:
            self.update(b)
        return self


@dataclass
class ExitPolicy:
    mode: str = "base"
    params: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.mode = str(self.mode or "base").lower()
        if self.mode not in MODES:
            raise ValueError(f"exit.mode necunoscut: {self.mode} (unul din {', '.join(MODES)})")
        raw = dict(self.params or {})
        if self.mode == "combo":
            modes = [str(m).lower() for m in (raw.get("modes") or [])]
            if not modes or any(m not in MODES or m == "combo" for m in modes):
                raise ValueError("exit combo: params.modes = listă de moduri (fără combo)")
        else:
            modes = [self.mode]
        self.modes = tuple(m for m in dict.fromkeys(modes) if m != "base")
        p: dict = {}
        for m in self.modes:
            p.update(DEFAULT_PARAMS[m])
        for k, v in raw.items():
            if k == "modes":
                continue
            try:
                p[k] = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"exit.params.{k}: număr invalid ({v!r})") from None
        if "partial" in self.modes and not 0 < p["partial_frac"] < 1:
            raise ValueError("exit.params.partial_frac trebuie să fie între 0 și 1")
        if "giveback" in self.modes and not 0 < p["gb_frac"] <= 1:
            raise ValueError("exit.params.gb_frac trebuie să fie în (0, 1]")
        if "channel" in self.modes and not 1 <= p["channel_n"] <= 1000:
            raise ValueError("exit.params.channel_n trebuie să fie între 1 și 1000")
        hold = sum(1 for k in HOLD_KEYS if k in p)
        if hold > 1:
            raise ValueError("exit.params: un singur câmp dintre max_hold_minutes / max_hold_hours / max_hold_days")
        for k, mult in HOLD_KEYS.items():
            if k in p and not 0 <= p[k] * mult <= MAX_HOLD_MINUTES:
                raise ValueError(f"exit.params.{k}: între 0 și 60 de zile (0 = fără limită)")
        self.p = p

    @classmethod
    def from_spec(cls, spec) -> "ExitPolicy":
        if isinstance(spec, ExitPolicy):
            return spec
        if not spec:
            return cls()
        if isinstance(spec, str):
            return cls(spec)
        return cls(spec.get("mode") or "base", spec.get("params") or {})

    @property
    def is_base(self) -> bool:
        return not self.modes

    @property
    def needs_indicators(self) -> bool:
        return any(m in ("trailing", "invalidation") for m in self.modes)

    @property
    def hold_minutes(self) -> float | None:
        """Time stop from the exit params in minutes (0 = none), None when the params do not set one."""
        for k, mult in HOLD_KEYS.items():
            if k in self.p:
                return self.p[k] * mult
        return None

    def to_dict(self) -> dict:
        d = {"mode": self.mode, "params": dict(self.p)}
        if self.mode == "combo":
            d["params"]["modes"] = list(self.modes)
        return d


def attach(pos, policy: ExitPolicy | None, history: list[dict] | None = None, bar_sec: int = 60,
           partial_frac: float | None = None, bias_fn=None) -> None:
    """Arm a SimPosition with a non-base policy (base -> the labeler's own code path, bit-identical).
    history: closed bars before the entry bar (indicator warm-up). partial_frac: effective fraction after lot
    rounding (0 disables the partial close), default the policy's. bias_fn(bar) -> +1/-1/0: the strategy's
    direction at the close of `bar` (signal_flip; closed bars only)."""
    if policy is None or policy.is_base:
        return
    pos.exit = policy
    pos.bar_sec = int(bar_sec)
    if pos.ind is None and policy.needs_indicators:
        pos.ind = IncIndicators().seed(history or [])
    frac = policy.p.get("partial_frac", 0.0) if partial_frac is None else partial_frac
    pos.xs = {"bars": 0, "best": None, "trailed": False, "partial_frac": float(frac) if "partial" in policy.modes else 0.0,
              "partial_done": False, "be_pending": False, "stall_checked": False}
    if "channel" in policy.modes:
        n = int(policy.p["channel_n"])
        pos.xs["chan_n"] = n
        pos.xs["chan"] = deque(((b["h"], b["l"]) for b in (history or [])[-n:]), maxlen=n)
    if "signal_flip" in policy.modes:
        pos.xs["bias_fn"] = bias_fn


def _try_be(pos, fav_px: float, spread_px: float) -> None:
    sg = pos.sign
    lock = pos.entry + sg * BE_LOCK_POINTS * pos.point
    if (lock - pos.sl) * sg <= 0:
        pos.be_done = True
    elif (fav_px - lock) * sg >= max(spread_px, pos.stops_px) + pos.point:
        pos.sl = lock
        pos.be_done = True


def step(pos, bar: dict, spread_px: float) -> tuple[str, float] | None:
    """One bar of `pos` (labeler.SimPosition armed by attach) under its policy. Indicators were already updated
    with this bar by the caller. Returns (reason, exit_price) when the (rest of the) position closes."""
    pol: ExitPolicy = pos.exit
    p, xs, modes = pol.p, pos.xs, pol.modes
    sg = pos.sign
    if sg > 0:
        lo, hi, op = bar["l"], bar["h"], bar["o"]
    else:
        lo, hi, op = bar["l"] + spread_px, bar["h"] + spread_px, bar["o"] + spread_px
    fav_px = hi if sg > 0 else lo
    adverse = (pos.entry - lo) if sg > 0 else (hi - pos.entry)
    favour = (hi - pos.entry) if sg > 0 else (pos.entry - lo)
    xs["bars"] += 1
    hit_sl = lo <= pos.sl if sg > 0 else hi >= pos.sl
    if hit_sl:
        pos.mae = max(pos.mae, adverse)
        px = min(pos.sl, op) if sg > 0 else max(pos.sl, op)
        px -= sg * pos.slip_px
        reason = "TRAIL" if xs["trailed"] else ("BE" if pos.be_done else "SL")
        return reason, px
    pos.mae = max(pos.mae, adverse)
    pos.mfe = max(pos.mfe, favour)
    r_dist = pos.r_dist
    # partial close at +partial_r (before the TP, which is further away)
    if xs["partial_frac"] > 0 and not xs["partial_done"] and favour >= p["partial_r"] * r_dist - pos.point / 2:
        ppx = pos.entry + sg * p["partial_r"] * r_dist
        if (ppx - pos.tp) * sg < 0:
            pos.book_partial(xs["partial_frac"], ppx)
            xs["partial_done"] = True
            xs["be_pending"] = True
    hit_tp = hi >= pos.tp if sg > 0 else lo <= pos.tp
    if hit_tp:
        return "TP", pos.tp
    if pos.be_at_r > 0 and not pos.be_done and r_dist > 0 and favour >= pos.be_at_r * r_dist - pos.point / 2:
        _try_be(pos, fav_px, spread_px)
    if xs["be_pending"] and not pos.be_done:
        _try_be(pos, fav_px, spread_px)

    # ---- decisions on the closed bar
    cpx = pos.close_price(bar["c"], spread_px)
    r_now = pos.r_of(cpx)
    ind = pos.ind
    if "invalidation" in modes and ind is not None and xs["bars"] >= int(p["min_bars"]):
        c, pc = ind.close, ind.prev_close
        e, pe = ind.ema20, ind.prev_ema20
        if e is not None and pe is not None and pc is not None and (pc - pe) * sg >= 0 > (c - e) * sg:
            return "INVALIDATION", cpx
        h, ph = ind.hist, ind.prev_hist
        if h is not None and ph is not None and ph * sg >= 0 > h * sg:
            return "INVALIDATION", cpx
    if "stall" in modes and not xs["stall_checked"] and bar["t"] + pos.bar_sec - pos.opened_t >= p["stall_minutes"] * 60:
        xs["stall_checked"] = True
        if -p["stall_band_r"] <= r_now <= p["stall_band_r"]:
            return "STALL", cpx
    if "giveback" in modes and r_dist > 0:
        mfe_r = pos.mfe / r_dist
        if mfe_r >= p["gb_arm_r"] and r_now <= mfe_r * (1 - p["gb_frac"]):
            return "GIVEBACK", cpx
    if "channel" in modes:
        buf = xs["chan"]
        full = len(buf) >= xs["chan_n"]
        c = bar["c"]
        crossed = full and (c < min(x[1] for x in buf) if sg > 0 else c > max(x[0] for x in buf))
        buf.append((bar["h"], bar["l"]))  # the current bar joins the channel of the next ones
        if crossed:
            return "CHANNEL", cpx
    if "signal_flip" in modes and xs.get("bias_fn") is not None:
        d = xs["bias_fn"](bar)
        if d is not None and (d * sg < 0 or (d == 0 and p["flip_on_flat"] > 0)):
            return "FLIP", cpx
    if "trailing" in modes and r_dist > 0:
        best = xs["best"]
        if best is None or (cpx - best) * sg > 0:
            best = xs["best"] = cpx
        if pos.r_of(best) >= p["trail_start_r"]:
            atr = ind.atr if ind is not None and ind.atr is not None else 0.0
            new_sl = best - sg * max(p["trail_atr_mult"] * atr, p["trail_min_r"] * r_dist)
            if (new_sl - pos.sl) * sg > pos.point / 2 and (cpx - new_sl) * sg >= max(spread_px, pos.stops_px) + pos.point:
                pos.sl = new_sl
                xs["trailed"] = True
    return None


__all__ = ["ExitPolicy", "IncIndicators", "attach", "step", "MODES", "DEFAULT_PARAMS", "AT_CLOSE_REASONS",
           "WARMUP_BARS", "HOLD_KEYS", "MAX_HOLD_MINUTES"]
