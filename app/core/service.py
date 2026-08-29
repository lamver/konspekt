"""Сервис приложения: вся логика встреч в одном месте.

UI и трей ходят только сюда и ничего не знают ни про SQLite, ни про
аудиодвижок. Это позволит на этапе 2 подменить NullCapture на настоящий
захват, не тронув ни строчки во фронте.
"""

from __future__ import annotations

import logging
from typing import Any

from ..asr import (
    MODEL_FILES,
    MODEL_REPO,
    MODEL_TOTAL_BYTES,
    GigaamTranscriber,
    ModelDownloader,
    NullTranscriber,
    Transcriber,
    TranscriptionQueue,
)
from ..audio import AudioCapture, NullCapture, WasapiCapture
from ..audio import devices as audio_devices
from ..core import paths
from ..core import settings as settings_mod
from ..core.events import (
    MEETINGS_CHANGED,
    MEETING_UPDATED,
    MODEL_DOWNLOAD,
    RECORDING_ERROR,
    RECORDING_STARTED,
    RECORDING_STOPPED,
    TRANSCRIPT_SEGMENT,
    bus,
)
from ..core.models import Meeting, MeetingStatus, NoteLine, TranscriptSegment, now
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
        self.settings = settings_mod.load()
        self.transcriber = transcriber or self._build_transcriber()
        # Очередь создаётся всегда: она дешёвая, а поток поднимается
        # только когда реально приходит первый чанк.
        self.asr_queue = TranscriptionQueue(self.transcriber, self._on_segment)
        self.capture = capture or self._build_capture()
        self.downloader = ModelDownloader(
            MODEL_REPO, MODEL_FILES, paths.models_dir() / "gigaam-v3-ctc"
        )
        self.active_meeting_id: str | None = None
        self._recover_stale_recordings()

    def _build_transcriber(self) -> Transcriber:
        """Движок распознавания по настройкам.

        Модель здесь не грузится: только объект. Веса поднимутся сами
        при первом чанке, чтобы не тормозить старт приложения.
        """
        if self.settings.asr.backend == "gigaam":
            return GigaamTranscriber(paths.models_dir() / "gigaam-v3-ctc")
        return NullTranscriber()

    def _build_capture(self) -> AudioCapture:
        """Настоящий захват, а при его недоступности — заглушка.

        Без звуковой подсистемы приложение всё равно остаётся рабочим
        блокнотом, просто без записи.
        """
        audio = self.settings.audio
        try:
            return WasapiCapture(
                mic_device_id=audio.mic_device_id or None,
                loopback_device_id=audio.loopback_device_id or None,
                capture_mic=audio.capture_mic,
                capture_system=audio.capture_system,
                chunk_seconds=audio.chunk_seconds,
                on_chunk=self._on_chunk,
            )
        except Exception:
            log.exception("Захват звука недоступен, работаем без записи")
            return NullCapture()

    # --- распознавание ---------------------------------------------------

    def _on_chunk(self, track: str, pcm, offset: float) -> None:
        """Готовый кусок звука из потока захвата. Возвращаемся мгновенно."""
        if not self.settings.asr.enabled:
            return
        meeting_id = self.active_meeting_id
        if meeting_id is None:
            return
        self.asr_queue.submit(meeting_id, track, pcm, offset)

    def _on_segment(self, segment: TranscriptSegment) -> None:
        """Распознанный кусок: в базу и сразу во фронт."""
        try:
            self.store.add_segment(segment)
        except Exception:
            log.exception("Не удалось сохранить сегмент транскрипта")
        bus.emit(TRANSCRIPT_SEGMENT, {"segment": segment.to_dict()})

    # --- модель ----------------------------------------------------------

    def model_status(self) -> dict[str, Any]:
        """Состояние весов для UI: скачаны ли, сколько уже лежит."""
        ready = getattr(self.transcriber, "is_downloaded", lambda: True)()
        return {
            "backend": self.settings.asr.backend,
            "enabled": self.settings.asr.enabled,
            "name": getattr(self.transcriber, "name", "null"),
            "downloaded": bool(ready),
            "loaded": bool(getattr(self.transcriber, "is_loaded", False)),
            "downloading": self.downloader.is_running,
            "bytes": self.downloader.downloaded_bytes(),
            "total_bytes": MODEL_TOTAL_BYTES,
        }

    def download_model(self) -> dict[str, Any]:
        """Скачать веса в фоне, отчитываясь в UI."""
        if self.downloader.is_running:
            return self.model_status()

        def progress(name: str, done: int, total: int) -> None:
            bus.emit(
                MODEL_DOWNLOAD,
                {"file": name, "bytes": done, "total": total, "state": "downloading"},
            )

        def finished(error: str | None) -> None:
            bus.emit(
                MODEL_DOWNLOAD,
                {"state": "error" if error else "ready", "message": error or ""},
            )

        self.downloader.start(progress, finished)
        return self.model_status()

    def cancel_model_download(self) -> dict[str, Any]:
        self.downloader.cancel()
        return self.model_status()

    def set_asr_enabled(self, enabled: bool) -> dict[str, Any]:
        self.settings.asr.enabled = bool(enabled)
        settings_mod.save(self.settings)
        return self.model_status()

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
        try:
            self.capture.start(meeting_id)
        except Exception as exc:
            # Устройства не открылись: откатываем статус, иначе встреча
            # навсегда зависнет в состоянии «идёт запись».
            self.active_meeting_id = None
            self.store.update_meeting(
                meeting_id, started_at=None, status=MeetingStatus.DRAFT
            )
            bus.emit(MEETINGS_CHANGED)
            bus.emit(RECORDING_ERROR, {"meeting_id": meeting_id, "message": str(exc)})
            log.error("Запись не начата: %s", exc)
            return None

        bus.emit(RECORDING_STARTED, {"meeting_id": meeting_id, "started_at": started})
        bus.emit(MEETINGS_CHANGED)
        return self.get_meeting(meeting_id)

    def stop_recording(self) -> dict[str, Any] | None:
        meeting_id = self.active_meeting_id
        if meeting_id is None:
            return None

        audio_path = self.capture.stop()
        # Даём распознаванию доделать хвост очереди: последние фразы
        # встречи важнее пары секунд ожидания.
        self.asr_queue.stop()
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

    # --- аудиоустройства ------------------------------------------------

    def list_audio_devices(self) -> dict[str, Any]:
        """Список устройств для экрана настроек."""
        data = audio_devices.describe()
        data["selected"] = {
            "mic_device_id": self.settings.audio.mic_device_id,
            "loopback_device_id": self.settings.audio.loopback_device_id,
            "capture_mic": self.settings.audio.capture_mic,
            "capture_system": self.settings.audio.capture_system,
        }
        return data

    def save_audio_settings(self, **fields: Any) -> dict[str, Any]:
        """Сохранить выбор устройств и применить его к следующей записи."""
        audio = self.settings.audio
        for key, value in fields.items():
            if hasattr(audio, key):
                setattr(audio, key, value)
        settings_mod.save(self.settings)

        # Менять устройства посреди записи нельзя: применим после стопа.
        if not self.capture.is_recording:
            self.capture = self._build_capture()
        return self.list_audio_devices()

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

    def set_theme(self, theme: str) -> str:
        """Сохранить тему оформления."""
        if theme not in ("system", "light", "dark"):
            theme = "system"
        self.settings.theme = theme
        settings_mod.save(self.settings)
        return theme

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
