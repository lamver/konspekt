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
class AudioSettings:
    """Что и с чего писать. id пустой — устройство по умолчанию."""

    mic_device_id: str = ""
    loopback_device_id: str = ""
    capture_mic: bool = True
    capture_system: bool = True
    chunk_seconds: float = 5.0


@dataclass
class AsrSettings:
    """Распознавание речи.

    `backend` = null отключает распознавание совсем: приложение остаётся
    блокнотом с записью звука, а модель не занимает память.
    """

    backend: str = "gigaam"      # null | gigaam
    language: str = "ru"
    enabled: bool = True         # распознавать прямо во время встречи
    # Определять язык каждой фразы и отдавать нерусскую речь Whisper.
    # Без этого английские реплики записываются кириллицей, как
    # «холло дис из зе фест сентинс».
    detect_language: bool = True
    # Размер Whisper для нерусской речи: base (79 МБ, быстрый) или
    # small (250 МБ, заметно точнее на коротких фразах и на языках
    # кроме английского). Берётся тот, что скачан; если есть оба,
    # решает эта настройка.
    whisper_size: str = "small"  # base | small


@dataclass
class LlmSettings:
    """Кто пишет саммари и отвечает в чате.

    `backend` = null отключает LLM: остаётся расшифровка и свои пометки.
    `local` поднимает модель на этой машине, наружу ничего не уходит.
    `remote` ходит в совместимый с OpenAI сервис по своему адресу.
    """

    backend: str = "local"        # null | local | remote
    # Для remote. Ключ лежит в настройках рядом с базой, а не в коде.
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    # Шаблон саммари под тип встречи, см. TEMPLATE_HINTS.
    template: str = ""           # "" | one_on_one | sales | standup | interview
    # Делать саммари сразу после остановки записи.
    auto_summary: bool = True


@dataclass
class Settings:
    window: WindowGeometry = field(default_factory=WindowGeometry)
    audio: AudioSettings = field(default_factory=AudioSettings)
    asr: AsrSettings = field(default_factory=AsrSettings)
    llm: LlmSettings = field(default_factory=LlmSettings)
    always_on_top: bool = True
    theme: str = "system"          # system | light | dark
    hotkey: str = "<ctrl>+<shift>+k"
    start_hidden: bool = False
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
        audio = _section(AudioSettings, raw.pop("audio", {}))
        asr = _section(AsrSettings, raw.pop("asr", {}))
        llm = _section(LlmSettings, raw.pop("llm", {}))
        known = {k: v for k, v in raw.items() if k in Settings.__dataclass_fields__}
        return Settings(window=window, audio=audio, asr=asr, llm=llm, **known)
    except Exception:
        # Битый конфиг не повод не запуститься.
        log.exception("Не удалось прочитать настройки, берём значения по умолчанию")
        return Settings()


def _section(cls, raw: dict[str, Any]):
    """Собрать секцию, молча выбросив поля из старых версий конфига."""
    return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


def save(settings: Settings) -> None:
    try:
        paths.settings_path().write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        log.exception("Не удалось сохранить настройки")
