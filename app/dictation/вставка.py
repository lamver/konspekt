"""Вставка распознанного текста в активное поле ввода чужого приложения.

Единственное звено, которого не хватало для диктовки: всё остальное
(микрофон, поиск речи, GigaAM) у нас уже есть.

Способов ровно два, и ни один не годится всегда:

- **Печать по буквам** (`SendInput` с `KEYEVENTF_UNICODE`). Не трогает
  буфер обмена человека, работает даже там, где вставку запрещают.
  Но каждая буква — отдельное системное событие, и на длинной фразе
  это заметно медленно, а некоторые программы (терминалы, часть Qt)
  теряют символы при быстром потоке.
- **Через буфер обмена и Ctrl+V.** Мгновенно и не зависит от длины,
  но затирает то, что человек скопировал. Возвращаем содержимое назад,
  однако между вставкой и возвратом есть окно в доли секунды, и если
  ровно тогда сработает менеджер буфера, он запомнит наш текст.

По умолчанию печатаем по буквам: чужой буфер обмена не наше имущество,
а испорченный буфер человек замечает и злится, тогда как лишние
полсекунды на длинной фразе терпимы. Для длинного текста порог
переключает на буфер: печатать абзац по букве это уже минуты.

Куда вставка не дойдёт никогда: окна, запущенные от администратора.
Windows не пропускает ввод из обычного процесса в окно с более высокими
правами, причём молча — ошибки не будет, текст просто не появится.
Поэтому проверяем права окна заранее и честно говорим человеку.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

ДОСТУПНО = sys.platform == "win32"

# Длиннее этого печатать по буквам слишком долго, идём через буфер.
# 120 знаков это примерно две строки: столько печатается за четверть
# секунды, а всё что больше человек уже воспринимает как задержку.
ПОРОГ_БУФЕРА = 120

# Пауза после каждого символа.
#
# Без неё текст приезжает искажённым, и это не догадка, а замер на
# настоящем Блокноте: «Привет, это диктовка. Ёжик, 42%!» превращалось
# в «Привет,!!!!!!!!!!!!!!!!». Отправка длинным пакетом за один вызов
# SendInput не помогает вовсе, а вот пауза в 12 мс даёт точное
# совпадение. Получатель разбирает очередь ввода не мгновенно, и
# сплошной поток он просто не успевает прочитать.
#
# Цена паузы — 12 мс на символ, то есть примерно секунда на сотню
# знаков. Ровно поэтому длинный текст идёт через буфер обмена.
ЗАДЕРЖКА_СИМВОЛА = 0.012

if ДОСТУПНО:
    _u = ctypes.windll.user32
    _k = ctypes.windll.kernel32

    INPUT_KEYBOARD = 1
    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL = 0x11
    VK_V = 0x56
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _INPUTUNION(ctypes.Union):
        # 32 байта, а не 24 по размеру клавиатурной части. Объединение в
        # INPUT общее для мыши, клавиатуры и «железного» события, и самый
        # большой из них — мышиный (32 байта на x64). Система сверяет
        # переданный размер структуры со своим и при несовпадении молча
        # отвергает ввод целиком: ошибки нет, текст просто не появляется.
        _fields_ = [("ki", _KEYBDINPUT), ("padding", ctypes.c_byte * 32)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    # Сигнатуры обязательны: без них ctypes считает аргументы 32-битными
    # и на x64 портит указатели. Вызов молча не срабатывает.
    _u.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    _u.SendInput.restype = wintypes.UINT
    _u.GetForegroundWindow.argtypes = []
    _u.GetForegroundWindow.restype = wintypes.HWND
    _u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u.GetWindowThreadProcessId.restype = wintypes.DWORD
    _u.OpenClipboard.argtypes = [wintypes.HWND]
    _u.OpenClipboard.restype = wintypes.BOOL
    _u.CloseClipboard.argtypes = []
    _u.CloseClipboard.restype = wintypes.BOOL
    _u.EmptyClipboard.argtypes = []
    _u.EmptyClipboard.restype = wintypes.BOOL
    _u.GetClipboardData.argtypes = [wintypes.UINT]
    _u.GetClipboardData.restype = wintypes.HANDLE
    _u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    _u.SetClipboardData.restype = wintypes.HANDLE
    _u.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    _u.IsClipboardFormatAvailable.restype = wintypes.BOOL
    _k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _k.GlobalAlloc.restype = wintypes.HGLOBAL
    _k.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _k.GlobalLock.restype = wintypes.LPVOID
    _k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _k.GlobalUnlock.restype = wintypes.BOOL
    _k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k.OpenProcess.restype = wintypes.HANDLE
    _k.CloseHandle.argtypes = [wintypes.HANDLE]
    _k.CloseHandle.restype = wintypes.BOOL


class НедоступноОкно(RuntimeError):
    """Окно есть, но ввод в него не дойдёт. Обычно права администратора."""


def окно_недоступно() -> bool:
    """Похоже ли активное окно на запущенное с правами администратора.

    Обычный процесс не может даже открыть такой процесс на чтение, и это
    самый дешёвый признак. Ошибаться в сторону «доступно» безопаснее:
    хуже показать лишнее предупреждение, чем молча ничего не вставить.
    """
    if not ДОСТУПНО:
        return False
    try:
        hwnd = _u.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        дескриптор = _k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if дескриптор:
            _k.CloseHandle(дескриптор)
            return False
        # ERROR_ACCESS_DENIED. Всё остальное (процесс уже закрылся и т.п.)
        # к правам отношения не имеет.
        return ctypes.get_last_error() == 5 or _k.GetLastError() == 5
    except Exception:
        log.debug("Не смогли проверить права активного окна", exc_info=True)
        return False


def _событие(символ: str, отпускание: bool = False) -> _INPUT:
    флаги = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if отпускание else 0)
    событие = _INPUT()
    событие.type = INPUT_KEYBOARD
    событие.u.ki = _KEYBDINPUT(0, ord(символ), флаги, 0, 0)
    return событие


def _клавиша(код: int, отпускание: bool = False) -> _INPUT:
    событие = _INPUT()
    событие.type = INPUT_KEYBOARD
    событие.u.ki = _KEYBDINPUT(код, 0, KEYEVENTF_KEYUP if отпускание else 0, 0, 0)
    return событие


def печатать(текст: str) -> bool:
    """Набрать текст посимвольно. Буфер обмена не трогается.

    Возвращает False, если система отвергла ввод: обычно это окно с
    правами выше наших, и молчать об этом нельзя — человек продиктует
    абзац и не поймёт, куда он делся.
    """
    if not ДОСТУПНО or not текст:
        return False

    # Разворачиваем текст в поток нажатий заранее, чтобы отправлять
    # пакетами, а не по одному событию.
    единицы: list[str] = []
    for символ in текст:
        if ord(символ) > 0xFFFF:
            # Эмодзи не влезает в 16 бит и едет суррогатной парой:
            # два события подряд, иначе получатель увидит мусор.
            кодовая = ord(символ) - 0x10000
            единицы.append(chr(0xD800 + (кодовая >> 10)))
            единицы.append(chr(0xDC00 + (кодовая & 0x3FF)))
        else:
            единицы.append(символ)

    for знак in единицы:
        события = (_INPUT * 2)(_событие(знак), _событие(знак, отпускание=True))
        if _u.SendInput(2, события, ctypes.sizeof(_INPUT)) != 2:
            log.warning("Ввод отвергнут системой на символе %r", знак)
            return False
        if ЗАДЕРЖКА_СИМВОЛА:
            time.sleep(ЗАДЕРЖКА_СИМВОЛА)
    return True


def _прочитать_буфер() -> str | None:
    """Что сейчас в буфере обмена. None — там не текст или буфер занят."""
    if not _u.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return None
    if not _открыть_буфер():
        return None
    try:
        дескриптор = _u.GetClipboardData(CF_UNICODETEXT)
        if not дескриптор:
            return None
        указатель = _k.GlobalLock(дескриптор)
        if not указатель:
            return None
        try:
            return ctypes.wstring_at(указатель)
        finally:
            _k.GlobalUnlock(дескриптор)
    finally:
        _u.CloseClipboard()


def _открыть_буфер(попыток: int = 10) -> bool:
    """Буфер обмена монопольный: его может держать другая программа.

    Одна неудачная попытка ничего не значит, менеджеры буфера открывают
    его на миллисекунды. Поэтому ждём, а не сдаёмся сразу.
    """
    for _ in range(попыток):
        if _u.OpenClipboard(None):
            return True
        time.sleep(0.01)
    return False


def _записать_буфер(текст: str) -> bool:
    if not _открыть_буфер():
        return False
    try:
        _u.EmptyClipboard()
        данные = ctypes.create_unicode_buffer(текст)
        размер = ctypes.sizeof(данные)
        память = _k.GlobalAlloc(GMEM_MOVEABLE, размер)
        if not память:
            return False
        указатель = _k.GlobalLock(память)
        if not указатель:
            return False
        ctypes.memmove(указатель, ctypes.byref(данные), размер)
        _k.GlobalUnlock(память)
        # После SetClipboardData память принадлежит системе, освобождать
        # её самим нельзя: это чужая теперь память.
        return bool(_u.SetClipboardData(CF_UNICODETEXT, память))
    finally:
        _u.CloseClipboard()


def вставить_через_буфер(текст: str) -> bool:
    """Положить текст в буфер, нажать Ctrl+V и вернуть буфер как было.

    Возврат обязателен: человек мог держать в буфере пароль или ссылку,
    и потерять это из-за диктовки он не подписывался.
    """
    if not ДОСТУПНО or not не_пусто(текст):
        return False
    прежнее = _прочитать_буфер()
    if not _записать_буфер(текст):
        return False
    try:
        события = (_INPUT * 4)(
            _клавиша(VK_CONTROL),
            _клавиша(VK_V),
            _клавиша(VK_V, отпускание=True),
            _клавиша(VK_CONTROL, отпускание=True),
        )
        if _u.SendInput(4, события, ctypes.sizeof(_INPUT)) != 4:
            log.warning("Ctrl+V отвергнут системой")
            return False
        # Приложение забирает содержимое буфера не мгновенно: вернём
        # прежнее слишком рано, и вставится именно прежнее.
        time.sleep(0.12)
        return True
    finally:
        if прежнее is not None:
            _записать_буфер(прежнее)


def не_пусто(текст: str) -> bool:
    return bool(текст and текст.strip())


def вставить(текст: str, через_буфер: bool | None = None) -> bool:
    """Вставить текст в активное поле ввода.

    `через_буфер=None` означает «реши сам»: короткое печатаем по буквам,
    длинное отправляем буфером. Явное значение нужно тем, у кого один из
    способов не работает в их программе.
    """
    if not не_пусто(текст):
        return False
    if not ДОСТУПНО:
        log.warning("Вставка текста есть только под Windows")
        return False
    if окно_недоступно():
        raise НедоступноОкно(
            "Активное окно запущено с правами администратора: "
            "Windows не пропустит туда ввод"
        )
    решение = через_буфер
    if решение is None:
        решение = len(текст) > ПОРОГ_БУФЕРА
    if решение:
        return вставить_через_буфер(текст)
    return печатать(текст)
