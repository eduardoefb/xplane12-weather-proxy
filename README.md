# X-Plane Weather Fallback

Local METAR and GRIB weather server for **X-Plane 12** when Laminar’s `weatherservice.x-plane.com` is down or unreliable.

The app downloads real weather from NOAA (and auxiliary products from Laminar when reachable), writes files into a local **staging directory** (`.weather_data/`), and runs a local HTTPS proxy that mimics Laminar’s weather API. X-Plane downloads from the proxy into its own `Output/real weather` cache.

Runs on **Linux** and **Windows** (native Python; WSL not required).

## How it works

X-Plane does **not** scan the weather folder on its own. It always:

1. Fetches a JSON **manifest** from `https://weatherservice.x-plane.com/api/v1/manifest/debug/<timestamp>`
2. Validates required GRIB types and exactly **two** validity timestamps (≤ 12 hours apart)
3. Downloads each METAR/GRIB URL from the manifest into `Output/real weather`
4. Loads the data into the sim

This project intercepts that hostname (via the system **hosts file**) and serves a compatible manifest plus local files from a Python HTTPS proxy on port **443**.

```
This app  →  NOAA / Laminar  →  .weather_data/  (staging)
                                      ↑
X-Plane  →  weatherservice.x-plane.com:443  →  proxy serves staging files
                                      ↓
                              Output/real weather/  (X-Plane's cache)
```

X-Plane uses normal OS name resolution. You add one line to the hosts file so `weatherservice.x-plane.com` resolves to `127.0.0.1` instead of Laminar’s servers. The app itself still reaches Laminar over the public internet (via Google/Cloudflare DNS) to download auxiliary products such as snow depth and WIFS turbulence data.

## Directories

| Path | Written by | Purpose |
|------|------------|---------|
| `.weather_data/` | This app | Staging — METAR/GRIB downloads and GRIB slicing. The HTTPS proxy serves files from here. |
| `Output/real weather/` | X-Plane | Sim cache — populated when X-Plane refreshes weather and downloads from the proxy. |
| `.grib_cache/` | This app | Raw NOAA GFS source files (intermediate downloads). |
| `.weather_proxy_certs/` | mkcert / app | TLS certificate for the local proxy. |
| `user_settings.json` | App UI | X-Plane root path and update interval (created at runtime). |

On first run after an upgrade, if `.weather_data/` is empty the app may copy existing files from your X-Plane weather cache into staging so the proxy can serve them immediately.

**Typical workflow:** start this app → wait for a METAR/GRIB cycle (or click **Update weather now**) → press **Refresh** in X-Plane weather settings.

## Requirements

- **Python 3.11+**
- **X-Plane 12** with real-weather downloads enabled
- Network access to NOAA NOMADS and METAR sources
- **Port 443** available locally
- System **eccodes** library (for GRIB slicing)

---

## Quick start — Linux

### 1. Install system dependencies

```bash
# Debian / Ubuntu
sudo apt install libeccodes-dev

# optional but useful
sudo apt install mkcert wgrib2
```

### 2. Clone and install Python packages

```bash
git clone <your-repo-url> xplane-weather-fallback
cd xplane-weather-fallback

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Trusted HTTPS certificate (recommended)

X-Plane validates TLS like any other HTTPS client. A plain self-signed cert will be rejected.

```bash
mkcert -install
# Certificates are generated automatically on first run, or manually:
mkdir -p .weather_proxy_certs
mkcert -cert-file .weather_proxy_certs/weatherservice.crt \
       -key-file .weather_proxy_certs/weatherservice.key \
       weatherservice.x-plane.com
```

### 4. Redirect weather hostname to localhost

Add or uncomment this line in `/etc/hosts`:

```
127.0.0.1 weatherservice.x-plane.com
```

One-liner (requires sudo):

```bash
sudo sed -i 's/^#\?.*weatherservice\.x-plane\.com.*/127.0.0.1 weatherservice.x-plane.com/' /etc/hosts
```

Verify:

```bash
ping -c 1 weatherservice.x-plane.com   # should reply from 127.0.0.1
```

### 5. Bind port 443 without sudo (optional)

Ports below 1024 normally require root on Linux. Grant the capability once:

```bash
sudo setcap 'cap_net_bind_service=+ep' "$(readlink -f .venv/bin/python3)"
```

Alternatively, run the app with `sudo` (not recommended for daily use).

### 6. Run the server

**GUI (default):**

```bash
python main.py
```

**Headless:**

```bash
python main.py --cli
```

On first launch, set your X-Plane root directory in the UI (saved to `user_settings.json`, git-ignored). Default path: `~/X-Plane_12`. The UI shows both the staging path (`.weather_data/`) and the X-Plane weather cache.

### 7. Refresh in X-Plane

Fully quit and restart X-Plane if needed, then press **Refresh** in **Settings → General → Weather**. X-Plane will download files from the local proxy into `Output/real weather/`.

Allow a minute after startup for the first METAR/GRIB download cycle to finish in `.weather_data/` before refreshing in the sim.

---

## Quick start — Windows

### 1. Install Python and dependencies

1. Install [Python 3.11+](https://www.python.org/downloads/) — check **“Add python.exe to PATH”** during setup.
2. Install **eccodes** (required for GRIB processing). Pick one approach:
   - **Conda (easiest):** install [Miniconda](https://docs.conda.io/en/latest/miniconda.html), then:
     ```cmd
     conda create -n xplane-weather python=3.11
     conda activate xplane-weather
     conda install -c conda-forge eccodes cfgrib
     ```
   - **Manual:** follow [ECMWF eccodes Windows build docs](https://confluence.ecmwf.int/display/ECC/Releases) and ensure the eccodes DLL directory is on your `PATH`.
3. Install **mkcert** (recommended for trusted HTTPS):
   ```cmd
   choco install mkcert
   ```
   or with [Scoop](https://scoop.sh/): `scoop install mkcert`

### 2. Clone and install Python packages

Open **Command Prompt** or **PowerShell** in the project folder:

```cmd
git clone <your-repo-url> xplane-weather-fallback
cd xplane-weather-fallback

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Trusted HTTPS certificate (recommended)

Run **Command Prompt as Administrator** (mkcert needs to install a local CA once):

```cmd
mkcert -install
```

Certificates are generated automatically on first app run, or create them manually:

```cmd
mkdir .weather_proxy_certs
mkcert -cert-file .weather_proxy_certs\weatherservice.crt ^
       -key-file .weather_proxy_certs\weatherservice.key ^
       weatherservice.x-plane.com
```

### 4. Redirect weather hostname to localhost

X-Plane resolves `weatherservice.x-plane.com` through the Windows hosts file — the same mechanism as `/etc/hosts` on Linux. No X-Plane setting is required.

1. Open **Notepad** → **Run as administrator**
2. **File → Open** → navigate to:
   ```
   C:\Windows\System32\drivers\etc\hosts
   ```
   (Change the file-type filter to **All Files** — `hosts` has no extension.)
3. Add this line at the end:
   ```
   127.0.0.1 weatherservice.x-plane.com
   ```
4. Save and close.

Verify in a new Command Prompt:

```cmd
ping weatherservice.x-plane.com
```

You should see replies from **127.0.0.1**, not a public IP.

The app’s **Setup help** button shows these steps if the redirect is missing.

### 5. Run the server as Administrator

Windows requires elevated privileges to bind port **443**. Right-click **Command Prompt** or **PowerShell** → **Run as administrator**, then:

```cmd
cd xplane-weather-fallback
.venv\Scripts\activate
python main.py
```

**Headless:**

```cmd
python main.py --cli
```

On first launch, set your X-Plane root directory in the UI. Default paths checked: `%USERPROFILE%\X-Plane 12`, `C:\X-Plane 12`, `D:\X-Plane 12`.

### 6. Refresh in X-Plane

Fully quit and restart X-Plane if needed, then press **Refresh** in **Settings → General → Weather**. X-Plane will download files from the local proxy into `Output\real weather\`.

Allow a minute after startup for the first METAR/GRIB download cycle to finish in `.weather_data\` before refreshing in the sim.

---

## What gets downloaded

| Source | Products |
|--------|----------|
| NOAA GFS | `wind-v2`, `temp-v2`, `dewp-v2`, `pres`, `srfc`, `prcp`, `trop-v2`, `svis` |
| Laminar (when online) | `snod`, WIFS turbulence/cloud (`turb`, `cnmb_*`) |
| Local reuse | `calt`, `ccov` (WAFS-encoded; copied from prior X-Plane downloads or template dirs) |

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
| `platform_support.py` | OS-specific paths, DNS, HTTPS, TLS helpers |
| `.weather_data/` | Local staging directory (METAR + GRIB written by this app) |
| `user_settings.json` | Local X-Plane path and update interval (created at runtime) |

## Troubleshooting

**Weather updated in app but not in X-Plane**

- The sim loads from `Output/real weather/`, not from `.weather_data/`. Press **Refresh** in X-Plane after the app finishes a download cycle.
- Confirm the proxy is running on port 443 and the hosts redirect is active.

**“Not updated yet” or epoch (1970) filenames**

- Manifest was rejected. Check `X-Plane 12/Log.txt` for `WXR` lines.
- Ensure the app is running and port 443 is listening.
- Confirm the hosts-file redirect is active and mkcert is installed.
- The app only creates `1970-01-01` symlinks when the HTTPS proxy **cannot** start (partial fallback). When the proxy is running, X-Plane uses real timestamps from the manifest and those epoch files are removed.

**SSL / certificate errors in Log.txt**

- Run `mkcert -install` and restart the app so it regenerates certs in `.weather_proxy_certs/`.

**Missing `calt` / `ccov`**

- These use WAFS-specific encoding not available from raw GFS. Let X-Plane download them once from Laminar (hosts redirect disabled), or copy existing files from `Output/real weather/` or other template directories (see `user_settings.weather_template_dirs`).

**Proxy won’t start on port 443**

- Another process is using 443 (IIS, Skype, another web server).
- **Linux:** run with `sudo`, or use `setcap` (see Linux quick start step 5).
- **Windows:** run the app as Administrator.

**Hosts redirect not working (Windows)**

- Did you edit `hosts` with Administrator privileges? A normal Notepad save will fail silently or save to the wrong place.
- Flush the DNS cache after editing: `ipconfig /flushdns`
- Confirm with `ping weatherservice.x-plane.com` — must show `127.0.0.1`.

**Test the local manifest**

Linux:

```bash
curl -s "https://weatherservice.x-plane.com/api/v1/manifest/debug/$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  | python3 -m json.tool | head
```

Windows (PowerShell):

```powershell
curl.exe -s "https://weatherservice.x-plane.com/api/v1/manifest/debug/$((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))"
```

## Reverting to Laminar’s servers

Comment out or remove the hosts entry for `weatherservice.x-plane.com` and stop this app.

- **Linux:** edit `/etc/hosts` and prefix the line with `#`, or remove it.
- **Windows:** edit `C:\Windows\System32\drivers\etc\hosts` (as Administrator) and remove or comment out the line.

X-Plane will use the official service again on the next refresh.

## License

Private / use at your own risk. Not affiliated with Laminar Research.
