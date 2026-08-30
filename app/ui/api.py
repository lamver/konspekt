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

    # --- импорт готовых записей -------------------------------------------

    def import_files(self, paths: list[str]) -> list[dict[str, Any]]:
        """Разобрать готовые записи: на каждый файл своя встреча."""
        return self._service.import_files(list(paths or []))

    def pick_and_import(self) -> list[dict[str, Any]]:
        """Выбрать файлы через системный диалог и поставить в очередь.

        Фильтр по расширениям тут только подсказка для глаз: сам разбор
        смотрит внутрь файла, поэтому запись с потерянным расширением
        тоже пройдёт, её достаточно выбрать через «Все файлы».
        """
        if not self._window:
            return []
        try:
            import webview  # noqa: PLC0415

            picked = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=True,
                file_types=(
                    "Аудио и видео (*.mp3;*.wav;*.m4a;*.ogg;*.opus;*.flac;*.aac;"
                    "*.wma;*.mp4;*.mkv;*.mov;*.webm;*.avi)",
                    "Все файлы (*.*)",
                ),
            )
        except Exception:
            log.exception("Не удалось открыть диалог выбора файлов")
            return []
        if not picked:
            return []
        return self._service.import_files([str(p) for p in picked])

    def import_status(self) -> dict[str, Any]:
        return self._service.import_status()

    def cancel_import(self, task_id: str) -> dict[str, Any]:
        return self._service.cancel_import(task_id)

    def clear_imports(self) -> dict[str, Any]:
        return self._service.clear_imports()

    # --- заметки ---------------------------------------------------------

    def save_notes(self, meeting_id: str, notes: str) -> bool:
        self._service.save_notes(meeting_id, notes)
        return True

    def add_note_line(self, meeting_id: str, text: str) -> dict[str, Any]:
        return self._service.add_note_line(meeting_id, text)

    # --- саммари и чат ----------------------------------------------------

    def llm_status(self) -> dict[str, Any]:
        return self._service.llm_status()

    def save_llm_settings(self, fields: dict[str, Any]) -> dict[str, Any]:
        return self._service.save_llm_settings(**(fields or {}))

    def check_llm(self) -> dict[str, Any]:
        return self._service.check_llm()

    def generate_summary(self, meeting_id: str) -> dict[str, Any]:
        return self._service.generate_summary(meeting_id)

    def stop_generation(self) -> dict[str, Any]:
        return self._service.stop_generation()

    def list_chat_messages(self, meeting_id: str) -> list[dict[str, Any]]:
        return self._service.list_chat_messages(meeting_id)

    def ask(self, meeting_id: str, question: str) -> dict[str, Any]:
        return self._service.ask(meeting_id, question)

    def clear_chat(self, meeting_id: str) -> dict[str, Any]:
        return self._service.clear_chat(meeting_id)

    # --- голоса -----------------------------------------------------------

    def meeting_voices(self, meeting_id: str) -> list[dict[str, Any]]:
        return self._service.meeting_voices(meeting_id)

    def name_voice(self, meeting_id: str, voice_id: str, name: str) -> dict[str, Any]:
        """Назвать говорящего. Имя закрепляется за голосом, а не за встречей."""
        return self._service.name_voice(meeting_id, voice_id, name)

    def list_people(self) -> list[dict[str, Any]]:
        return self._service.list_people()

    def forget_person(self, person_id: str) -> bool:
        self._service.forget_person(person_id)
        return True

    # --- эталон голоса ----------------------------------------------------

    def enrollment_status(self) -> dict[str, Any]:
        return self._service.enrollment_status()

    def start_enrollment(self) -> dict[str, Any]:
        return self._service.start_enrollment()

    def finish_enrollment(self, name: str = "") -> dict[str, Any]:
        return self._service.finish_enrollment(name)

    def cancel_enrollment(self) -> dict[str, Any]:
        return self._service.cancel_enrollment()

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
