"""Настройки приложения в JSON рядом с базой."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field
from typing import Any

from . import paths

log = logging.getLogger(__name__)


@dataclass
class WindowGeometry:
    x: int | None = None
    y: int | None = None
    width: int = 460
    height: int = 640


@dataclass
class Settings:
    window: WindowGeometry = field(default_factory=WindowGeometry)
    always_on_top: bool = True
    hotkey: str = "<ctrl>+<shift>+k"
    start_hidden: bool = False
    # Заготовки под следующие этапы
    asr_backend: str = "null"       # null | gigaam | whisper
    llm_backend: str = "null"       # null | openai | gigachat | local
    language: str = "ru"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load() -> Settings:
    path = paths.settings_path()
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        window = WindowGeometry(**raw.pop("window", {}))
        known = {k: v for k, v in raw.items() if k in Settings.__dataclass_fields__}
        return Settings(window=window, **known)
    except Exception:
        # Битый конфиг не повод не запуститься.
        log.exception("Не удалось прочитать настройки, берём значения по умолчанию")
        return Settings()


def save(settings: Settings) -> None:
    try:
        paths.settings_path().write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        log.exception("Не удалось сохранить настройки")
