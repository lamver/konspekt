"""Сервис приложения: вся логика встреч в одном месте.

UI и трей ходят только сюда и ничего не знают ни про SQLite, ни про
аудиодвижок. Это позволит на этапе 2 подменить NullCapture на настоящий
захват, не тронув ни строчки во фронте.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from ..asr import (
    MODEL_DIR_NAME,
    MODEL_FILES,
    MODEL_REPO,
    MODEL_TOTAL_BYTES,
    GigaamTranscriber,
    ModelDownloader,
    NullTranscriber,
    Transcriber,
    TranscriptionQueue,
)
from ..asr.embedder import MODEL_DIR_NAME as EMBEDDER_DIR_NAME
from ..asr.embedder import MODEL_FILES as EMBEDDER_FILES
from ..asr.embedder import MODEL_REPO as EMBEDDER_REPO
from ..asr.embedder import VoiceEmbedder
from ..asr.langid import MODEL_DIR_NAME as LANGID_DIR_NAME
from ..asr.langid import LanguageDetector
from ..asr.router import LanguageRouter
from ..asr.whisper import DEFAULT_SIZE as WHISPER_DEFAULT_SIZE
from ..asr.whisper import SIZES as WHISPER_SIZES
from ..asr.whisper import WhisperTranscriber
from ..asr.enroll import (
    MIN_SECONDS,
    PROMPTS,
    SAMPLE_RATE,
    TARGET_SECONDS,
    VoiceEnrollment,
)
from ..asr.voices import VoiceRoster
from ..audio import AudioCapture, NullCapture, WasapiCapture
from ..audio import devices as audio_devices
from ..core import paths
from ..core import settings as settings_mod
from ..core.events import (
    IMPORT_CHANGED,
    IMPORT_PROGRESS,
    MEETINGS_CHANGED,
    MEETING_UPDATED,
    MODEL_DOWNLOAD,
    RECORDING_ERROR,
    RECORDING_STARTED,
    RECORDING_STOPPED,
    TRANSCRIPT_SEGMENT,
    bus,
)
from ..core.importer import ImportQueue
from ..core.models import (
    Meeting,
    MeetingStatus,
    NoteLine,
    Person,
    TranscriptSegment,
    now,
)
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
        # Кто говорит: отпечаток голоса и состав участников встречи.
        self.embedder = VoiceEmbedder(paths.models_dir() / EMBEDDER_DIR_NAME)
        self.roster = VoiceRoster()
        # Очередь создаётся всегда: она дешёвая, а поток поднимается
        # только когда реально приходит первый чанк.
        self.asr_queue = TranscriptionQueue(
            self.transcriber,
            self._on_segment,
            embedder=self.embedder,
            roster=self.roster,
        )
        self.capture = capture or self._build_capture()
        # Разбор готовых записей. Поток поднимается только когда человек
        # действительно бросит файлы в окно.
        self.importer = ImportQueue(
            transcribe_chunk=self._import_chunk,
            create_meeting=self._import_meeting,
            finish_meeting=self._import_finished,
            prepare_meeting=self._prepare_voices,
            on_change=self._on_import_change,
        )
        self.downloader = ModelDownloader(
            MODEL_REPO, MODEL_FILES, paths.models_dir() / MODEL_DIR_NAME
        )
        self.active_meeting_id: str | None = None
        # Запись эталона голоса: живёт только пока человек читает фразы.
        self._enrollment: VoiceEnrollment | None = None
        self._enroll_capture: WasapiCapture | None = None
        # Сдвиг времени для второго и последующих включений записи в одной
        # встрече: без него каждый заход начинался бы с нуля.
        self.time_offset: float = 0.0
        self._recover_stale_recordings()

    def _build_transcriber(self) -> Transcriber:
        """Движок распознавания по настройкам.

        Модель здесь не грузится: только объект. Веса поднимутся сами
        при первом чанке, чтобы не тормозить старт приложения.
        """
        if self.settings.asr.backend != "gigaam":
            return NullTranscriber()

        russian = GigaamTranscriber(paths.models_dir() / MODEL_DIR_NAME)
        if not self.settings.asr.detect_language:
            return russian

        # Определитель языка и Whisper подключаем, только если их веса на
        # месте: без них русская речь распознаётся ровно как раньше, а
        # иностранная останется кириллицей. Это хуже, но работает.
        detector = LanguageDetector(paths.models_dir() / LANGID_DIR_NAME)
        foreign = WhisperTranscriber(paths.models_dir() / self._whisper_dir())
        if not (detector.is_downloaded() and foreign.is_downloaded()):
            log.info("Определение языка выключено: нет весов")
            return russian
        log.info("Нерусская речь идёт в %s", foreign.name)
        return LanguageRouter(russian, detector, foreign)

    def _whisper_dir(self) -> str:
        """Каталог Whisper по настройке, с откатом на тот, что скачан.

        Человек мог попросить small, но не докачать его на слабом
        интернете. Лучше распознавать через base, чем не распознавать
        вовсе и писать чужую речь кириллицей.
        """
        want = getattr(self.settings.asr, "whisper_size", WHISPER_DEFAULT_SIZE)
        if want not in WHISPER_SIZES:
            log.info("Размер Whisper %r незнаком, берём %s", want, WHISPER_DEFAULT_SIZE)
            want = WHISPER_DEFAULT_SIZE
        order = [want, WHISPER_DEFAULT_SIZE] + list(WHISPER_SIZES)
        seen: set[str] = set()
        for size in order:
            if size in seen:
                continue
            seen.add(size)
            name = WHISPER_SIZES[size]["dir"]
            if WhisperTranscriber(paths.models_dir() / name).is_downloaded():
                if size != want:
                    log.info("Whisper %s не скачан, берём %s", want, size)
                return name
        return WHISPER_SIZES[want]["dir"]

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
        self.asr_queue.submit(meeting_id, track, pcm, offset + self.time_offset)

    def _last_segment_end(self, meeting_id: str) -> float:
        """Докуда уже дошёл транскрипт этой встречи, в секундах."""
        try:
            segments = self.store.list_segments(meeting_id)
        except Exception:
            log.exception("Не удалось прочитать транскрипт встречи %s", meeting_id)
            return 0.0
        return max((s.end for s in segments), default=0.0)

    def _on_segment(self, segment: TranscriptSegment) -> None:
        """Распознанный кусок: в базу и сразу во фронт."""
        try:
            self.store.add_segment(segment)
        except Exception:
            log.exception("Не удалось сохранить сегмент транскрипта")
        bus.emit(TRANSCRIPT_SEGMENT, {"segment": segment.to_dict()})

    # --- голоса ----------------------------------------------------------

    def _prepare_voices(self, meeting_id: str) -> None:
        """Начать встречу с чистым составом, но со знакомыми голосами.

        Состав участников свой у каждой встречи: «Собеседник 1» сегодня и
        «Собеседник 1» вчера это разные люди. А вот база знакомых голосов
        общая, поэтому её загружаем целиком: если человек уже назван, его
        имя подставится само.
        """
        try:
            self.roster.reset()
            self.roster.forget_all()
            # Язык прошлой встречи к новой отношения не имеет.
            reset = getattr(self.transcriber, "reset", None)
            if callable(reset):
                reset()
            for person in self.store.list_people():
                if person.embedding:
                    self.roster.remember(person.id, person.name, np.asarray(person.embedding))
        except Exception:
            log.exception("Не удалось поднять базу голосов")

    def _remember_voices(self, meeting_id: str) -> None:
        """Сохранить голоса участников встречи в общую базу.

        Безымянных не сохраняем намеренно. «Собеседник 2» ничего не значит
        на следующей встрече, а база при этом за месяц забилась бы сотней
        безымянных векторов, среди которых узнавание стало бы случайным.
        Как только человека назвали, его голос попадает в базу.
        """
        try:
            # Отпечатки участников этой встречи храним всегда, даже
            # безымянные: иначе назвать говорящего можно было бы только
            # пока открыто окно, а через день встреча стала бы безымянной
            # навсегда.
            for voice in self.roster.voices():
                centroid = voice.centroid
                if centroid is None:
                    continue
                self.store.save_meeting_voice(
                    meeting_id, voice.id, voice.track, voice.label,
                    centroid, voice.samples, voice.person_id,
                )
            for voice in self.roster.voices():
                if voice.person_id is None:
                    continue
                person = self.store.get_person(voice.person_id)
                centroid = voice.centroid
                if person is None or centroid is None:
                    continue
                # Копим эталон между встречами: новый голос усредняется со
                # старым по весу числа фраз, поэтому знакомый человек
                # узнаётся тем увереннее, чем чаще он говорил.
                old = np.asarray(person.embedding, dtype=np.float32)
                if old.size == centroid.size:
                    total = person.samples + voice.samples
                    mixed = (old * person.samples + centroid * voice.samples) / total
                    norm = float(np.linalg.norm(mixed))
                    if norm > 1e-6:
                        person.embedding = (mixed / norm).tolist()
                        person.samples = min(total, 200)
                else:
                    person.embedding = centroid.tolist()
                    person.samples = voice.samples
                self.store.save_person(person)
        except Exception:
            log.exception("Не удалось сохранить голоса участников")

    def list_people(self) -> list[dict[str, Any]]:
        """Знакомые голоса для UI."""
        return [p.to_dict() for p in self.store.list_people()]

    def meeting_voices(self, meeting_id: str) -> list[dict[str, Any]]:
        """Участники встречи: кто говорил и сколько реплик."""
        counts: dict[str, dict[str, Any]] = {}
        for seg in self.store.list_segments(meeting_id):
            if not seg.voice_id:
                continue
            item = counts.setdefault(
                seg.voice_id,
                {
                    "voice_id": seg.voice_id,
                    "label": seg.voice_label,
                    "person_id": seg.person_id,
                    "track": seg.speaker.value,
                    "lines": 0,
                },
            )
            item["lines"] += 1
            # Метка могла поменяться по ходу встречи: показываем последнюю.
            item["label"] = seg.voice_label or item["label"]
        return list(counts.values())

    def name_voice(self, meeting_id: str, voice_id: str, name: str) -> dict[str, Any]:
        """Назвать участника встречи.

        Это же и есть способ запомнить голос: пока человек безымянный, он
        живёт только внутри встречи, а как только получил имя, его голос
        уходит в общую базу и будет узнан на следующих встречах.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("Имя не может быть пустым")

        self.roster.rename(voice_id, name)
        self.store.relabel_segments(meeting_id, voice_id, name)

        # Отпечаток ищем сначала в живом составе встречи, а если её уже
        # закрыли — в базе. Без этого назвать говорящего в старой встрече
        # было невозможно: состав участников жил только в памяти.
        voice = self.roster.get(voice_id)
        saved = self.store.get_meeting_voice(meeting_id, voice_id)
        if voice is not None and voice.centroid is not None:
            centroid = voice.centroid
            samples = voice.samples
            person_id = voice.person_id
        elif saved is not None:
            centroid = saved["embedding"]
            samples = saved["samples"]
            person_id = saved["person_id"]
        else:
            centroid, samples, person_id = None, 1, None

        if centroid is not None:
            if person_id:
                person = self.store.get_person(person_id)
                if person is not None:
                    person.name = name
                    self.store.save_person(person)
            else:
                person = Person(
                    name=name,
                    embedding=np.asarray(centroid, dtype=np.float32).tolist(),
                    samples=max(1, samples),
                )
                self.store.save_person(person)
                person_id = person.id
                if voice is not None:
                    voice.person_id = person_id
            if person_id:
                self.store.link_segments(meeting_id, voice_id, person_id)
                # Голос встречи тоже подписываем: при следующем открытии
                # он уже будет связан с человеком.
                self.store.save_meeting_voice(
                    meeting_id, voice_id,
                    saved["track"] if saved else (voice.track if voice else "them"),
                    name, centroid, samples, person_id,
                )

        bus.emit(MEETING_UPDATED, {"meeting_id": meeting_id})
        return {"voice_id": voice_id, "label": name, "person_id": person_id}

    def forget_person(self, person_id: str) -> None:
        """Забыть голос: человек перестанет узнаваться на новых встречах."""
        self.store.delete_person(person_id)

    # --- эталон владельца -------------------------------------------------

    def enrollment_status(self) -> dict[str, Any]:
        """Состояние записи эталона для UI."""
        owner = self.store.get_owner()
        active = self._enrollment is not None
        return {
            "recording": active,
            "seconds": round(self._enrollment.seconds, 1) if active else 0.0,
            "progress": round(self._enrollment.progress, 3) if active else 0.0,
            "enough": self._enrollment.enough if active else False,
            "target": TARGET_SECONDS,
            "has_owner": owner is not None,
            "owner_name": owner.name if owner else "",
            "prompts": list(PROMPTS),
        }

    def start_enrollment(self) -> dict[str, Any]:
        """Начать запись эталона голоса.

        Пишем только микрофон: эталон владельца снимаем с того устройства,
        куда он говорит, а системный звук здесь только помешал бы.
        """
        if self.active_meeting_id is not None:
            raise RuntimeError("Нельзя записывать голос во время встречи")
        if self._enrollment is not None:
            return self.enrollment_status()

        try:
            self.embedder.load()
        except Exception as exc:
            raise RuntimeError(f"Модель распознавания голоса недоступна: {exc}") from exc

        self._enrollment = VoiceEnrollment(self.embedder, SAMPLE_RATE)
        self._enroll_capture = WasapiCapture(
            mic_device_id=self.settings.audio.mic_device_id or None,
            capture_mic=True,
            capture_system=False,
            chunk_seconds=1.0,
            on_chunk=self._on_enroll_chunk,
        )
        try:
            self._enroll_capture.start("voice-enroll")
        except Exception as exc:
            self._enrollment = None
            self._enroll_capture = None
            raise RuntimeError(str(exc)) from exc
        log.info("Запись эталона голоса начата")
        return self.enrollment_status()

    def _on_enroll_chunk(self, track: str, pcm, offset: float) -> None:
        enrollment = self._enrollment
        if enrollment is None:
            return
        try:
            enrollment.feed(pcm)
        except Exception:
            log.exception("Ошибка накопления эталона голоса")

    def cancel_enrollment(self) -> dict[str, Any]:
        self._stop_enroll_capture()
        self._enrollment = None
        return self.enrollment_status()

    def finish_enrollment(self, name: str = "") -> dict[str, Any]:
        """Закончить запись и сохранить эталон."""
        enrollment = self._enrollment
        self._stop_enroll_capture()
        self._enrollment = None
        if enrollment is None:
            raise RuntimeError("Запись голоса не начиналась")

        vector = enrollment.result()
        if vector is None:
            raise RuntimeError(
                f"Речи слишком мало: нужно хотя бы {int(MIN_SECONDS)} секунд"
            )

        # Владелец в базе один: перезапись эталона заменяет старый, а не
        # плодит вторую запись того же человека.
        owner = self.store.get_owner()
        name = (name or "").strip() or (owner.name if owner else "Я")
        person = owner or Person(kind="owner")
        person.name = name
        person.kind = "owner"
        person.embedding = vector.tolist()
        person.samples = max(1, enrollment.samples())
        self.store.save_person(person)
        log.info("Эталон голоса сохранён: %s", name)
        return self.enrollment_status()

    def _stop_enroll_capture(self) -> None:
        capture = self._enroll_capture
        self._enroll_capture = None
        if capture is None:
            return
        try:
            path = capture.stop()
            # Файл эталона не нужен: нам важен только вектор.
            if path:
                Path(path).unlink(missing_ok=True)
        except Exception:
            log.exception("Не удалось остановить запись эталона")

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
        # Время сегментов отсчитывается от начала куска записи, а не от
        # начала встречи. Если запись останавливали и включали снова,
        # второй заход начался бы опять с нуля и лёг поверх первого:
        # реплики перемешивались и склеивались в кашу. Поэтому запоминаем,
        # сколько уже записано, и сдвигаем на эту величину.
        self.time_offset = self._last_segment_end(meeting_id)
        self._prepare_voices(meeting_id)
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
        # Последняя фраза ещё сидит внутри VAD: он ждал паузу, а её уже
        # не будет. Выталкиваем до остановки очереди, иначе потеряется.
        self.asr_queue.flush(meeting_id)
        # Даём распознаванию доделать хвост очереди: последние фразы
        # встречи важнее пары секунд ожидания.
        self.asr_queue.stop()
        # Голоса запоминаем только теперь: за время встречи эталон каждого
        # участника собрался из всех его фраз, а не из первой попавшейся.
        self._remember_voices(meeting_id)
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

    # --- импорт файлов ----------------------------------------------------

    def import_files(self, paths: list[str]) -> list[dict[str, Any]]:
        """Поставить готовые записи в очередь разбора.

        На каждый файл заводится своя встреча, названная по имени файла:
        пачка записей превращается в пачку встреч, а не в одну кашу.
        """
        if not paths:
            return []
        tasks = self.importer.add(paths)
        bus.emit(IMPORT_CHANGED, {"tasks": self.importer.tasks()})
        return tasks

    def import_status(self) -> dict[str, Any]:
        return {"tasks": self.importer.tasks(), "busy": self.importer.busy}

    def cancel_import(self, task_id: str) -> dict[str, Any]:
        self.importer.cancel(task_id)
        return self.import_status()

    def clear_imports(self) -> dict[str, Any]:
        self.importer.clear_finished()
        return self.import_status()

    def _import_meeting(self, title: str) -> str:
        """Встреча под импортируемый файл.

        Помечаем её как идущую обработку: в списке сразу видно, что
        транскрипт ещё дописывается.
        """
        meeting = Meeting(title=title or _default_title(), status=MeetingStatus.PROCESSING)
        meeting.started_at = now()
        self.store.create_meeting(meeting)
        bus.emit(MEETINGS_CHANGED)
        return meeting.id

    def _import_chunk(self, meeting_id: str, track: str, pcm, offset: float) -> None:
        """Кусок звука из файла в то же распознавание, что и живая речь.

        Никакого отдельного пути: файл проходит через тот же VAD, ту же
        очередь и то же определение говорящих, поэтому импортированная
        встреча выглядит ровно как записанная.
        """
        if not self.settings.asr.enabled:
            return
        # block=True: чтение файла идёт в десятки раз быстрее распознавания,
        # и без ожидания очередь переполняется, а речь молча теряется. На
        # живой 25-минутной записи так пропал 421 фрагмент речи.
        self.asr_queue.submit(meeting_id, track, pcm, offset, block=True)

    def _import_finished(self, meeting_id: str, duration: float) -> None:
        """Файл дочитан: дождаться распознавания и закрыть встречу."""
        try:
            # Последняя фраза сидит внутри VAD и ждёт паузу, которой уже
            # не будет: выталкиваем, иначе потеряем конец записи.
            self.asr_queue.flush(meeting_id, block=True)
            self.asr_queue.wait_idle()
            self._remember_voices(meeting_id)
        except Exception:
            log.exception("Не удалось завершить импорт встречи %s", meeting_id)
        started = now() - max(0.0, duration)
        self.store.update_meeting(
            meeting_id,
            started_at=started,
            ended_at=now(),
            status=MeetingStatus.READY,
        )
        bus.emit(MEETINGS_CHANGED)
        bus.emit(MEETING_UPDATED, {"meeting_id": meeting_id})

    def _on_import_change(self, task: Any) -> None:
        bus.emit(IMPORT_PROGRESS, {"task": task.to_dict()})

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
        # Разбор файлов может идти долго: при выходе бросаем его, а
        # недоделанные встречи остаются с тем, что успели распознать.
        self.importer.stop()
        settings_mod.save(self.settings)
        self.store.close()


def _default_title() -> str:
    import datetime

    return "Встреча " + datetime.datetime.now().strftime("%d.%m %H:%M")
