"""Иконка в трее на pystray.

pystray требует собственный цикл событий, а pywebview занимает главный поток,
поэтому трей живёт в фоновом потоке и общается с окном только
через шину событий.
"""

from __future__ import annotations

import logging
import threading

import pystray
from PIL import Image, ImageDraw

from ..core.events import (
    APP_QUIT,
    RECORDING_STARTED,
    RECORDING_STOPPED,
    WINDOW_SHOW,
    WINDOW_TOGGLE,
    bus,
)

log = logging.getLogger(__name__)

ACCENT = (214, 90, 63)      # терракота, цвет акцента в UI
INK = (43, 41, 38)          # основной тёмный
REC = (220, 53, 69)         # красный индикатор записи


def _make_icon(recording: bool = False) -> Image.Image:
    """Рисуем иконку кодом, чтобы не тащить бинарные ассеты в репо."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Страница блокнота
    d.rounded_rectangle([10, 6, 54, 58], radius=8, fill=INK)
    d.rounded_rectangle([10, 6, 20, 58], radius=8, fill=ACCENT)
    # Строки текста
    for y in (20, 30, 40):
        d.rounded_rectangle([26, y, 48, y + 4], radius=2, fill=(250, 249, 247))

    if recording:
        d.ellipse([38, 38, 60, 60], fill=REC)

    return img


class TrayIcon:
    def __init__(self, on_new_meeting=None) -> None:
        self._on_new_meeting = on_new_meeting
        self._recording = False
        self._icon = pystray.Icon(
            "konspekt",
            icon=_make_icon(False),
            title="Konspekt",
            menu=self._build_menu(),
        )
        self._thread: threading.Thread | None = None
        bus.on(RECORDING_STARTED, lambda _p: self._set_recording(True))
        bus.on(RECORDING_STOPPED, lambda _p: self._set_recording(False))

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                "Показать Konspekt",
                lambda: bus.emit(WINDOW_SHOW),
                default=True,
            ),
            pystray.MenuItem("Скрыть / показать", lambda: bus.emit(WINDOW_TOGGLE)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Новая встреча", self._new_meeting),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", lambda: bus.emit(APP_QUIT)),
        )

    def _new_meeting(self) -> None:
        if self._on_new_meeting:
            self._on_new_meeting()
        bus.emit(WINDOW_SHOW)

    def _set_recording(self, recording: bool) -> None:
        if self._recording == recording:
            return
        self._recording = recording
        try:
            self._icon.icon = _make_icon(recording)
            self._icon.title = "Konspekt — идёт запись" if recording else "Konspekt"
        except Exception:
            log.debug("Не удалось обновить иконку трея", exc_info=True)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:
            log.debug("Трей уже остановлен", exc_info=True)
