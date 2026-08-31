"""Профили данных: установленная копия и копия из исходников не мешают друг другу.

Проверяет ровно то, из-за чего профили и заводились: разные каталоги
данных и разные замки единственного экземпляра.
"""

import importlib
import os
import tempfile

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

    _с_окружением(KONSPEKT_PROFILE="разработка", KONSPEKT_DATA_DIR=None)
    рабочий = paths.data_dir()
    рабочий_замок = single.mutex_name()

    assert обычный != рабочий, f"каталоги совпали: {обычный}"
    assert обычный_замок != рабочий_замок, f"замки совпали: {обычный_замок}"
    assert "разработка" in str(рабочий), рабочий
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


def main() -> None:
    try:
        проверить_профиль_разводит_данные()
        проверить_прямой_каталог()
    finally:
        _с_окружением(KONSPEKT_PROFILE=None, KONSPEKT_DATA_DIR=None)
    print("Профили: все проверки прошли")


if __name__ == "__main__":
    main()
