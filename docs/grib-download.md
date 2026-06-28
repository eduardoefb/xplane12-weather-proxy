# GRIB download

How this app builds X-Plane GRIB files from NOAA GFS, supplements them with Laminar products, and serves everything through the local weather proxy.

## Overview

GRIB handling spans three modules:

| Module | Role |
|--------|------|
| [`grib_engine.py`](../grib_engine.py) | NOAA GFS download, slice, and most products |
| [`aux_products.py`](../aux_products.py) | Snow depth and WIFS products from Laminar |
| [`main.py`](../main.py) | Scheduling (3-hour validity windows) |

Unlike METAR (one global text dump), GRIB is **many binary files per validity window** — up to **10 nomads products × 2 windows**, plus **snod** and **4 WIFS types × 2 windows**.

## End-to-end flow

```
main.py
  → grib_validity_windows(now)     # two 3-hour UTC windows
  → for each window:
       GribEngine.run_cycle()
         → resolve_gfs_source()      # pick GFS cycle + forecast hour
         → download_grib_source()   # NOAA NOMADS → .grib_cache/
         → write_grib_products()    # slice → .weather_data/
         → copy calt/ccov templates
  → sync_aux_products()              # Laminar snod + WIFS → .weather_data/
  → weather_proxy.py manifest
  → X-Plane downloads via proxy  →  Output/real weather/
```

## When a GRIB cycle runs

[`main.py`](../main.py) triggers GRIB work via `_maybe_run_grib()`:

- Computes **two validity windows** with [`grib_validity_windows()`](../time_utils.py): the current 3-hour boundary and the next (e.g. at `04:30Z` → `03:00Z` and `06:00Z`).
- Runs once per window if that validity is not already in `last_processed_times`.
- On startup and when the 3-hour anchor changes, runs with `force=True`.
- Retries incomplete sets every 300 s (`GRIB_RETRY_SLEEP_SECONDS`) if not forced.
- GRIB work runs in a **background thread** so the UI is not blocked.
- After each GRIB batch, [`sync_aux_products()`](../aux_products.py) runs for Laminar products.

**Update weather now** discards `last_processed_times` and forces both windows.

## Part 1: NOAA GFS (most GRIB products)

### Picking the model run

[`resolve_gfs_source(validity, now)`](../time_utils.py) maps a target validity time to:

- `date_cycle` — `YYYYMMDD`
- `cycle` — GFS run hour (0, 6, 12, 18)
- `forecast` — forecast hour `f###` (multiple of 3, up to 36)

It picks a recent cycle whose forecast reaches the validity time and respects [`GFS_PUBLICATION_DELAY_HOURS`](../config.py) (4 hours) so NOAA has likely published the file.

Example log:

```
GRIB cycle -> validity 2026-06-28 03:00Z (gfs.20260627 cycle 18Z + f009)
```

### Downloading raw source files

[`download_grib_source()`](../grib_engine.py) fetches intermediate GRIB2 into [`.grib_cache/`](../.grib_cache/):

| Resolution | Used for | Source |
|------------|----------|--------|
| **1.00°** | wind, temp, dewp, pres, srfc, prcp, trop | NOMADS `filter_gfs_1p00.pl`; fallback AWF file |
| **0.25°** | surface visibility (`svis`) | NOMADS `filter_gfs_0p25.pl` |

- Streams with `requests.get(stream=True)`
- Caches as `gfs_{resolution}_{date}_{cycle}_f{forecast}.grib2`
- Skips re-download if cache exists and is > 1 KB

NOAA downloads use normal HTTPS — not the local proxy.

### Slicing into X-Plane products

[`write_grib_products()`](../grib_engine.py) writes per-product files to **staging** (`.weather_data/`):

```
GRIB-2026-06-28-03.00-ZULU-wind-v2.grib
```

#### Generated from NOAA (8 types)

| Product | Grid | Key fields |
|---------|------|------------|
| `wind-v2` | 1° | U/V at 12 isobaric levels + 10 m |
| `temp-v2` | 1° | Temperature at 12 levels + 2 m |
| `dewp-v2` | 1° | RH at 12 levels + 2 m (`2r` layer) |
| `pres` | 1° | MSL / surface pressure |
| `srfc` | 1° | Orography |
| `prcp` | 1° | Precipitation rate |
| `trop-v2` | 1° | Tropopause height/temperature |
| `svis` | 0.25° | Surface visibility |

Slicing ([`slice_grib_product()`](../grib_engine.py)):

1. Try **wgrib2** (if on `PATH`)
2. Else **eccodes** Python API with per-product message matchers

#### Copied from local templates (2 types)

| Product | Source |
|---------|--------|
| `calt` | Copied via [`_preserve_cloud_products()`](../grib_engine.py) |
| `ccov` | Same |

These use **WAFS-specific encoding** that raw GFS cannot produce. The engine searches staging, X-Plane's `Output/real weather/`, and [`GRIB_CLOUD_TEMPLATE_DIRS`](../config.py), then copies the nearest match.

## Part 2: Laminar downloads

[`sync_aux_products()`](../aux_products.py) fetches products NOAA does not provide. These are required for a valid X-Plane manifest.

### What is downloaded from Laminar

| Manifest section | Laminar type | Local filename suffix | Description |
|------------------|--------------|----------------------|-------------|
| `nomads_extra` | `snod` | `snod` | Snow depth |
| `wifs` | `cnmb_coverage` | `cbcv-v2` | Cloud coverage |
| `wifs` | `cnmb_bases` | `cbbs-v2` | Cloud bases |
| `wifs` | `cnmb_tops` | `cbtp-v2` | Cloud tops |
| `wifs` | `turb` | `turb-v2` | Turbulence / icing |

### How Laminar download works (hosts bypass)

Your hosts file redirects `weatherservice.x-plane.com` to localhost, so the app **cannot** use normal DNS to reach Laminar for aux downloads. Instead:

1. [`resolve_public_ipv4()`](../platform_support.py) — query Google (`8.8.8.8`) / Cloudflare (`1.1.1.1`) DNS for Laminar's real IP
2. [`https_get_bypass_hosts()`](../platform_support.py) — fetch Laminar's official manifest over TLS to that IP (SNI/Host still `weatherservice.x-plane.com`)
3. For each manifest entry, [`https_download_bypass_hosts()`](../platform_support.py) downloads the GRIB bytes into `.weather_data/`

If Laminar is unreachable, [`_copy_template()`](../aux_products.py) reuses the newest cached file from staging, X-Plane's weather cache, or template directories.

### WIFS mirroring

Laminar publishes WIFS at **03:00Z** on the validity day. X-Plane requires exactly **two** GRIB timestamps in the manifest. [`_mirror_wifs_to_validity_windows()`](../aux_products.py) copies the 03:00Z WIFS files into both active 3-hour windows (e.g. `03:00Z` and `06:00Z`) so the proxy manifest stays valid.

## Completeness check

[`products_ready()`](../grib_engine.py) verifies all 10 types in [`GRIB_PRODUCTS`](../config.py) exist in staging for each validity window:

```
calt, ccov, dewp-v2, prcp, pres, srfc, svis, temp-v2, trop-v2, wind-v2
```

`snod` and WIFS are validated separately by the proxy manifest builder — they must be present for X-Plane to accept the manifest.

## How X-Plane gets GRIB files

1. Files live in `.weather_data/`
2. [`WeatherManifestBuilder.build()`](../weather_proxy.py) scans staging for both validity windows
3. Builds manifest sections `nomads`, `nomads_extra`, `wifs` with URLs, MD5 checksums, and `depicts_weather_at_datetime`
4. X-Plane downloads from the proxy and saves to `Output/real weather/`

The proxy warns if manifest types are missing or if GRIB timestamps ≠ 2.

## Directories

| Path | Contents |
|------|----------|
| `.grib_cache/` | Large raw NOAA GFS downloads (reused across slices) |
| `.weather_data/` | Final X-Plane-format GRIB files served by the proxy |
| `Output/real weather/` | X-Plane's copy after Refresh |

## Source summary

| Product group | Source |
|---------------|--------|
| `wind-v2`, `temp-v2`, `dewp-v2`, `pres`, `srfc`, `prcp`, `trop-v2`, `svis` | **NOAA GFS** (NOMADS) |
| `calt`, `ccov` | **Local templates** (often from prior Laminar/X-Plane downloads) |
| `snod`, `cbcv-v2`, `cbbs-v2`, `cbtp-v2`, `turb-v2` | **Laminar** (`weatherservice.x-plane.com`, DNS bypass) |

## Example log

```
GRIB cycle -> validity 2026-06-28 03:00Z (gfs.20260627 cycle 18Z + f009)
Using cached GRIB: gfs_1p00_20260627_18_f009.grib2
Slicing dewp-v2 -> GRIB-2026-06-28-03.00-ZULU-dewp-v2.grib
GRIB file written: GRIB-2026-06-28-03.00-ZULU-dewp-v2.grib
Preserved calt from GRIB-2026-06-27-21.00-ZULU-calt.grib -> GRIB-2026-06-28-03.00-ZULU-calt.grib
[aux] Downloading snod (03:00Z) ...
[aux] Downloading turb (03:00Z) ...
```

## Key files

| File | Role |
|------|------|
| [`config.py`](../config.py) | Product lists, NOMADS URLs |
| [`grib_engine.py`](../grib_engine.py) | NOAA download, slice, calt/ccov copy |
| [`aux_products.py`](../aux_products.py) | Laminar snod + WIFS |
| [`time_utils.py`](../time_utils.py) | Validity windows, GFS cycle resolution |
| [`platform_support.py`](../platform_support.py) | DNS/HTTPS bypass for Laminar |
| [`weather_proxy.py`](../weather_proxy.py) | Manifest assembly |
| [`main.py`](../main.py) | GRIB scheduling + aux sync |

## See also

- [metar-download.md](metar-download.md) — METAR flow (NOAA/AWC only, no Laminar)
