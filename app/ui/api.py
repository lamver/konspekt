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
from . import win32

log = logging.getLogger(__name__)

# Тот же минимум, что задан окну при создании.
MIN_WIDTH = 360
MIN_HEIGHT = 420


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

    # --- аудиоустройства ---------------------------------------------------

    def list_audio_devices(self) -> dict[str, Any]:
        return self._service.list_audio_devices()

    def save_audio_settings(self, fields: dict[str, Any]) -> dict[str, Any]:
        return self._service.save_audio_settings(**fields)

    # --- распознавание ---------------------------------------------------

    def model_status(self) -> dict[str, Any]:
        return self._service.model_status()

    def download_model(self) -> dict[str, Any]:
        return self._service.download_model()

    def cancel_model_download(self) -> dict[str, Any]:
        return self._service.cancel_model_download()

    def set_asr_enabled(self, enabled: bool) -> dict[str, Any]:
        return self._service.set_asr_enabled(enabled)

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
            if not (win32.AVAILABLE and win32.minimize(self._window)):
                self._window.minimize()
        return True

    def move_window(self, dx: int, dy: int) -> bool:
        """Сдвиг окна при перетаскивании за заголовок.

        Тащим вручную вместо easy_drag: тот перехватывает мышь на всём
        документе и ломает выделение текста в заметках.
        """
        if self._window:
            try:
                rect = win32.get_rect(self._window) if win32.AVAILABLE else None
                if rect:
                    x, y, w, h = rect
                    win32.set_geometry(self._window, x + int(dx), y + int(dy), w, h)
                else:
                    self._window.move(self._window.x + int(dx), self._window.y + int(dy))
            except Exception:
                log.debug("Не удалось переместить окно", exc_info=True)
        return True

    def quit_app(self) -> bool:
        bus.emit(APP_QUIT)
        return True

    def resize_window(self, dx: int, dy: int, edge: str = "se") -> dict[str, Any]:
        """Растянуть окно за край.

        frameless-окно лишено системных рамок, поэтому тянем сами: фронт
        шлёт смещение мыши, а мы превращаем его в новый размер. При тяге
        за левый или верхний край окно ещё и двигается, иначе
        противоположная сторона уезжала бы вместе с курсором.
        """
        if not self._window:
            return {}
        try:
            rect = win32.get_rect(self._window) if win32.AVAILABLE else None
            if rect:
                x, y, width, height = rect
            else:
                width, height = self._window.width, self._window.height
                x, y = self._window.x, self._window.y
            dx, dy = int(dx), int(dy)

            if "e" in edge:
                width += dx
            if "w" in edge:
                width -= dx
                x += dx
            if "s" in edge:
                height += dy
            if "n" in edge:
                height -= dy
                y += dy

            # Тот же минимум, что задан окну при создании: без него окно
            # схлопывается в полоску, из которой его не вернуть.
            width = max(MIN_WIDTH, width)
            height = max(MIN_HEIGHT, height)

            # Один вызов вместо resize+move: иначе окно дёргается, а на
            # WinForms эти методы ещё и ждут UI-поток и вешают мост.
            if not (win32.AVAILABLE and win32.set_geometry(self._window, x, y, width, height)):
                self._window.resize(width, height)
                if "w" in edge or "n" in edge:
                    self._window.move(x, y)
            self._service.save_window_geometry(x, y, width, height)
            return {"width": width, "height": height}
        except Exception:
            log.debug("Не удалось изменить размер окна", exc_info=True)
            return {}

    def toggle_pin(self, pinned: bool) -> bool:
        """Закрепить окно поверх остальных."""
        if self._window:
            # Через pywebview этот вызов уходил в UI-поток и намертво вешал
            # окно, потому что тот в это время ждал ответа от моста.
            if not (win32.AVAILABLE and win32.set_on_top(self._window, bool(pinned))):
                self._window.on_top = bool(pinned)
        self._service.settings.always_on_top = bool(pinned)
        return bool(pinned)

    def save_geometry(self, x: int, y: int, width: int, height: int) -> bool:
        self._service.save_window_geometry(x, y, width, height)
        return True

    def get_settings(self) -> dict[str, Any]:
        return self._service.get_settings()

    def set_theme(self, theme: str) -> str:
        """Запомнить выбранную тему. Применяет её сам фронт."""
        return self._service.set_theme(theme)
