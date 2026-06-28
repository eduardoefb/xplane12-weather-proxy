# X-Plane Weather Fallback

Local METAR and GRIB weather server for **X-Plane 12** when Laminar’s `weatherservice.x-plane.com` is down or unreliable.

The app downloads real weather from NOAA (and auxiliary products from Laminar when reachable), writes files into X-Plane’s `Output/real weather` folder, and runs a local HTTPS proxy that mimics Laminar’s weather API so the sim can refresh normally.

## How it works

X-Plane does **not** scan the weather folder on its own. It always:

1. Fetches a JSON **manifest** from `https://weatherservice.x-plane.com/api/v1/manifest/debug/<timestamp>`
2. Validates required GRIB types and exactly **two** validity timestamps (≤ 12 hours apart)
3. Downloads each METAR/GRIB URL from the manifest into `Output/real weather`
4. Loads the data into the sim

This project intercepts that hostname (via `/etc/hosts`) and serves a compatible manifest plus local files from a Python HTTPS proxy on port **443**.

```
X-Plane  →  weatherservice.x-plane.com:443
              ↓  (/etc/hosts → 127.0.0.1)
           weather_proxy.py
              ↓
           Output/real weather/  (METAR + GRIB written by this app)
```

## Requirements

- **Python 3.11+**
- **X-Plane 12** with real-weather downloads enabled
- Network access to NOAA NOMADS and METAR sources
- **Port 443** available locally (requires `sudo` or `cap_net_bind_service` on the Python binary)
- System **eccodes** library (for GRIB slicing):

  ```bash
  # Debian / Ubuntu
  sudo apt install libeccodes-dev

  # optional but useful
  sudo apt install mkcert wgrib2
  ```

## Quick start

```bash
git clone <your-repo-url> xplane-weather-fallback
cd xplane-weather-fallback

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1. Trusted HTTPS certificate (recommended)

X-Plane validates TLS like any other HTTPS client. A plain self-signed cert will be rejected.

```bash
mkcert -install
# Certificates are generated automatically on first run, or manually:
mkdir -p .weather_proxy_certs
mkcert -cert-file .weather_proxy_certs/weatherservice.crt \
       -key-file .weather_proxy_certs/weatherservice.key \
       weatherservice.x-plane.com
```

### 2. Redirect weather hostname to localhost

```bash
# Add or uncomment in /etc/hosts:
127.0.0.1 weatherservice.x-plane.com
```

Or use the one-liner from the app’s **Setup help** button / startup banner.

### 3. Run the server

**GUI (default):**

```bash
python main.py
```

**Headless:**

```bash
python main.py --cli
```

On first launch, set your X-Plane root directory in the UI (saved to `user_settings.json`, git-ignored).

### 4. Refresh in X-Plane

Fully quit and restart X-Plane if needed, then press **Refresh** in **Settings → General → Weather**.

Allow a minute after startup for the first METAR/GRIB download cycle to finish.

## Binding port 443 without sudo

```bash
sudo setcap 'cap_net_bind_service=+ep' "$(readlink -f .venv/bin/python3)"
```

Then you can run `python main.py` as a normal user.

## What gets downloaded

| Source | Products |
|--------|----------|
| NOAA GFS | `wind-v2`, `temp-v2`, `dewp-v2`, `pres`, `srfc`, `prcp`, `trop-v2`, `svis` |
| Laminar (when online) | `snod`, WIFS turbulence/cloud (`turb`, `cnmb_*`) |
| Local reuse | `calt`, `ccov` (WAFS-encoded; copied from prior X-Plane downloads or `~/tmp/weather/`) |

METAR files are fetched every **15 minutes**; GRIB every **3 hours**.

## Project layout

| File | Role |
|------|------|
| `main.py` | Orchestrator: METAR/GRIB cycles + proxy |
| `metar_engine.py` | NOAA METAR download and file writing |
| `grib_engine.py` | GFS download, slice, and X-Plane GRIB generation |
| `aux_products.py` | Snow depth + WIFS products from Laminar |
| `weather_proxy.py` | HTTPS server emulating `weatherservice.x-plane.com` |
| `gui.py` | Desktop UI (ttkbootstrap) |
| `setup_utils.py` | Hosts + mkcert setup checks |
| `user_settings.json` | Local X-Plane path and update interval (created at runtime) |

## Troubleshooting

**“Not updated yet” or epoch (1970) filenames**

- Manifest was rejected. Check `X-Plane_12/Log.txt` for `WXR` lines.
- Ensure the app is running and port 443 is listening.
- Confirm `/etc/hosts` redirect is active and mkcert is installed.

**SSL / certificate errors in Log.txt**

- Run `mkcert -install` and restart the app so it regenerates certs in `.weather_proxy_certs/`.

**Missing `calt` / `ccov`**

- These use WAFS-specific encoding not available from raw GFS. Let X-Plane download them once from Laminar (hosts redirect disabled), or copy existing files to `~/tmp/weather/`.

**Proxy won’t start on port 443**

- Another process is using 443, or you need `sudo` / `setcap` (see above).

**Test the local manifest**

```bash
curl -s "https://weatherservice.x-plane.com/api/v1/manifest/debug/$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  | python3 -m json.tool | head
```

## Reverting to Laminar’s servers

Comment out or remove the `/etc/hosts` line for `weatherservice.x-plane.com` and stop this app. X-Plane will use the official service again.

## License

Private / use at your own risk. Not affiliated with Laminar Research.
