"""Economic calendar fallback: the public ForexFactory weekly JSON feed, used when the MT5 EA cannot deliver a calendar
(Exness: CalendarValueHistory hangs the EA, so InpCalendar=false and the news blackout never applied — 04.11 13:11 and
13:17 two SELLs straddled the ADP release at 13:15 UTC and both hit the SL).

The fetch runs in a background thread (never on the event loop); the engine copies the cached items into rt.calendar
whenever the EA calendar is missing or stale."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime

IMPACT = {"high": 3, "medium": 2, "low": 1}
FETCH_EVERY_S = 4 * 3600.0
RETRY_S = 600.0


def parse_events(raw: list, max_items: int = 400) -> list[dict]:
    """ForexFactory rows -> the bridge calendar item format ({t, ccy, imp, name}); holidays and unknown rows dropped."""
    items = []
    for ev in raw or []:
        if not isinstance(ev, dict):
            continue
        imp = IMPACT.get(str(ev.get("impact") or "").strip().lower())
        ccy = str(ev.get("country") or "").strip().upper()[:8]
        date = str(ev.get("date") or "")
        if not imp or not ccy or not date:
            continue
        try:
            t = int(datetime.fromisoformat(date).timestamp())
        except ValueError:
            continue
        items.append({"t": t, "ccy": ccy, "imp": imp, "name": str(ev.get("title") or "")[:160]})
    items.sort(key=lambda x: x["t"])
    return items[:max_items]


class NewsFeed:
    def __init__(self, url: str, log=None):
        self.url = url
        self.log = log
        self.items: list[dict] = []
        self.fetched_ts: float | None = None
        self.error: str | None = None
        self._next_try = 0.0
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def poll(self, now: float) -> None:
        """Start a fetch in the background when the cache is older than FETCH_EVERY_S (or after a failed try)."""
        if not self.url or now < self._next_try or (self._thread is not None and self._thread.is_alive()):
            return
        if self.fetched_ts is not None and now - self.fetched_ts < FETCH_EVERY_S:
            return
        self._next_try = now + RETRY_S
        self._thread = threading.Thread(target=self._fetch, name="news-feed", daemon=True)
        self._thread.start()

    def _fetch(self) -> None:
        try:
            req = urllib.request.Request(self.url, headers={"User-Agent": "tradingBot/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            items = parse_events(raw)
        except Exception as exc:  # noqa: BLE001 - any network/parse failure is reported, never raised into the loop
            with self._lock:
                self.error = f"{type(exc).__name__}: {exc}"[:200]
            if self.log:
                self.log("WARN", "news", f"Calendarul de rezervă (ForexFactory) nu a putut fi descărcat: {self.error}")
            return
        with self._lock:
            self.items, self.fetched_ts, self.error = items, time.time(), None
        if self.log:
            hi = sum(1 for x in items if x["imp"] >= 3)
            self.log("INFO", "news", f"Calendar economic de rezervă (ForexFactory): {len(items)} evenimente săptămâna "
                                     f"aceasta ({hi} de impact mare)")

    def snapshot(self) -> tuple[list[dict], float | None]:
        with self._lock:
            return list(self.items), self.fetched_ts
