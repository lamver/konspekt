"""Единственный экземпляр программы.

Зачем. По жалобе пользователя: он запустил Konspekt дважды (иконка на
рабочем столе, потом ещё раз из трея — окно-то спрятано, кажется, что
программа не работает). Два процесса начали качать веса модели в один и
тот же файл, куски перемешались, и распознавание перестало работать
совсем, без единого сообщения.

Общая беда шире весов: две копии делят одну базу и один каталог записей.
Поэтому второй запуск не поднимает второе приложение, а показывает окно
уже работающего и тихо уходит.

Как. Именованный мьютекс Windows живёт ровно столько, сколько живёт
процесс: даже после жёсткого убийства система освобождает его сама, и
замок не может залипнуть навсегда. На остальных системах падаем на файл
с проверкой живости процесса.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

MUTEX_NAME = "Local\\KonspektSingleInstance"
_ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """Замок «я такой один». Держится до конца работы процесса."""

    def __init__(self, lock_path: Path | None = None) -> None:
        self._lock_path = lock_path
        self._handle = None
        self._fd: int | None = None

    def acquire(self) -> bool:
        """True, если мы первый экземпляр."""
        if sys.platform == "win32":
            return self._acquire_mutex()
        return self._acquire_file()

    def _acquire_mutex(self) -> bool:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        self._handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            # Дескриптор всё равно выдан, и пока он открыт, мьютекс жив.
            # Не закрыв его, мы бы не пустили и следующий честный запуск
            # после закрытия первого экземпляра.
            kernel32.CloseHandle(self._handle)
            self._handle = None
            log.info("Konspekt уже запущен, показываем его окно")
            return False
        return True

    def _acquire_file(self) -> bool:
        path = self._lock_path
        if path is None:
            return True
        try:
            self._fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if not self._stale(path):
                return False
            path.unlink(missing_ok=True)
            return self._acquire_file()
        os.write(self._fd, str(os.getpid()).encode())
        return True

    @staticmethod
    def _stale(path: Path) -> bool:
        try:
            pid = int(path.read_text().strip() or 0)
        except (OSError, ValueError):
            return True
        if pid <= 0:
            return True
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        return False

    def release(self) -> None:
        if self._handle is not None:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
            if self._lock_path:
                self._lock_path.unlink(missing_ok=True)


# Файл-сигнал «покажи окно». Проще и надёжнее оконных сообщений: не
# зависит от того, есть ли у pywebview доступный хук, и одинаково
# работает на всех системах.
SHOW_REQUEST = "show.request"


def wake_running_instance(data_dir: Path) -> None:
    """Попросить уже запущенный экземпляр показать окно.

    Человек запускает программу второй раз именно потому, что не видит
    первую: она в трее. Промолчать было бы издевательством — надо
    показать ему окно, за которым он и пришёл.
    """
    try:
        (data_dir / SHOW_REQUEST).write_text("1", encoding="utf-8")
    except OSError:
        log.warning("Не удалось разбудить работающий экземпляр")


def watch_show_requests(data_dir: Path, show: "Callable[[], None]") -> None:
    """Следить за просьбами показать окно от новых запусков."""
    import threading
    import time

    marker = data_dir / SHOW_REQUEST

    def run() -> None:
        while True:
            time.sleep(1.0)
            try:
                if not marker.exists():
                    continue
                marker.unlink(missing_ok=True)
            except OSError:
                continue
            try:
                show()
            except Exception:
                log.exception("Не удалось показать окно по просьбе второго запуска")

    threading.Thread(target=run, name="show-watch", daemon=True).start()
