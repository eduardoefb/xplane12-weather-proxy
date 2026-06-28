"""Shared configuration for the X-Plane fallback weather server."""

import os

from platform_support import cloud_template_dirs, default_xplane_root

# Local staging directory for METAR/GRIB produced by this app (served by the proxy).
WEATHER_STAGING_DIR = os.path.join(os.path.dirname(__file__), ".weather_data")

# X-Plane's real-weather cache (X-Plane writes here after downloading from the proxy).
XP_WEATHER_DIR = os.path.join(default_xplane_root(), "Output", "real weather")

# METAR cycle dump (X-Plane format with observation time headers).
METAR_CYCLE_URL = (
    "https://tgftp.nws.noaa.gov/data/observations/metar/cycles/{hour:02d}Z.TXT"
)
# Global cache fallback (CSV; reformatted to X-Plane layout).
METAR_CACHE_URL = "https://aviationweather.gov/data/cache/metars.cache.csv.gz"

# NOAA NOMADS GFS 0.25-degree aviation weather (AWF) product.
NOMADS_GFS_BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"
NOMADS_FILTER_GFS_025 = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
NOMADS_FILTER_GFS_100 = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_1p00.pl"

# HTTP client settings.
HTTP_TIMEOUT_SECONDS = 120
HTTP_USER_AGENT = "xplane-weather-fallback/1.0"

# Loop polling interval (seconds).
MAIN_LOOP_SLEEP_SECONDS = 30
METAR_RETRY_SLEEP_SECONDS = 60
GRIB_RETRY_SLEEP_SECONDS = 300

# GFS model runs every 6 hours; allow time for publication.
GFS_PUBLICATION_DELAY_HOURS = 4

# Pressure levels used by X-Plane wind-v2 / temp-v2 (12 isobaric levels).
ISOBARIC_LEVELS_HPA = (
    100, 150, 200, 250, 300, 400, 500, 600, 700, 800, 900, 950,
)

# GRIB products required by X-Plane 12 for each 3-hour validity window.
GRIB_PRODUCTS = (
    "calt",
    "ccov",
    "dewp-v2",
    "prcp",
    "pres",
    "srfc",
    "svis",
    "temp-v2",
    "trop-v2",
    "wind-v2",
)

# Built from NOAA GFS; 1.00-degree fields match X-Plane's grid.
GRIB_GENERATED_1DEG = (
    "dewp-v2",
    "prcp",
    "pres",
    "srfc",
    "temp-v2",
    "trop-v2",
    "wind-v2",
)

# 0.25-degree surface visibility.
GRIB_GENERATED_025DEG = ("svis",)

# Cloud-layer files use WAFS-specific encoding; reuse existing local copies.
GRIB_PRESERVED_PRODUCTS = ("calt", "ccov")

# Extra directories searched for calt/ccov templates (official X-Plane or prior downloads).
GRIB_CLOUD_TEMPLATE_DIRS = cloud_template_dirs()

# Local cache for downloaded source GRIB files.
GRIB_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".grib_cache")

# Local HTTPS proxy emulating weatherservice.x-plane.com (port 443 needs root/cap).
WEATHER_PROXY_ENABLED = True
WEATHER_PROXY_HOST = "0.0.0.0"
WEATHER_PROXY_PORT = 443
WEATHER_PROXY_CERT_DIR = os.path.join(os.path.dirname(__file__), ".weather_proxy_certs")
