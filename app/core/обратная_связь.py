"""Сообщить о проблеме или предложить идею из раздела «О программе».

Отчёт собирается здесь, на компьютере, и человек видит его целиком до
того, как что-то куда-то уйдёт. Программа обещает, что наружу без
спроса уходит только номер версии, и отчёт это обещание не ломает: он
уходит лишь по нажатию кнопки, в том виде, в каком показан.

Куда уходит: пока сервера-приёмника нет, отчёт открывается новой
задачей на GitHub (`issues/new` с заполненным заголовком и началом
текста) и целиком кладётся в буфер обмена и в файл рядом с данными.
Длинный текст в адресе страницы не уместится: Windows режет адрес около
двух тысяч знаков, а кириллица в адресе весит девять байт на букву.
Поэтому в адресе только заголовок и короткая просьба вставить отчёт из
буфера, а сам отчёт человек вставляет одним Ctrl+V.

Что вычищаем из журнала перед отправкой: имя пользователя в путях,
почты, телефоны и прочее личное (`personal.mask`), токены и ключи, а
также буквальные значения секретов из настроек, если они вдруг попали
в строку. Строки длиннее трёхсот знаков обрезаем: длинная строка в
журнале — это почти всегда чужой текст, а не событие программы.
"""

from __future__ import annotations

import datetime as dt
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

REPO = "lamver/konspekt"
СТРОК_ЖУРНАЛА = 150
# Сколько знаков адреса страницы можно считать безопасными: Windows
# через ShellExecute обрезает длиннее примерно 2 083.
БЮДЖЕТ_АДРЕСА = 1900
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


def заголовок(вид: str, текст: str, язык: str) -> str:
    """Заголовок задачи: вид и первая строка текста, не длиннее 70 знаков."""
    с = _слова(язык)
    первая = " ".join(текст.strip().split())
    if len(первая) > 70:
        первая = первая[:69].rstrip() + "…"
    return f"[{с[вид if вид in ВИДЫ else 'bug']}] {первая}"


def ссылка_github(вид: str, текст: str, язык: str) -> str:
    """Адрес новой задачи с заголовком и началом текста в пределах бюджета.

    Тело здесь — лишь начало текста и просьба вставить отчёт из буфера:
    остальное не влезет в адрес, а обрезанный молча отчёт хуже короткого.
    """
    с = _слова(язык)
    основа = f"https://github.com/{REPO}/issues/new?"
    название = urllib.parse.quote(заголовок(вид, текст, язык))
    хвост = "\n\n" + с["paste"]
    слова = текст.strip().split()
    начало = ""
    for слово in слова:
        проба = (начало + " " + слово).strip()
        адрес = основа + "title=" + название + "&body=" + urllib.parse.quote(проба + "…" + хвост)
        if len(адрес) > БЮДЖЕТ_АДРЕСА:
            break
        начало = проба
    тело = (начало + ("…" if начало and len(начало) < len(" ".join(слова)) else "")) + хвост
    return основа + "title=" + название + "&body=" + urllib.parse.quote(тело.strip())


def сохранить_отчёт(отчёт: str) -> Path:
    """Файл отчёта рядом с данными: на случай, если браузер не открылся
    или человек хочет отправить его сам."""
    папка = paths.data_dir() / "отчёты"
    папка.mkdir(parents=True, exist_ok=True)
    путь = папка / f"отчёт-{dt.datetime.now():%Y%m%d-%H%M%S}.md"
    путь.write_text(отчёт, encoding="utf-8")
    return путь


def показать_файл(путь: Path) -> bool:
    """Открыть папку с отчётом в проводнике системы."""
    try:
        if sys.platform == "win32":
            import subprocess  # noqa: PLC0415

            subprocess.Popen(["explorer", "/select,", str(путь)])  # noqa: S603, S607
            return True
        if sys.platform == "darwin":
            import subprocess  # noqa: PLC0415

            subprocess.Popen(["open", "-R", str(путь)])  # noqa: S603, S607
            return True
        import webbrowser  # noqa: PLC0415

        return webbrowser.open(путь.parent.as_uri())
    except OSError:
        log.info("Не удалось открыть папку с отчётом", exc_info=True)
        return False
