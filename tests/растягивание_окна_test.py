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
# --- Растягивание ведёт свой поток, а не мост -------------------------------
# Во время записи мост отвечает с задержкой, и покадровые смещения дёргали
# окно. Теперь мост зовётся раз, на нажатие, а размер ставит поток Win32.
# Курсор, кнопку и окно подменяем: проверяем арифметику краёв и минимум.
import ctypes  # noqa: E402

if hasattr(win32, "_resize_loop"):
    def прогон(край: str, путь: list[tuple[int, int]], прямоугольник=(100, 100, 1000, 800)):
        шаги = list(путь)
        поставлено: list[tuple] = []
        курсор = {"xy": шаги[0]}

        def нажата() -> bool:
            if not шаги:
                return False
            курсор["xy"] = шаги.pop(0)
            return True

        class U:
            @staticmethod
            def GetWindowRect(hwnd, ссылка):
                r = ctypes.cast(ссылка, ctypes.POINTER(win32.wintypes.RECT)).contents
                r.left, r.top, r.right, r.bottom = прямоугольник
                return True

            @staticmethod
            def SetWindowPos(hwnd, после, x, y, w, h, флаги):
                поставлено.append((x, y, w, h))
                return True

        старое = (win32._u, win32._cursor, win32._pressed, win32.time.sleep)
        win32._u, win32._cursor, win32._pressed = U, lambda: курсор["xy"], нажата
        win32.time.sleep = lambda s: None
        try:
            win32._resize_loop(1, край, 360, 420)
        finally:
            win32._u, win32._cursor, win32._pressed, win32.time.sleep = старое
        return поставлено[-1] if поставлено else None

    se = прогон("se", [(500, 500), (550, 530), (600, 560)])
    assert se == (100, 100, 1000, 760), se
    print("[ok] за правый нижний угол: растут ширина и высота, угол на месте")
    nw = прогон("nw", [(500, 500), (450, 480)])
    assert nw == (50, 80, 950, 720), nw
    print("[ok] за левый верхний угол: окно растёт влево-вверх, правый низ на месте")
    узко = прогон("e", [(500, 500), (-2000, 500)])
    assert узко == (100, 100, 360, 700), узко
    print("[ok] уже минимума окно не сжимается: 360 точек")
    низко = прогон("n", [(500, 500), (500, 5000)])
    assert низко == (100, 380, 900, 420), низко
    print("[ok] за верхний край вниз: высота не меньше 420, низ на месте")

print()
print("Растягивание окна не нагружает диск.")
