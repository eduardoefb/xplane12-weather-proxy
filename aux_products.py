"""Download snod and WIFS GRIB products from Laminar's weather service."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any
from config import (
    GRIB_CLOUD_TEMPLATE_DIRS,
    HTTP_TIMEOUT_SECONDS,
    XP_WEATHER_DIR,
)
from time_utils import grib_filename, grib_validity_windows, utc_now, wifs_validity_time

LAMINAR_HOST = "weatherservice.x-plane.com"

# Manifest type -> local GRIB filename suffix.
WIFS_LOCAL_SUFFIX = {
    "cnmb_coverage": "cbcv-v2",
    "cnmb_bases": "cbbs-v2",
    "cnmb_tops": "cbtp-v2",
    "turb": "turb-v2",
}

WIFS_MANIFEST_TYPES = tuple(WIFS_LOCAL_SUFFIX.keys())
SNOD_MANIFEST_TYPE = "snod"


def laminar_server_ip() -> str | None:
    """Resolve Laminar's weather service IP via DNS (bypasses /etc/hosts)."""
    try:
        result = subprocess.run(
            ["dig", "+short", LAMINAR_HOST, "A"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    for line in result.stdout.splitlines():
        address = line.strip().rstrip(".")
        if address and ":" not in address:
            return address
    return None


def _curl_get(url: str, server_ip: str) -> bytes | None:
    try:
        result = subprocess.run(
            [
                "curl",
                "-sf",
                "--max-time",
                str(HTTP_TIMEOUT_SECONDS),
                "--resolve",
                f"{LAMINAR_HOST}:443:{server_ip}",
                url,
            ],
            capture_output=True,
            timeout=HTTP_TIMEOUT_SECONDS + 5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def _fetch_manifest(now: datetime) -> dict[str, Any] | None:
    ip = laminar_server_ip()
    if ip is None:
        return None

    url = (
        f"https://{LAMINAR_HOST}/api/v1/manifest/debug/"
        f"{now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    body = _curl_get(url, ip)
    if body is None:
        return None
    try:
        import json

        return json.loads(body)
    except ValueError:
        return None


def _download_file(url: str, dest_path: str, *, server_ip: str | None = None) -> bool:
    ip = server_ip or laminar_server_ip()
    if ip is None:
        return False

    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    temp_path = f"{dest_path}.part"
    try:
        result = subprocess.run(
            [
                "curl",
                "-sf",
                "--max-time",
                str(HTTP_TIMEOUT_SECONDS),
                "--resolve",
                f"{LAMINAR_HOST}:443:{ip}",
                url,
                "-o",
                temp_path,
            ],
            timeout=HTTP_TIMEOUT_SECONDS + 5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0 or not os.path.isfile(temp_path):
        return False
    if os.path.getsize(temp_path) < 1024:
        os.remove(temp_path)
        return False
    if os.path.exists(dest_path):
        os.remove(dest_path)
    shutil.move(temp_path, dest_path)
    return True


def _copy_template(product_suffix: str, validity: datetime, output_dir: str) -> bool:
    target = os.path.join(output_dir, grib_filename(validity, product_suffix))
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        return True

    pattern = f"GRIB-*-ZULU-{product_suffix}.grib"
    search_dirs = [output_dir, *GRIB_CLOUD_TEMPLATE_DIRS]
    candidates: list[tuple[float, str]] = []
    for directory in search_dirs:
        if not os.path.isdir(directory):
            continue
        for path in glob.glob(os.path.join(directory, pattern)):
            if os.path.getsize(path) > 1024:
                candidates.append((os.path.getmtime(path), path))

    if not candidates:
        return False

    candidates.sort(reverse=True)
    shutil.copy2(candidates[0][1], target)
    print(
        f"[aux] Reused cached {product_suffix} from "
        f"{os.path.basename(candidates[0][1])} -> {os.path.basename(target)}"
    )
    return True


def _sync_manifest_group(
    manifest: dict[str, Any],
    section: str,
    manifest_type: str,
    local_suffix: str,
    output_dir: str,
    server_ip: str,
) -> list[str]:
    written: list[str] = []
    for group in manifest.get("products", {}).get(section, []):
        if group.get("type") != manifest_type:
            continue
        for entry in group.get("files", []):
            depicts = entry.get("depicts_weather_at_datetime", "")
            if not depicts:
                continue
            validity = datetime.fromisoformat(depicts.replace("Z", "+00:00"))
            dest = os.path.join(output_dir, grib_filename(validity, local_suffix))
            if os.path.isfile(dest) and os.path.getsize(dest) > 1024:
                written.append(os.path.basename(dest))
                continue
            url = entry.get("url", "")
            if not url:
                continue
            print(f"[aux] Downloading {manifest_type} ({validity:%H:%M}Z) ...")
            if _download_file(url, dest, server_ip=server_ip):
                written.append(os.path.basename(dest))
            elif _copy_template(local_suffix, validity, output_dir):
                written.append(os.path.basename(dest))
    return written


def _mirror_wifs_to_validity_windows(output_dir: str, now: datetime) -> list[str]:
    """
    Copy canonical 03:00Z WIFS files into the active 3-hour validity windows.

    X-Plane requires exactly two GRIB timestamps; listing WIFS at 03:00Z adds a
    third. Laminar publishes WIFS at 03:00Z, so we mirror them locally.
    """
    source_time = wifs_validity_time(now)
    written: list[str] = []
    for validity in grib_validity_windows(now):
        for suffix in WIFS_LOCAL_SUFFIX.values():
            source = os.path.join(output_dir, grib_filename(source_time, suffix))
            if not os.path.isfile(source) or os.path.getsize(source) <= 1024:
                continue
            target = os.path.join(output_dir, grib_filename(validity, suffix))
            if (
                os.path.isfile(target)
                and os.path.getsize(target) > 1024
                and os.path.getmtime(target) >= os.path.getmtime(source)
            ):
                written.append(os.path.basename(target))
                continue
            shutil.copy2(source, target)
            written.append(os.path.basename(target))
    return written


def sync_aux_products(
    output_dir: str = XP_WEATHER_DIR,
    now: datetime | None = None,
) -> list[str]:
    """
    Ensure snod (nomads_extra) and WIFS GRIB files exist locally.

    Downloads from Laminar when reachable; otherwise reuses the newest cached copy.
    """
    now = now or utc_now()
    written: list[str] = []

    server_ip = laminar_server_ip()
    manifest = _fetch_manifest(now) if server_ip else None

    if manifest is not None and server_ip is not None:
        written.extend(
            _sync_manifest_group(
                manifest,
                "nomads_extra",
                SNOD_MANIFEST_TYPE,
                SNOD_MANIFEST_TYPE,
                output_dir,
                server_ip,
            )
        )
        for manifest_type, local_suffix in WIFS_LOCAL_SUFFIX.items():
            written.extend(
                _sync_manifest_group(
                    manifest,
                    "wifs",
                    manifest_type,
                    local_suffix,
                    output_dir,
                    server_ip,
                )
            )

    # Fallback: reuse any cached copies for expected paths.
    for validity in grib_validity_windows(now):
        if _copy_template(SNOD_MANIFEST_TYPE, validity, output_dir):
            name = grib_filename(validity, SNOD_MANIFEST_TYPE)
            if name not in written:
                written.append(name)

    wifs_time = wifs_validity_time(now)
    for local_suffix in WIFS_LOCAL_SUFFIX.values():
        if _copy_template(local_suffix, wifs_time, output_dir):
            name = grib_filename(wifs_time, local_suffix)
            if name not in written:
                written.append(name)

    written.extend(_mirror_wifs_to_validity_windows(output_dir, now))
    return written
