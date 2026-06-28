"""Local stand-in for weatherservice.x-plane.com when LR servers are down."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import ssl
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from config import (
    GRIB_PRODUCTS,
    WEATHER_PROXY_CERT_DIR,
    WEATHER_PROXY_HOST,
    WEATHER_PROXY_PORT,
    WEATHER_STAGING_DIR,
)
from platform_support import generate_self_signed_cert
from setup_utils import generate_mkcert_material, mkcert_ca_installed, setup_instructions
from time_utils import grib_filename, grib_validity_windows, metar_filename, round_to_quarter_hour, utc_now

# Local GRIB suffix -> manifest "type" field used by X-Plane's weather service.
NOMADS_TYPE_MAP = {
    "wind-v2": "wind",
    "temp-v2": "temp",
    "dewp-v2": "dewp",
    "trop-v2": "trop",
    "srfc": "srfc",
    "svis": "svis",
    "ccov": "ccov",
    "calt": "calt",
    "pres": "pres",
    "prcp": "prcp",
}

NOMADS_EXTRA_TYPE_MAP = {
    "snod": "snod",
}

WIFS_TYPE_MAP = {
    "cbcv-v2": "cnmb_coverage",
    "cbbs-v2": "cnmb_bases",
    "cbtp-v2": "cnmb_tops",
    "turb-v2": "turb",
}

REQUIRED_MANIFEST_TYPES = (
    set(NOMADS_TYPE_MAP.values())
    | set(NOMADS_EXTRA_TYPE_MAP.values())
    | set(WIFS_TYPE_MAP.values())
)

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_offset(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _file_checksum(path: str) -> tuple[int, str]:
    digest = hashlib.md5()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _manifest_grib_product(path: str, url: str, validity: datetime) -> dict[str, Any]:
    size, checksum = _file_checksum(path)
    return {
        "url": url,
        "file_size": size,
        "checksum": checksum,
        "depicts_weather_at_datetime": _iso_offset(validity),
    }


def _manifest_metar_product(path: str, url: str, created: datetime) -> dict[str, Any]:
    size, checksum = _file_checksum(path)
    return {
        "url": url,
        "file_size": size,
        "checksum": checksum,
        "created_at_datetime": _iso_offset(created),
    }


def _collect_grib_timestamps(manifest: dict[str, Any]) -> set[str]:
    timestamps: set[str] = set()
    for section in ("nomads", "nomads_extra", "wifs"):
        for group in manifest["products"].get(section, []):
            for file_entry in group.get("files", []):
                depicts = file_entry.get("depicts_weather_at_datetime")
                if depicts:
                    timestamps.add(depicts)
    return timestamps


class WeatherManifestBuilder:
    """Build a weatherservice-compatible manifest from local staging files."""

    def __init__(self, staging_dir: str, base_url: str) -> None:
        self.staging_dir = staging_dir
        self.base_url = base_url.rstrip("/")
        self._routes: dict[str, str] = {}

    def _register(self, url_path: str, local_path: str) -> str:
        url = f"{self.base_url}{url_path}"
        self._routes[url_path] = local_path
        return url

    def _local_path(self, filename: str) -> str | None:
        path = os.path.join(self.staging_dir, filename)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        return None

    def _parse_metar_time(self, path: str) -> datetime | None:
        match = re.search(
            r"metar-(\d{4})-(\d{2})-(\d{2})-(\d{2})\.(\d{2})\.txt$",
            os.path.basename(path),
        )
        if not match:
            return None
        year, month, day, hour, minute = map(int, match.groups())
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)

    def _discover_metar_files(
        self, reference: datetime, pre_metar: datetime
    ) -> list[tuple[datetime, str]]:
        found: list[tuple[datetime, str]] = []
        seen_paths: set[str] = set()

        for metar_time in (pre_metar, reference):
            local_path = self._local_path(metar_filename(metar_time))
            if local_path and local_path not in seen_paths:
                found.append((metar_time, local_path))
                seen_paths.add(local_path)

        if len(found) >= 2:
            return found[:2]

        pattern = os.path.join(self.staging_dir, "metar-*.txt")
        extras = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        for path in extras:
            if path in seen_paths or os.path.getsize(path) <= 0:
                continue
            parsed = self._parse_metar_time(path)
            if parsed is None:
                continue
            found.append((parsed, path))
            seen_paths.add(path)
            if len(found) >= 2:
                break

        found.sort(key=lambda item: item[0])
        return found[-2:]

    def build(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or utc_now()
        self._routes.clear()
        validity_windows = grib_validity_windows(now)
        reference = round_to_quarter_hour(now)
        pre_metar = reference - timedelta(minutes=15)

        metar_entries: list[dict[str, Any]] = []
        for metar_time, local_path in self._discover_metar_files(reference, pre_metar):
            token = hashlib.md5(local_path.encode()).hexdigest()[:12]
            url_path = (
                f"/weather/v1/{metar_time:%Y/%m/%d}/metar/"
                f"{metar_time:%H%M}Z_{token}.txt"
            )
            url = self._register(url_path, local_path)
            metar_entries.append(_manifest_metar_product(local_path, url, metar_time))

        nomads_by_type: dict[str, list[dict[str, Any]]] = {
            manifest_type: [] for manifest_type in set(NOMADS_TYPE_MAP.values())
        }
        for validity in validity_windows:
            for local_suffix, manifest_type in NOMADS_TYPE_MAP.items():
                local_name = grib_filename(validity, local_suffix)
                local_path = self._local_path(local_name)
                if not local_path:
                    continue
                token = hashlib.md5(local_path.encode()).hexdigest()[:12]
                url_path = (
                    f"/weather/v1/{validity:%Y/%m/%d}/{manifest_type}/"
                    f"{validity.hour:02d}_{token}.grib"
                )
                url = self._register(url_path, local_path)
                nomads_by_type[manifest_type].append(
                    _manifest_grib_product(local_path, url, validity)
                )

        nomads = []
        for manifest_type, files in nomads_by_type.items():
            if not files:
                continue
            files.sort(key=lambda entry: entry["depicts_weather_at_datetime"])
            nomads.append({"type": manifest_type, "files": files})

        nomads_extra_by_type: dict[str, list[dict[str, Any]]] = {
            manifest_type: [] for manifest_type in set(NOMADS_EXTRA_TYPE_MAP.values())
        }
        for validity in validity_windows:
            for local_suffix, manifest_type in NOMADS_EXTRA_TYPE_MAP.items():
                local_name = grib_filename(validity, local_suffix)
                local_path = self._local_path(local_name)
                if not local_path:
                    continue
                token = hashlib.md5(local_path.encode()).hexdigest()[:12]
                url_path = (
                    f"/weather/v1/{validity:%Y/%m/%d}/{manifest_type}/"
                    f"{validity.hour:02d}_{token}.grib"
                )
                url = self._register(url_path, local_path)
                nomads_extra_by_type[manifest_type].append(
                    _manifest_grib_product(local_path, url, validity)
                )

        nomads_extra = []
        for manifest_type, files in nomads_extra_by_type.items():
            if not files:
                continue
            files.sort(key=lambda entry: entry["depicts_weather_at_datetime"])
            nomads_extra.append({"type": manifest_type, "files": files})

        wifs_by_type: dict[str, list[dict[str, Any]]] = {
            manifest_type: [] for manifest_type in set(WIFS_TYPE_MAP.values())
        }
        for validity in validity_windows:
            for local_suffix, manifest_type in WIFS_TYPE_MAP.items():
                local_name = grib_filename(validity, local_suffix)
                local_path = self._local_path(local_name)
                if not local_path:
                    continue
                token = hashlib.md5(local_path.encode()).hexdigest()[:12]
                url_path = (
                    f"/weather/v1/{validity:%Y/%m/%d}/{manifest_type}/"
                    f"{validity.hour:02d}_{token}.grib"
                )
                url = self._register(url_path, local_path)
                wifs_by_type[manifest_type].append(
                    _manifest_grib_product(local_path, url, validity)
                )

        manifest = {
            "product_file_set_id": int(reference.timestamp()) % 1_000_000,
            "created_at_datetime": _iso_z(now),
            "reference_datetime": _iso_z(reference),
            "next_generation_at_datetime": _iso_z(reference + timedelta(minutes=15)),
            "is_protected": False,
            "products": {
                "metar": metar_entries,
                "nomads": nomads,
                "nomads_extra": nomads_extra,
                "wifs": [
                    {"type": manifest_type, "files": files}
                    for manifest_type, files in wifs_by_type.items()
                    if files
                ],
            },
        }

        present_types = set()
        for group in nomads:
            present_types.add(group["type"])
        for group in nomads_extra:
            present_types.add(group["type"])
        for group in manifest["products"]["wifs"]:
            present_types.add(group["type"])
        missing_types = REQUIRED_MANIFEST_TYPES - present_types
        if missing_types:
            print(
                "[weather-proxy] WARNING: manifest missing GRIB types "
                f"(X-Plane will reject): {sorted(missing_types)}"
            )

        grib_timestamps = _collect_grib_timestamps(manifest)
        if len(grib_timestamps) != 2:
            print(
                "[weather-proxy] WARNING: manifest has "
                f"{len(grib_timestamps)} GRIB timestamps (X-Plane requires exactly 2): "
                f"{sorted(grib_timestamps)}"
            )
        if len(metar_entries) < 2:
            print(
                "[weather-proxy] WARNING: manifest has "
                f"{len(metar_entries)} METAR files (X-Plane expects 2)."
            )

        return manifest

    @property
    def routes(self) -> dict[str, str]:
        return dict(self._routes)


def remove_epoch_aliases(xplane_weather_dir: str) -> None:
    """Remove 1970-01-01 fallback symlinks/copies from X-Plane's weather cache."""
    targets = [os.path.join(xplane_weather_dir, "metar-1970-01-01-00.00.txt")]
    for product in GRIB_PRODUCTS:
        targets.append(
            os.path.join(
                xplane_weather_dir, f"GRIB-1970-01-01-00.00-ZULU-{product}.grib"
            )
        )
    for path in targets:
        if os.path.islink(path) or os.path.isfile(path):
            os.remove(path)


def publish_epoch_aliases(
    staging_dir: str,
    xplane_weather_dir: str,
    now: datetime | None = None,
) -> None:
    """
    Fallback when the local HTTPS proxy is unavailable.

    Without a manifest X-Plane keeps weather time at Unix epoch zero and looks
    for 1970-01-01 filenames in its real-weather cache. Copy the latest staging
    files there. Not used when the proxy on port 443 is running.
    """
    now = now or utc_now()
    os.makedirs(xplane_weather_dir, exist_ok=True)
    metar_src = os.path.join(staging_dir, metar_filename(round_to_quarter_hour(now)))
    metar_dst = os.path.join(xplane_weather_dir, "metar-1970-01-01-00.00.txt")
    if os.path.isfile(metar_src):
        _replace_symlink_or_copy(metar_src, metar_dst)

    for validity in grib_validity_windows(now):
        for product in GRIB_PRODUCTS:
            src = os.path.join(staging_dir, grib_filename(validity, product))
            if not os.path.isfile(src):
                continue
            dst = os.path.join(
                xplane_weather_dir,
                f"GRIB-1970-01-01-00.00-ZULU-{product}.grib",
            )
            _replace_symlink_or_copy(src, dst)


def _replace_symlink_or_copy(source: str, target: str) -> None:
    if os.path.islink(target) or os.path.isfile(target):
        os.remove(target)
    try:
        os.symlink(os.path.basename(source), target)
    except OSError:
        import shutil

        shutil.copy2(source, target)


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Suppress noisy tracebacks when X-Plane closes idle TLS connections."""

    def handle_error(self, request, client_address) -> None:
        exc_type, exc, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, BrokenPipeError) or (
            exc_type is ssl.SSLError and "EOF occurred" in str(exc)
        ):
            return
        super().handle_error(request, client_address)


def _ensure_tls_material(cert_dir: str, hostname: str) -> tuple[str, str]:
    os.makedirs(cert_dir, exist_ok=True)
    cert_path = os.path.join(cert_dir, "weatherservice.crt")
    key_path = os.path.join(cert_dir, "weatherservice.key")
    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        return cert_path, key_path

    if mkcert_ca_installed() and generate_mkcert_material(cert_path, key_path, hostname):
        print(f"Generated mkcert TLS certificate for {hostname}")
        return cert_path, key_path

    print(f"Generating self-signed TLS certificate for {hostname} ...")
    print("  Tip: install mkcert for trusted HTTPS (mkcert -install)")
    if not generate_self_signed_cert(cert_path, key_path, hostname):
        raise RuntimeError(
            "Could not generate TLS certificate. Install mkcert or the cryptography package."
        )
    return cert_path, key_path


class WeatherProxyServer:
    """HTTPS server emulating weatherservice.x-plane.com."""

    def __init__(
        self,
        staging_dir: str = WEATHER_STAGING_DIR,
        host: str = WEATHER_PROXY_HOST,
        port: int = WEATHER_PROXY_PORT,
        hostname: str = "weatherservice.x-plane.com",
    ) -> None:
        self.staging_dir = staging_dir
        self.host = host
        self.port = port
        self.hostname = hostname
        self.base_url = f"https://{hostname}"
        self._builder = WeatherManifestBuilder(staging_dir, self.base_url)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _make_handler(self):
        builder = self._builder

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args) -> None:
                print(f"[weather-proxy] {self.address_string()} {fmt % args}")

            def handle_one_request(self) -> None:
                try:
                    super().handle_one_request()
                except (ConnectionResetError, BrokenPipeError):
                    pass

            def do_HEAD(self) -> None:
                self._serve_request(send_body=False)

            def do_GET(self) -> None:
                self._serve_request(send_body=True)

            def _serve_request(self, *, send_body: bool) -> None:
                try:
                    path = unquote(urlparse(self.path).path)
                    if path.startswith("/api/v1/manifest/"):
                        manifest = builder.build(utc_now())
                        body = json.dumps(manifest).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        if send_body:
                            self.wfile.write(body)
                        return

                    local_path = builder.routes.get(path)
                    if not local_path:
                        builder.build(utc_now())
                        local_path = builder.routes.get(path)
                    if local_path and os.path.isfile(local_path):
                        size = os.path.getsize(local_path)
                        content_type = (
                            "text/plain"
                            if local_path.endswith(".txt")
                            else "application/octet-stream"
                        )
                        self.send_response(200)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Content-Length", str(size))
                        self.end_headers()
                        if send_body:
                            with open(local_path, "rb") as handle:
                                self.wfile.write(handle.read())
                        return

                    self.send_response(404)
                    self.end_headers()
                except (ConnectionResetError, BrokenPipeError):
                    pass

        return Handler

    def start(self) -> None:
        if self._httpd is not None:
            return

        cert_path, key_path = _ensure_tls_material(WEATHER_PROXY_CERT_DIR, self.hostname)
        handler = self._make_handler()
        self._httpd = _QuietThreadingHTTPServer((self.host, self.port), handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        self._httpd.socket = context.wrap_socket(self._httpd.socket, server_side=True)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="weather-proxy",
            daemon=True,
        )
        self._thread.start()
        self._print_setup_instructions()

    def _print_setup_instructions(self) -> None:
        print("\n" + "=" * 72)
        print("Local X-Plane weather proxy is running")
        print(f"  Listening: https://{self.host}:{self.port}/")
        print(f"  Serving files from: {self.staging_dir}")
        for paragraph in setup_instructions(WEATHER_PROXY_CERT_DIR):
            print(f"\n{paragraph}")
        print("=" * 72 + "\n")

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd = None
