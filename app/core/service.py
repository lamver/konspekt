"""Сервис приложения: вся логика встреч в одном месте.

UI и трей ходят только сюда и ничего не знают ни про SQLite, ни про
аудиодвижок. Это позволит на этапе 2 подменить NullCapture на настоящий
захват, не тронув ни строчки во фронте.
"""

from __future__ import annotations

import logging
from typing import Any

from ..asr import NullTranscriber, Transcriber
from ..audio import AudioCapture, NullCapture
from ..core import settings as settings_mod
from ..core.events import (
    MEETINGS_CHANGED,
    MEETING_UPDATED,
    RECORDING_STARTED,
    RECORDING_STOPPED,
    bus,
)
from ..core.models import Meeting, MeetingStatus, NoteLine, now
from ..storage import Store

log = logging.getLogger(__name__)


class AppService:
    def __init__(
        self,
        store: Store | None = None,
        capture: AudioCapture | None = None,
        transcriber: Transcriber | None = None,
    ) -> None:
        self.store = store or Store()
        self.capture = capture or NullCapture()
        self.transcriber = transcriber or NullTranscriber()
        self.settings = settings_mod.load()
        self.active_meeting_id: str | None = None
        self._recover_stale_recordings()

    def _recover_stale_recordings(self) -> None:
        """Чиним встречи, зависшие в статусе «идёт запись».

        Приложение могло упасть или быть убито во время записи. Тогда в базе
        остаётся встреча со статусом recording, которой уже никто не пишет,
        и в списке навсегда мигает красная точка.
        """
        for meeting in self.store.list_meetings():
            if meeting.status is not MeetingStatus.RECORDING:
                continue
            ended = meeting.ended_at or meeting.started_at or meeting.created_at
            self.store.update_meeting(
                meeting.id, status=MeetingStatus.READY, ended_at=ended
            )
            log.info("Восстановлена прерванная запись: %s", meeting.id)

    # --- встречи ---------------------------------------------------------

    def list_meetings(self) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.store.list_meetings()]

    def create_meeting(self, title: str | None = None) -> dict[str, Any]:
        meeting = Meeting(title=title or _default_title())
        self.store.create_meeting(meeting)
        bus.emit(MEETINGS_CHANGED)
        return meeting.to_dict()

    def get_meeting(self, meeting_id: str) -> dict[str, Any] | None:
        meeting = self.store.get_meeting(meeting_id)
        if meeting is None:
            return None
        data = meeting.to_dict()
        data["note_lines"] = [n.to_dict() for n in self.store.list_note_lines(meeting_id)]
        data["segments"] = [s.to_dict() for s in self.store.list_segments(meeting_id)]
        return data

    def update_meeting(self, meeting_id: str, **fields: Any) -> dict[str, Any] | None:
        self.store.update_meeting(meeting_id, **fields)
        meeting = self.store.get_meeting(meeting_id)
        if meeting is None:
            return None
        bus.emit(MEETING_UPDATED, {"meeting": meeting.to_dict()})
        # Заголовок виден в списке, поэтому список тоже надо освежить.
        if "title" in fields:
            bus.emit(MEETINGS_CHANGED)
        return meeting.to_dict()

    def delete_meeting(self, meeting_id: str) -> None:
        if self.active_meeting_id == meeting_id:
            self.stop_recording()
        self.store.delete_meeting(meeting_id)
        bus.emit(MEETINGS_CHANGED)

    # --- запись ----------------------------------------------------------

    def start_recording(self, meeting_id: str | None = None) -> dict[str, Any] | None:
        if self.capture.is_recording:
            log.warning("Запись уже идёт, повторный старт проигнорирован")
            return self.get_meeting(self.active_meeting_id) if self.active_meeting_id else None

        if meeting_id is None:
            meeting_id = self.create_meeting()["id"]

        meeting = self.store.get_meeting(meeting_id)
        if meeting is None:
            return None

        started = now()
        self.store.update_meeting(
            meeting_id, started_at=started, status=MeetingStatus.RECORDING
        )
        self.active_meeting_id = meeting_id
        self.capture.start(meeting_id)

        bus.emit(RECORDING_STARTED, {"meeting_id": meeting_id, "started_at": started})
        bus.emit(MEETINGS_CHANGED)
        return self.get_meeting(meeting_id)

    def stop_recording(self) -> dict[str, Any] | None:
        meeting_id = self.active_meeting_id
        if meeting_id is None:
            return None

        audio_path = self.capture.stop()
        self.active_meeting_id = None
        self.store.update_meeting(
            meeting_id,
            ended_at=now(),
            status=MeetingStatus.READY,
            audio_path=audio_path,
        )
        bus.emit(RECORDING_STOPPED, {"meeting_id": meeting_id})
        bus.emit(MEETINGS_CHANGED)
        return self.get_meeting(meeting_id)

    @property
    def is_recording(self) -> bool:
        return self.capture.is_recording

    # --- заметки ---------------------------------------------------------

    def save_notes(self, meeting_id: str, notes: str) -> None:
        """Автосохранение всего текста заметок (вызывается по debounce из UI)."""
        self.store.update_meeting(meeting_id, notes=notes)

    def add_note_line(self, meeting_id: str, text: str) -> dict[str, Any]:
        """Тезис с привязкой к моменту встречи.

        Смещение считаем от старта записи; если запись не идёт, ставим 0 —
        заметка всё равно ценна, просто без якоря в транскрипте.
        """
        meeting = self.store.get_meeting(meeting_id)
        offset = 0.0
        if meeting and meeting.started_at:
            offset = max(0.0, now() - meeting.started_at)
        line = NoteLine(meeting_id=meeting_id, text=text, offset=offset)
        self.store.add_note_line(line)
        return line.to_dict()

    # --- настройки -------------------------------------------------------

    def get_settings(self) -> dict[str, Any]:
        return self.settings.to_dict()

    def save_window_geometry(self, x: int, y: int, width: int, height: int) -> None:
        self.settings.window.x = int(x)
        self.settings.window.y = int(y)
        self.settings.window.width = int(width)
        self.settings.window.height = int(height)
        settings_mod.save(self.settings)

    def shutdown(self) -> None:
        if self.capture.is_recording:
            self.stop_recording()
        settings_mod.save(self.settings)
        self.store.close()


def _default_title() -> str:
    import datetime

    return "Встреча " + datetime.datetime.now().strftime("%d.%m %H:%M")
