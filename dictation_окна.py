"""Настоящее чужое окно для проверок диктовки: своё, а не Блокнот.

Почему не Блокнот, хотя он и был первым выбором. В Windows 11 он
переиспользует одно и то же окно: открываешь второй файл — окно то же
самое, меняется лишь заголовок и вкладка. Проверено обходом окон, все
запуски подряд дают один и тот же дескриптор.

Отсюда три беды разом.

**«Своё окно» было выдумкой.** Проверка думала, что печатает в свой
временный файл, а печатала в общее окно, где рядом открыты файлы
человека. Однажды так и вышло: пробный текст уехал в чужой файл.

**Закрыть за собой невозможно.** `WM_CLOSE` закрывает вкладку, а не
окно, а `kill()` бьёт по запускателю, который к окну отношения не имеет.
Окна копились за вечер и всплывали поверх, уводя фокус. Ровно поэтому
полный прогон падал там, где одиночный проходил пять раз из пяти.

**Мусор оставался с несохранёнными изменениями.** Такое окно на закрытии
спрашивает «сохранить?», вешает диалог и рушит всё, что идёт следом.

Поэтому здесь поднимается своё окно с обычным полем ввода. Оно ничем не
хуже как приёмник: для Windows это точно такое же чужое окно с полем
ввода, а ввод туда идёт тем же путём. Зато оно наше целиком — своё на
каждую проверку, закрывается насмерть, ничего человеческого рядом нет.

Что осталось от Windows и никуда не делось: `SetForegroundWindow` из
фонового процесса система игнорирует (защита от воровства фокуса), и
обходится это временной привязкой к потоку ввода активного окна.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes

ДОСТУПНО = sys.platform == "win32"

if ДОСТУПНО:
    _u = ctypes.windll.user32
    _k = ctypes.windll.kernel32

    LRESULT = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
    WPARAM = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
    LPARAM = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long

    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)

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

    # Сигнатуры обязательны: без них на x64 дескрипторы обрезаются до
    # 32 бит и вызовы молча не срабатывают.
    _u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    _u.DefWindowProcW.restype = LRESULT
    _u.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    _u.CreateWindowExW.restype = wintypes.HWND
    _u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    _u.SendMessageW.restype = LRESULT
    _u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    _u.PostMessageW.restype = wintypes.BOOL
    _u.GetForegroundWindow.restype = wintypes.HWND
    _u.SetForegroundWindow.argtypes = [wintypes.HWND]
    _u.SetForegroundWindow.restype = wintypes.BOOL
    _u.IsWindow.argtypes = [wintypes.HWND]
    _u.IsWindow.restype = wintypes.BOOL
    _u.SetFocus.argtypes = [wintypes.HWND]
    _u.SetFocus.restype = wintypes.HWND

    WS_OVERLAPPEDWINDOW = 0x00CF0000
    WS_CHILD = 0x40000000
    WS_VISIBLE = 0x10000000
    ES_MULTILINE = 0x0004
    ES_AUTOVSCROLL = 0x0040
    WS_EX_TOPMOST = 0x00000008
    WM_DESTROY = 0x0002
    WM_CLOSE = 0x0010
    WM_SETFOCUS = 0x0007
    WM_GETTEXT = 0x000D
    WM_SETTEXT = 0x000C
    WM_GETTEXTLENGTH = 0x000E
    SW_SHOW = 5
    SW_RESTORE = 9


class НетОкна(RuntimeError):
    """Сцену воспроизвести не удалось. Беда среды, а не программы."""


# Обработчики окон и объекты классов, отданные системе. Держим их вечно,
# на весь век процесса.
#
# Причина не в аккуратности, а в падении. Windows зовёт обработчик и
# после закрытия окна, при разборе очереди сообщений. Стоит сборщику
# мусора добраться до него раньше, и процесс валится с нарушением
# доступа к памяти (код 0xC0000005) уже на выходе, когда все проверки
# прошли. Выглядит как поломка программы, хотя сломана уборка.
_живые: list = []


def поднять(hwnd: int) -> None:
    """Вытащить окно на передний план.

    `SetForegroundWindow` из фонового процесса Windows игнорирует: это
    защита от программ, ворующих фокус. Штатный обход — на время
    привязаться к потоку ввода активного окна.
    """
    _u.ShowWindow(hwnd, SW_RESTORE)
    свой = _k.GetCurrentThreadId()
    чужой = _u.GetWindowThreadProcessId(_u.GetForegroundWindow(), None)
    _u.AttachThreadInput(свой, чужой, True)
    try:
        _u.BringWindowToTop(hwnd)
        _u.SetForegroundWindow(hwnd)
    finally:
        _u.AttachThreadInput(свой, чужой, False)


class ОкноПриёмник:
    """Своё окно с полем ввода: принимает текст, как любое чужое."""

    КЛАСС = "KonspektПроверкаВвода"
    счётчик = 0

    def __init__(self, метка: str = "") -> None:
        ОкноПриёмник.счётчик += 1
        self.заголовок = f"Приёмник Konspekt {ОкноПриёмник.счётчик} {метка}".strip()
        self.hwnd = 0
        self.поле = 0
        self._поток: threading.Thread | None = None
        self._готово = threading.Event()
        self._беда: str | None = None
        self._proc = None  # держим ссылку: иначе GC съест обработчик

    def __enter__(self) -> "ОкноПриёмник":
        if not ДОСТУПНО:
            raise НетОкна("окна проверяются только под Windows")
        self._поток = threading.Thread(
            target=self._жить, name="приёмник-проверки", daemon=True
        )
        self._поток.start()
        if not self._готово.wait(5.0) or self._беда:
            raise НетОкна(self._беда or "окно не поднялось")
        self.взять_фокус()
        return self

    def __exit__(self, *_) -> None:
        if self.hwnd and _u.IsWindow(self.hwnd):
            _u.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            предел = time.time() + 3
            while time.time() < предел and _u.IsWindow(self.hwnd):
                time.sleep(0.1)

    # --- фокус ------------------------------------------------------------

    def взять_фокус(self, ждать: float = 8.0) -> None:
        предел = time.time() + ждать
        while time.time() < предел and not self.фокус_наш():
            поднять(self.hwnd)
            time.sleep(0.25)
        if not self.фокус_наш():
            raise НетОкна("не удалось получить фокус")
        # Курсор должен стоять именно в поле ввода, иначе текст уйдёт в
        # окно и пропадёт.
        _u.SendMessageW(self.hwnd, WM_SETFOCUS, 0, 0)
        time.sleep(0.2)

    def фокус_наш(self) -> bool:
        активное = _u.GetForegroundWindow()
        return активное in (self.hwnd, self.поле)

    def удержать_фокус(self) -> None:
        """Вернуть фокус, если его увели прямо перед вводом.

        На рабочей машине окна всплывают сами: уведомления, чужие
        программы. Без этого текст уходит в чужое окно, а проверка
        винит наш код.
        """
        if not self.фокус_наш():
            self.взять_фокус(ждать=4.0)

    # --- содержимое -------------------------------------------------------

    def содержимое(self) -> str:
        """Что оказалось в поле ввода."""
        длина = _u.SendMessageW(self.поле, WM_GETTEXTLENGTH, 0, 0)
        буфер = ctypes.create_unicode_buffer(int(длина) + 1)
        # Адрес буфера передаём числом: LPARAM это целое, а не указатель,
        # и ctypes.cast на нём отказывается работать.
        _u.SendMessageW(
            self.поле, WM_GETTEXT, len(буфер), ctypes.addressof(буфер)
        )
        return буфер.value.strip()

    def очистить(self) -> None:
        пусто = ctypes.create_unicode_buffer("")
        _u.SendMessageW(self.поле, WM_SETTEXT, 0, ctypes.addressof(пусто))

    # --- окно -------------------------------------------------------------

    def _жить(self) -> None:
        try:
            self._создать()
        except Exception as беда:  # noqa: BLE001
            self._беда = str(беда)
            self._готово.set()
            return
        self._готово.set()

        сообщение = wintypes.MSG()
        while _u.GetMessageW(ctypes.byref(сообщение), None, 0, 0) > 0:
            _u.TranslateMessage(ctypes.byref(сообщение))
            _u.DispatchMessageW(ctypes.byref(сообщение))

    def _создать(self) -> None:
        экземпляр = _k.GetModuleHandleW(None)

        def обработчик(hwnd, сообщение, wparam, lparam):
            if сообщение == WM_SETFOCUS:
                # Курсор ставим в поле ввода: без этого текст приедет в
                # окно и потеряется.
                _u.SetFocus(self.поле)
                return 0
            if сообщение == WM_CLOSE:
                _u.DestroyWindow(hwnd)
                return 0
            if сообщение == WM_DESTROY:
                _u.PostQuitMessage(0)
                return 0
            return _u.DefWindowProcW(hwnd, сообщение, wparam, lparam)

        self._proc = WNDPROC(обработчик)
        # Системе отданы указатели на обработчик и на структуру класса.
        # Она обращается к ним и после закрытия окна, поэтому держим их
        # до конца процесса, иначе получаем падение при уборке мусора.
        _живые.append(self._proc)

        класс = WNDCLASS()
        класс.lpfnWndProc = self._proc
        класс.hInstance = экземпляр
        класс.lpszClassName = self.КЛАСС
        класс.hbrBackground = ctypes.windll.gdi32.CreateSolidBrush(0xFFFFFF)
        класс.hCursor = _u.LoadCursorW(None, 32512)  # IDC_ARROW
        _живые.append(класс)
        # Класс регистрируется один раз на процесс. Повторный вызов
        # вернёт ошибку «уже есть», и это не беда: окон мы делаем много,
        # а класс у них общий.
        _u.RegisterClassW(ctypes.byref(класс))

        self.hwnd = _u.CreateWindowExW(
            WS_EX_TOPMOST, self.КЛАСС, self.заголовок, WS_OVERLAPPEDWINDOW,
            80, 80, 640, 260, None, None, экземпляр, None,
        )
        if not self.hwnd:
            raise НетОкна(f"окно не создалось: {ctypes.get_last_error()}")

        self.поле = _u.CreateWindowExW(
            0, "EDIT", "", WS_CHILD | WS_VISIBLE | ES_MULTILINE | ES_AUTOVSCROLL,
            0, 0, 640, 230, self.hwnd, None, экземпляр, None,
        )
        if not self.поле:
            raise НетОкна(f"поле ввода не создалось: {ctypes.get_last_error()}")

        _u.ShowWindow(self.hwnd, SW_SHOW)
        _u.SetFocus(self.поле)
