"""Точка входа Konspekt.

Порядок важен: pywebview обязан владеть главным потоком, поэтому трей и
слушатель горячих клавиш поднимаются раньше и работают в фоне,
а `webview.start()` блокирует поток до закрытия приложения.
"""

from __future__ import annotations

import io
import logging
import os
import sys

import webview

from . import __version__
from .core import paths
from .core.events import APP_QUIT, WINDOW_SHOW, bus
from .core.service import AppService
from .core.single import SingleInstance, wake_running_instance, watch_show_requests
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
        # Обычно INFO: подробности только мешают читать журнал по жалобе.
        # KONSPEKT_LOG=debug включает разбор решений — например, почему
        # программа промолчала о чужой речи в системном звуке. Без этого
        # молчание неотличимо от поломки, и разбираться приходится
        # вслепую.
        level=logging.DEBUG if os.environ.get("KONSPEKT_LOG", "").lower() == "debug"
        else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )

    # Чужие библиотеки в подробном режиме заливают журнал своим: PIL
    # перечисляет полсотни форматов картинок, httpx пишет каждый запрос.
    # Из-за этого разбор собственной беды приходится выискивать глазами,
    # а ради него всё и включалось.
    for чужой in ("PIL", "httpx", "httpcore", "urllib3", "comtypes",
                  "matplotlib", "asyncio"):
        logging.getLogger(чужой).setLevel(logging.INFO)


def main() -> int:
    _setup_logging()
    # Версия в первой строке журнала: по жалобе сразу видно, на какой
    # сборке сидит человек, и заодно видно, что обновление доехало.
    log.info("Запуск Konspekt %s, данные в %s", __version__, paths.data_dir())

    # Вторая копия делила бы с первой базу, каталог записей и загрузку
    # весов. У пользователя именно это и сломало распознавание: два
    # процесса писали модель в один файл и перемешали её.
    instance = SingleInstance(paths.data_dir() / "konspekt.lock")
    if not instance.acquire():
        log.info("Уже запущен другой экземпляр, показываем его окно")
        wake_running_instance(paths.data_dir())
        return 0

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
        # Человек, не нашедший окно в трее, запустит программу ещё раз.
        # Для него это и есть «открыть Konspekt», так что открываем.
        watch_show_requests(paths.data_dir(), lambda: bus.emit(WINDOW_SHOW, {}))

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
        instance.release()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
