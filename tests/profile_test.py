"""Профили данных: установленная копия и копия из исходников не мешают друг другу.

Проверяет ровно то, из-за чего профили и заводились: разные каталоги
данных и разные замки единственного экземпляра.
"""

import importlib
import os
import tempfile
from pathlib import Path

import testenv  # noqa: F401

from app.core import paths, single


def _с_окружением(**переменные):
    старое = {к: os.environ.get(к) for к in переменные}
    for к, з in переменные.items():
        if з is None:
            os.environ.pop(к, None)
        else:
            os.environ[к] = з
    importlib.reload(paths)
    importlib.reload(single)
    return старое


def проверить_профиль_разводит_данные() -> None:
    _с_окружением(KONSPEKT_PROFILE=None, KONSPEKT_DATA_DIR=None)
    обычный = paths.data_dir()
    обычный_замок = single.mutex_name()

    _с_окружением(KONSPEKT_PROFILE="razrabotka", KONSPEKT_DATA_DIR=None)
    рабочий = paths.data_dir()
    рабочий_замок = single.mutex_name()

    assert обычный != рабочий, f"каталоги совпали: {обычный}"
    assert обычный_замок != рабочий_замок, f"замки совпали: {обычный_замок}"
    assert "razrabotka" in str(рабочий), рабочий
    print(f"✓ профиль разводит данные: {обычный.name} и {рабочий.name}")
    print(f"✓ профиль разводит замок: {рабочий_замок}")


def проверить_прямой_каталог() -> None:
    with tempfile.TemporaryDirectory() as врем:
        _с_окружением(KONSPEKT_DATA_DIR=врем, KONSPEKT_PROFILE=None)
        assert str(paths.data_dir()) == врем, paths.data_dir()
        # Прямой каталог сильнее профиля: тесту нужна именно его папка.
        _с_окружением(KONSPEKT_DATA_DIR=врем, KONSPEKT_PROFILE="разработка")
        assert str(paths.data_dir()) == врем, paths.data_dir()
    print("✓ KONSPEKT_DATA_DIR перекрывает профиль")


def проверить_запускаемость_bat() -> None:
    """Файлы .bat должны читаться как cmd, а не как utf-8.

    Из-за чего написано: русские комментарии в `konspekt.bat` сломали
    запуск наглухо. Редактор пишет файл в utf-8, а cmd читает его в
    cp866, кириллица превращается в мусор, и каждая строка комментария
    выполняется как команда. Файл при этом выглядит безупречно.
    """
    for имя in ("konspekt.bat", "tools/проверить_сборку.bat"):
        путь = Path(имя)
        if not путь.is_file():
            continue
        байты = путь.read_bytes()
        # Любой байт за пределами ASCII в тексте команд означает, что
        # cmd прочтёт файл не так, как мы его писали.
        плохие = [b for b in байты if b > 127]
        assert not плохие, (
            f"{имя}: {len(плохие)} не-ASCII байт, cmd прочтёт их как cp866 "
            "и попытается выполнить комментарии как команды"
        )
    print("✓ файлы .bat состоят из ASCII и запустятся")


def main() -> None:
    try:
        проверить_профиль_разводит_данные()
        проверить_прямой_каталог()
        проверить_запускаемость_bat()
    finally:
        _с_окружением(KONSPEKT_PROFILE=None, KONSPEKT_DATA_DIR=None)
    print("Профили: все проверки прошли")


if __name__ == "__main__":
    main()
