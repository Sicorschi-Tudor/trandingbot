"""Session window (IANA timezone, DST-safe) with manual override."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import DAYS, iso


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Europe/Chisinau")
    except Exception:
        return ZoneInfo("UTC")


def parse_hhmm(s: str) -> time:
    hh, mm = str(s).strip().split(":")[:2]
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"invalid time {s!r}")
    return time(h, m)


def _local_to_utc(d: date, t: time, tz: ZoneInfo) -> datetime:
    # zoneinfo resolves nonexistent/ambiguous local times via fold=0; round-trip through UTC
    return datetime.combine(d, t, tzinfo=tz).astimezone(timezone.utc)


def windows_utc(schedule: dict, around: datetime, days_back: int = 1, days_fwd: int = 8) -> list[tuple[datetime, datetime]]:
    tz = _tz(schedule.get("tz"))
    start, end = parse_hhmm(schedule["start"]), parse_hhmm(schedule["end"])
    days = {d.upper() for d in schedule.get("days") or []}
    local_today = around.astimezone(tz).date()
    out = []
    for off in range(-days_back, days_fwd + 1):
        d = local_today + timedelta(days=off)
        if DAYS[d.weekday()] not in days:
            continue
        s_utc = _local_to_utc(d, start, tz)
        e_day = d if end > start else d + timedelta(days=1)
        e_utc = _local_to_utc(e_day, end, tz)
        if e_utc > s_utc:
            out.append((s_utc, e_utc))
    return out


def scheduled_open(schedule: dict, now: datetime) -> tuple[bool, datetime | None, datetime | None]:
    """returns (open_now, current_window_start, next_change)"""
    wins = windows_utc(schedule, now)
    for s, e in wins:
        if s <= now < e:
            return True, s, e
    nxt = min((s for s, _ in wins if s > now), default=None)
    return False, None, nxt


def session_status(schedule: dict, now: datetime | None = None, override: str | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    enabled = bool(schedule.get("enabled", True))
    try:
        sched_open, _, next_change = scheduled_open(schedule, now)
    except Exception:
        sched_open, next_change = False, None
    if override == "start":
        open_now = True
    elif override == "stop":
        open_now = False
    elif not enabled:
        open_now, next_change = True, None
    else:
        open_now = sched_open
    return {
        "enabled": enabled,
        "open_now": open_now,
        "start": schedule.get("start"),
        "end": schedule.get("end"),
        "days": list(schedule.get("days") or []),
        "tz": schedule.get("tz"),
        "next_change_utc": iso(next_change) if next_change else None,
        "override": override,
    }


def local_day(now: datetime, tz_name: str) -> str:
    return now.astimezone(_tz(tz_name)).date().isoformat()


def local_day_start_utc(now: datetime, tz_name: str) -> datetime:
    tz = _tz(tz_name)
    d = now.astimezone(tz).date()
    return _local_to_utc(d, time(0, 0), tz)


def session_start_utc(schedule: dict, now: datetime, tz_fallback: str) -> datetime:
    """Start of the current session window, or local midnight if not inside a scheduled window."""
    try:
        is_open, start, _ = scheduled_open(schedule, now)
        if is_open and start:
            return start
    except Exception:
        pass
    return local_day_start_utc(now, schedule.get("tz") or tz_fallback)


def validate_schedule(sch: dict) -> None:
    parse_hhmm(sch["start"])
    parse_hhmm(sch["end"])
    for d in sch.get("days") or []:
        if d.upper() not in DAYS:
            raise ValueError(f"invalid day {d!r}")
    ZoneInfo(sch["tz"])
