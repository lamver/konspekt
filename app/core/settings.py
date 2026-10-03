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

# Сколько окна должно остаться на экране, чтобы его можно было поймать
# мышью: полоска заголовка. Меньше — окно считается потерянным.
ВИДНО_ПО_ШИРИНЕ = 80
ВИДНО_ПО_ВЫСОТЕ = 24

# Меньше этого окно нерабочее: столбец записей и кнопки уже не помещаются.
# Тот же минимум задан самому окну при создании.
МИН_ШИРИНА = 360
МИН_ВЫСОТА = 420


def геометрия_годится(
    x: int | None,
    y: int | None,
    width: int,
    height: int,
    экраны: list[tuple[int, int, int, int]] | None = None,
) -> bool:
    """Останется ли окно с таким положением доступным человеку.

    Две беды, от которых это спасает.

    Свёрнутое окно Windows отвечает координатами -32000, -32000. Это не
    место на экране, а условный угол для минимизированных окон. Стоит
    записать их в настройки — и при следующем запуске окно создаётся там
    по-настоящему: значок в трее есть, а окна нет нигде.

    Второй случай — отключённый монитор. Окно осталось на координатах
    второго экрана, а экрана больше нет.

    `экраны` — прямоугольники мониторов (x, y, ширина, высота). Без них
    судим только по заведомо невозможным координатам: список мониторов
    знает лишь система, а этот модуль должен работать и без Windows.
    """
    if x is None or y is None:
        # Положение не задано: окно поставит система, это нормально.
        return True
    x, y, width, height = int(x), int(y), int(width), int(height)
    # Свёрнутому окну Windows приписывает размер вроде 160x28. Приняв
    # его за настоящий, мы запомнили бы окно, в которое ничего не влезет.
    if width < МИН_ШИРИНА or height < МИН_ВЫСОТА:
        return False
    # Свёрнутое окно и прочая бессмыслица.
    if abs(x) >= 30000 or abs(y) >= 30000:
        return False
    if not экраны:
        return True
    for эx, эy, эш, эв in экраны:
        видно_вширь = min(x + width, эx + эш) - max(x, эx)
        видно_ввысь = min(y + height, эy + эв) - max(y, эy)
        if видно_вширь >= ВИДНО_ПО_ШИРИНЕ and видно_ввысь >= ВИДНО_ПО_ВЫСОТЕ:
            return True
    return False


def поставить_по_центру(
    width: int,
    height: int,
    экраны: list[tuple[int, int, int, int]] | None = None,
) -> tuple[int | None, int | None]:
    """Куда вернуть потерянное окно: середина первого экрана.

    Без списка экранов отдаём None: пусть место выберет система, это
    всегда лучше, чем наугад поставить окно в чужие координаты.
    """
    if not экраны:
        return (None, None)
    эx, эy, эш, эв = экраны[0]
    return (эx + max(0, (эш - width) // 2), эy + max(0, (эв - height) // 2))


def починить_геометрию(
    geom: WindowGeometry,
    экраны: list[tuple[int, int, int, int]] | None = None,
) -> bool:
    """Привести запомненное окно в годный вид. Вернуть: чинили ли.

    Меняет `geom` на месте. Размер правим первым: место считается от
    размера, и от испорченного размера получилось бы кривое место.
    """
    целое = WindowGeometry()
    чинили = False
    if geom.width < МИН_ШИРИНА or geom.height < МИН_ВЫСОТА:
        geom.width, geom.height = целое.width, целое.height
        чинили = True
    if not геометрия_годится(geom.x, geom.y, geom.width, geom.height, экраны):
        geom.x, geom.y = поставить_по_центру(geom.width, geom.height, экраны)
        чинили = True
    return чинили


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
    # Какая своя модель пишет заметки и отвечает: fast, smart или strong
    # (см. LOCAL_MODELS в llm/local.py).
    local_model: str = "fast"
    # Скрывать номера карт, телефоны, паспорта и прочее, прежде чем
    # отправить текст встречи на свой сервер. Своей модели на этом
    # компьютере это не нужно: текст никуда не уходит.
    mask_personal_remote: bool = True


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
class TelegramSettings:
    """Свой бот в Telegram: перешли голосовое — получи расшифровку.

    Выключен по умолчанию: бот ходит в интернет, а это человек решает сам.
    Токен лежит зашифрованным средствами Windows (core/секрет.py): это ключ
    от бота, ему не место открытым текстом в файле настроек.
    """

    enabled: bool = False
    token: str = ""          # спрятанный токен, см. секрет.спрятать
    chat_id: int = 0         # хозяин бота: ему можно всегда
    chat_name: str = ""
    # Кому ещё можно: chosen — только отмеченным в списке, all — всем.
    access: str = "chosen"
    # Кто писал боту, кроме хозяина: {"id", "name", "username", "allowed"}.
    users: list = field(default_factory=list)


@dataclass
class ВниманиеSettings:
    """Когда Konspekt подаёт голос сам.

    Две разные вещи, и путать их нельзя.

    `сторож` — слушать системный звук, пока запись не идёт. Открыл Zoom,
    а кнопку нажать забыл: программа заметит разговор и предложит
    записать. Ничего не пишется на диск, пока человек не согласится.
    Включено по умолчанию: забытая запись — самая частая и самая
    обидная потеря, а цена ошибки здесь всего лишь одно уведомление.

    `уведомления` — показывать вопрос системным тостом поверх всех окон,
    а не только плашкой внутри программы. Во время встречи окно свёрнуто
    в трей, и плашку в нём человек увидит через час, когда уже поздно.
    Но тост посреди чужой работы раздражает, поэтому его можно выключить
    и остаться с плашкой.
    """

    сторож: bool = True
    уведомления: bool = True


@dataclass
class Settings:
    window: WindowGeometry = field(default_factory=WindowGeometry)
    audio: AudioSettings = field(default_factory=AudioSettings)
    asr: AsrSettings = field(default_factory=AsrSettings)
    llm: LlmSettings = field(default_factory=LlmSettings)
    dictation: DictationSettings = field(default_factory=DictationSettings)
    внимание: ВниманиеSettings = field(default_factory=ВниманиеSettings)
    telegram: TelegramSettings = field(default_factory=TelegramSettings)
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
    # Масштаб интерфейса, 1.0 — как задумано. Нужен на мониторах, где
    # мелкий шрифт не читается, и на маленьких экранах, где окно тесно.
    ui_zoom: float = 1.0
    # Ключ лицензии как его вставили. Хранится сам ключ, а не отметка
    # «куплено»: отметку правят в файле одной строкой, а ключ
    # проверяется подписью при каждом запуске.
    license_key: str = ""
    # Каким видом кнопка «Копировать» у заметок копирует без вопросов:
    # markdown или plain. Запоминается последний выбранный в меню.
    copy_mode: str = "markdown"
    # Свёрнуты ли заметки над перепиской. Одно положение на все встречи.
    summary_collapsed: bool = False
    # Какие папки в списке встреч свёрнуты.
    collapsed_folders: list[str] = field(default_factory=list)

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
        внимание = _section(ВниманиеSettings, raw.pop("внимание", {}))
        telegram = _section(TelegramSettings, raw.pop("telegram", {}))
        known = {k: v for k, v in raw.items() if k in Settings.__dataclass_fields__}
        return Settings(
            window=window, audio=audio, asr=asr, llm=llm,
            dictation=dictation, внимание=внимание, telegram=telegram, **known,
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
