"""Persistent user preferences (X-Plane path, update interval)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "user_settings.json")

DEFAULT_XPLANE_ROOT = os.path.expanduser("~/X-Plane_12")
DEFAULT_UPDATE_INTERVAL_MINUTES = 15


@dataclass
class UserSettings:
    xplane_root: str = DEFAULT_XPLANE_ROOT
    update_interval_minutes: int = DEFAULT_UPDATE_INTERVAL_MINUTES

    @property
    def weather_output_dir(self) -> str:
        return os.path.join(os.path.expanduser(self.xplane_root), "Output", "real weather")

    @property
    def update_interval_seconds(self) -> float:
        return max(1, self.update_interval_minutes) * 60


def _normalize(settings: UserSettings) -> UserSettings:
    root = os.path.expanduser(settings.xplane_root.strip() or DEFAULT_XPLANE_ROOT)
    minutes = int(settings.update_interval_minutes)
    if minutes < 1:
        minutes = 1
    if minutes > 24 * 60:
        minutes = 24 * 60
    return UserSettings(xplane_root=root, update_interval_minutes=minutes)


def load_settings() -> UserSettings:
    if not os.path.isfile(SETTINGS_PATH):
        return _normalize(UserSettings())

    try:
        with open(SETTINGS_PATH, encoding="utf-8") as handle:
            raw: dict[str, Any] = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return _normalize(UserSettings())

    return _normalize(
        UserSettings(
            xplane_root=str(raw.get("xplane_root", DEFAULT_XPLANE_ROOT)),
            update_interval_minutes=int(
                raw.get("update_interval_minutes", DEFAULT_UPDATE_INTERVAL_MINUTES)
            ),
        )
    )


def save_settings(settings: UserSettings) -> UserSettings:
    settings = _normalize(settings)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as handle:
        json.dump(asdict(settings), handle, indent=2)
        handle.write("\n")
    return settings
