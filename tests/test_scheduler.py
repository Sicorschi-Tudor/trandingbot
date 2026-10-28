from datetime import datetime, timezone

from app.scheduler import local_day, session_start_utc, session_status

SCH = {"enabled": True, "start": "09:00", "end": "13:00", "days": ["MON", "TUE", "WED", "THU", "FRI"],
       "tz": "Europe/Chisinau"}


def u(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_winter_offset_utc_plus_2():
    # Thu 15 Jan 2026: 09:00 Chisinau = 07:00 UTC
    assert not session_status(SCH, u(2026, 1, 15, 6, 59))["open_now"]
    assert session_status(SCH, u(2026, 1, 15, 7, 0))["open_now"]
    assert session_status(SCH, u(2026, 1, 15, 10, 59))["open_now"]
    assert not session_status(SCH, u(2026, 1, 15, 11, 0))["open_now"]


def test_summer_offset_utc_plus_3():
    # Wed 15 Jul 2026: 09:00 Chisinau = 06:00 UTC
    assert session_status(SCH, u(2026, 7, 15, 6, 0))["open_now"]
    assert not session_status(SCH, u(2026, 7, 15, 10, 0))["open_now"]


def test_dst_switch_days():
    sch = dict(SCH, days=["SUN", "MON"])
    # Sun 29 Mar 2026 (spring forward early morning): session already on UTC+3
    assert session_status(sch, u(2026, 3, 29, 6, 0))["open_now"]
    assert not session_status(sch, u(2026, 3, 29, 5, 59))["open_now"]
    # Sun 25 Oct 2026 (fall back): UTC+2 again
    assert session_status(sch, u(2026, 10, 25, 7, 0))["open_now"]
    assert not session_status(sch, u(2026, 10, 25, 6, 30))["open_now"]


def test_weekend_closed_and_next_change():
    st = session_status(SCH, u(2026, 10, 31, 8, 0))  # Saturday
    assert not st["open_now"]
    assert st["next_change_utc"] == "2026-11-02T07:00:00Z"  # Monday 09:00 (UTC+2)
    st = session_status(SCH, u(2026, 10, 28, 8, 0))  # Wed, inside window
    assert st["open_now"] and st["next_change_utc"] == "2026-10-28T11:00:00Z"


def test_override_and_disabled():
    sat = u(2026, 10, 31, 8, 0)
    assert session_status(SCH, sat, "start")["open_now"]
    wed = u(2026, 10, 28, 8, 0)
    assert not session_status(SCH, wed, "stop")["open_now"]
    assert session_status(dict(SCH, enabled=False), sat)["open_now"]


def test_overnight_window():
    sch = dict(SCH, start="22:00", end="02:00", tz="UTC")
    assert session_status(sch, u(2026, 10, 28, 23, 0))["open_now"]
    assert session_status(sch, u(2026, 10, 29, 1, 0))["open_now"]  # belongs to Wed window
    assert not session_status(sch, u(2026, 10, 29, 3, 0))["open_now"]


def test_session_start_and_local_day():
    assert session_start_utc(SCH, u(2026, 10, 28, 8, 0), "Europe/Chisinau") == u(2026, 10, 28, 7, 0)
    # outside window -> local midnight (UTC+2 on 28 Oct 2026)
    assert session_start_utc(SCH, u(2026, 10, 28, 15, 0), "Europe/Chisinau") == u(2026, 10, 27, 22, 0)
    assert local_day(u(2026, 10, 27, 22, 30), "Europe/Chisinau") == "2026-10-28"
