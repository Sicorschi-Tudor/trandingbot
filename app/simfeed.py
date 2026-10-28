"""SIMULATED feed (random walk with trending segments). Everything it produces is labeled SIMULATED."""
from __future__ import annotations

import asyncio
import math
import random
import time

from .config import TF_SECONDS
from .state import Runtime

SPECS = {
    "EURUSD": {"price": 1.0800, "digits": 5, "point": 0.00001, "tick_size": 0.00001, "tick_value": 1.0,
               "contract_size": 100000, "spread": (6, 11), "sigma": 8e-6},
    "GBPUSD": {"price": 1.2700, "digits": 5, "point": 0.00001, "tick_size": 0.00001, "tick_value": 1.0,
               "contract_size": 100000, "spread": (8, 13), "sigma": 9e-6},
    "XAUUSD": {"price": 2650.00, "digits": 2, "point": 0.01, "tick_size": 0.01, "tick_value": 1.0,
               "contract_size": 100, "spread": (15, 25), "sigma": 1.1e-5},
}
DEFAULT_SPEC = {"price": 1.0, "digits": 5, "point": 0.00001, "tick_size": 0.00001, "tick_value": 1.0,
                "contract_size": 100000, "spread": (6, 11), "sigma": 8e-6}
SIM_TFS = ["M1", "M5", "M15", "M30", "H1", "H4"]
TICK_INTERVAL = 0.25
TICKS_PER_MIN = int(60 / TICK_INTERVAL)
HISTORY_MINUTES = 7 * 24 * 60
SUBSTEPS = 12


class SimSymbol:
    def __init__(self, symbol: str, rng: random.Random):
        self.symbol = symbol
        self.spec = dict(SPECS.get(symbol, DEFAULT_SPEC))
        self.rng = rng
        self.price = self.spec["price"]
        self.drift = 0.0
        self.regime_left = 0.0  # seconds
        self.cur: dict[str, dict] = {}
        self.spread = self.spec["spread"][0]

    def _new_regime(self) -> None:
        r = self.rng.random()
        k = 0.012 * self.spec["sigma"]
        self.drift = k if r < 0.33 else (-k if r < 0.66 else 0.0)
        self.regime_left = self.rng.uniform(40, 240) * 60

    def step(self, dt_ticks: float = 1.0) -> None:
        """advance price by dt_ticks tick-equivalents."""
        self.regime_left -= dt_ticks * TICK_INTERVAL
        if self.regime_left <= 0:
            self._new_regime()
        s = self.spec["sigma"] * math.sqrt(dt_ticks)
        self.price *= 1.0 + self.drift * dt_ticks + self.rng.gauss(0.0, s)
        lo, hi = self.spec["spread"]
        if self.rng.random() < 0.1:
            self.spread = self.rng.randint(lo, hi)

    def bid_ask(self) -> tuple[float, float]:
        d, p = self.spec["digits"], self.spec["point"]
        bid = round(self.price, d)
        return bid, round(bid + self.spread * p, d)

    def info(self, now: float) -> dict:
        bid, ask = self.bid_ask()
        sp = self.spec
        return {"bid": bid, "ask": ask, "time_msc": int(now * 1000), "spread_points": self.spread,
                "point": sp["point"], "digits": sp["digits"], "volume_min": 0.01, "volume_max": 100.0,
                "volume_step": 0.01, "stops_level": 0, "freeze_level": 0, "tick_value": sp["tick_value"],
                "tick_size": sp["tick_size"], "contract_size": sp["contract_size"], "trade_mode": 4,
                "filling_mode": 3, "simulated": True}

    def history(self, end_minute: int) -> dict[str, list[dict]]:
        """generate M1 history ending before end_minute (unix s), aggregate to SIM_TFS."""
        m1: list[dict] = []
        start = end_minute - HISTORY_MINUTES * 60
        sub = TICKS_PER_MIN / SUBSTEPS
        d = self.spec["digits"]
        for i in range(HISTORY_MINUTES):
            t = start + i * 60
            o = h = l = self.price
            for _ in range(SUBSTEPS):
                self.step(sub)
                h, l = max(h, self.price), min(l, self.price)
            m1.append({"t": t, "o": round(o, d), "h": round(h, d), "l": round(l, d), "c": round(self.price, d),
                       "tv": self.rng.randint(20, 160), "sp": self.spread})
        out = {"M1": m1}
        for tf in SIM_TFS[1:]:
            sec = TF_SECONDS[tf]
            agg: list[dict] = []
            for b in m1:
                bt = b["t"] // sec * sec
                if agg and agg[-1]["t"] == bt:
                    a = agg[-1]
                    a["h"], a["l"], a["c"] = max(a["h"], b["h"]), min(a["l"], b["l"]), b["c"]
                    a["tv"] += b["tv"]
                else:
                    agg.append(dict(b, t=bt))
            out[tf] = agg
        return out


class SimFeed:
    def __init__(self, rt: Runtime, seed: int | None = None):
        self.rt = rt
        self.rng = random.Random(seed)
        self.syms: dict[str, SimSymbol] = {}

    def ensure_symbols(self) -> None:
        now = time.time()
        for s in self.rt.active_symbols():
            if s in self.syms:
                continue
            ss = SimSymbol(s, random.Random(self.rng.random()))
            end_minute = int(now // 60 * 60)
            hist = ss.history(end_minute)
            for tf, bars in hist.items():
                self.rt.ingest_history(s, tf, bars[-2000:])
                if bars:
                    last = bars[-1]
                    if last["t"] + TF_SECONDS[tf] > now:
                        ss.cur[tf] = dict(last)
            self.syms[s] = ss
            self.rt.log("INFO", "simfeed", f"Feed SIMULAT pornit pentru {s} ({HISTORY_MINUTES // 60} ore istoric generat)")

    def tick(self, now: float | None = None) -> None:
        now = now or time.time()
        active = set(self.rt.active_symbols())
        for s, ss in self.syms.items():
            if s not in active:
                continue
            ss.step()
            info = ss.info(now)
            bid = info["bid"]
            for tf in SIM_TFS:
                sec = TF_SECONDS[tf]
                bt = int(now // sec * sec)
                b = ss.cur.get(tf)
                if b is None or b["t"] != bt:
                    o = b["c"] if b else bid
                    b = {"t": bt, "o": o, "h": max(o, bid), "l": min(o, bid), "c": bid, "tv": 0, "sp": ss.spread}
                    ss.cur[tf] = b
                b["h"], b["l"], b["c"] = max(b["h"], bid), min(b["l"], bid), bid
                b["tv"] += 1
                b["sp"] = ss.spread
                self.rt.ingest_bar(s, tf, dict(b))
            self.rt.ingest_quote(s, info)

    async def run(self) -> None:
        self.ensure_symbols()
        while True:
            try:
                self.ensure_symbols()
                self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover
                self.rt.log("ERROR", "simfeed", f"tick error: {e!r}")
            await asyncio.sleep(TICK_INTERVAL)
