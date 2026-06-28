"""System setup checks for the local weather proxy."""

from __future__ import annotations

import os
import shutil
import subprocess

from platform_support import (
    WEATHER_HOST,
    hosts_redirect_active,
    hosts_redirect_commented,
    hosts_setup_instructions,
    mkcert_install_hint,
    read_hosts_lines,
)

__all__ = [
    "WEATHER_HOST",
    "read_hosts_lines",
    "hosts_redirect_active",
    "hosts_redirect_commented",
    "mkcert_available",
    "mkcert_ca_installed",
    "generate_mkcert_material",
    "setup_status",
    "setup_instructions",
]


def mkcert_available() -> bool:
    return shutil.which("mkcert") is not None


def mkcert_ca_installed() -> bool:
    mkcert = shutil.which("mkcert")
    if not mkcert:
        return False
    try:
        result = subprocess.run(
            [mkcert, "-CAROOT"],
            capture_output=True,
            text=True,
            check=True,
        )
        caroot = result.stdout.strip()
        return bool(caroot and os.path.isfile(os.path.join(caroot, "rootCA.pem")))
    except (OSError, subprocess.CalledProcessError):
        return False


def generate_mkcert_material(cert_path: str, key_path: str, hostname: str = WEATHER_HOST) -> bool:
    mkcert = shutil.which("mkcert")
    if not mkcert:
        return False
    os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    try:
        subprocess.run(
            [mkcert, "-cert-file", cert_path, "-key-file", key_path, hostname],
            check=True,
        )
        return os.path.isfile(cert_path) and os.path.isfile(key_path)
    except (OSError, subprocess.CalledProcessError):
        return False


def setup_status(cert_dir: str) -> dict[str, bool | str]:
    cert_path = os.path.join(cert_dir, "weatherservice.crt")
    using_mkcert = os.path.isfile(cert_path) and mkcert_ca_installed()
    return {
        "hosts_active": hosts_redirect_active(),
        "hosts_commented": hosts_redirect_commented(),
        "mkcert_available": mkcert_available(),
        "mkcert_ca_installed": mkcert_ca_installed(),
        "tls_trusted": using_mkcert,
    }


def setup_instructions(cert_dir: str) -> list[str]:
    lines: list[str] = []
    status = setup_status(cert_dir)

    if not status["hosts_active"]:
        lines.append(hosts_setup_instructions())
        if status["hosts_commented"]:
            lines.append("Note: this entry exists but is commented out with #.")

    if not status["tls_trusted"]:
        if status["mkcert_available"] and not status["mkcert_ca_installed"]:
            lines.append(
                "Install a trusted local certificate authority (one time):\n"
                "  mkcert -install"
            )
        if status["mkcert_available"]:
            remove_cmd = "rmdir /s /q" if os.name == "nt" else "rm -rf"
            lines.append(
                "Regenerate a browser/X-Plane-trusted certificate:\n"
                f"  {remove_cmd} {cert_dir}\n"
                f"  mkcert -cert-file {cert_dir}/weatherservice.crt "
                f"-key-file {cert_dir}/weatherservice.key {WEATHER_HOST}\n"
                "Then restart this app."
            )
        else:
            lines.append(mkcert_install_hint())

    if status["hosts_active"] and status["tls_trusted"]:
        lines.append("Setup looks good. Restart X-Plane and press Refresh in Weather.")

    return lines
