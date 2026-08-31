"""Пути к пользовательским данным и ресурсам приложения.

Единственное место, которое знает, где что лежит. При сборке PyInstaller
ресурсы уезжают в `sys._MEIPASS`, и правка нужна будет только здесь.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Konspekt"


def profile() -> str:
    """Имя профиля данных: чем разведены установленная копия и разработка.

    Пустая строка — обычный профиль пользователя. Иначе к каталогу данных
    и к замку единственного экземпляра добавляется суффикс, и две копии
    перестают делить базу, записи и веса моделей.

    Без этого запуск из исходников рядом с установленной программой не
    поднимал вторую копию вовсе: он натыкался на общий мьютекс и лишь
    показывал окно уже работающей.
    """
    return os.environ.get("KONSPEKT_PROFILE", "").strip()


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
    # Прямое указание каталога: нужно тестам, которым удобнее временная
    # папка, чем чужой профиль в AppData.
    прямой = os.environ.get("KONSPEKT_DATA_DIR", "").strip()
    if прямой:
        path = Path(прямой)
        path.mkdir(parents=True, exist_ok=True)
        return path
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    имя = APP_NAME
    if profile():
        имя = f"{APP_NAME} ({profile()})"
    path = base / имя
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
    """Каталог весов моделей.

    В режиме разработки удобнее держать их рядом с кодом: не надо лазить
    в AppData и легко посмотреть, что скачалось. Если папки `models` в
    репозитории нет (то есть мы внутри собранного бинарника), уходим в
    пользовательские данные.
    """
    local = resource_dir() / "models"
    if local.is_dir():
        return local
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path
