"""Точка входа Konspekt.

Порядок важен: pywebview обязан владеть главным потоком, поэтому трей и
слушатель горячих клавиш поднимаются раньше и работают в фоне,
а `webview.start()` блокирует поток до закрытия приложения.
"""

from __future__ import annotations

import io
import logging
import sys

import webview

from . import __version__
from .core import paths
from .core.events import APP_QUIT, WINDOW_SHOW, bus
from .core.service import AppService
from .tray import TrayIcon
from .ui.hotkeys import HotkeyManager
from .ui.window import MainWindow

log = logging.getLogger(__name__)


def _console_stream():
    """Поток для консольного лога, безопасный для кириллицы.

    В обычном cmd.exe кодировка часто cp1252/cp866, и русская строка роняет
    logging с UnicodeEncodeError. Переключаем stdout на UTF-8, а если это
    невозможно (stdout подменён или его нет, как в сборке под --windowed),
    молча отказываемся от консольного вывода: файловый лог всё равно ведётся.
    """
    stream = sys.stdout
    if stream is None:
        return None
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
            return stream
        except (ValueError, OSError):
            pass
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        return io.TextIOWrapper(buffer, encoding="utf-8", errors="replace")
    return stream


def _setup_logging() -> None:
    handlers: list[logging.Handler] = [
        logging.FileHandler(paths.data_dir() / "konspekt.log", encoding="utf-8"),
    ]
    console = _console_stream()
    if console is not None:
        handlers.insert(0, logging.StreamHandler(console))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def main() -> int:
    _setup_logging()
    # Версия в первой строке журнала: по жалобе сразу видно, на какой
    # сборке сидит человек, и заодно видно, что обновление доехало.
    log.info("Запуск Konspekt %s, данные в %s", __version__, paths.data_dir())

    service = AppService()
    main_window = MainWindow(service)
    window = main_window.create()

    tray = TrayIcon(on_new_meeting=service.create_meeting)
    hotkeys = HotkeyManager(service.settings.hotkey)

    def on_quit(_payload: dict) -> None:
        tray.stop()
        hotkeys.stop()

    bus.on(APP_QUIT, on_quit)

    def on_started() -> None:
        # Трей и хоткеи поднимаем после старта GUI: до этого момента
        # окна ещё нет, и событие WINDOW_SHOW было бы некому обработать.
        tray.start()
        hotkeys.start()

    try:
        webview.start(on_started, gui=None, debug=False)
    finally:
        log.info("Завершение работы")
        tray.stop()
        hotkeys.stop()
        service.shutdown()
        # Самый спокойный момент для обновления: человек сам закрыл окно,
        # запись не идёт, терять нечего. Установщик тихий, поэтому со
        # стороны это выглядит так, будто ничего не произошло, а при
        # следующем запуске уже новая версия.
        try:
            service.updater.install_on_quit()
        except Exception:
            log.exception("Обновление при выходе не удалось")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
