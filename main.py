#!/usr/bin/env python3
"""
X-Plane 12 fallback weather server.

Downloads METAR and GRIB into a local staging directory and serves them via an
HTTPS proxy so X-Plane can refresh normally when Laminar's servers are down.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable

from aux_products import sync_aux_products
from config import GRIB_PRODUCTS, GRIB_RETRY_SLEEP_SECONDS, WEATHER_PROXY_ENABLED
from grib_engine import GribEngine
from metar_engine import MetarEngine
from platform_support import privileged_port_hint
from time_utils import grib_validity_windows, round_to_quarter_hour, round_to_three_hour, utc_now
from user_settings import UserSettings, load_settings
from weather_proxy import WeatherProxyServer, publish_epoch_aliases, remove_epoch_aliases


def _seed_staging_if_empty(staging_dir: str, xplane_weather_dir: str) -> None:
    """Reuse existing X-Plane cache files when upgrading to the staging layout."""
    os.makedirs(staging_dir, exist_ok=True)
    try:
        if any(os.scandir(staging_dir)):
            return
    except OSError:
        return
    if not os.path.isdir(xplane_weather_dir):
        return

    import glob
    import shutil

    patterns = ("metar-*.txt", "GRIB-*.grib")
    copied = 0
    for pattern in patterns:
        for path in glob.glob(os.path.join(xplane_weather_dir, pattern)):
            if "1970-01-01" in os.path.basename(path):
                continue
            if os.path.getsize(path) <= 0:
                continue
            dest = os.path.join(staging_dir, os.path.basename(path))
            shutil.copy2(path, dest)
            copied += 1
    if copied:
        print(
            f"Seeded {copied} weather file(s) from X-Plane cache into {staging_dir}"
        )


class WeatherServer:
    """Coordinates METAR (15-minute) and GRIB (3-hour) refresh cycles."""

    def __init__(
        self,
        settings: UserSettings | None = None,
        *,
        on_stopped: Callable[[], None] | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.staging_dir = self.settings.weather_staging_dir
        self.xplane_weather_dir = self.settings.weather_output_dir
        self.template_dirs = self.settings.weather_template_dirs
        os.makedirs(self.staging_dir, exist_ok=True)
        _seed_staging_if_empty(self.staging_dir, self.xplane_weather_dir)
        self.metar_engine = MetarEngine(self.staging_dir)
        self.grib_engine = GribEngine(
            self.staging_dir,
            template_dirs=self.template_dirs,
        )
        self.weather_proxy = (
            WeatherProxyServer(self.staging_dir) if WEATHER_PROXY_ENABLED else None
        )
        self._proxy_active = False
        self._grib_lock = threading.Lock()
        self._last_grib_retry = 0.0
        self._stop_event = threading.Event()
        self._on_stopped = on_stopped
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def apply_settings(self, settings: UserSettings) -> None:
        """Hot-reload paths and timing from saved user settings."""
        was_running = self.running
        if was_running:
            self.stop()
        self.settings = settings
        self.staging_dir = settings.weather_staging_dir
        self.xplane_weather_dir = settings.weather_output_dir
        self.template_dirs = settings.weather_template_dirs
        os.makedirs(self.staging_dir, exist_ok=True)
        _seed_staging_if_empty(self.staging_dir, self.xplane_weather_dir)
        self.metar_engine = MetarEngine(self.staging_dir)
        self.grib_engine = GribEngine(
            self.staging_dir,
            template_dirs=self.template_dirs,
        )
        if self.weather_proxy is not None:
            self.weather_proxy.stop()
        self.weather_proxy = (
            WeatherProxyServer(self.staging_dir) if WEATHER_PROXY_ENABLED else None
        )
        self._proxy_active = False
        self._last_grib_retry = 0.0
        if was_running:
            self.start()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="weather-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self.weather_proxy is not None:
            self.weather_proxy.stop()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        if self._on_stopped is not None:
            self._on_stopped()

    def _maybe_publish_epoch_aliases(self, now=None) -> None:
        """Copy staging files to X-Plane's cache when the HTTPS proxy is not running."""
        if self._proxy_active:
            return
        publish_epoch_aliases(self.staging_dir, self.xplane_weather_dir, now)

    def force_weather_update(self) -> None:
        """Re-download METAR and GRIB products for the current cycles."""

        def worker() -> None:
            now = utc_now()
            print("Manual weather update started...")
            current_rounded = round_to_quarter_hour(now)
            self.metar_engine.last_processed_time = None
            self.metar_engine.run_cycle(current_rounded)

            with self._grib_lock:
                if self.grib_engine.busy:
                    print("GRIB update already in progress; METAR was refreshed.")
                    self._maybe_publish_epoch_aliases(now)
                    return
                self.grib_engine._busy = True
                try:
                    for validity in grib_validity_windows(now):
                        self.grib_engine.last_processed_times.discard(validity)
                        self.grib_engine.run_cycle(validity, now, force=True)
                    sync_aux_products(
                        self.staging_dir,
                        now,
                        template_dirs=self.template_dirs,
                    )
                    self._maybe_publish_epoch_aliases(now)
                    print("Manual weather update finished.")
                finally:
                    self.grib_engine._busy = False

        threading.Thread(target=worker, name="weather-manual", daemon=True).start()

    def force_grib_update(self) -> None:
        """Backward-compatible alias for manual refresh."""
        self.force_weather_update()

    def _run_grib_async(self, validity_times: list, *, force: bool = False) -> None:
        def worker() -> None:
            with self._grib_lock:
                self.grib_engine._busy = True
                try:
                    now = utc_now()
                    for validity in validity_times:
                        if not force and not self.grib_engine.should_run(validity):
                            continue
                        self.grib_engine.run_cycle(validity, now, force=force)
                    sync_aux_products(
                        self.staging_dir,
                        now,
                        template_dirs=self.template_dirs,
                    )
                    self._maybe_publish_epoch_aliases(now)
                finally:
                    self.grib_engine._busy = False

        thread = threading.Thread(target=worker, name="grib-worker", daemon=True)
        thread.start()

    def _maybe_run_grib(self, now, *, force: bool = False) -> None:
        if self.grib_engine.busy:
            return

        pending = [
            validity
            for validity in grib_validity_windows(now)
            if force or self.grib_engine.should_run(validity)
        ]
        if not pending:
            return

        if not force:
            elapsed = time.monotonic() - self._last_grib_retry
            if elapsed < GRIB_RETRY_SLEEP_SECONDS:
                return

        self._last_grib_retry = time.monotonic()
        self._run_grib_async(pending, force=force)

    def _maybe_run_metar(self, now) -> None:
        current_rounded = round_to_quarter_hour(now)
        if self.metar_engine.should_run(current_rounded):
            self.metar_engine.run_cycle(current_rounded)
            self._maybe_publish_epoch_aliases(now)

    def _run_loop(self) -> None:
        print("Starting X-Plane 12 Fallback Weather Server...")
        print(f"X-Plane root: {self.settings.xplane_root}")
        print(f"Staging directory: {self.staging_dir}")
        print(f"X-Plane weather cache: {self.xplane_weather_dir}")
        print(f"Background check interval: {self.settings.update_interval_minutes} minutes")
        print("METAR cycle: every 15 minutes (when due)")
        print(f"GRIB cycle: every 3 hours ({', '.join(GRIB_PRODUCTS)})")

        self._proxy_active = False
        if self.weather_proxy is not None:
            try:
                self.weather_proxy.start()
                self._proxy_active = True
                remove_epoch_aliases(self.xplane_weather_dir)
            except OSError as exc:
                print(f"WARNING: weather proxy could not bind to port 443: {exc}")
                print(f"  {privileged_port_hint(sys.executable)}")
                print("  Epoch-named aliases will still be written as a partial fallback.")

        if not self._proxy_active:
            self._maybe_publish_epoch_aliases()

        now = utc_now()
        self._maybe_run_metar(now)
        sync_aux_products(
            self.staging_dir,
            now,
            template_dirs=self.template_dirs,
        )
        self._maybe_publish_epoch_aliases(now)
        self._maybe_run_grib(now, force=True)
        last_grib_anchor = round_to_three_hour(now)

        while not self._stop_event.is_set():
            now = utc_now()
            self._maybe_run_metar(now)

            grib_anchor = round_to_three_hour(now)
            if grib_anchor != last_grib_anchor:
                last_grib_anchor = grib_anchor
                self._maybe_run_grib(now, force=True)
            else:
                self._maybe_run_grib(now)

            if self._stop_event.wait(self.settings.update_interval_seconds):
                break

        print("Weather server stopped.")


def run_cli() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    server = WeatherServer()
    server.start()
    try:
        while server.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.stop()


def run_gui() -> None:
    from gui import main as gui_main

    gui_main()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ("--cli", "-c"):
        run_cli()
    else:
        run_gui()


if __name__ == "__main__":
    main()
