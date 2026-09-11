# -*- coding: utf-8 -*-
"""Записи, которым не соответствует ни одна встреча.

Откуда они берутся. Программа заводит папку под звук сразу, как
начинается запись, а встреча попадает в базу позже. Оборванный запуск,
снятый процесс, отменённая встреча — и папка остаётся сиротой. Раньше
сюда же сыпали проверки, пока не научились жить в своей песочнице.

Такая папка не видна в программе никак: переслушать её нельзя, в списке
встреч её нет. Просто занятое место на диске.

    python tools/осиротевшие_записи.py            # посмотреть
    python tools/осиротевшие_записи.py --удалить  # убрать

Правило одно и оно строгое: трогаем только те папки, чьего
идентификатора нет в базе. Если база не прочиталась, не делаем ничего:
лучше оставить мусор, чем снести живое.
"""
import datetime
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def профиль() -> Path:
    """Каталог данных Konspekt на этой машине."""
    прямой = os.environ.get("KONSPEKT_DATA_DIR", "").strip()
    if прямой:
        return Path(прямой)
    имя = "Konspekt"
    if os.environ.get("KONSPEKT_PROFILE", "").strip():
        имя = "Konspekt (%s)" % os.environ["KONSPEKT_PROFILE"].strip()
    if sys.platform == "win32":
        основа = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        основа = Path.home() / "Library" / "Application Support"
    else:
        основа = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return основа / имя


корень = профиль()
боевая = корень / "konspekt.db"
записи = корень / "audio"

if not боевая.exists() or not записи.exists():
    sys.stdout.write("В %s нет базы или папки записей.\n" % корень)
    raise SystemExit(1)

# Читаем базу через копию: не мешаем работающей программе и ничего в
# ней не блокируем.
копия = Path(tempfile.mkdtemp()) / "копия.db"
shutil.copy2(боевая, копия)
for хвост in ("-wal", "-shm"):
    рядом = Path(str(боевая) + хвост)
    if рядом.exists():
        shutil.copy2(рядом, str(копия) + хвост)

бд = sqlite3.connect(str(копия))
встречи = {р[0] for р in бд.execute("select id from meetings")}
бд.close()

if not встречи:
    sys.stdout.write("База пуста или не прочиталась: ничего не трогаем.\n")
    raise SystemExit(1)

папки = [п for п in записи.iterdir() if п.is_dir()]
сироты = [п for п in папки if п.name not in встречи]

размер = 0
for п in сироты:
    for ф in п.rglob("*"):
        if ф.is_file():
            размер += ф.stat().st_size

sys.stdout.write("Встреч в базе: %d, папок с записями: %d\n"
                 % (len(встречи), len(папки)))
sys.stdout.write("Осиротевших папок: %d, занимают %.2f ГБ\n"
                 % (len(сироты), размер / 1024**3))

if "--удалить" not in sys.argv:
    сироты.sort(key=lambda п: п.stat().st_mtime, reverse=True)
    if сироты:
        sys.stdout.write("\nСамые свежие:\n")
    for п in сироты[:10]:
        когда = datetime.datetime.fromtimestamp(п.stat().st_mtime).strftime("%d.%m %H:%M")
        сколько = sum(ф.stat().st_size for ф in п.rglob("*") if ф.is_file())
        sys.stdout.write("  %s  %-26s %7.1f МБ\n"
                         % (когда, п.name[:26], сколько / 1024**2))
    sys.stdout.write("\nНичего не удалено. Чтобы убрать: --удалить\n")
    raise SystemExit(0)

убрано = 0
for п in сироты:
    try:
        shutil.rmtree(п)
        убрано += 1
    except OSError as беда:
        sys.stdout.write("Не удалось убрать %s: %s\n" % (п.name, беда))

осталось = len([п for п in записи.iterdir() if п.is_dir()])
sys.stdout.write("Убрано папок: %d, осталось: %d\n" % (убрано, осталось))
sys.stdout.write("Встреч в базе по-прежнему: %d\n" % len(встречи))
