# -*- coding: utf-8 -*-
"""Окно из файлов другой сборки замечается и чинится само.

Зачем (задача №5 в konspekt-releases, 05.10): «О программе» показывало
0.15.3, а меню настроек было из 0.8–0.11. Номер версии живёт в питоне,
окно в web/, и одно обновилось без другого, молча. Причина нашлась на
пробе: Конспект, запущенный посреди тихой установки, занимает свой exe,
и установщик откатывается.
"""
from __future__ import annotations

import testenv  # noqa: F401  русский вывод и песочница вместо боевого профиля

import ctypes
import logging
import shutil
import sys
import tempfile
import threading
import types
from pathlib import Path

from app import __main__ as запуск
from app import __version__
from app.core import paths
from app.core.целостность import идёт_установка, опись, пора_чинить, проверить_окно, расхождения

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


КОРЕНЬ = Path(__file__).resolve().parent
корень = Path(tempfile.mkdtemp(prefix="konspekt-окно-"))
web = корень / "web"
данные = корень / "данные"
данные.mkdir()
shutil.copytree(КОРЕНЬ / "web", web)
сборка = опись(web)

проверить("index.html" in сборка and "app.js" in сборка and "i18n/ru.json" in сборка,
          f"в описи окно целиком, с вложенными папками: {len(сборка)} файлов")
проверить(расхождения(web, сборка) == [], "свежая установка совпадает со сборкой")

(web / "старьё.txt").write_text("после прошлых версий", encoding="utf-8")
проверить(расхождения(web, сборка) == [], "лишний файл от старой версии не беда")

# То, что было у человека: меню без «Telegram» и «Лицензии».
index = web / "index.html"
index.write_text(index.read_text(encoding="utf-8").replace('data-tab="telegram"', ""), encoding="utf-8")
(web / "i18n" / "sr.json").unlink()
беды = расхождения(web, сборка)
проверить(беды == ["i18n/sr.json: нет", "index.html: другой"], f"старое меню и пропавший файл видны: {беды}")

# В разработке web/ правится каждый день, а опись от прошлой сборки:
# сверять там не с чем.
build_info = types.ModuleType("app.build_info")
build_info.WEB_FILES = сборка  # type: ignore[attr-defined]
sys.modules["app.build_info"] = build_info
проверить(проверить_окно(web) == [], "в разработке не сверяется")


class Журнал(logging.Handler):
    def __init__(self):
        super().__init__()
        self.строки: list[str] = []

    def emit(self, запись):
        self.строки.append(запись.getMessage())


class Обновление:
    def __init__(self, ready=None):
        self.ready = ready
        self.качали: list[str] = []

    def download_later(self, версия):
        self.качали.append(версия)


def запустить(обновление: Обновление) -> None:
    запуск._сверить_окно(types.SimpleNamespace(updater=обновление))
    for поток in threading.enumerate():
        if поток.name == "web-integrity":
            поток.join(5)


журнал = Журнал()
запуск.log.addHandler(журнал)
sys.frozen = True  # type: ignore[attr-defined]
try:
    проверить(len(проверить_окно(web)) == 2, "в собранной программе сверяется")

    paths.web_dir = lambda: web  # type: ignore[assignment]
    paths.data_dir = lambda: данные  # type: ignore[assignment]

    обновление = Обновление()
    запустить(обновление)
    проверить(any("Файлы окна не от этой сборки" in с and "index.html" in с for с in журнал.строки),
              f"беда записана в журнал: {журнал.строки}")
    проверить(обновление.качали == [__version__],
              f"своя же версия качается и встанет поверх, человеку ничего не делать: {обновление.качали}")

    обновление = Обновление()
    запустить(обновление)
    проверить(обновление.качали == [], "второй раз ту же версию не качаем: круга нет")

    обновление = Обновление(ready=object())
    (данные / "починка_окна.txt").unlink()
    запустить(обновление)
    проверить(обновление.качали == [], "уже скачана новая версия — она и починит")

    # Всё совпадает — ни строчки в журнале, ни загрузки.
    журнал.строки.clear()
    (данные / "починка_окна.txt").unlink(missing_ok=True)
    build_info.WEB_FILES = опись(web)  # type: ignore[attr-defined]
    обновление = Обновление()
    запустить(обновление)
    проверить(not обновление.качали and not any("не от этой сборки" in с for с in журнал.строки),
              "целое окно молчит и ничего не качает")
finally:
    del sys.frozen  # type: ignore[attr-defined]
    del sys.modules["app.build_info"]
    запуск.log.removeHandler(журнал)

проверить(пора_чинить(данные, "9.9.9") and not пора_чинить(данные, "9.9.9") and пора_чинить(данные, "9.9.10"),
          "починка по разу на каждую версию")
shutil.rmtree(корень, ignore_errors=True)

# Запуск посреди установки: сразу выходим, иначе займём свой exe.
if sys.platform == "win32":
    kernel32 = ctypes.windll.kernel32
    имя = "KonspektSetup-проба"
    проверить(not идёт_установка(имя), "установки нет — запускаемся")
    замок = kernel32.CreateMutexW(None, False, имя)
    проверить(идёт_установка(имя), "идёт установка — видим её замок")
    kernel32.CloseHandle(замок)
    проверить(not идёт_установка(имя), "установщик закончил — снова запускаемся")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Окно из файлов другой сборки замечается и чинится само.")
