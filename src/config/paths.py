"""Application paths for development and packaged Windows builds."""

import os
import sys
from pathlib import Path


def get_app_data_dir() -> Path:
    """Return the writable application root for this user."""
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA")
        base_dir = (
            Path(local_app_data)
            if local_app_data
            else Path.home() / "AppData" / "Local"
        )
        return base_dir / "Travian Kingdoms Assistant"
    return Path(__file__).resolve().parents[2]


def get_data_dir() -> Path:
    """Return the application data directory."""
    return get_app_data_dir() / "data"


def get_settings_path() -> Path:
    """Return the user settings file path."""
    return get_app_data_dir() / "config" / "settings.json"
