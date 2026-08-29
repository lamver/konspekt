"""Интерфейс захвата звука и его заглушка для этапа 1.

На этапе 2 сюда приедет `WasapiCapture`: два потока (микрофон и
системный loopback) с тем же протоколом, так что UI править не придётся.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Protocol, runtime_checkable

from ..core.events import RECORDING_LEVEL, bus

log = logging.getLogger(__name__)


@runtime_checkable
class AudioCapture(Protocol):
    """Контракт любого источника звука."""

    def start(self, meeting_id: str) -> None: ...
    def stop(self) -> str | None:
        """Остановить запись и вернуть путь к файлу, если он есть."""
        ...

    @property
    def is_recording(self) -> bool: ...


class NullCapture:
    """Заглушка: ничего не пишет, но честно шлёт уровни в UI.

    Благодаря этому весь путь «старт → индикатор → таймер → стоп»
    проверяется целиком ещё до появления настоящего аудио.
    """

    def __init__(self) -> None:
        self._recording = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self, meeting_id: str) -> None:
        if self._recording:
            return
        self._recording = True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._emit_levels, args=(meeting_id,), daemon=True
        )
        self._thread.start()
        log.info("NullCapture: запись начата (%s)", meeting_id)

    def stop(self) -> str | None:
        if not self._recording:
            return None
        self._recording = False
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        log.info("NullCapture: запись остановлена")
        return None

    def _emit_levels(self, meeting_id: str) -> None:
        t = 0.0
        while not self._stop.wait(0.1):
            t += 0.1
            # Две разные синусоиды, чтобы дорожки визуально различались.
            bus.emit(RECORDING_LEVEL, {
                "meeting_id": meeting_id,
                "me": abs(math.sin(t * 2.1)) * 0.7,
                "them": abs(math.sin(t * 1.3 + 1.0)) * 0.5,
            })
