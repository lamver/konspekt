"""Оконные операции напрямую через WinAPI.

Зачем не через pywebview: его методы (`on_top`, `move`, `resize`) идут
через WinForms `Invoke`, то есть ждут UI-поток. А вызовы из JS приходят в
отдельном потоке моста, пока UI-поток занят обработкой того же самого
вызова. Получается взаимная блокировка: поток моста ждёт UI, UI ждёт
мост, окно намертво виснет. Ловилось на кнопке «поверх всех окон».

`SetWindowPos` и `ShowWindow` такой болезни лишены: их можно звать из
любого потока. На не-Windows модуль сообщает, что недоступен, и вызывающий
код откатывается на обычный путь pywebview.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes

AVAILABLE = sys.platform == "win32"

if AVAILABLE:
    _u = ctypes.windll.user32

    # Сигнатуры обязательны: без них ctypes считает все аргументы 32-битными
    # и на x64 обрезает дескриптор окна. Вызов молча возвращает ошибку.
    _u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _u.FindWindowW.restype = wintypes.HWND
    _u.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    _u.SetWindowPos.restype = wintypes.BOOL
    _u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _u.GetWindowRect.restype = wintypes.BOOL
    _u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u.ShowWindow.restype = wintypes.BOOL
    _u.GetDpiForWindow.argtypes = [wintypes.HWND]
    _u.GetDpiForWindow.restype = wintypes.UINT
    _u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u.SendMessageW.restype = wintypes.LPARAM
    _u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u.PostMessageW.restype = wintypes.BOOL
    _u.ReleaseCapture.argtypes = []
    _u.ReleaseCapture.restype = wintypes.BOOL
    _u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _u.GetCursorPos.restype = wintypes.BOOL
    _u.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _u.GetAsyncKeyState.restype = ctypes.c_short

    HWND_TOPMOST = wintypes.HWND(-1)
    HWND_NOTOPMOST = wintypes.HWND(-2)
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    SW_MINIMIZE = 6

    # Сообщения окна
    WM_NCLBUTTONDOWN = 0x00A1
    HTCAPTION = 2
    VK_LBUTTON = 0x01

_drag_lock = threading.Lock()
_drag_active = False


def _hwnd(window) -> int:
    """Найти дескриптор окна по заголовку.

    У pywebview на WinForms есть `.gui.BrowserView`, но лезть в его
    внутренности хрупко, а заголовок у нас свой и единственный.
    """
    if not AVAILABLE:
        return 0
    title = getattr(window, "title", None) or "Konspekt"
    return int(_u.FindWindowW(None, title) or 0)


def scale(hwnd: int) -> float:
    """Во сколько раз физические пиксели крупнее логических.

    WinAPI работает в физических, а pywebview принимает логические, и на
    мониторе с масштабом 125% окно иначе прыгало бы при каждом ресайзе.
    """
    if not AVAILABLE or not hwnd:
        return 1.0
    try:
        return (_u.GetDpiForWindow(hwnd) or 96) / 96.0
    except Exception:
        return 1.0


def get_rect(window) -> tuple[int, int, int, int] | None:
    """Текущая геометрия окна в логических пикселях: x, y, ширина, высота."""
    hwnd = _hwnd(window)
    if not hwnd:
        return None
    r = wintypes.RECT()
    if not _u.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    k = scale(hwnd)
    return (
        int(r.left / k),
        int(r.top / k),
        int((r.right - r.left) / k),
        int((r.bottom - r.top) / k),
    )


def set_geometry(window, x: int, y: int, width: int, height: int) -> bool:
    """Переставить и растянуть окно одним вызовом, без ожидания UI-потока."""
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    k = scale(hwnd)
    return bool(
        _u.SetWindowPos(
            hwnd, wintypes.HWND(0),
            int(x * k), int(y * k), int(width * k), int(height * k),
            SWP_NOZORDER | SWP_NOACTIVATE,
        )
    )


def set_on_top(window, pinned: bool) -> bool:
    """Закрепить окно поверх остальных или отпустить."""
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    return bool(
        _u.SetWindowPos(
            hwnd, HWND_TOPMOST if pinned else HWND_NOTOPMOST,
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    )


def minimize(window) -> bool:
    """Свернуть окно."""
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    _u.ShowWindow(hwnd, SW_MINIMIZE)
    return True


def start_drag(window) -> bool:
    """Захватить окно для перетаскивания мышью.

    Системный захват заголовка (ReleaseCapture + WM_NCLBUTTONDOWN) здесь не
    работает: ReleaseCapture отпускает мышь только в своём потоке, а захват
    держит поток WebView2. Поэтому цикл перетаскивания просто не стартовал,
    и окно стояло на месте.

    Вместо этого водим окно сами: отдельный поток следит за курсором и
    двигает окно через SetWindowPos, пока не отпущена левая кнопка. Поток
    свой, UI-поток и мост pywebview не задействованы, так что занятость
    Python распознаванием перетаскиванию не мешает.
    """
    global _drag_active
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    with _drag_lock:
        if _drag_active:
            return True
        _drag_active = True
    threading.Thread(target=_drag_loop, args=(hwnd,), daemon=True).start()
    return True


def _cursor() -> tuple[int, int]:
    p = wintypes.POINT()
    _u.GetCursorPos(ctypes.byref(p))
    return int(p.x), int(p.y)


def _pressed() -> bool:
    return bool(_u.GetAsyncKeyState(VK_LBUTTON) & 0x8000)


def _drag_loop(hwnd: int) -> None:
    """Двигать окно вслед за курсором, пока держат левую кнопку."""
    global _drag_active
    try:
        r = wintypes.RECT()
        if not _u.GetWindowRect(hwnd, ctypes.byref(r)):
            return
        start_x, start_y = _cursor()
        left, top = int(r.left), int(r.top)
        # Кнопку могли отпустить, пока сообщение шло через мост.
        deadline = time.monotonic() + 0.2
        while not _pressed() and time.monotonic() < deadline:
            time.sleep(0.005)
        while _pressed():
            x, y = _cursor()
            _u.SetWindowPos(
                hwnd, wintypes.HWND(0),
                left + (x - start_x), top + (y - start_y), 0, 0,
                SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE,
            )
            time.sleep(0.008)
    except Exception:
        pass
    finally:
        with _drag_lock:
            _drag_active = False
