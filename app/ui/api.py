"""JS-мост: всё, что видит фронт через `window.pywebview.api`.

Намеренно тонкий слой: только валидация и делегирование в AppService,
чтобы логика не разъехалась между бэкендом и браузером.

Важно: pywebview обходит публичные атрибуты этого объекта, чтобы построить
JS-обёртку. Ссылки на окно и сервис поэтому спрятаны под подчёркивание —
иначе обход уходит в объект окна и падает с бесконечной рекурсией.
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.events import APP_QUIT, WINDOW_HIDE, bus
from ..core.service import AppService

log = logging.getLogger(__name__)


class Api:
    def __init__(self, service: AppService) -> None:
        self._service = service
        self._window = None  # проставляется в MainWindow после создания окна

    def _attach(self, window: Any) -> None:
        self._window = window

    # --- встречи ---------------------------------------------------------

    def list_meetings(self) -> list[dict[str, Any]]:
        return self._service.list_meetings()

    def get_meeting(self, meeting_id: str) -> dict[str, Any] | None:
        return self._service.get_meeting(meeting_id)

    def create_meeting(self, title: str | None = None) -> dict[str, Any]:
        return self._service.create_meeting(title)

    def update_meeting(self, meeting_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        return self._service.update_meeting(meeting_id, **(fields or {}))

    def delete_meeting(self, meeting_id: str) -> bool:
        self._service.delete_meeting(meeting_id)
        return True

    # --- запись ----------------------------------------------------------

    def start_recording(self, meeting_id: str | None = None) -> dict[str, Any] | None:
        return self._service.start_recording(meeting_id)

    def stop_recording(self) -> dict[str, Any] | None:
        return self._service.stop_recording()

    def recording_state(self) -> dict[str, Any]:
        return {
            "is_recording": self._service.is_recording,
            "meeting_id": self._service.active_meeting_id,
        }

    # --- заметки ---------------------------------------------------------

    def save_notes(self, meeting_id: str, notes: str) -> bool:
        self._service.save_notes(meeting_id, notes)
        return True

    def add_note_line(self, meeting_id: str, text: str) -> dict[str, Any]:
        return self._service.add_note_line(meeting_id, text)

    # --- окно -------------------------------------------------------------

    def hide_window(self) -> bool:
        bus.emit(WINDOW_HIDE)
        return True

    def minimize_window(self) -> bool:
        if self._window:
            self._window.minimize()
        return True

    def move_window(self, dx: int, dy: int) -> bool:
        """Сдвиг окна при перетаскивании за заголовок.

        Тащим вручную вместо easy_drag: тот перехватывает мышь на всём
        документе и ломает выделение текста в заметках.
        """
        if self._window:
            try:
                self._window.move(self._window.x + int(dx), self._window.y + int(dy))
            except Exception:
                log.debug("Не удалось переместить окно", exc_info=True)
        return True

    def quit_app(self) -> bool:
        bus.emit(APP_QUIT)
        return True

    def toggle_pin(self, pinned: bool) -> bool:
        """Закрепить окно поверх остальных."""
        if self._window:
            self._window.on_top = bool(pinned)
        self._service.settings.always_on_top = bool(pinned)
        return bool(pinned)

    def save_geometry(self, x: int, y: int, width: int, height: int) -> bool:
        self._service.save_window_geometry(x, y, width, height)
        return True

    def get_settings(self) -> dict[str, Any]:
        return self._service.get_settings()
