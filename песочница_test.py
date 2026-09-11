# -*- coding: utf-8 -*-
"""Проверки не трогают живой профиль человека.

Беда, из-за которой это написано. Проверки поднимают настоящий
`AppService`, а он умеет сохранять настройки и писать записи на диск.
Без своего каталога он делает это в профиль человека — в тот самый,
где лежат его встречи.

Обнаружилось не по коду, а по следам: обычный прогон набора выключил
человеку «писать системный звук» в живых настройках и насыпал в
`%APPDATA%\\Konspekt\\audio` 139 папок с записями почти на полгигабайта.
База при этом уцелела, но могла и не уцелеть.

Чинится это в `testenv.py` — одном месте, которое импортируют все
проверки. Здесь сторожим, что оно работает и что новая проверка не
забудет его импортировать.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import testenv  # noqa: F401  русский вывод и свой каталог данных

from app.core import paths

КОРЕНЬ = Path(__file__).resolve().parent
БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


def живой_профиль() -> Path:
    """Где лежат настоящие данные человека на этой машине."""
    if sys.platform == "win32":
        основа = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        основа = Path.home() / "Library" / "Application Support"
    else:
        основа = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return основа / "Konspekt"


def проверить_подмену() -> None:
    """Сам testenv уводит данные в сторону от живого профиля."""
    куда = paths.data_dir()
    проверить(куда != живой_профиль(),
              f"каталог данных проверки не живой профиль ({куда})")
    проверить(str(куда).startswith(tempfile.gettempdir()),
              f"каталог проверки лежит во временной папке ({куда})")


def проверить_импорт_везде() -> None:
    """Каждая проверка уводит данные в сторону от живого профиля.

    Обычный способ — импорт `testenv`. Но годится и свой каталог,
    выставленный руками до импорта программы: некоторым проверкам он
    нужен именно свой, чтобы заглянуть внутрь. Ругаемся только на тех,
    у кого нет ни того, ни другого: такая проверка молча уйдёт писать в
    профиль человека, и заметят это по чужой жалобе.
    """
    без_обвязки: list[str] = []
    for файл in sorted(КОРЕНЬ.glob("*_test.py")):
        текст = io.open(файл, encoding="utf-8", errors="replace").read()
        # Проверке, которая не поднимает программу, обвязка не нужна:
        # запускалка на JS, разбор текста, арифметика.
        трогает = any(н in текст for н in (
            "AppService", "WasapiCapture", "wasapi._Track", "Store(",
            "paths.data_dir", "settings_mod",
        ))
        if not трогает:
            continue
        защищён = ("import testenv" in текст
                   or "KONSPEKT_DATA_DIR" in текст)
        if not защищён:
            без_обвязки.append(файл.name)
    проверить(not без_обвязки,
              "все проверки, поднимающие программу, уводят данные в песочницу"
              + (f" (забыли: {', '.join(без_обвязки)})" if без_обвязки else ""))


def проверить_живьём() -> None:
    """Запускаем дочернюю проверку и смотрим, куда она пишет.

    Самое честное доказательство: не чтение кода, а настоящий процесс.
    Просим его сказать, где у него каталог данных, и требуем, чтобы это
    был не профиль человека.
    """
    скрипт = (
        "import testenv\n"
        "from app.core import paths\n"
        "print(paths.data_dir())\n"
    )
    временный = КОРЕНЬ / "_проба_изоляции.py"
    временный.write_text(скрипт, encoding="utf-8")
    try:
        итог = subprocess.run(
            [sys.executable, str(временный)], cwd=КОРЕНЬ,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={k: v for k, v in os.environ.items() if k != "KONSPEKT_DATA_DIR"},
        )
    finally:
        временный.unlink(missing_ok=True)
    куда = (итог.stdout or "").strip().split("\n")[-1] if итог.stdout else ""
    проверить(bool(куда), f"дочерняя проверка сказала свой каталог ({итог.stderr[:200]})")
    if куда:
        проверить(Path(куда) != живой_профиль(),
                  f"дочерняя проверка пишет не в живой профиль ({куда})")


def проверить_чистоту_профиля() -> None:
    """Живой профиль не изменился, пока шла эта проверка.

    Сравниваем время правки настроек до и после: если что-то в наборе
    полезет в профиль, это будет видно сразу.
    """
    настройки = живой_профиль() / "settings.json"
    if not настройки.exists():
        print("[..] живого профиля нет, сравнивать не с чем")
        return
    было = настройки.stat().st_mtime
    # Поднимаем настоящую службу — то самое, что портило настройки.
    from app.core.service import AppService
    from app.storage import Store

    служба = AppService(store=Store(str(Path(tempfile.mkdtemp()) / "t.db")))
    служба.settings.audio.capture_system = False
    from app.core import settings as settings_mod
    settings_mod.save(служба.settings)
    стало = настройки.stat().st_mtime
    проверить(было == стало,
              "сохранение настроек из проверки не тронуло живой профиль")


def main() -> int:
    проверить_подмену()
    проверить_импорт_везде()
    проверить_живьём()
    проверить_чистоту_профиля()

    if БЕДЫ:
        print(f"\nНе прошло проверок: {len(БЕДЫ)}")
        return 1
    print("\nПроверки живут в своей песочнице и не трогают данные человека.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
