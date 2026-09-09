"""Снять окно программы в PNG средствами Windows.

Отдельным файлом, потому что у pywebview снимка нет вовсе, а способ
съёмки нетривиален: обычный захват экрана сфотографировал бы всё, что
случайно оказалось поверх окна, включая чужие уведомления. PrintWindow
просит саму программу нарисовать себя в отданный ей холст, поэтому в
кадр не попадает ничего постороннего, даже если окно частично закрыто.

Флаг PW_RENDERFULLCONTENT обязателен: без него содержимое WebView2
выходит чёрным прямоугольником, потому что рисуется не обычным GDI.
"""

from __future__ import annotations

import ctypes
from pathlib import Path

PW_RENDERFULLCONTENT = 0x00000002


def снять_окно(заголовок: str, куда: Path) -> None:
    import win32gui
    import win32ui

    hwnd = win32gui.FindWindow(None, заголовок)
    if not hwnd:
        raise RuntimeError(f"не нашли окно «{заголовок}»")

    левый, верхний, правый, нижний = win32gui.GetWindowRect(hwnd)
    ширина, высота = правый - левый, нижний - верхний
    if ширина <= 0 or высота <= 0:
        raise RuntimeError(f"у окна нулевой размер: {ширина}x{высота}")

    окно_dc = win32gui.GetWindowDC(hwnd)
    исток = win32ui.CreateDCFromHandle(окно_dc)
    приёмник = исток.CreateCompatibleDC()
    холст = win32ui.CreateBitmap()
    холст.CreateCompatibleBitmap(исток, ширина, высота)
    приёмник.SelectObject(холст)

    try:
        ок = ctypes.windll.user32.PrintWindow(
            hwnd, приёмник.GetSafeHdc(), PW_RENDERFULLCONTENT
        )
        if not ок:
            raise RuntimeError("PrintWindow отказался рисовать окно")

        данные = холст.GetBitmapBits(True)
        из_окна = холст.GetInfo()

        from PIL import Image

        картинка = Image.frombuffer(
            "RGB",
            (из_окна["bmWidth"], из_окна["bmHeight"]),
            данные, "raw", "BGRX", 0, 1,
        )
        куда.parent.mkdir(parents=True, exist_ok=True)
        картинка.save(куда, "PNG", optimize=True)
    finally:
        win32gui.DeleteObject(холст.GetHandle())
        приёмник.DeleteDC()
        исток.DeleteDC()
        win32gui.ReleaseDC(hwnd, окно_dc)
