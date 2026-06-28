# METAR download
 
How this app fetches global METAR data, formats it for X-Plane, and serves it through the local weather proxy.

## Overview

METAR handling lives in [`metar_engine.py`](../metar_engine.py) and is orchestrated by [`main.py`](../main.py).

The app does **not** download per-airport METAR files. It fetches **one global dump** from public NOAA/AWC sources and writes **one or more timestamped text files** into [`.weather_data/`](../.weather_data/) (staging). X-Plane later receives those files via the HTTPS proxy manifest.

**Laminar is not used for METAR.** All METAR data comes from U.S. government aviation weather services.

## End-to-end flow

```
main.py
  → round_to_quarter_hour(UTC now)
  → MetarEngine.run_cycle()
  → fetch_global_metars()
       ├─ (1) aviationweather.gov  metars.cache.csv.gz   [primary]
       └─ (2) tgftp.nws.noaa.gov   cycles/{hour}Z.TXT   [fallback]
  → write_metar_file()  →  .weather_data/metar-YYYY-MM-DD-HH.MM.txt
  → weather_proxy.py builds manifest from staging
  → X-Plane downloads via proxy  →  Output/real weather/
```

## When a download runs

[`main.py`](../main.py) calls `_maybe_run_metar()` on each loop iteration (default interval: 15 minutes from `user_settings.json`).

1. Current UTC time is rounded **down** to the nearest 15-minute boundary via [`round_to_quarter_hour()`](../time_utils.py) (e.g. `03:37` → `03:30`).
2. [`MetarEngine.should_run()`](../metar_engine.py) returns true only if that quarter-hour has not already been processed (`last_processed_time` guard).
3. **Update weather now** resets `last_processed_time` and forces an immediate cycle.

METAR refreshes at most **once per 15-minute UTC slot**.

## Data sources (NOAA / AWC only)

[`fetch_global_metars()`](../metar_engine.py) tries sources in this order:

### Primary: Aviation Weather Center global cache

| | |
|---|---|
| **URL** | `https://aviationweather.gov/data/cache/metars.cache.csv.gz` |
| **Config** | [`METAR_CACHE_URL`](../config.py) |
| **Format** | gzip-compressed CSV |
| **Why preferred** | Updates continuously; each row has its own `observation_time` |

Processing (`_fetch_metar_cache()` → `_format_cache_metar()`):

1. Decompress gzip
2. Parse CSV rows
3. For each station, take `raw_text` (the METAR string)
4. Build X-Plane's expected block:

   ```
   YYYY/MM/DD HH:MM
   METAR KJFK 281353Z ...
   ```

5. Join all stations into one text blob

### Fallback: NOAA hourly cycle files

If the cache fails (network error, empty file, etc.):

| | |
|---|---|
| **URL** | `https://tgftp.nws.noaa.gov/data/observations/metar/cycles/{hour:02d}Z.TXT` |
| **Config** | [`METAR_CYCLE_URL`](../config.py) |
| **Logic** | Try **current UTC hour** first, then **previous hour** |

These files are already close to X-Plane's text layout (no CSV conversion).

Both sources use `requests.get()` with a 120 s timeout and `User-Agent: xplane-weather-fallback/1.0`.

METAR downloads use normal HTTPS to NOAA/AWC. They do **not** go through the local proxy or the `weatherservice.x-plane.com` hosts redirect.

## What is downloaded from Laminar?

**Nothing.** METAR is entirely sourced from:

- Aviation Weather Center (`aviationweather.gov`)
- NOAA FTP (`tgftp.nws.noaa.gov`)

Laminar's weather service is only involved for certain GRIB products (see [grib-download.md](grib-download.md)).

## How files are written

[`MetarEngine.run_cycle()`](../metar_engine.py) calls `write_metar_file()`, which saves to **staging** (`.weather_data/`), not X-Plane's folder.

Filename format ([`metar_filename()`](../time_utils.py)):

```
metar-2026-06-28-03.30.txt
```

For each successful download the engine may write **up to 3 files** with the **same content**:

| File | Reason |
|------|--------|
| `metar-{target_time}.txt` | Primary file for the cycle being processed |
| `metar-{target_time - 15min}.txt` | X-Plane's manifest expects **two** METAR entries 15 minutes apart |
| `metar-{current_rounded}.txt` | Keeps the current quarter-hour file fresh between boundary ticks |

## How X-Plane gets the data

The app does **not** push METARs directly into X-Plane's cache.

1. Files sit in `.weather_data/`
2. [`WeatherManifestBuilder`](../weather_proxy.py) discovers the current and previous quarter-hour METAR files
3. The local proxy serves a manifest with two METAR entries (`created_at_datetime` + MD5 checksum)
4. X-Plane (with hosts redirect) downloads each URL from the proxy
5. X-Plane saves into `Output/real weather/`

Press **Refresh** in X-Plane weather settings after the app finishes a cycle.

## Epoch fallback (proxy down only)

If port 443 cannot bind, [`publish_epoch_aliases()`](../weather_proxy.py) copies the latest staging METAR to `metar-1970-01-01-00.00.txt` in X-Plane's cache. This is **disabled** when the proxy is running.

## Example log

```
[00:36:11] METAR cycle -> metar-2026-06-28-03.30.txt
[00:36:11] Fetching global METAR cache ...
[00:36:13] METAR file written: metar-2026-06-28-03.30.txt
[00:36:13] METAR file written: metar-2026-06-28-03.15.txt
```

## Key files

| File | Role |
|------|------|
| [`config.py`](../config.py) | `METAR_CACHE_URL`, `METAR_CYCLE_URL` |
| [`metar_engine.py`](../metar_engine.py) | Download, format, write |
| [`time_utils.py`](../time_utils.py) | 15-minute rounding + filename |
| [`main.py`](../main.py) | Scheduling loop |
| [`weather_proxy.py`](../weather_proxy.py) | Manifest + serving staging METARs |
