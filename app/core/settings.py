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
    # Ширина боковой колонки. Живёт здесь, а не в localStorage:
    # хранилище webview очищается между запусками, и выбранная
    # ширина каждый раз слетала бы к исходной.
    sidebar_width: int = 240


# До 0.6.1 звук доходил до распознавания кусками по 5 секунд, и это
# число попало в settings.json у всех, кто пользовался программой
# раньше. Уменьшение значения по умолчанию таких людей не спасало:
# файл настроек всегда сильнее умолчания, и обновление до 0.6.1, 0.7.0
# и 0.7.1 не меняло у них ровным счётом ничего. Поэтому старое значение
# правится при чтении, один раз.
УСТАРЕВШИЙ_ЧАНК = 5.0


@dataclass
class AudioSettings:
    """Что и с чего писать. id пустой — устройство по умолчанию."""

    mic_device_id: str = ""
    loopback_device_id: str = ""
    capture_mic: bool = True
    capture_system: bool = True
    # Насколько крупными кусками звук из карты доходит до распознавания.
    # На запись в WAV не влияет: писать в файл дорожки продолжают блоками
    # по 0.1с независимо от этого числа. Раньше стояло 5с и это было
    # главным источником «сказал — и тишина» (issue #2): VAD и модель
    # вообще не видят звук, пока не наберётся целый чанк, то есть даже
    # мгновенная короткая фраза в начале окна ждала до 5 секунд просто
    # чтобы попасть в очередь. 0.5с достаточно: GigaAM всё равно сам
    # добивает вход тишиной до 6с при распознавании, точность не падает.
    chunk_seconds: float = 0.5


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
class DictationSettings:
    """Диктовка: наговорил в любом приложении, получил текст в поле ввода.

    Выключена по умолчанию. Она перехватывает клавиши глобально и пишет
    микрофон, а такое нельзя включать за человека молча: он не просил.
    """

    enabled: bool = False
    hotkey: str = "<ctrl>+<shift>+d"
    # hold — держать клавишу, пока говоришь; toggle — нажал/нажал.
    mode: str = "hold"
    # auto — короткое печатаем, длинное вставляем буфером.
    # type — всегда по буквам (не трогает буфер, но медленно).
    # clipboard — всегда буфером (быстро, но буфер на миг чужой).
    paste_method: str = "auto"


@dataclass
class Settings:
    window: WindowGeometry = field(default_factory=WindowGeometry)
    audio: AudioSettings = field(default_factory=AudioSettings)
    asr: AsrSettings = field(default_factory=AsrSettings)
    llm: LlmSettings = field(default_factory=LlmSettings)
    dictation: DictationSettings = field(default_factory=DictationSettings)
    always_on_top: bool = True
    theme: str = "system"          # system | light | dark
    hotkey: str = "<ctrl>+<shift>+k"
    start_hidden: bool = False
    language: str = "ru"
    # Смотреть, не вышла ли новая версия. Приложение, которое лезет
    # в сеть без спроса, противоречит обещанию приватности, поэтому
    # проверку можно выключить.
    check_updates: bool = True
    # Когда смотрели в последний раз, unix-время.
    last_version_check: float = 0.0
    # Скачивать и ставить обновление самим, без похода на сайт. Установка
    # происходит при выходе из программы, поэтому работу не прерывает.
    # Выключается для тех, кто хочет решать сам, что и когда ставится.
    auto_update: bool = True

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
        if audio.chunk_seconds >= УСТАРЕВШИЙ_ЧАНК:
            # Ровно то, из-за чего у давних пользователей текст
            # по-прежнему появлялся только через несколько секунд.
            log.info(
                "Чиним унаследованный chunk_seconds %.1f -> %.1f",
                audio.chunk_seconds, AudioSettings.chunk_seconds,
            )
            audio.chunk_seconds = AudioSettings.chunk_seconds
        asr = _section(AsrSettings, raw.pop("asr", {}))
        llm = _section(LlmSettings, raw.pop("llm", {}))
        dictation = _section(DictationSettings, raw.pop("dictation", {}))
        known = {k: v for k, v in raw.items() if k in Settings.__dataclass_fields__}
        return Settings(
            window=window, audio=audio, asr=asr, llm=llm,
            dictation=dictation, **known,
        )
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
