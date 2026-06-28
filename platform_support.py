"""Cross-platform helpers for Linux and Windows."""

from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import ssl
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

WEATHER_HOST = "weatherservice.x-plane.com"
LOOPBACK = "127.0.0.1"


def is_windows() -> bool:
    return sys.platform == "win32"


def hosts_file_path() -> str:
    if is_windows():
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        return os.path.join(system_root, "System32", "drivers", "etc", "hosts")
    return "/etc/hosts"


def read_hosts_lines() -> list[str]:
    try:
        with open(hosts_file_path(), encoding="utf-8") as handle:
            return handle.readlines()
    except OSError:
        return []


def hosts_redirect_active(hostname: str = WEATHER_HOST) -> bool:
    for line in read_hosts_lines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        host_part = stripped.split("#", 1)[0]
        if hostname in host_part and LOOPBACK in host_part:
            return True
    return False


def hosts_redirect_commented(hostname: str = WEATHER_HOST) -> bool:
    for line in read_hosts_lines():
        stripped = line.strip()
        if hostname in stripped and LOOPBACK in stripped and stripped.startswith("#"):
            return True
    return False


def default_xplane_root() -> str:
    if is_windows():
        candidates = [
            os.path.join(os.path.expanduser("~"), "X-Plane 12"),
            r"C:\X-Plane 12",
            r"D:\X-Plane 12",
        ]
        for path in candidates:
            if os.path.isdir(path):
                return path
        return candidates[0]
    return os.path.expanduser("~/X-Plane_12")


def cloud_template_dirs() -> tuple[str, ...]:
    dirs: list[str] = []
    if is_windows():
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            dirs.append(os.path.join(local_app, "xplane-weather", "templates"))
    else:
        dirs.append(os.path.expanduser("~/tmp/weather"))
    dirs.append(os.path.join(os.path.dirname(__file__), "templates", "grib"))
    return tuple(dirs)


def privileged_port_hint(python_executable: str) -> str:
    if is_windows():
        return "Run this app as Administrator to bind port 443."
    return (
        "Run with sudo or: "
        f"sudo setcap 'cap_net_bind_service=+ep' {python_executable}"
    )


def mkcert_install_hint() -> str:
    if is_windows():
        return (
            "Install mkcert for trusted HTTPS (X-Plane usually rejects self-signed certs):\n"
            "  Manual (no Chocolatey/Scoop):\n"
            "    1. Download mkcert from https://github.com/FiloSottile/mkcert/releases\n"
            "       (e.g. mkcert-v*-windows-amd64.exe)\n"
            "    2. Rename to mkcert.exe and put it in a folder on PATH\n"
            "       (e.g. C:\\Tools\\mkcert.exe and add C:\\Tools to PATH)\n"
            "    3. Open Command Prompt as Administrator:\n"
            "       mkcert -install\n"
            "  Or: choco install mkcert  /  scoop install mkcert\n"
            "  Then delete .weather_proxy_certs and restart this app."
        )
    return (
        "Install mkcert for trusted HTTPS (recommended):\n"
        "  sudo apt install mkcert && mkcert -install"
    )


def hosts_setup_instructions(hostname: str = WEATHER_HOST) -> str:
    path = hosts_file_path()
    if is_windows():
        return (
            f"Redirect {hostname} to localhost in the hosts file (requires Administrator):\n"
            f"  1. Open Notepad as Administrator\n"
            f"  2. Open {path}\n"
            f"  3. Add: {LOOPBACK} {hostname}\n"
            f"  4. Save and restart this app"
        )
    return (
        f"Enable the local weather proxy in /etc/hosts (requires sudo):\n"
        f"  sudo sed -i 's/^#\\?.*{hostname}.*/{LOOPBACK} {hostname}/' {path}\n"
        f"Or add manually: {LOOPBACK} {hostname}"
    )


def resolve_public_ipv4(hostname: str) -> str | None:
    """Resolve A record via public DNS, bypassing the local hosts file."""
    try:
        import dns.resolver
    except ImportError:
        return _resolve_public_ipv4_subprocess(hostname)

    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = ["8.8.8.8", "1.1.1.1"]
    try:
        answers = resolver.resolve(hostname, "A")
    except Exception:
        return _resolve_public_ipv4_subprocess(hostname)

    for answer in answers:
        address = str(answer).strip().rstrip(".")
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.version == 4:
            return address
    return None


def _resolve_public_ipv4_subprocess(hostname: str) -> str | None:
    import shutil
    import subprocess

    if is_windows():
        cmd = ["nslookup", hostname, "8.8.8.8"]
    else:
        dig = shutil.which("dig")
        if dig:
            cmd = [dig, "+short", hostname, "A"]
        else:
            cmd = ["nslookup", hostname, "8.8.8.8"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    for line in result.stdout.splitlines():
        token = line.strip().rstrip(".")
        if not token or ":" in token:
            continue
        try:
            ip = ipaddress.ip_address(token)
        except ValueError:
            continue
        if ip.version == 4:
            return token
    return None


def https_get_bypass_hosts(
    url: str,
    hostname: str,
    server_ip: str,
    *,
    timeout: int,
    user_agent: str,
) -> bytes | None:
    """GET over HTTPS to server_ip while validating TLS for hostname."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    context = ssl.create_default_context()
    try:
        with socket.create_connection((server_ip, 443), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as secure:
                secure.sendall(
                    f"GET {path} HTTP/1.1\r\n"
                    f"Host: {hostname}\r\n"
                    f"User-Agent: {user_agent}\r\n"
                    f"Connection: close\r\n\r\n".encode("ascii")
                )
                response = http.client.HTTPResponse(secure)
                response.begin()
                if response.status != 200:
                    return None
                return response.read()
    except (OSError, ssl.SSLError, http.client.HTTPException, ValueError):
        return None


def https_download_bypass_hosts(
    url: str,
    dest_path: str,
    hostname: str,
    server_ip: str,
    *,
    timeout: int,
    user_agent: str,
) -> bool:
    body = https_get_bypass_hosts(
        url,
        hostname,
        server_ip,
        timeout=timeout,
        user_agent=user_agent,
    )
    if body is None or len(body) < 1024:
        return False

    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    temp_path = f"{dest_path}.part"
    try:
        with open(temp_path, "wb") as handle:
            handle.write(body)
        if os.path.exists(dest_path):
            os.remove(dest_path)
        os.replace(temp_path, dest_path)
        return True
    except OSError:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return False


def generate_self_signed_cert(
    cert_path: str,
    key_path: str,
    hostname: str,
) -> bool:
    """Generate a self-signed TLS certificate when mkcert is unavailable."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        return _generate_self_signed_cert_openssl(cert_path, key_path, hostname)

    os.makedirs(os.path.dirname(cert_path) or ".", exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    with open(key_path, "wb") as handle:
        handle.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    with open(cert_path, "wb") as handle:
        handle.write(cert.public_bytes(serialization.Encoding.PEM))
    return True


def _generate_self_signed_cert_openssl(
    cert_path: str,
    key_path: str,
    hostname: str,
) -> bool:
    import shutil
    import subprocess

    openssl = shutil.which("openssl")
    if not openssl:
        return False
    os.makedirs(os.path.dirname(cert_path) or ".", exist_ok=True)
    try:
        subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                key_path,
                "-out",
                cert_path,
                "-days",
                "825",
                "-nodes",
                "-subj",
                f"/CN={hostname}",
                "-addext",
                f"subjectAltName=DNS:{hostname}",
            ],
            check=True,
        )
        return os.path.isfile(cert_path) and os.path.isfile(key_path)
    except (OSError, subprocess.CalledProcessError):
        return False
