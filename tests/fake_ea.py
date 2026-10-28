"""Fake MT5 EA: drives the server through the §3 bridge protocol end to end.

Live usage (server started with DATA_SOURCE=mt5_bridge):
    .venv/bin/python -m tests.fake_ea http://127.0.0.1:8013
Keys are read from .env (MT5_BRIDGE_KEY, ADMIN_TOKEN). Also used by tests/test_integration_ea.py
with a FastAPI TestClient instead of a live server.
"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

MAGIC = 552211
TF_SEC = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
SYMBOL_INFO = {
    "point": 0.00001, "digits": 5, "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
    "stops_level": 0, "freeze_level": 0, "tick_value": 1.0, "tick_size": 0.00001, "contract_size": 100000,
    "trade_mode": 4, "filling_mode": 3,
}


def parse_lines(text: str) -> list[list[str]]:
    return [ln.split("|") for ln in text.strip().splitlines() if ln.strip()]


def gen_bars(symbol: str, tf: str, n: int, now: float, last_price: float, seed: int = 7) -> list[dict]:
    """n bars ending with the forming bar; random walk that ends at last_price."""
    rnd = random.Random(f"{seed}-{symbol}-{tf}")
    sec = TF_SEC[tf]
    step = 0.00012 * (sec / 60) ** 0.5
    closes = [last_price]
    for _ in range(n - 1):
        closes.append(closes[-1] - rnd.gauss(0.00002 * (sec / 60) ** 0.5, step))
    closes.reverse()
    t_last = int(now) // sec * sec
    bars, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        bars.append({"symbol": symbol, "tf": tf, "t": t_last - (n - 1 - i) * sec, "o": round(o, 5),
                     "h": round(max(o, c) + abs(rnd.gauss(0, step / 3)), 5),
                     "l": round(min(o, c) - abs(rnd.gauss(0, step / 3)), 5), "c": round(c, 5),
                     "tv": rnd.randint(50, 900), "sp": 10})
        prev = c
    return bars


class FakeEA:
    def __init__(self, post, bridge_key: str, bid: float = 1.08000):
        """post(path, json, headers) -> (status_code, text); a thin adapter over httpx or TestClient."""
        self._post = post
        self.key = bridge_key
        self.bid = bid
        self.symbols = ["EURUSD"]
        self.tfs = ["M5"]
        self.hist: list[tuple[str, str]] = []
        self.positions: list[dict] = []
        self.deals: list[dict] = []
        self.results: list[dict] = []
        self.executed: set[str] = set()

    def payload(self, history_for: list[tuple[str, str]] | None = None) -> dict:
        now = time.time()
        ask = round(self.bid + 0.00010, 5)
        bars = []
        for s in self.symbols:
            for tf in self.tfs:
                n = 300 if history_for and (s, tf) in history_for else 3
                bars += gen_bars(s, tf, n, now, self.bid)
        return {
            "ea_version": "1.00", "magic": MAGIC, "time_utc": int(now),
            "terminal": {"connected": True, "trade_allowed": True, "build": 4620},
            "account": {"login": 52123456, "server": "MetaQuotes-Demo", "company": "MetaQuotes Ltd.", "trade_mode": 0,
                        "currency": "USD", "balance": 10000.0, "equity": 10000.0 + sum(p["profit"] for p in self.positions),
                        "margin": 0.0, "margin_free": 10000.0, "leverage": 100, "profit": 0.0,
                        "trade_allowed": True, "trade_expert": True, "margin_mode": 2},
            "symbols": [dict(SYMBOL_INFO, symbol=s, bid=self.bid, ask=ask, time_msc=int(now * 1000), spread_points=10)
                        for s in self.symbols],
            "bars": bars, "positions": list(self.positions), "deals": list(self.deals), "results": list(self.results),
        }

    def sync(self, history: bool = False) -> list[list[str]]:
        code, text = self._post("/bridge/v1/sync", self.payload(self.hist if history else None),
                                {"X-Bridge-Key": self.key})
        assert code == 200, f"sync HTTP {code}: {text}"
        self.results = []  # delivered: EA clears its buffer after HTTP 200
        lines = parse_lines(text)
        assert lines[0][0] == "OK" and lines[0][1].isdigit(), lines[0]
        for p in lines:
            if p[0] == "SYMBOLS":
                self.symbols = [x for x in p[1].split(",") if x]
            elif p[0] == "TF":
                self.tfs = [x for x in p[1].split(",") if x in TF_SEC]
        self.hist = [(p[1], p[2]) for p in lines if p[0] == "HIST"]
        return lines


def run_scenario(ea: FakeEA, admin, log=print) -> dict:
    """admin(method, path, body) -> (status_code, json). Returns collected facts; raises AssertionError on failure."""
    # 1) handshake: OK / SYMBOLS / TF / HIST
    lines = ea.sync()
    kinds = [p[0] for p in lines]
    assert kinds[:3] == ["OK", "SYMBOLS", "TF"], kinds
    assert "EURUSD" in ea.symbols and "M5" in ea.tfs, lines
    assert ("EURUSD", "M5") in ea.hist, f"expected HIST|EURUSD|M5, got {ea.hist}"
    log(f"handshake OK: SYMBOLS={ea.symbols} TF={ea.tfs} HIST={len(ea.hist)} perechi")
    ea.sync(history=True)  # send full history for every HIST pair
    code, cd = admin("GET", "/api/v1/candles?symbol=EURUSD&tf=M5&limit=300", None)
    assert code == 200 and len(cd["bars"]) >= 100, f"candles after HIST: {code} {len(cd.get('bars', []))}"
    log(f"istoric primit: {len(cd['bars'])} bare M5")

    code, st = admin("GET", "/api/v1/status", None)
    assert st["bridge"]["connected"] and st["account"]["is_demo"] and st["account"]["login_masked"] == "****3456", st
    assert st["feed_label"] == "MT5_FEED"

    # 2) DEMO + arm + test order
    code, r = admin("POST", "/api/v1/control/mode", {"mode": "DEMO"})
    assert code == 200 and r["mode"] == "DEMO", (code, r)
    code, r = admin("POST", "/api/v1/control/execution", {"enabled": True})
    assert code == 200 and r["execution_enabled"], (code, r)
    code, intent = admin("POST", "/api/v1/control/test-order", {"symbol": "EURUSD", "side": "BUY"})
    assert code == 200, (code, intent)
    assert intent["risk_check"]["passed"], [c for c in intent["risk_check"]["checks"] if not c["ok"]]
    assert intent["state"] == "QUEUED" and intent["mode"] == "DEMO", intent
    cmd_id = intent["id"]
    log(f"test-order: intent {cmd_id[:8]} QUEUED vol={intent['volume']} SL={intent['sl']} TP={intent['tp']}")

    # 3) OPEN delivered exactly once
    lines = ea.sync()
    opens = [p for p in lines if p[0] == "OPEN"]
    assert len(opens) == 1 and opens[0][1] == cmd_id, lines
    _, cid, sym, side, vol, sl, tp, dev, comment = opens[0]
    assert (sym, side) == ("EURUSD", "BUY") and float(sl) < ea.bid < float(tp) and comment == f"ai:{cmd_id[:8]}"
    ea.executed.add(cid)
    again = [p for p in ea.sync() if p[0] in ("OPEN", "MODIFY", "CLOSE")]
    assert not again, f"command re-sent: {again}"
    code, its = admin("GET", "/api/v1/intents?limit=10", None)
    assert next(i for i in its if i["id"] == cmd_id)["state"] == "DELIVERED"
    log(f"OPEN livrat o singură dată: {'|'.join(opens[0])}")

    # 4) result + position
    ticket, now = 50001234, int(time.time())
    price = round(ea.bid + 0.0001, 5)
    ea.results = [{"cmd_id": cid, "ok": True, "retcode": 10009, "retcode_desc": "done", "order": 70001, "deal": 80001,
                   "position": ticket, "price": price, "volume": float(vol), "comment": "Request executed"}]
    ea.positions = [{"ticket": ticket, "symbol": "EURUSD", "type": 0, "volume": float(vol), "price_open": price,
                     "sl": float(sl), "tp": float(tp), "price_current": ea.bid, "profit": -0.1, "swap": 0.0,
                     "magic": MAGIC, "comment": comment, "time": now},
                    {"ticket": 42, "symbol": "EURUSD", "type": 1, "volume": 0.1, "price_open": 1.081, "sl": 0.0,
                     "tp": 0.0, "price_current": ea.bid, "profit": 3.0, "swap": 0.0, "magic": 0, "comment": "",
                     "time": now - 3600}]
    ea.sync()
    code, its = admin("GET", "/api/v1/intents?limit=10", None)
    it = next(i for i in its if i["id"] == cmd_id)
    assert it["state"] == "FILLED" and it["ticket"] == ticket and it["result"]["retcode"] == 10009, it
    code, pos = admin("GET", "/api/v1/positions", None)
    mine = [p for p in pos["open"] if p["ticket"] == ticket]
    manual = [p for p in pos["open"] if p["ticket"] == 42]
    assert mine and mine[0]["bot_owned"] and mine[0]["source"] == "MT5" and mine[0]["side"] == "BUY", pos
    assert manual and not manual[0]["bot_owned"], pos
    log(f"rezultat 10009 → FILLED, poziția #{ticket} vizibilă (bot_owned), poziția manuală #42 bot_owned=false")

    # 5) position closed at TP: deal IN + deal OUT
    close_price = float(tp)
    ea.positions = [p for p in ea.positions if p["ticket"] != ticket]
    ea.deals = [
        {"ticket": 80001, "order": 70001, "position_id": ticket, "symbol": "EURUSD", "type": 0, "entry": 0,
         "volume": float(vol), "price": price, "profit": 0.0, "commission": -0.035, "swap": 0.0, "magic": MAGIC,
         "reason": 3, "time": now, "comment": comment},
        {"ticket": 80002, "order": 70002, "position_id": ticket, "symbol": "EURUSD", "type": 1, "entry": 1,
         "volume": float(vol), "price": close_price, "profit": round((close_price - price) * 100000 * float(vol), 2),
         "commission": -0.035, "swap": 0.0, "magic": MAGIC, "reason": 5, "time": now + 60, "comment": f"[tp {tp}]"},
    ]
    ea.sync()
    ea.sync()  # deals are re-sent every sync (last 24 h) → must not duplicate
    code, trades = admin("GET", "/api/v1/trades?limit=10", None)
    mine = [t for t in trades if t["ticket"] == ticket]
    assert len(mine) == 1, trades
    t = mine[0]
    assert t["close_reason"] == "TP" and t["mode"] == "DEMO" and t["side"] == "BUY" and t["price_open"] == price, t
    code, pos = admin("GET", "/api/v1/positions", None)
    assert not [p for p in pos["open"] if p["ticket"] == ticket]
    log(f"deal OUT → trade #{ticket} TP profit={t['profit']} (fără duplicate)")

    admin("POST", "/api/v1/control/execution", {"enabled": False})
    return {"intent_id": cmd_id, "ticket": ticket, "trade": t}


def _read_env(path: Path) -> dict:
    out = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        if "=" in ln and not ln.lstrip().startswith("#"):
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main(base: str) -> int:
    import httpx
    env = _read_env(Path(__file__).resolve().parent.parent / ".env")
    admin_hdr = {"X-Admin-Token": env.get("ADMIN_TOKEN", "")}
    cli = httpx.Client(base_url=base, timeout=10)

    def post(path, body, headers):
        r = cli.post(path, json=body, headers=headers)
        return r.status_code, r.text

    def admin(method, path, body):
        r = cli.request(method, path, json=body, headers=admin_hdr if method != "GET" else None)
        return r.status_code, r.json()

    run_scenario(FakeEA(post, env.get("MT5_BRIDGE_KEY", "")), admin)
    print("FAKE EA: toate verificările au trecut")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8013"))
