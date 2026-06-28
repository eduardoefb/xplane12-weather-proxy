# X-Plane Weather Fallback (Used for backup only, when X-Plane weather server is down)

Local METAR and GRIB weather server for **X-Plane 12** when Laminar’s `weatherservice.x-plane.com` is down or unreliable.

This app downloads weather from NOAA (and selected GRIB products from Laminar when reachable), writes files into a **staging directory** (`.weather_data/`), and runs a local HTTPS proxy on port **443** that mimics Laminar’s weather API. X-Plane downloads from the proxy into its own `Output/real weather` cache.

Runs on **Linux** and **Windows** (native Python; WSL not required).

**Repository:** [github.com/eduardoefb/xplane12-weather-proxy](https://github.com/eduardoefb/xplane12-weather-proxy)

## Documentation

| Topic | Guide |
|-------|--------|
| METAR download (NOAA / AWC, 15-minute cycle) | [docs/metar-download.md](docs/metar-download.md) |
| GRIB download (NOAA GFS + Laminar aux products, 3-hour cycle) | [docs/grib-download.md](docs/grib-download.md) |

## How it works

X-Plane does **not** scan the weather folder on its own. It always:

1. Fetches a JSON **manifest** from `https://weatherservice.x-plane.com/api/v1/manifest/debug/<timestamp>`
2. Validates required GRIB types and exactly **two** validity timestamps (≤ 12 hours apart)
3. Downloads each METAR/GRIB URL from the manifest
4. Loads the data into the sim

You redirect `weatherservice.x-plane.com` to `127.0.0.1` via the system **hosts file**. This app answers on port 443 with a compatible manifest and serves files from staging.

```
NOAA / Laminar  →  this app  →  .weather_data/  (staging)
                                      ↑
X-Plane  →  hosts file  →  local proxy :443
                                      ↓
                              Output/real weather/  (X-Plane cache)
```

**Typical workflow:** start this app → wait for a cycle or click **Update weather now** → press **Refresh** in X-Plane (**Settings → General → Weather**).

## What gets downloaded

| Data | Source | Details |
|------|--------|---------|
| **METAR** | NOAA / Aviation Weather Center | Global dump every 15 min — [docs/metar-download.md](docs/metar-download.md) |
| **GRIB** (wind, temp, dewp, pres, srfc, prcp, trop, svis) | NOAA GFS via NOMADS | Sliced every 3 h — [docs/grib-download.md](docs/grib-download.md) |
| **GRIB** (calt, ccov) | Local templates | Copied from prior X-Plane/Laminar downloads |
| **GRIB** (snod, WIFS turb/cloud) | Laminar (when online) | DNS bypass to real server — [docs/grib-download.md](docs/grib-download.md#part-2-laminar-downloads) |

METAR is **never** fetched from Laminar. Only snow depth and WIFS GRIB products still contact Laminar’s servers.

## Directories

| Path | Written by | Purpose |
|------|------------|---------|
| `.weather_data/` | This app | Staging — proxy serves files from here |
| `Output/real weather/` | X-Plane | Sim cache after Refresh |
| `.grib_cache/` | This app | Raw NOAA GFS downloads (intermediate) |
| `.weather_proxy_certs/` | mkcert / app | TLS certificate for the proxy |
| `user_settings.json` | App UI | X-Plane path and update interval |

## Requirements

- **Python 3.11+**
- **X-Plane 12** with real-weather downloads enabled
- Network access to NOAA NOMADS and METAR sources
- **Port 443** available locally
- **eccodes** (GRIB slicing) — see platform notes below

---

## Quick start — Linux

```bash
# System deps (Debian/Ubuntu)
sudo apt install libeccodes-dev mkcert   # mkcert optional but recommended

git clone https://github.com/eduardoefb/xplane12-weather-proxy.git
cd xplane12-weather-proxy

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

mkcert -install   # one-time, for trusted HTTPS

# Redirect weather hostname (requires sudo)
echo '127.0.0.1 weatherservice.x-plane.com' | sudo tee -a /etc/hosts

# Allow binding port 443 without sudo (one-time)
sudo setcap 'cap_net_bind_service=+ep' "$(readlink -f .venv/bin/python3)"

python main.py    # or: python main.py --cli
```

Set your X-Plane root in the UI (default `~/X-Plane_12`), wait for the first download cycle, then **Refresh** weather in X-Plane.

---

## Quick start — Windows

Two ways to install **eccodes** (required for GRIB slicing). **Option A (pip only)** is enough for most users — no Anaconda/conda required.

### Option A — Python + pip (recommended)

The `eccodes` package on PyPI includes the native library for Windows (v2.37+). A normal `pip install` is sufficient.

#### 1. Install Python

1. Download **Python 3.11+** from [python.org](https://www.python.org/downloads/)
2. Run the installer and check **“Add python.exe to PATH”**
3. Verify in a **new** Command Prompt:

   ```cmd
   python --version
   ```

If GRIB slicing fails with a DLL error, install the [Microsoft Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist) (x64).

#### 2. Clone the project and install packages

```cmd
git clone https://github.com/eduardoefb/xplane12-weather-proxy.git
cd xplane12-weather-proxy

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Verify eccodes:

```cmd
python -m eccodes selfcheck
```

#### 3. Install mkcert (trusted HTTPS)

With [Chocolatey](https://chocolatey.org/): `choco install mkcert`  
With [Scoop](https://scoop.sh/): `scoop install mkcert`

Then run **Command Prompt as Administrator** once:

```cmd
mkcert -install
```

#### 4. Redirect weather hostname (hosts file)

Edit `C:\Windows\System32\drivers\etc\hosts` **as Administrator** (Notepad → Run as administrator; set file filter to **All Files**).

Add:

```
127.0.0.1 weatherservice.x-plane.com
```

Verify: `ping weatherservice.x-plane.com` should reply from **127.0.0.1**.

#### 5. Run the app

Open **Command Prompt or PowerShell as Administrator** (port **443** requires elevation):

```cmd
cd xplane12-weather-proxy
.venv\Scripts\activate
python main.py
```

Set your X-Plane root in the UI (e.g. `C:\X-Plane 12`), wait for the first download cycle, then press **Refresh** in X-Plane weather settings.

---

### Option B — Miniconda (alternative)

Use this if pip/eccodes fails on your machine, or if you already use conda.

#### 1. Install Miniconda

1. Download **Miniconda** for Windows:  
   https://docs.anaconda.com/miniconda/miniconda-install/
2. Run the installer (check **Add to PATH**, or use **Anaconda Prompt**)
3. Verify: `conda --version`

#### 2. Create environment

```cmd
conda create -n xplane-weather python=3.11 -y
conda activate xplane-weather
conda install -c conda-forge eccodes cfgrib -y
pip install -r requirements.txt
```

Then follow **Option A** steps 3–5 (mkcert, hosts, run as Administrator). Use `conda activate xplane-weather` instead of activating `.venv`.

---

The **Setup help** button in the GUI shows OS-specific hosts and certificate steps.

**Note:** Windows support is untested by the author; pip + eccodes wheels should work, but conda is a solid fallback if you hit install issues.

---

## Project layout

| File / path | Role |
|-------------|------|
| `main.py` | Orchestrator: METAR/GRIB cycles + proxy |
| `metar_engine.py` | NOAA METAR download — see [docs/metar-download.md](docs/metar-download.md) |
| `grib_engine.py` | GFS download and slicing — see [docs/grib-download.md](docs/grib-download.md) |
| `aux_products.py` | Laminar snod + WIFS downloads |
| `weather_proxy.py` | HTTPS server emulating `weatherservice.x-plane.com` |
| `platform_support.py` | Cross-platform hosts, DNS bypass, TLS |
| `gui.py` | Desktop UI |
| `docs/` | Detailed download documentation |

## Troubleshooting

| Problem | What to check |
|---------|----------------|
| Weather updated in app but not in sim | Press **Refresh** in X-Plane; sim reads `Output/real weather/`, not `.weather_data/` |
| “Not updated yet” / 1970 dates | Proxy not running or manifest rejected — check `Log.txt` for `WXR` lines |
| SSL errors | Run `mkcert -install`, restart app |
| Missing `calt` / `ccov` | Copy from a prior Laminar download or see [grib-download.md](docs/grib-download.md) |
| Port 443 in use | **Linux:** `setcap` or sudo — **Windows:** run as Administrator |
| Hosts redirect fails (Windows) | Edit hosts as Admin; run `ipconfig /flushdns` |
| eccodes / DLL error (Windows) | Install [VC++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist); run `python -m eccodes selfcheck`; or try conda (README Option B) |

**Test the local manifest:**

```bash
curl -s "https://weatherservice.x-plane.com/api/v1/manifest/debug/$(date -u +%Y-%m-%dT%H:%M:%SZ)" | python3 -m json.tool | head
```

## Reverting to Laminar’s servers

Remove or comment out the `weatherservice.x-plane.com` line in your hosts file and stop this app. X-Plane will use the official service on the next refresh.

## License

Private / use at your own risk. Not affiliated with Laminar Research.
