"""Снимок окна Konspekt через экран.

WebView2 рисует содержимое мимо BitBlt окна: прямой снимок выходит
пустым бежевым прямоугольником. Поэтому снимаем весь рабочий стол
(включая второй монитор) и вырезаем окно по его координатам.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import sys
import time

import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image


def find_window() -> int | None:
    """Окно запущенной из исходников копии.

    Одновременно бывает открыта и установленная версия с тем же
    заголовком, и снимок легко сделать не с того окна. Отличаем по
    процессу: у dev-копии это python.exe.
    """
    found = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        if win32gui.GetWindowText(hwnd) != "Konspekt":
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            h = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION
                                     | win32con.PROCESS_VM_READ, False, pid)
            name = win32process.GetModuleFileNameEx(h, 0)
        except Exception:
            name = ""
        found.append((hwnd, name))

    win32gui.EnumWindows(cb, None)
    dev = [h for h, n in found if n.lower().endswith("python.exe")]
    if dev:
        return dev[0]
    return found[0][0] if found else None


def desktop_shot() -> Image.Image:
    x = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
    y = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
    w = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
    h = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
    dc = win32gui.GetDC(0)
    src = win32ui.CreateDCFromHandle(dc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    mem.BitBlt((0, 0), (w, h), src, (x, y), win32con.SRCCOPY)
    info = bmp.GetInfo()
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                           bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    win32gui.DeleteObject(bmp.GetHandle())
    mem.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(0, dc)
    img.info["origin"] = (x, y)
    return img


def shot(path: str, click: tuple[int, int] | None = None) -> None:
    hwnd = find_window()
    if hwnd is None:
        sys.exit("окно Konspekt не найдено")
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    # SetForegroundWindow Windows молча игнорирует, когда активно чужое
    # окно, и клик уходит мимо: так пара кликов улетела в браузер.
    # Поэтому поднимаем окно поверх всех явно и проверяем результат.
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
    time.sleep(0.5)
    win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
    time.sleep(0.8)
    if win32gui.GetForegroundWindow() != hwnd:
        print("внимание: окно не активно, клик может уйти мимо")

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    if click:
        # Координаты задаём внутри окна, а кликаем по экрану: так же,
        # как это сделал бы человек мышью.
        cx, cy = left + click[0], top + click[1]
        win32api.SetCursorPos((cx, cy))
        time.sleep(0.2)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
        time.sleep(1.2)
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)

    desk = desktop_shot()
    ox, oy = desk.info["origin"]
    img = desk.crop((left - ox, top - oy, right - ox, bottom - oy))
    img.save(path)
    print(f"снимок: {path} {img.size}, окно {left},{top}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "_shot.png"
    click = None
    if len(sys.argv) > 3:
        click = (int(sys.argv[2]), int(sys.argv[3]))
    shot(path, click)
