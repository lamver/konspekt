"""Пути к пользовательским данным и ресурсам приложения.

Единственное место, которое знает, где что лежит. При сборке PyInstaller
ресурсы уезжают в `sys._MEIPASS`, и правка нужна будет только здесь.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Konspekt"


def resource_dir() -> Path:
    """Каталог с неизменяемыми ресурсами (фронт, иконки)."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent.parent.parent


def web_dir() -> Path:
    return resource_dir() / "web"


def data_dir() -> Path:
    """Каталог пользовательских данных, создаётся при первом обращении."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "konspekt.db"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def audio_dir() -> Path:
    path = data_dir() / "audio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path
