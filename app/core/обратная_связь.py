"""Сообщить о проблеме или предложить идею из раздела «О программе».

Отчёт собирается здесь, на компьютере, и человек видит его целиком до
того, как что-то куда-то уйдёт. Программа обещает, что наружу без
спроса уходит только номер версии, и отчёт это обещание не ломает: он
уходит лишь по нажатию кнопки, в том виде, в каком показан.

Куда уходит: на страницу программы на витрине, где пишут без
регистрации. Вариант с задачами на GitHub владелец отверг 08.11: для
обычных людей это муторно. Приложение лишь открывает страницу и
передаёт в адресе версию и систему, ничего личного. Русский интерфейс
ведёт на aisearch.ru, остальные на aisearch.tech: сообщения с
российской витрины остаются в РФ, с международной идут в немецкий
контур, так решено на стороне сайта.

Сведения для поддержки (система и хвост журнала) не отправляются
никуда: отдельная кнопка кладёт их в буфер обмена, когда их попросят.

Что вычищаем из журнала перед отправкой: имя пользователя в путях,
почты, телефоны и прочее личное (`personal.mask`), токены и ключи, а
также буквальные значения секретов из настроек, если они вдруг попали
в строку. Строки длиннее трёхсот знаков обрезаем: длинная строка в
журнале — это почти всегда чужой текст, а не событие программы.
"""

from __future__ import annotations

import getpass
import logging
import os
import platform
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from app import __version__

from . import paths, personal

log = logging.getLogger(__name__)

# Страницы «написать разработчику» на витринах. Адрес можно подменить,
# чтобы проверить кнопку на живой сборке до выкладки страницы.
ВИТРИНА_RU = os.environ.get("KONSPEKT_FEEDBACK_RU", "https://aisearch.ru/programmy/konspekt/napisat")
ВИТРИНА_INTL = os.environ.get("KONSPEKT_FEEDBACK_INTL", "https://aisearch.tech/programs/konspekt/feedback")
СТРОК_ЖУРНАЛА = 150
МАКС_СТРОКА = 300

ВИДЫ = ("bug", "idea")

# Слова на языке человека: отчёт читает автор, но пишет его человек, и
# заголовок задачи на GitHub должен быть на том языке, на котором он
# говорит, чтобы ответ пришёл на том же.
СЛОВА: dict[str, dict[str, str]] = {
    "ru": {
        "bug": "Ошибка", "idea": "Идея",
        "contact": "Связь", "system": "Сведения о системе",
        "log": "Журнал, последние строки", "paste": "Полный отчёт скопирован в буфер обмена: вставьте его сюда (Ctrl+V).",
        "version": "Версия", "build": "Сборка", "source": "запуск из исходников",
        "os": "Система", "python": "Python", "ui_lang": "Язык интерфейса",
        "asr": "Распознавание", "llm": "Модель", "license": "Лицензия",
        "yes": "есть", "no": "нет", "expired": "не подошла",
        "telegram": "Бот Telegram", "on": "включён", "off": "выключен",
    },
    "en": {
        "bug": "Bug", "idea": "Idea",
        "contact": "Contact", "system": "System details",
        "log": "Log, last lines", "paste": "The full report is in your clipboard: paste it here (Ctrl+V).",
        "version": "Version", "build": "Build", "source": "running from source",
        "os": "OS", "python": "Python", "ui_lang": "Interface language",
        "asr": "Speech recognition", "llm": "Model", "license": "License",
        "yes": "yes", "no": "no", "expired": "invalid",
        "telegram": "Telegram bot", "on": "on", "off": "off",
    },
    "es": {
        "bug": "Error", "idea": "Idea",
        "contact": "Contacto", "system": "Datos del sistema",
        "log": "Registro, últimas líneas", "paste": "El informe completo está en el portapapeles: péguelo aquí (Ctrl+V).",
        "version": "Versión", "build": "Compilación", "source": "ejecución desde el código fuente",
        "os": "Sistema", "python": "Python", "ui_lang": "Idioma de la interfaz",
        "asr": "Reconocimiento de voz", "llm": "Modelo", "license": "Licencia",
        "yes": "sí", "no": "no", "expired": "no válida",
        "telegram": "Bot de Telegram", "on": "activado", "off": "desactivado",
    },
    "sr": {
        "bug": "Greška", "idea": "Ideja",
        "contact": "Kontakt", "system": "Podaci o sistemu",
        "log": "Dnevnik, poslednji redovi", "paste": "Ceo izveštaj je u ostavi: nalepite ga ovde (Ctrl+V).",
        "version": "Verzija", "build": "Izdanje", "source": "pokretanje iz izvornog koda",
        "os": "Sistem", "python": "Python", "ui_lang": "Jezik interfejsa",
        "asr": "Prepoznavanje govora", "llm": "Model", "license": "Licenca",
        "yes": "da", "no": "ne", "expired": "nevažeća",
        "telegram": "Telegram bot", "on": "uključen", "off": "isključen",
    },
}

ПОЧТА = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Токен бота Telegram, ключи OpenAI и GitHub, заголовок Authorization,
# спрятанные значения из настроек.
СЕКРЕТЫ = re.compile(
    r"\b\d{8,11}:[A-Za-z0-9_-]{30,}\b"
    r"|\bsk-[A-Za-z0-9_-]{16,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|Bearer\s+\S+"
    r"|\b(?:dpapi|plain):[A-Za-z0-9+/=]+"
)


def _слова(язык: str) -> dict[str, str]:
    return СЛОВА.get(язык) or СЛОВА["en"]


def очистить_строку(строка: str, секреты: tuple[str, ...] = ()) -> str:
    """Одна строка журнала без личного и без секретов."""
    for секрет in секреты:
        if секрет:
            строка = строка.replace(секрет, "<secret>")
    дом = str(Path.home())
    for вариант in {дом, дом.replace("\\", "/")}:
        if вариант:
            строка = re.sub(re.escape(вариант), "~", строка, flags=re.IGNORECASE)
    try:
        имя = getpass.getuser()
    except Exception:  # noqa: BLE001  на части систем имя недоступно
        имя = ""
    if имя and len(имя) > 2:
        строка = re.sub(re.escape(имя), "~", строка, flags=re.IGNORECASE)
    строка = ПОЧТА.sub("<email>", строка)
    строка = СЕКРЕТЫ.sub("<secret>", строка)
    строка = personal.mask(строка)
    if len(строка) > МАКС_СТРОКА:
        строка = строка[:МАКС_СТРОКА] + "…"
    return строка


def очистить_журнал(строки: list[str], секреты: tuple[str, ...] = ()) -> list[str]:
    """Хвост журнала, пригодный к отправке. DEBUG-строки выбрасываем:
    там разбор чужой речи, а не события программы."""
    итог = []
    for строка in строки:
        строка = строка.rstrip("\r\n")
        if not строка.strip():
            continue
        if " DEBUG " in строка:
            continue
        итог.append(очистить_строку(строка, секреты))
    return итог


def хвост_журнала(путь: Path | None = None, строк: int = СТРОК_ЖУРНАЛА) -> list[str]:
    """Последние строки файла журнала. Нет файла — пусто."""
    путь = путь or paths.data_dir() / "konspekt.log"
    try:
        with open(путь, "rb") as f:
            f.seek(0, os.SEEK_END)
            размер = f.tell()
            f.seek(max(0, размер - 256 * 1024))
            кусок = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return кусок.splitlines()[-строк:]


def _из_сборки() -> bool:
    try:
        from app import build_info  # noqa: PLC0415

        return bool(getattr(build_info, "WEB_FILES", None)) and getattr(sys, "frozen", False)
    except ImportError:
        return False


def сведения_о_системе(settings: Any, лицензия: dict[str, Any] | None, язык: str) -> list[str]:
    """Строки «ключ: значение» о сборке и настройках. Ни ключей, ни
    адресов, ни почт: только то, что помогает воспроизвести."""
    с = _слова(язык)
    try:
        from app.build_info import BUILD_DATE  # noqa: PLC0415
    except ImportError:
        BUILD_DATE = "?"
    сборка = BUILD_DATE if _из_сборки() else f"{BUILD_DATE}, {с['source']}"
    ос = f"{platform.system()} {platform.release()} {platform.machine()}"
    asr = getattr(settings, "asr", None)
    llm = getattr(settings, "llm", None)
    tg = getattr(settings, "telegram", None)
    строки = [
        f"{с['version']}: {__version__}",
        f"{с['build']}: {сборка}",
        f"{с['os']}: {ос}",
        f"{с['python']}: {platform.python_version()}",
        f"{с['ui_lang']}: {getattr(settings, 'language', '?')}",
    ]
    if asr is not None:
        строки.append(
            f"{с['asr']}: {asr.backend}, {asr.language}"
            + (", auto" if getattr(asr, "detect_language", False) else "")
            + (f", whisper {asr.whisper_size}" if getattr(asr, "whisper_size", "") else "")
        )
    if llm is not None:
        if llm.backend == "remote":
            # Имя модели безобидно, адрес и ключ — нет.
            строки.append(f"{с['llm']}: remote, {llm.model or '?'}")
        else:
            строки.append(f"{с['llm']}: {llm.backend}, {getattr(llm, 'local_model', '')}")
    if лицензия is not None:
        if лицензия.get("licensed"):
            состояние = с["yes"]
        elif лицензия.get("error"):
            состояние = с["expired"]
        else:
            состояние = с["no"]
        строки.append(f"{с['license']}: {состояние}")
    if tg is not None:
        строки.append(f"{с['telegram']}: {с['on'] if tg.enabled else с['off']}")
    return строки


def секреты_из_настроек(settings: Any) -> tuple[str, ...]:
    """Буквальные значения, которых в отчёте быть не должно ни при каких
    обстоятельствах."""
    from . import секрет  # noqa: PLC0415

    значения: list[str] = []
    for путь in (("license_key",), ("llm", "api_key"), ("llm", "base_url"), ("telegram", "token")):
        объект = settings
        for имя in путь:
            объект = getattr(объект, имя, None)
            if объект is None:
                break
        if isinstance(объект, str) and объект.strip():
            значения.append(объект)
            # Токен в настройках спрятан; в журнал он мог попасть открытым.
            открытый = секрет.достать(объект)
            if открытый:
                значения.append(открытый)
    # Длинные первыми: иначе короткий префикс съест часть длинного.
    return tuple(sorted(set(значения), key=len, reverse=True))


def собрать_отчёт(
    вид: str,
    текст: str,
    контакт: str,
    со_сведениями: bool,
    settings: Any,
    лицензия: dict[str, Any] | None,
    строки_журнала: list[str] | None = None,
) -> str:
    """Отчёт в Markdown: то, что уйдёт, в точности то, что показано."""
    язык = getattr(settings, "language", "en") or "en"
    с = _слова(язык)
    вид = вид if вид in ВИДЫ else "bug"
    части = [f"## {с[вид]}", "", текст.strip(), ""]
    if контакт.strip():
        части += [f"{с['contact']}: {контакт.strip()}", ""]
    if со_сведениями:
        секреты = секреты_из_настроек(settings)
        части += [
            "<details>", f"<summary>{с['system']}</summary>", "",
            *сведения_о_системе(settings, лицензия, язык), "", "</details>", "",
        ]
        журнал = очистить_журнал(
            строки_журнала if строки_журнала is not None else хвост_журнала(), секреты
        )
        if журнал:
            части += [
                "<details>", f"<summary>{с['log']}</summary>", "", "```",
                *журнал, "```", "</details>", "",
            ]
    return "\n".join(части).rstrip() + "\n"


def ссылка_на_витрину(язык: str) -> str:
    """Адрес страницы «написать разработчику» с версией и системой.

    В адресе только то, что страница подставит в форму за человека:
    версия, система, язык и пометка, что пришли из программы.
    """
    основа = ВИТРИНА_RU if язык == "ru" else ВИТРИНА_INTL
    параметры = {
        "v": __version__,
        "os": f"{platform.system()} {platform.release()} {platform.machine()}",
        "lang": язык if язык in СЛОВА else "en",
        "src": "app",
    }
    разделитель = "&" if "?" in основа else "?"
    return основа + разделитель + urllib.parse.urlencode(параметры)


def сведения_для_поддержки(settings: Any, лицензия: dict[str, Any] | None) -> str:
    """Текст для буфера обмена: система и вычищенный хвост журнала."""
    язык = getattr(settings, "language", "en") or "en"
    с = _слова(язык)
    секреты = секреты_из_настроек(settings)
    части = [с["system"], "", *сведения_о_системе(settings, лицензия, язык), ""]
    журнал = очистить_журнал(хвост_журнала(), секреты)
    if журнал:
        части += [с["log"], "", *журнал]
    return "\n".join(части).rstrip() + "\n"


def открыть_витрину(язык: str) -> dict[str, Any]:
    """Открыть страницу в браузере. Не открылась: адрес отдаём окну,
    чтобы человек мог перейти сам."""
    import webbrowser  # noqa: PLC0415

    адрес = ссылка_на_витрину(язык)
    try:
        ок = bool(webbrowser.open(адрес))
    except OSError:
        ок = False
    return {"ok": ок, "url": адрес}
