"""System setup checks for the local weather proxy."""

from __future__ import annotations

import os
import shutil
import subprocess

WEATHER_HOST = "weatherservice.x-plane.com"
HOSTS_PATH = "/etc/hosts"
MKCERT_HOSTS_LINE = f"127.0.0.1 {WEATHER_HOST}"


def read_hosts_lines() -> list[str]:
    try:
        with open(HOSTS_PATH, encoding="utf-8") as handle:
            return handle.readlines()
    except OSError:
        return []


def hosts_redirect_active() -> bool:
    for line in read_hosts_lines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if WEATHER_HOST in stripped and "127.0.0.1" in stripped.split("#", 1)[0]:
            return True
    return False


def hosts_redirect_commented() -> bool:
    for line in read_hosts_lines():
        stripped = line.strip()
        if WEATHER_HOST in stripped and "127.0.0.1" in stripped and stripped.startswith("#"):
            return True
    return False


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
        lines.append(
            "Enable the local weather proxy in /etc/hosts (requires sudo):\n"
            f"  sudo sed -i 's/^#\\?.*{WEATHER_HOST}.*/127.0.0.1 {WEATHER_HOST}/' {HOSTS_PATH}\n"
            f"Or add manually: 127.0.0.1 {WEATHER_HOST}"
        )
        if status["hosts_commented"]:
            lines.append("Note: this entry exists but is commented out with #.")

    if not status["tls_trusted"]:
        if status["mkcert_available"] and not status["mkcert_ca_installed"]:
            lines.append(
                "Install a trusted local certificate authority (one time):\n"
                "  mkcert -install"
            )
        if status["mkcert_available"]:
            lines.append(
                "Regenerate a browser/X-Plane-trusted certificate:\n"
                f"  rm -rf {cert_dir}\n"
                f"  mkcert -cert-file {cert_dir}/weatherservice.crt "
                f"-key-file {cert_dir}/weatherservice.key {WEATHER_HOST}\n"
                "Then restart this app."
            )
        else:
            lines.append(
                "Install mkcert for trusted HTTPS (recommended):\n"
                "  sudo apt install mkcert && mkcert -install"
            )

    if status["hosts_active"] and status["tls_trusted"]:
        lines.append("Setup looks good. Restart X-Plane and press Refresh in Weather.")

    return lines
