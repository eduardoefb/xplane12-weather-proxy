"""METAR download and file generation for X-Plane real weather."""

from __future__ import annotations

import csv
import gzip
import io
import os
from datetime import datetime, timedelta, timezone

import requests

from config import (
    HTTP_TIMEOUT_SECONDS,
    HTTP_USER_AGENT,
    METAR_CACHE_URL,
    METAR_CYCLE_URL,
    WEATHER_STAGING_DIR,
)


def _http_get(url: str) -> str | None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Connecting to {url} ...")
    try:
        response = requests.get(
            url,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": HTTP_USER_AGENT},
        )
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as exc:
        print(f"Error fetching data: {exc}")
        return None


def _metar_cycle_hours(now: datetime) -> list[int]:
    """Try the current UTC hour cycle first, then the previous hour as fallback."""
    return [now.hour, (now.hour + 23) % 24]


def _fetch_metar_cycle(now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    for hour in _metar_cycle_hours(now):
        url = METAR_CYCLE_URL.format(hour=hour)
        data = _http_get(url)
        if data and data.strip():
            return data if data.endswith("\n") else data + "\n"
    return None


def _format_cache_metar(payload: bytes) -> str | None:
    """Convert the AWC CSV cache into X-Plane's METAR text layout."""
    reader = csv.DictReader(io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8"))
    blocks: list[str] = []
    for row in reader:
        raw = (row.get("raw_text") or "").strip()
        if not raw:
            continue
        obs_raw = (row.get("observation_time") or "").strip()
        if obs_raw:
            obs = datetime.fromisoformat(obs_raw.replace("Z", "+00:00"))
            header = obs.strftime("%Y/%m/%d %H:%M")
        else:
            header = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M")
        blocks.append(f"{header}\n{raw}\n")
    if not blocks:
        print("METAR cache contained no observations.")
        return None
    return "\n".join(blocks)


def _fetch_metar_cache() -> str | None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching global METAR cache ...")
    try:
        response = requests.get(
            METAR_CACHE_URL,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": HTTP_USER_AGENT},
        )
        response.raise_for_status()
        return _format_cache_metar(gzip.decompress(response.content))
    except (requests.exceptions.RequestException, OSError, ValueError) as exc:
        print(f"Error fetching METAR cache: {exc}")
        return None


def fetch_global_metars(now: datetime | None = None) -> str | None:
    """Download a global METAR dump in the layout X-Plane expects."""
    # Cache updates continuously with per-station observation times (freshest).
    data = _fetch_metar_cache()
    if data:
        return data
    return _fetch_metar_cycle(now)


def write_metar_file(filename: str, data: str, staging_dir: str = WEATHER_STAGING_DIR) -> bool:
    """Write a METAR text file into the weather staging directory."""
    os.makedirs(staging_dir, exist_ok=True)
    filepath = os.path.join(staging_dir, filename)
    try:
        with open(filepath, "w", encoding="utf-8") as handle:
            handle.write(data)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] METAR file written: {filename}")
        return True
    except OSError as exc:
        print(f"Error writing METAR file: {exc}")
        return False


class MetarEngine:
    """Handles the 15-minute METAR refresh cycle."""

    def __init__(self, staging_dir: str = WEATHER_STAGING_DIR) -> None:
        self.staging_dir = staging_dir
        self.last_processed_time = None

    def run_cycle(self, target_time) -> bool:
        from time_utils import metar_filename, round_to_quarter_hour

        filename = metar_filename(target_time)
        print(
            f"\n[{datetime.now().strftime('%H:%M:%S')}] METAR cycle -> {filename}"
        )
        metar_data = fetch_global_metars(target_time)
        if not metar_data:
            return False
        if not write_metar_file(filename, metar_data, self.staging_dir):
            return False

        self.last_processed_time = target_time

        # X-Plane needs pre/post METAR files 15 minutes apart in the manifest.
        pre_time = target_time - timedelta(minutes=15)
        pre_filename = metar_filename(pre_time)
        pre_path = os.path.join(self.staging_dir, pre_filename)
        if not os.path.isfile(pre_path):
            write_metar_file(pre_filename, metar_data, self.staging_dir)

        # Keep the current quarter-hour file fresh even between boundary ticks.
        current_rounded = round_to_quarter_hour(target_time)
        current_filename = metar_filename(current_rounded)
        if current_filename != filename:
            write_metar_file(current_filename, metar_data, self.staging_dir)

        return True

    def should_run(self, current_rounded_time) -> bool:
        return current_rounded_time != self.last_processed_time
