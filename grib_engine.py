"""GRIB download, slicing, and X-Plane file generation."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlencode

import requests

from config import (
    GRIB_CACHE_DIR,
    GRIB_CLOUD_TEMPLATE_DIRS,
    GRIB_GENERATED_025DEG,
    GRIB_GENERATED_1DEG,
    GRIB_PRESERVED_PRODUCTS,
    GRIB_PRODUCTS,
    HTTP_TIMEOUT_SECONDS,
    HTTP_USER_AGENT,
    ISOBARIC_LEVELS_HPA,
    NOMADS_FILTER_GFS_025,
    NOMADS_FILTER_GFS_100,
    NOMADS_GFS_BASE,
    WEATHER_STAGING_DIR,
)
from time_utils import grib_filename, resolve_gfs_source


def build_awf_url(date_cycle: str, cycle: int, forecast: int) -> str:
    return (
        f"{NOMADS_GFS_BASE}/gfs.{date_cycle}/{cycle:02d}/atmos/"
        f"gfs.t{cycle:02d}z.awf_0p25.f{forecast:03d}.grib2"
    )


def build_filter_url(date_cycle: str, cycle: int, forecast: int, resolution: str = "1p00") -> str:
    """NOMADS subset request for fields needed by X-Plane GRIB products."""
    if resolution == "0p25":
        base_url = NOMADS_FILTER_GFS_025
        file_name = f"gfs.t{cycle:02d}z.pgrb2.0p25.f{forecast:03d}"
        params = {
            "dir": f"/gfs.{date_cycle}/{cycle:02d}/atmos",
            "file": file_name,
            "var_VIS": "on",
            "lev_surface": "on",
        }
        return f"{base_url}?{urlencode(params)}"

    base_url = NOMADS_FILTER_GFS_100
    file_name = f"gfs.t{cycle:02d}z.pgrb2.1p00.f{forecast:03d}"
    params = {
        "dir": f"/gfs.{date_cycle}/{cycle:02d}/atmos",
        "file": file_name,
        "var_UGRD": "on",
        "var_VGRD": "on",
        "var_TMP": "on",
        "var_RH": "on",
        "var_PRMSL": "on",
        "var_HGT": "on",
        "var_PRATE": "on",
        "lev_10_m_above_ground": "on",
        "lev_2_m_above_ground": "on",
        "lev_mean_sea_level": "on",
        "lev_surface": "on",
        "lev_tropopause": "on",
    }
    for level in ISOBARIC_LEVELS_HPA:
        params[f"lev_{level}_mb"] = "on"
    return f"{base_url}?{urlencode(params)}"


def download_grib_source(
    date_cycle: str,
    cycle: int,
    forecast: int,
    resolution: str = "1p00",
    cache_dir: str = GRIB_CACHE_DIR,
) -> str | None:
    """Download a source GRIB2 file from NOAA NOMADS."""
    os.makedirs(cache_dir, exist_ok=True)
    cache_name = f"gfs_{resolution}_{date_cycle}_{cycle:02d}_f{forecast:03d}.grib2"
    cache_path = os.path.join(cache_dir, cache_name)

    if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 1024:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Using cached GRIB: {cache_name}")
        return cache_path

    urls = [
        (
            f"filtered GFS {resolution}",
            build_filter_url(date_cycle, cycle, forecast, resolution),
        ),
    ]
    if resolution == "1p00":
        urls.append(("AWF", build_awf_url(date_cycle, cycle, forecast)))

    for label, url in urls:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Downloading {label} GRIB ...")
        temp_path = None
        try:
            with requests.get(
                url,
                stream=True,
                timeout=HTTP_TIMEOUT_SECONDS,
                headers={"User-Agent": HTTP_USER_AGENT},
            ) as response:
                response.raise_for_status()
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".grib2", dir=cache_dir
                ) as tmp:
                    temp_path = tmp.name
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            tmp.write(chunk)
            if os.path.getsize(temp_path) < 1024:
                os.remove(temp_path)
                print(f"{label} download was empty; trying next source.")
                continue
            if os.path.exists(cache_path):
                os.remove(cache_path)
            shutil.move(temp_path, cache_path)
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"GRIB cached ({os.path.getsize(cache_path) // 1024} KiB): {cache_name}"
            )
            return cache_path
        except (requests.exceptions.RequestException, OSError) as exc:
            print(f"Error downloading {label} GRIB: {exc}")
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    return None


def _match_wind_v2(gid) -> bool:
    import eccodes

    short_name = eccodes.codes_get(gid, "shortName", ktype=str)
    if short_name not in {"u", "v", "10u", "10v"}:
        return False
    type_of_level = eccodes.codes_get(gid, "typeOfLevel", ktype=str)
    if type_of_level == "isobaricInhPa":
        return eccodes.codes_get(gid, "level", ktype=int) in ISOBARIC_LEVELS_HPA
    if type_of_level == "heightAboveGround":
        return eccodes.codes_get(gid, "level", ktype=int) == 10
    return False


def _match_temp_v2(gid) -> bool:
    import eccodes

    short_name = eccodes.codes_get(gid, "shortName", ktype=str)
    if short_name not in {"t", "2t"}:
        return False
    type_of_level = eccodes.codes_get(gid, "typeOfLevel", ktype=str)
    if type_of_level == "isobaricInhPa":
        return eccodes.codes_get(gid, "level", ktype=int) in ISOBARIC_LEVELS_HPA
    if type_of_level == "heightAboveGround":
        return eccodes.codes_get(gid, "level", ktype=int) == 2
    return False


def _match_dewp_v2(gid) -> bool:
    import eccodes

    short_name = eccodes.codes_get(gid, "shortName", ktype=str)
    type_of_level = eccodes.codes_get(gid, "typeOfLevel", ktype=str)
    if short_name == "2r" and type_of_level == "heightAboveGround":
        return eccodes.codes_get(gid, "level", ktype=int) == 2
    if short_name == "r" and type_of_level == "isobaricInhPa":
        return eccodes.codes_get(gid, "level", ktype=int) in ISOBARIC_LEVELS_HPA
    return False


def _match_pres(gid) -> bool:
    import eccodes

    short_name = eccodes.codes_get(gid, "shortName", ktype=str)
    if short_name in {"prmsl", "mslet"}:
        return True
    return (
        short_name == "sp"
        and eccodes.codes_get(gid, "typeOfLevel", ktype=str) == "surface"
    )


def _match_srfc(gid) -> bool:
    import eccodes

    return (
        eccodes.codes_get(gid, "shortName", ktype=str) == "orog"
        and eccodes.codes_get(gid, "typeOfLevel", ktype=str) == "surface"
    )


def _match_svis(gid) -> bool:
    import eccodes

    return (
        eccodes.codes_get(gid, "shortName", ktype=str) == "vis"
        and eccodes.codes_get(gid, "typeOfLevel", ktype=str) == "surface"
    )


def _match_prcp(gid) -> bool:
    import eccodes

    return (
        eccodes.codes_get(gid, "shortName", ktype=str) == "prate"
        and eccodes.codes_get(gid, "typeOfLevel", ktype=str) == "surface"
    )


def _match_trop_v2(gid) -> bool:
    import eccodes

    short_name = eccodes.codes_get(gid, "shortName", ktype=str)
    if short_name not in {"gh", "t"}:
        return False
    return eccodes.codes_get(gid, "typeOfLevel", ktype=str) == "tropopause"


PRODUCT_MATCHERS: dict[str, Callable] = {
    "wind-v2": _match_wind_v2,
    "temp-v2": _match_temp_v2,
    "dewp-v2": _match_dewp_v2,
    "pres": _match_pres,
    "srfc": _match_srfc,
    "svis": _match_svis,
    "prcp": _match_prcp,
    "trop-v2": _match_trop_v2,
}


def _slice_with_eccodes(source_path: str, output_path: str, matcher: Callable) -> bool:
    import eccodes

    matched = 0
    with open(source_path, "rb") as src, open(output_path, "wb") as dst:
        while True:
            gid = eccodes.codes_grib_new_from_file(src)
            if gid is None:
                break
            try:
                if matcher(gid):
                    eccodes.codes_write(gid, dst)
                    matched += 1
            finally:
                eccodes.codes_release(gid)

    if matched == 0:
        if os.path.exists(output_path):
            os.remove(output_path)
        return False
    return True


def _wgrib2_match_pattern(product: str) -> str | None:
    levels = "|".join(str(level) for level in ISOBARIC_LEVELS_HPA)
    patterns = {
        "wind-v2": rf":(UGRD|VGRD|10u|10v):(({levels}) mb|10 m above ground):",
        "temp-v2": rf":TMP:(({levels}) mb|2 m above ground):",
        "dewp-v2": rf":RH:(({levels}) mb|2 m above ground):",
        "pres": r":(PRMSL|MSLET):",
        "srfc": r":(HGT|OROG):surface:",
        "svis": r":VIS:surface:",
        "prcp": r":PRATE:surface:",
        "trop-v2": r":(HGT|TMP):tropopause:",
    }
    return patterns.get(product)


def _slice_with_wgrib2(source_path: str, output_path: str, product: str) -> bool:
    wgrib2 = shutil.which("wgrib2")
    pattern = _wgrib2_match_pattern(product)
    if not wgrib2 or not pattern:
        return False

    cmd = [wgrib2, source_path, "-match", pattern, "-grib", output_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return False

    if result.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        if os.path.exists(output_path):
            os.remove(output_path)
        return False
    return True


def slice_grib_product(source_path: str, output_path: str, product: str) -> bool:
    """Extract one X-Plane GRIB product from a raw NOAA GRIB2 file."""
    matcher = PRODUCT_MATCHERS.get(product)
    if matcher is None:
        raise ValueError(f"Unknown GRIB product: {product}")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if _slice_with_wgrib2(source_path, output_path, product):
        return True

    try:
        return _slice_with_eccodes(source_path, output_path, matcher)
    except ImportError:
        print(
            "eccodes is not installed; install requirements.txt "
            "or place wgrib2 on PATH."
        )
        return False


def _parse_grib_validity(path: str) -> datetime | None:
    """Parse GRIB-YYYY-MM-DD-HH.00-ZULU-<product>.grib validity from the basename."""
    basename = os.path.basename(path)
    parts = basename.split("-")
    if len(parts) < 5:
        return None
    try:
        return datetime(
            int(parts[1]),
            int(parts[2]),
            int(parts[3]),
            int(parts[4].split(".")[0]),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def _find_cloud_template(
    product: str,
    validity_time: datetime,
    staging_dir: str,
    template_dirs: tuple[str, ...] = (),
) -> str | None:
    """Find a calt/ccov source file for the requested validity window."""
    target_name = grib_filename(validity_time, product)
    validity_key = validity_time.strftime("%Y-%m-%d-%H")

    search_dirs = [staging_dir, *template_dirs, *GRIB_CLOUD_TEMPLATE_DIRS]
    exact_matches: list[str] = []
    other_matches: list[tuple[int, str]] = []

    for directory in search_dirs:
        if not os.path.isdir(directory):
            continue

        exact_path = os.path.join(directory, target_name)
        if os.path.isfile(exact_path) and os.path.getsize(exact_path) > 0:
            exact_matches.append(exact_path)

        pattern = os.path.join(directory, f"GRIB-*-ZULU-{product}.grib")
        for path in glob.glob(pattern):
            if os.path.getsize(path) <= 0:
                continue
            basename = os.path.basename(path)
            if directory == staging_dir and validity_key in basename:
                continue
            parsed = _parse_grib_validity(path)
            if parsed is None:
                continue
            hour_delta = abs(
                int((parsed - validity_time).total_seconds()) // 3600
            )
            other_matches.append((hour_delta, path))

    if exact_matches:
        return exact_matches[0]

    if other_matches:
        other_matches.sort(key=lambda item: (item[0], -os.path.getmtime(item[1])))
        return other_matches[0][1]

    return None


def _preserve_cloud_products(
    validity_time: datetime,
    staging_dir: str,
    template_dirs: tuple[str, ...] = (),
) -> list[str]:
    """
    calt/ccov use WAFS-specific GRIB encoding unavailable from raw GFS filters.
    Reuse an existing local copy from another validity window when possible.
    """
    preserved: list[str] = []

    for product in GRIB_PRESERVED_PRODUCTS:
        filename = grib_filename(validity_time, product)
        output_path = os.path.join(staging_dir, filename)
        if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
            preserved.append(filename)
            continue

        source = _find_cloud_template(product, validity_time, staging_dir, template_dirs)
        if source is None:
            print(f"No local template found for {filename}")
            continue

        shutil.copy2(source, output_path)
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] "
            f"Preserved {product} from {os.path.basename(source)} -> {filename}"
        )
        preserved.append(filename)

    return preserved


def products_ready(validity_time: datetime, staging_dir: str) -> bool:
    return all(
        os.path.isfile(os.path.join(staging_dir, grib_filename(validity_time, product)))
        and os.path.getsize(os.path.join(staging_dir, grib_filename(validity_time, product))) > 0
        for product in GRIB_PRODUCTS
    )


def write_grib_products(
    validity_time: datetime,
    source_1deg: str,
    source_025deg: str | None,
    staging_dir: str = WEATHER_STAGING_DIR,
    *,
    template_dirs: tuple[str, ...] = (),
) -> list[str]:
    """Slice and write all required GRIB products for one validity time."""
    os.makedirs(staging_dir, exist_ok=True)
    written: list[str] = []
    for product in GRIB_GENERATED_1DEG:
        filename = grib_filename(validity_time, product)
        output_path = os.path.join(staging_dir, filename)
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] "
            f"Slicing {product} -> {filename}"
        )
        if slice_grib_product(source_1deg, output_path, product):
            written.append(filename)
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"GRIB file written: {filename}"
            )
        else:
            print(f"Failed to build {filename}")

    if source_025deg:
        for product in GRIB_GENERATED_025DEG:
            filename = grib_filename(validity_time, product)
            output_path = os.path.join(staging_dir, filename)
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"Slicing {product} -> {filename}"
            )
            if slice_grib_product(source_025deg, output_path, product):
                written.append(filename)
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"GRIB file written: {filename}"
                )
            else:
                print(f"Failed to build {filename}")

    written.extend(
        _preserve_cloud_products(validity_time, staging_dir, template_dirs)
    )
    return written


class GribEngine:
    """Handles the 3-hour GRIB refresh cycle."""

    def __init__(
        self,
        staging_dir: str = WEATHER_STAGING_DIR,
        *,
        template_dirs: tuple[str, ...] = (),
    ) -> None:
        self.staging_dir = staging_dir
        self.template_dirs = template_dirs
        self.last_processed_times: set[datetime] = set()
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def run_cycle(self, validity_time: datetime, now: datetime | None = None, *, force: bool = False) -> bool:
        now = now or datetime.now(validity_time.tzinfo)
        if not force and products_ready(validity_time, self.staging_dir):
            self.last_processed_times.add(validity_time)
            return True

        date_cycle, cycle, forecast = resolve_gfs_source(validity_time, now)
        print(
            f"\n[{datetime.now().strftime('%H:%M:%S')}] GRIB cycle -> "
            f"validity {validity_time.strftime('%Y-%m-%d %H:%M')}Z "
            f"(gfs.{date_cycle} cycle {cycle:02d}Z + f{forecast:03d})"
        )

        source_1deg = download_grib_source(date_cycle, cycle, forecast, "1p00")
        if not source_1deg:
            return False

        source_025deg = download_grib_source(date_cycle, cycle, forecast, "0p25")
        write_grib_products(
            validity_time,
            source_1deg,
            source_025deg,
            self.staging_dir,
            template_dirs=self.template_dirs,
        )
        if products_ready(validity_time, self.staging_dir):
            self.last_processed_times.add(validity_time)
            return True

        missing = [
            grib_filename(validity_time, product)
            for product in GRIB_PRODUCTS
            if not os.path.isfile(
                os.path.join(self.staging_dir, grib_filename(validity_time, product))
            )
        ]
        print(f"Incomplete GRIB set; still missing: {', '.join(missing)}")
        return False

    def should_run(self, validity_time: datetime) -> bool:
        return validity_time not in self.last_processed_times
