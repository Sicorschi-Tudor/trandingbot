"""RESEARCH_SYMBOLS: archive-only symbols walked after the active set (D1/H4/H1, ARCHIVE_EXTRA targets/throttling),
broker name aliases on UNKNOWN_SYMBOL, unavailable marking, never analysed / quoted / traded."""
import time

from app import archive, bridge
from app.config import Settings, TF_SECONDS
from app.db import DB
from app.engine import Engine
from tests.conftest import make_settings, quote

DAY = 86400


def make_engine(tmp_path, ea="1.13", **kw):
    kw.setdefault("ACTIVE_SYMBOLS", "EURUSD")
    kw.setdefault("ARCHIVE_EXTRA", "D1:30")
    kw.setdefault("ARCHIVE_EXTRA_INTERVAL_S", 20)
    kw.setdefault("RESEARCH_SYMBOLS", "XAUUSD,US500")
    s = make_settings(tmp_path, **kw)
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    eng.rt.bridge.update({"ea_version": ea, "clock_skew_s": 0, "srv_offset": None, "last_sync_ts": time.time()})
    return eng


def parse(line):
    _, sym, tf, end, count = line.split("|")
    return sym, tf, int(end), int(count)


def reply(rt, line, now, bars=None, err=None, n=None):
    sym, tf, end, count = parse(line)
    if bars is None:
        sec = TF_SECONDS[tf]
        k = n or count
        bars = [{"t": end - (k - i) * sec, "o": 1.1, "h": 1.2, "l": 1.0, "c": 1.15, "tv": 1, "sp": 5} for i in range(k)]
    item = {"symbol": sym, "tf": tf, "end_utc": end, "bars": bars}
    if err is not None:
        item["err"] = err
    archive.ingest_payload(rt, [item], now)


def finish_active(rt, now):
    """Answer every active (base + extended) request with 'nothing older'; returns the first research line + time."""
    for i in range(50):
        now += 21
        line = archive.next_request(rt, now)
        assert line is not None
        sym = parse(line)[0]
        if sym in rt.active_symbols():
            reply(rt, line, now + 1, bars=[])
            continue
        return line, now
    raise AssertionError("active walk never finished")


def test_default_list_and_never_active(tmp_path):
    assert Settings.model_fields["RESEARCH_SYMBOLS"].default == (
        "XAUUSD,XAGUSD,US500,US30,NAS100,UK100,DE40,JP225,XTIUSD,XBRUSD,BTCUSD,ETHUSD,USDCHF,USDMXN,USDZAR,USDTRY")
    eng = make_engine(tmp_path, ACTIVE_SYMBOLS="EURUSD,XAUUSD", RESEARCH_SYMBOLS="XAUUSD,US500,eurusd")
    rt = eng.rt
    # an active symbol is never a research symbol; research symbols never become active / analysed
    assert archive.research_list(rt) == ["US500"]
    assert rt.active_symbols() == ["EURUSD", "XAUUSD"]
    assert "US500" not in rt.active_symbols()
    assert archive.research_list(rt) and archive.research_targets(rt.settings) == {"D1": 30, "H4": 1825, "H1": 1095}
    # off -> nothing
    eng2 = make_engine(tmp_path / "b", RESEARCH_SYMBOLS="off")
    assert archive.research_list(eng2.rt) == []


def test_research_after_active_complete_d1_first(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    now = time.time()
    # while active pairs are incomplete no research symbol is asked for
    first = archive.next_request(rt, now)
    assert parse(first)[0] == "EURUSD"
    reply(rt, first, now + 1, bars=[])
    line, now = finish_active(rt, now)
    assert all(rt.archive_status["EURUSD"][tf]["done"] for tf in rt.archive_status["EURUSD"])
    sym, tf, _, count = parse(line)
    assert (sym, tf) == ("XAUUSD", "D1") and count <= archive.EXTRA_MAX_BATCH
    assert rt.archive_plan["pending"]["extended"] is True and rt.archive_plan["pending"]["research"] is True
    reply(rt, line, now + 1, bars=[])
    now += 22
    line = archive.next_request(rt, now)
    assert parse(line)[:2] == ("US500", "D1")  # every research symbol's D1 before any H4
    reply(rt, line, now + 1, bars=[])
    now += 22
    assert parse(archive.next_request(rt, now))[:2] == ("XAUUSD", "H4")


def test_alias_resolution_on_unknown_symbol(tmp_path):
    eng = make_engine(tmp_path, RESEARCH_SYMBOLS="US500")
    rt = eng.rt
    line, now = finish_active(rt, time.time())
    assert parse(line)[0] == "US500"
    reply(rt, line, now + 1, bars=[], err="UNKNOWN_SYMBOL")
    now += 22
    line = archive.next_request(rt, now)
    assert parse(line)[:2] == ("SPX500", "D1")
    reply(rt, line, now + 1, bars=[], err=4301)  # EA <= 1.12 code for the same failure
    now += 22
    line = archive.next_request(rt, now)
    assert parse(line)[0] == "US500.cash"
    reply(rt, line, now + 1, n=5)
    archive.drain()
    st = rt.archive_plan["research"]["US500"]
    assert st["broker"] == "US500.cash" and st["available"] is True and st["tried"] == ["US500", "SPX500"]
    # archived under the canonical name
    rows = rt.db.query("SELECT DISTINCT symbol FROM bars_archive")
    assert [r["symbol"] for r in rows] == ["US500"]
    assert rt.archive_status["US500"]["D1"]["bars"] == 5
    # next requests keep using the resolved broker name
    now += 22
    assert parse(archive.next_request(rt, now))[0] == "US500.cash"
    v = archive.status_view(rt, now)
    assert "US500" not in v["symbols"] and "US500.cash" not in v["symbols"]
    r = v["research"]["US500"]
    assert r["broker_symbol"] == "US500.cash" and r["available"] is True
    assert set(r["tfs"]) == {"D1", "H4", "H1"} and r["tfs"]["D1"]["bars"] == 5
    assert v["phase"] == "research" and v["research_enabled"] is True


def test_unavailable_after_all_aliases(tmp_path):
    eng = make_engine(tmp_path, RESEARCH_SYMBOLS="DE40,XAUUSD")
    rt = eng.rt
    line, now = finish_active(rt, time.time())
    asked = []
    while parse(line)[0] != "XAUUSD":
        asked.append(parse(line)[0])
        reply(rt, line, now + 1, bars=[], err="UNKNOWN_SYMBOL")
        now += 22
        line = archive.next_request(rt, now)
    assert asked == ["DE40", "GER40", "DAX40"]
    st = rt.archive_plan["research"]["DE40"]
    assert st["available"] is False and st["broker"] is None
    reply(rt, line, now + 1, bars=[], err="UNKNOWN_SYMBOL")  # XAUUSD has no alias -> unavailable at once
    now += 22
    assert archive.next_request(rt, now) is None  # nothing left: never asked again
    v = archive.status_view(rt, now)
    assert v["research"]["DE40"]["available"] is False and v["research"]["XAUUSD"]["available"] is False
    assert v["research"]["DE40"]["tried"] == ["DE40", "GER40", "DAX40"]
    assert v["phase"] == "done"


def test_throttling_positions_and_ea_version(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    line, now = finish_active(rt, time.time())
    reply(rt, line, now + 5, n=3)
    # the interval counts from the research reply
    assert archive.next_request(rt, now + 5 + 19) is None
    line = archive.next_request(rt, now + 5 + 21)
    assert line is not None and parse(line)[0] == "XAUUSD"
    reply(rt, line, now + 30, n=3)
    # a bot position holds the research walk back (ARCHIVE_EXTRA_WITH_POSITIONS=false)
    rt.mt5_positions = [{"magic": int(rt.settings.MT5_MAGIC), "symbol": "EURUSD"}]
    assert archive.next_request(rt, now + 100) is None
    rt.mt5_positions = []
    assert archive.next_request(rt, now + 100) is not None
    rt.archive_plan["pending"] = None
    # EA < 1.13 (no UNKNOWN_SYMBOL reply): research is never asked for
    rt.bridge["ea_version"] = "1.12"
    assert archive.next_request(rt, now + 200) is None
    assert archive.status_view(rt, now + 200)["research_ea_ok"] is False
    # ARCHIVE_EXTRA off: research off too
    eng2 = make_engine(tmp_path / "b", ARCHIVE_EXTRA="off")
    assert archive.research_targets(eng2.rt.settings) == {}


def test_never_quoted_or_analysed_via_bridge(tmp_path):
    eng = make_engine(tmp_path)
    rt = eng.rt
    rt.mt5_account = {"login": 1, "trade_mode": 0}
    now = time.time()
    payload = {"ea_version": "1.13", "magic": int(rt.settings.MT5_MAGIC), "account": {"login": 1, "trade_mode": 0},
               "symbols": [dict(symbol="EURUSD", **quote(now=now)), dict(symbol="XAUUSD", **quote(now=now))],
               "bars": [{"symbol": "XAUUSD", "tf": "M5", "t": int(now // 300 * 300 - 300), "o": 1, "h": 2, "l": 1,
                         "c": 1.5, "tv": 1, "sp": 1}]}
    text = bridge.process_sync(eng, payload, now)
    lines = text.splitlines()
    sym_line = next(x for x in lines if x.startswith("SYMBOLS|"))
    assert sym_line == "SYMBOLS|EURUSD"
    assert not any(x.startswith("HIST|XAUUSD") or "|XAUUSD|" in x and not x.startswith("HISTX") for x in lines)
    assert "XAUUSD" not in rt.quotes and "EURUSD" in rt.quotes
    assert not any(k[0] == "XAUUSD" for k in rt.bars)
    assert "XAUUSD" not in rt.last_analysis
