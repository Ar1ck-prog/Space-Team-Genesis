"""Timezone helpers using fixed UTC offsets (no tzdata dependency, no DST)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# Zones without daylight saving time, so a fixed offset is exact for any date.
ZONES = [
    ("Asia/Almaty", "+05:00"),
    ("Asia/Tokyo", "+09:00"),
    ("Europe/Istanbul", "+03:00"),
    ("Asia/Dubai", "+04:00"),
    ("Asia/Kolkata", "+05:30"),
    ("America/Bogota", "-05:00"),
    ("America/Sao_Paulo", "-03:00"),
    ("America/Lima", "-05:00"),
]


def offset_tz(offset: str) -> timezone:
    sign = 1 if offset[0] == "+" else -1
    hh, mm = offset[1:].split(":")
    return timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def parse(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp without offset: {ts}")
    return dt


def local_date(ts: str, offset: str) -> date:
    return parse(ts).astimezone(offset_tz(offset)).date()


def in_period(ts: str, period_start: str, period_end: str) -> bool:
    t = parse(ts)
    return parse(period_start) <= t <= parse(period_end)
