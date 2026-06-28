"""UTC time helpers for METAR (15-minute) and GRIB (3-hour) cycles."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def round_to_quarter_hour(dt: datetime) -> datetime:
    """Round UTC time down to the nearest 15-minute boundary."""
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)


def round_to_three_hour(dt: datetime) -> datetime:
    """Round UTC time down to the nearest 3-hour boundary (00, 03, ..., 21)."""
    hour = (dt.hour // 3) * 3
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0)


def metar_filename(dt: datetime) -> str:
    """X-Plane METAR filename: metar-YYYY-MM-DD-HH.MM.txt"""
    return f"metar-{dt.strftime('%Y-%m-%d-%H.%M')}.txt"


def grib_filename(dt: datetime, product: str) -> str:
    """X-Plane GRIB filename: GRIB-YYYY-MM-DD-HH.00-ZULU-<product>.grib"""
    return f"GRIB-{dt.strftime('%Y-%m-%d-%H')}.00-ZULU-{product}.grib"


def grib_validity_windows(now: datetime | None = None) -> list[datetime]:
    """
    Return the two 3-hour validity times X-Plane expects: the current window
    (at or before now) and the next window (after now).
    """
    now = now or utc_now()
    current = round_to_three_hour(now)
    nxt = current + timedelta(hours=3)
    return [current, nxt]


def wifs_validity_time(now: datetime | None = None) -> datetime:
    """WIFS icing/turbulence products use 03:00Z on the current validity day."""
    now = now or utc_now()
    current = round_to_three_hour(now)
    return current.replace(hour=3, minute=0, second=0, microsecond=0)


def resolve_gfs_source(validity: datetime, now: datetime | None = None) -> tuple[str, int, int]:
    """
    Map a GRIB validity time to a GFS cycle date (YYYYMMDD), cycle hour, and
    forecast hour for the awf_0p25 / pgrb2.0p25 products.

    Picks the most recent model cycle whose forecast reaches the validity time
    and is likely published (cycle + GFS_PUBLICATION_DELAY_HOURS <= now).
    """
    from config import GFS_PUBLICATION_DELAY_HOURS

    now = now or utc_now()
    validity = validity.astimezone(timezone.utc)

    candidates: list[tuple[datetime, int, int]] = []
    for day_offset in (0, -1):
        base_date = (validity + timedelta(days=day_offset)).date()
        for cycle in (18, 12, 6, 0):
            cycle_start = datetime.combine(
                base_date, datetime.min.time(), tzinfo=timezone.utc
            ).replace(hour=cycle)
            forecast_hours = int((validity - cycle_start).total_seconds() // 3600)
            if forecast_hours <= 0 or forecast_hours > 36 or forecast_hours % 3 != 0:
                continue
            if now < cycle_start + timedelta(hours=GFS_PUBLICATION_DELAY_HOURS):
                continue
            candidates.append((cycle_start, cycle, forecast_hours))

    if not candidates:
        # Fallback: mirror weather-mirror logic for the validity hour itself.
        four_hours_ago = validity - timedelta(hours=4)
        cycle = (four_hours_ago.hour // 6) * 6
        adjustment = 0 if validity.day == four_hours_ago.day else 24
        forecast = ((adjustment + validity.hour - cycle) // 3) * 3
        date_cycle = four_hours_ago.strftime("%Y%m%d")
        return date_cycle, cycle, max(forecast, 3)

    cycle_start, cycle, forecast_hours = max(candidates, key=lambda item: item[0])
    return cycle_start.strftime("%Y%m%d"), cycle, forecast_hours
