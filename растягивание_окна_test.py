# -*- coding: utf-8 -*-
"""Растягивание окна не пишет настройки на каждом шаге.

Окно без рамки растягивается через мост: на каждом кадре движения мыши
фронт зовёт resize_window. Раньше каждый такой вызов писал settings.json
на диск, и во время записи звука окно при растягивании подвисало.
Теперь размер запоминается один раз, после паузы, и с итоговым размером.
"""
from __future__ import annotations

import testenv  # noqa: F401  песочница и русский вывод

import time

from app.ui import win32
from app.ui.api import Api


class Окно:
    x, y, width, height = 100, 100, 900, 700

    def resize(self, w: int, h: int) -> None:
        self.width, self.height = w, h

    def move(self, x: int, y: int) -> None:
        self.x, self.y = x, y


class Сервис:
    def __init__(self) -> None:
        self.записи: list[tuple] = []

    def save_window_geometry(self, *место) -> None:
        self.записи.append(место)


win32.AVAILABLE = False  # без настоящего окна: размер считает сам Api
api = Api.__new__(Api)
api._service = Сервис()
api._window = Окно()

for _ in range(30):
    api.resize_window(5, 3, "se")
сразу = len(api._service.записи)
time.sleep(0.7)

assert сразу == 0, f"во время растягивания настройки писались {сразу} раз"
print("[ok] во время растягивания настройки на диск не пишутся")
assert api._service.записи == [(100, 100, 1050, 790)], api._service.записи
print("[ok] после паузы записано одно место, итоговое: 1050×790")
print()
print("Растягивание окна не нагружает диск.")
