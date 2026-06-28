"""Download snod and WIFS GRIB products from Laminar's weather service."""

from __future__ import annotations

import glob
import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any

from config import (
    GRIB_CLOUD_TEMPLATE_DIRS,
    HTTP_TIMEOUT_SECONDS,
    HTTP_USER_AGENT,
    WEATHER_STAGING_DIR,
)
from platform_support import (
    WEATHER_HOST,
    https_download_bypass_hosts,
    https_get_bypass_hosts,
    resolve_public_ipv4,
)
from time_utils import grib_filename, grib_validity_windows, utc_now, wifs_validity_time

LAMINAR_HOST = WEATHER_HOST

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
    """Resolve Laminar's weather service IP via public DNS (bypasses hosts file)."""
    return resolve_public_ipv4(LAMINAR_HOST)


def _fetch_manifest(now: datetime) -> dict[str, Any] | None:
    ip = laminar_server_ip()
    if ip is None:
        return None

    url = (
        f"https://{LAMINAR_HOST}/api/v1/manifest/debug/"
        f"{now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    body = https_get_bypass_hosts(
        url,
        LAMINAR_HOST,
        ip,
        timeout=HTTP_TIMEOUT_SECONDS,
        user_agent=HTTP_USER_AGENT,
    )
    if body is None:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def _download_file(url: str, dest_path: str, *, server_ip: str | None = None) -> bool:
    ip = server_ip or laminar_server_ip()
    if ip is None:
        return False
    return https_download_bypass_hosts(
        url,
        dest_path,
        LAMINAR_HOST,
        ip,
        timeout=HTTP_TIMEOUT_SECONDS,
        user_agent=HTTP_USER_AGENT,
    )


def _copy_template(
    product_suffix: str,
    validity: datetime,
    staging_dir: str,
    template_dirs: tuple[str, ...] = (),
) -> bool:
    target = os.path.join(staging_dir, grib_filename(validity, product_suffix))
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        return True

    pattern = f"GRIB-*-ZULU-{product_suffix}.grib"
    search_dirs = [staging_dir, *template_dirs, *GRIB_CLOUD_TEMPLATE_DIRS]
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
    staging_dir: str,
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
            dest = os.path.join(staging_dir, grib_filename(validity, local_suffix))
            if os.path.isfile(dest) and os.path.getsize(dest) > 1024:
                written.append(os.path.basename(dest))
                continue
            url = entry.get("url", "")
            if not url:
                continue
            print(f"[aux] Downloading {manifest_type} ({validity:%H:%M}Z) ...")
            if _download_file(url, dest, server_ip=server_ip):
                written.append(os.path.basename(dest))
            elif _copy_template(local_suffix, validity, staging_dir):
                written.append(os.path.basename(dest))
    return written


def _mirror_wifs_to_validity_windows(staging_dir: str, now: datetime) -> list[str]:
    """
    Copy canonical 03:00Z WIFS files into the active 3-hour validity windows.

    X-Plane requires exactly two GRIB timestamps; listing WIFS at 03:00Z adds a
    third. Laminar publishes WIFS at 03:00Z, so we mirror them locally.
    """
    source_time = wifs_validity_time(now)
    written: list[str] = []
    for validity in grib_validity_windows(now):
        for suffix in WIFS_LOCAL_SUFFIX.values():
            source = os.path.join(staging_dir, grib_filename(source_time, suffix))
            if not os.path.isfile(source) or os.path.getsize(source) <= 1024:
                continue
            target = os.path.join(staging_dir, grib_filename(validity, suffix))
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
    staging_dir: str = WEATHER_STAGING_DIR,
    now: datetime | None = None,
    *,
    template_dirs: tuple[str, ...] = (),
) -> list[str]:
    """
    Ensure snod (nomads_extra) and WIFS GRIB files exist locally.

    Downloads from Laminar when reachable; otherwise reuses the newest cached copy.
    """
    now = now or utc_now()
    os.makedirs(staging_dir, exist_ok=True)
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
                staging_dir,
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
                    staging_dir,
                    server_ip,
                )
            )

    # Fallback: reuse any cached copies for expected paths.
    for validity in grib_validity_windows(now):
        if _copy_template(SNOD_MANIFEST_TYPE, validity, staging_dir, template_dirs):
            name = grib_filename(validity, SNOD_MANIFEST_TYPE)
            if name not in written:
                written.append(name)

    wifs_time = wifs_validity_time(now)
    for local_suffix in WIFS_LOCAL_SUFFIX.values():
        if _copy_template(local_suffix, wifs_time, staging_dir, template_dirs):
            name = grib_filename(wifs_time, local_suffix)
            if name not in written:
                written.append(name)

    written.extend(_mirror_wifs_to_validity_windows(staging_dir, now))
    return written
