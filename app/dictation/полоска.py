"""Полоска «слушаю» поверх всех окон, не крадущая фокус.

Без неё диктовка слепая: человек говорит и не знает, слышат ли его,
а в режиме переключателя ещё и не знает, идёт ли запись до сих пор.

Главное требование — не трогать фокус. Обычное окно, всплывая, забирает
фокус себе, и текст после этого поедет не в письмо, а в нашу полоску:
у Handy этому посвящён отдельный раздел в README, они на этом обожглись.
Спасает `WS_EX_NOACTIVATE`: окно видно, но система никогда не делает его
активным. Плюс `WS_EX_TOOLWINDOW`, чтобы полоска не появлялась в
Alt+Tab и на панели задач.

Почему не окно pywebview. Второе окно WebView2 весит десятки мегабайт
памяти и поднимается почти секунду — ради полоски в три слова это
расточительство, а секунда задержки убивает саму мысль о быстрой
диктовке. Здесь чистый WinAPI: окно появляется мгновенно.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes

log = logging.getLogger(__name__)

ДОСТУПНО = sys.platform == "win32"

ШИРИНА = 210
ВЫСОТА = 44
# Отступ от нижнего края экрана. Внизу по центру: там полоска не
# закрывает ни поле ввода, ни то, что человек читает.
ОТСТУП_СНИЗУ = 120

if ДОСТУПНО:
    _u = ctypes.windll.user32
    _g = ctypes.windll.gdi32

    WS_EX_TOPMOST = 0x00000008
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_LAYERED = 0x00080000
    WS_POPUP = 0x80000000
    SW_SHOWNA = 8  # показать, не делая активным
    SW_HIDE = 0
    # Показ и скрытие через SetWindowPos с явным SWP_NOACTIVATE, а не
    # через ShowWindow. Так намерение записано в самом вызове: «покажи,
    # но не активируй». ShowWindow такого флага не имеет вовсе, и его
    # безобидность держится только на стиле окна.
    #
    # Честно о доказательствах: замер показал, что при живом
    # WS_EX_NOACTIVATE оба способа ведут себя одинаково (девять прогонов
    # подряд). Поэтому это выбор в пользу явного, а не починка
    # воспроизведённой беды.
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    SWP_HIDEWINDOW = 0x0080
    WM_DESTROY = 0x0002
    WM_PAINT = 0x000F
    LWA_ALPHA = 0x00000002
    DT_CENTER = 0x00000001
    DT_VCENTER = 0x00000004
    DT_SINGLELINE = 0x00000020

    # LRESULT и WPARAM на x64 шириной с указатель. Оставив стандартные
    # 32-битные типы, обработчик падает на каждом втором сообщении с
    # «int too long to convert»: окно живёт, но система заваливает
    # журнал ошибками, а часть сообщений остаётся необработанной.
    LRESULT = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
    WPARAM = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
    LPARAM = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long

    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)

    _u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    _u.DefWindowProcW.restype = LRESULT
    _u.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    _u.CreateWindowExW.restype = wintypes.HWND
    _u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u.IsWindowVisible.argtypes = [wintypes.HWND]
    _u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    _u.InvalidateRect.argtypes = [wintypes.HWND, wintypes.LPVOID, wintypes.BOOL]
    _u.SetLayeredWindowAttributes.argtypes = [
        wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD,
    ]
    _u.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    _u.SetWindowPos.restype = wintypes.BOOL

    class WNDCLASS(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class PAINTSTRUCT(ctypes.Structure):
        _fields_ = [
            ("hdc", wintypes.HDC),
            ("fErase", wintypes.BOOL),
            ("rcPaint", wintypes.RECT),
            ("fRestore", wintypes.BOOL),
            ("fIncUpdate", wintypes.BOOL),
            ("rgbReserved", ctypes.c_byte * 32),
        ]


class Полоска:
    """Окошко «слушаю» поверх всех окон. Живёт в своём потоке."""

    КЛАСС = "KonspektDictationHint"

    def __init__(self) -> None:
        self._hwnd = 0
        self._поток: threading.Thread | None = None
        self._готово = threading.Event()
        self._текст = "Слушаю…"
        self._класс_зарегистрирован = False
        self._proc = None  # держим ссылку: иначе GC съест обработчик

    # --- показ и скрытие --------------------------------------------------

    def показать(self, текст: str = "Слушаю…") -> None:
        if not ДОСТУПНО:
            return
        self._текст = текст
        if self._hwnd:
            self._перерисовать()
            self._переключить(показать=True)
            return
        self._поток = threading.Thread(
            target=self._жить, name="диктовка-полоска", daemon=True
        )
        self._поток.start()
        # Ждём недолго: не появилась полоска — не повод ронять диктовку.
        self._готово.wait(1.0)

    def сменить_текст(self, текст: str) -> None:
        """Например, с «Слушаю» на «Распознаю»: человек ждёт и нервничает."""
        if not ДОСТУПНО or not self._hwnd:
            return
        self._текст = текст
        self._перерисовать()

    def скрыть(self) -> None:
        if ДОСТУПНО and self._hwnd:
            self._переключить(показать=False)

    def _переключить(self, показать: bool) -> None:
        """Показать или спрятать, ни при каких условиях не трогая фокус."""
        _u.SetWindowPos(
            self._hwnd, None, 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE
            | (SWP_SHOWWINDOW if показать else SWP_HIDEWINDOW),
        )

    def закрыть(self) -> None:
        if ДОСТУПНО and self._hwnd:
            _u.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)
            self._hwnd = 0

    @property
    def видна(self) -> bool:
        return bool(ДОСТУПНО and self._hwnd and _u.IsWindowVisible(self._hwnd))

    @property
    def hwnd(self) -> int:
        return self._hwnd

    def _перерисовать(self) -> None:
        if self._hwnd:
            _u.InvalidateRect(self._hwnd, None, True)

    # --- окно -------------------------------------------------------------

    def _жить(self) -> None:
        try:
            self._создать()
        except Exception:
            log.warning("Полоска диктовки не создалась", exc_info=True)
            self._готово.set()
            return
        self._готово.set()

        # Свой цикл сообщений: окно живёт в этом потоке и обязано
        # разбирать свою очередь, иначе система считает его зависшим.
        сообщение = wintypes.MSG()
        while _u.GetMessageW(ctypes.byref(сообщение), None, 0, 0) > 0:
            _u.TranslateMessage(ctypes.byref(сообщение))
            _u.DispatchMessageW(ctypes.byref(сообщение))

    def _создать(self) -> None:
        экземпляр = ctypes.windll.kernel32.GetModuleHandleW(None)

        def обработчик(hwnd, сообщение, wparam, lparam):
            if сообщение == WM_PAINT:
                self._рисовать(hwnd)
                return 0
            if сообщение == WM_DESTROY:
                _u.PostQuitMessage(0)
                return 0
            return _u.DefWindowProcW(hwnd, сообщение, wparam, lparam)

        self._proc = WNDPROC(обработчик)

        класс = WNDCLASS()
        класс.lpfnWndProc = self._proc
        класс.hInstance = экземпляр
        класс.lpszClassName = self.КЛАСС
        класс.hbrBackground = _g.CreateSolidBrush(0x1A1A1A)
        класс.hCursor = _u.LoadCursorW(None, 32512)  # IDC_ARROW
        # Класс мог остаться от прошлого запуска в этом же процессе.
        _u.RegisterClassW(ctypes.byref(класс))

        ширина_экрана = _u.GetSystemMetrics(0)
        высота_экрана = _u.GetSystemMetrics(1)
        x = (ширина_экрана - ШИРИНА) // 2
        y = высота_экрана - ОТСТУП_СНИЗУ

        self._hwnd = _u.CreateWindowExW(
            # NOACTIVATE — то, ради чего всё затевалось: без него окно
            # заберёт фокус, и продиктованный текст уедет не туда.
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED,
            self.КЛАСС, "Konspekt", WS_POPUP,
            x, y, ШИРИНА, ВЫСОТА,
            None, None, экземпляр, None,
        )
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        _u.SetLayeredWindowAttributes(self._hwnd, 0, 235, LWA_ALPHA)
        self._переключить(показать=True)

    def _рисовать(self, hwnd) -> None:
        ps = PAINTSTRUCT()
        hdc = _u.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            область = wintypes.RECT(0, 0, ШИРИНА, ВЫСОТА)
            кисть = _g.CreateSolidBrush(0x1A1A1A)
            _u.FillRect(hdc, ctypes.byref(область), кисть)
            _g.DeleteObject(кисть)

            _g.SetBkMode(hdc, 1)  # TRANSPARENT
            # Красная точка как у любой записи: понятно без слов и на
            # любом языке.
            _g.SetTextColor(hdc, 0x4444EE)  # BGR: красный
            точка = wintypes.RECT(16, 0, 32, ВЫСОТА)
            _u.DrawTextW(hdc, "●", -1, ctypes.byref(точка),
                         DT_VCENTER | DT_SINGLELINE)

            _g.SetTextColor(hdc, 0xF0F0F0)
            текст = wintypes.RECT(34, 0, ШИРИНА - 10, ВЫСОТА)
            _u.DrawTextW(hdc, self._текст, -1, ctypes.byref(текст),
                         DT_VCENTER | DT_SINGLELINE)
        finally:
            _u.EndPaint(hwnd, ctypes.byref(ps))
