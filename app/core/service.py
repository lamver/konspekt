"""Сервис приложения: вся логика встреч в одном месте.

UI и трей ходят только сюда и ничего не знают ни про SQLite, ни про
аудиодвижок. Это позволит на этапе 2 подменить NullCapture на настоящий
захват, не тронув ни строчки во фронте.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import shutil
import threading
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

from ..asr import (
    MODEL_DIR_NAME,
    СТАРАЯ_ПАПКА_МОДЕЛИ,
    MODEL_FILES,
    MODEL_REPO,
    MODEL_TOTAL_BYTES,
    GigaamTranscriber,
    ModelDownloader,
    MissedSpan,
    NullTranscriber,
    Transcriber,
    TranscriptionQueue,
)
from ..asr.embedder import MODEL_DIR_NAME as EMBEDDER_DIR_NAME
from ..asr.download import DownloadBusy
from ..asr.gigaam import ModelBroken, ModelMissing
from ..asr.embedder import MODEL_FILES as EMBEDDER_FILES
from ..asr.embedder import MODEL_REPO as EMBEDDER_REPO
from ..asr.embedder import VoiceEmbedder
from ..asr.langid import MODEL_DIR_NAME as LANGID_DIR_NAME
from ..asr.langid import CYRILLIC_LANGS, LanguageDetector
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
from ..audio.buffers import WavWriter, float_to_int16
from ..core import paths
from ..core import settings as settings_mod
from ..core.i18n import t as _t
from ..core.events import (
    CHAT_CHUNK,
    CHAT_ERROR,
    CHAT_MESSAGE,
    IMPORT_CHANGED,
    LLM_DOWNLOAD,
    IMPORT_PROGRESS,
    MEETINGS_CHANGED,
    MEETING_UPDATED,
    MODEL_DOWNLOAD,
    NEW_VERSION,
    RECOGNITION_BACKFILL,
    RECORDING_ERROR,
    RECORDING_STARTED,
    RECORDING_STOPPED,
    SUMMARY_CHUNK,
    SUMMARY_ERROR,
    SUMMARY_READY,
    SUMMARY_STATUS,
    TRANSCRIPT_DRAFT,
    TRANSCRIPT_SEGMENT,
    bus,
)
from ..core.importer import ImportQueue
from ..core.updater import Updater
from ..core.version_check import VersionChecker
from ..core.models import (
    ChatMessage,
    Meeting,
    MeetingStatus,
    NoteLine,
    Person,
    Speaker,
    TranscriptSegment,
    now,
)
from ..llm import LlmError, LlmManager, chat_messages, summary_messages
from ..llm.chunking import estimate_tokens, fits, split_transcript
from ..llm.prompts import chunk_messages, merge_messages
from ..storage import Store

log = logging.getLogger(__name__)

# Сколько токенов отдаём под расшифровку. Окно модели 32k, но в нём
# живёт ещё и ответ, и промпт, и заметки человека. Цифры взяты с
# запасом: отказ сервера хуже, чем лишняя часть при разборе.
TRANSCRIPT_BUDGET = 24000
# В чате места меньше: туда же идёт саммари и вся переписка.
CHAT_BUDGET = 20000


# Каким шагом резать дорожку старой встречи для досчёта. Тот же размер,
# каким идёт разбор загруженного файла: он уже подобран так, чтобы кусок
# влезал в память и распознавался за разумное время.
ШАГ_ДОСЧЁТА = 30.0


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
        # Скачивание весов по требованию. Заводим до очереди: она
        # сразу получает ссылку на _ensure_asr_model. Замок нужен, потому
        # что чанки приходят из разных потоков.
        self._asr_model_lock = threading.Lock()
        self._asr_download_failed = False
        # Перекачивали ли уже повреждённые веса. Одна попытка починки на
        # запуск: если и свежая загрузка не грузится, дело не в файлах,
        # и бесконечный круг закачек по 220 МБ никому не поможет.
        self._asr_repaired = False
        # Идёт ли фоновая загрузка прямо сейчас: окно спрашивает об этом
        # при открытии, а события прогресса могли начаться до него.
        self._asr_downloading = False
        # Очередь создаётся всегда: она дешёвая, а поток поднимается
        # только когда реально приходит первый чанк.
        self.asr_queue = TranscriptionQueue(
            self.transcriber,
            self._on_segment,
            on_draft=self._on_draft,
            embedder=self.embedder,
            roster=self.roster,
            # Веса качаются при первой же реплике, а не при установке:
            # иначе на чистой машине расшифровка молча не появлялась.
            ensure_model=self._ensure_asr_model,
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
        self._убрать_прошлую_модель()
        self.active_meeting_id: str | None = None
        # Модель для саммари и чата. Сервер поднимается только когда
        # человек действительно что-то спросит.
        self.llm = LlmManager(lambda: self.settings.llm)
        # Модель считает один запрос за раз: параллельные запросы к
        # локальному серверу просто замедляют друг друга.
        self._llm_lock = threading.RLock()
        self._llm_busy = False
        self._llm_cancel = False
        # Запись эталона голоса: живёт только пока человек читает фразы.
        self._enrollment: VoiceEnrollment | None = None
        self._enroll_capture: WasapiCapture | None = None
        # Сдвиг времени для второго и последующих включений записи в одной
        # встрече: без него каждый заход начинался бы с нуля.
        self.time_offset: float = 0.0
        # Открытые дорожки разбираемых файлов: встреча -> дорожка -> писатель.
        # Импорт идёт кусками в фоновом потоке, поэтому файл держим
        # открытым до конца разбора, а не открываем на каждый кусок.
        self._import_writers: dict[str, dict[str, WavWriter]] = {}
        self._recover_stale_recordings()
        self._спасти_пустые_встречи()
        self._добрать_недосчитанное()
        # Проверка новой версии идёт в фоне и не задерживает старт.
        self._version_checker = VersionChecker(self)
        # Обновление скачиваем сами: гонять человека на страницу релиза
        # за установщиком — это работа, которую программа может сделать
        # за него. Ставится оно при выходе, чтобы не прерывать встречу.
        self.updater = Updater(self)
        # Видно ли окно. Окно проставляет флаг при показе и скрытии:
        # тихому обновлению нужно знать, смотрит человек в программу
        # или она давно свёрнута в трей.
        self._window_visible = False
        bus.on(NEW_VERSION, self._on_new_version)
        self._version_checker.check_later()
        # И дальше перепроверяем сами: программу закрывают крестиком в
        # трей, а не выходом, поэтому проверки «только при запуске» она
        # может не увидеть неделями.
        self._version_checker.watch()
        # Модель распознавания качаем сразу, не дожидаясь первой встречи.
        # Программу ставят ради расшифровки, и лучше потратить эти минуты
        # тогда, когда человек только осматривается, чем когда он уже
        # бросил файл и ждёт результата.
        self._prefetch_asr_model()
        # Диктовка: своя клавиша, свой микрофон, чужое окно. Поднимается
        # последней и только если человек её включил: она перехватывает
        # клавиши глобально, а такое незачем делать без спроса.
        self.диктовка = None
        self._клавиша_диктовки = None
        self.запустить_диктовку()

    # --- диктовка ---------------------------------------------------------

    def запустить_диктовку(self) -> bool:
        """Поднять или переподнять диктовку по текущим настройкам."""
        self.остановить_диктовку()
        настройки = getattr(self.settings, "dictation", None)
        if настройки is None or not настройки.enabled:
            return False

        from ..dictation.клавиша import КлавишаДиктовки
        from ..dictation.служба import СлужбаДиктовки

        self.диктовка = СлужбаДиктовки(self)
        self._клавиша_диктовки = КлавишаДиктовки(
            сочетание=настройки.hotkey,
            режим=настройки.mode,
            на_старт=self.диктовка.начать,
            на_стоп=self.диктовка.закончить,
        )
        if not self._клавиша_диктовки.старт():
            self._клавиша_диктовки = None
            self.диктовка = None
            return False
        # Первая загрузка модели занимает пару секунд. Прогреваем её
        # заранее, иначе человек получит эти секунды в подарок к первой
        # же диктовке и решит, что она медленная.
        self.диктовка.прогреть()
        return True

    def остановить_диктовку(self) -> None:
        if self._клавиша_диктовки is not None:
            self._клавиша_диктовки.стоп()
            self._клавиша_диктовки = None
        if self.диктовка is not None:
            # Именно закрыть, а не отменить: у диктовки есть своё окно
            # поверх всех остальных, и оно должно уйти вместе с ней.
            self.диктовка.закрыть()
            self.диктовка = None

    def dictation_settings(self) -> dict:
        """Настройки диктовки для окна плюс то, работает ли она сейчас."""
        н = self.settings.dictation
        return {
            "enabled": н.enabled,
            "hotkey": н.hotkey,
            "mode": н.mode,
            "paste_method": н.paste_method,
            # Включить мало: клавишу мог не отдать перехватчик другой
            # программы, а модель может быть не скачана. Человек должен
            # видеть не своё намерение, а настоящее положение дел.
            "running": self._клавиша_диктовки is not None,
            "problem": (
                self.диктовка._почему_нельзя() if self.диктовка is not None else ""
            ),
        }

    def save_dictation_settings(self, **fields) -> dict:
        н = self.settings.dictation
        if "enabled" in fields:
            н.enabled = bool(fields["enabled"])
        if fields.get("hotkey"):
            н.hotkey = str(fields["hotkey"])
        if fields.get("mode") in ("hold", "toggle"):
            н.mode = str(fields["mode"])
        if fields.get("paste_method") in ("auto", "type", "clipboard"):
            н.paste_method = str(fields["paste_method"])
        settings_mod.save(self.settings)
        # Переподнимаем сразу: настройка, которая применится «когда-нибудь
        # потом», для человека выглядит как сломанная.
        self.запустить_диктовку()
        return self.dictation_settings()

    def _on_new_version(self, payload: dict) -> None:
        """Нашлась новая версия — тихо качаем её в фоне."""
        if not self.settings.auto_update:
            return
        self.updater.download_later(str(payload.get("latest") or ""))

    def _убрать_прошлую_модель(self) -> None:
        """Удалить веса модели, которой мы больше не пользуемся.

        До 0.7.3 русскую речь распознавала CTC-сборка GigaAM. Она
        путала похожие слова и на трудной записи выдавала несуществующие
        («сумасодия» вместо «с ума сойти»), поэтому мы перешли на RNNT.
        Файлы у них разные, подхватить старые нельзя, и без уборки они
        так и лежали бы у человека мёртвым грузом на 214 МБ.
        """
        старая = paths.models_dir() / СТАРАЯ_ПАПКА_МОДЕЛИ
        if not старая.is_dir():
            return
        try:
            весило = sum(f.stat().st_size for f in старая.rglob("*") if f.is_file())
            shutil.rmtree(старая)
        except OSError:
            # Не смогли — не беда: место занято, но работе не мешает.
            log.warning("Не удалось убрать прошлую модель из %s", старая)
            return
        log.info("Убрана прошлая модель распознавания, освобождено %.0f МБ",
                 весило / 1024 ** 2)

    def _prefetch_asr_model(self) -> None:
        """Начать загрузку весов в фоне сразу после запуска.

        Строго в отдельном потоке: старт окна задерживать нельзя, иначе
        приложение несколько минут не будет показывать вообще ничего.
        """
        # Тестам и разбору поломок нужна возможность запустить приложение
        # без похода в сеть: 220 МБ на каждый прогон никому не нужны.
        if os.environ.get("KONSPEKT_NO_PREFETCH") == "1":
            return
        if not self.settings.asr.enabled:
            return

        def run() -> None:
            try:
                self._ensure_asr_model()
            except Exception:
                # Не смогли — не беда: попробуем ещё раз при первой
                # реплике, а до тех пор приложение работает как обычно.
                log.exception("Фоновая загрузка модели не удалась")

        # Раньше здесь стоял ранний выход, если файлы уже на диске. Из-за
        # него повреждённые веса доживали до первой реплики: человек жал
        # запись, ждал и получал пустоту. Теперь загрузку в память
        # проверяем сразу при запуске, пока он ещё осматривается, и порча
        # чинится до того, как понадобится расшифровка.
        threading.Thread(target=run, name="asr-prefetch", daemon=True).start()
        log.info("Готовим модель распознавания в фоне")

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
        return LanguageRouter(
            russian, detector, foreign,
            fallback_lang=self.settings.asr.language,
        )

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

    def _on_draft(self, segment: TranscriptSegment) -> None:
        """Черновик идущей речи: только на экран, мимо базы.

        Человек говорит, а текст появляется лишь после паузы: на длинной
        фразе кажется, что программа не работает. Показываем сказанное на
        ходу. Черновик живёт до конца фразы, потом на его месте появится
        настоящая реплика — она и попадёт в базу, поиск и саммари.
        """
        if not segment.text or not segment.text.strip():
            return
        bus.emit(TRANSCRIPT_DRAFT, {"segment": segment.to_dict()})

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
            raise RuntimeError(self._msg("python.enroll.recording_conflict"))
        if self._enrollment is not None:
            return self.enrollment_status()

        try:
            self.embedder.load()
        except Exception as exc:
            raise RuntimeError(self._msg("python.enroll.model_unavailable", exc=exc)) from exc

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
            raise RuntimeError(self._msg("python.enroll.not_started"))

        vector = enrollment.result()
        if vector is None:
            raise RuntimeError(
                self._msg("python.enroll.too_little", min=int(MIN_SECONDS))
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
        # Фоновая загрузка идёт мимо ModelDownloader.start, поэтому его
        # флага мало: без этого окно при запуске показывало бы кнопку
        # «Скачать», хотя загрузка уже идёт.
        busy = self.downloader.is_running or self._asr_downloading
        return {
            "backend": self.settings.asr.backend,
            "enabled": self.settings.asr.enabled,
            "name": getattr(self.transcriber, "name", "null"),
            "downloaded": bool(ready),
            "loaded": bool(getattr(self.transcriber, "is_loaded", False)),
            "downloading": bool(busy),
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

    def asr_settings(self) -> dict[str, Any]:
        """Что показать на экране настроек распознавания.

        Вместе со значениями отдаём и то, скачан ли каждый размер
        Whisper: выбирать размер, которого нет на диске, человек должен
        осознанно, а не обнаружить потом, что речь опять пишется
        кириллицей.
        """
        asr = self.settings.asr
        sizes = []
        for код, о in WHISPER_SIZES.items():
            путь = paths.models_dir() / о["dir"]
            sizes.append({
                "code": код,
                "bytes": о["bytes"],
                "downloaded": WhisperTranscriber(путь).is_downloaded(),
            })
        langid = LanguageDetector(paths.models_dir() / LANGID_DIR_NAME)
        return {
            "language": asr.language,
            "detect_language": asr.detect_language,
            "whisper_size": getattr(asr, "whisper_size", WHISPER_DEFAULT_SIZE),
            "sizes": sizes,
            # Определять язык не на чем, если весов определителя нет.
            "langid_ready": langid.is_downloaded(),
            "active_size": self._whisper_dir(),
        }

    def save_asr_settings(self, **fields: Any) -> dict[str, Any]:
        """Применить настройки распознавания.

        Движок пересобираем сразу, иначе смена языка или размера Whisper
        доходила бы только до следующего запуска программы, а человек
        решил бы, что настройка не работает. Посреди записи не трогаем:
        подмена движка на ходу теряет накопленный кусок звука.
        """
        asr = self.settings.asr
        было = (asr.language, asr.detect_language,
                getattr(asr, "whisper_size", WHISPER_DEFAULT_SIZE))

        язык = fields.get("language")
        if isinstance(язык, str) and язык.strip():
            asr.language = язык.strip()
        if "detect_language" in fields:
            asr.detect_language = bool(fields["detect_language"])
        размер = fields.get("whisper_size")
        if размер in WHISPER_SIZES:
            asr.whisper_size = размер

        settings_mod.save(self.settings)

        стало = (asr.language, asr.detect_language, asr.whisper_size)
        if стало != было and not self.capture.is_recording:
            self.transcriber = self._build_transcriber()
        return self.asr_settings()

    def _ensure_asr_model(self) -> bool:
        """Дождаться весов распознавания, скачав их при необходимости.

        Иначе на чистой установке получалось молчаливое ничто: человек
        бросал файл, импорт бодро доходил до конца, а расшифровка
        оставалась пустой, потому что модели на диске нет. Ошибка при
        этом была только в журнале, которого никто не читает.

        Качаем один раз и по требованию, а не при установке: 220 МБ в
        дистрибутиве не нужны тем, кто пользуется только заметками.
        """
        transcriber = self.transcriber
        ready = getattr(transcriber, "is_downloaded", lambda: True)()
        if ready:
            return self._ensure_asr_usable()
        if self._asr_download_failed:
            # Уже пробовали и не смогли: не долбим сеть на каждом чанке.
            return False

        with self._asr_model_lock:
            # Пока ждали замок, скачать мог соседний поток.
            if getattr(transcriber, "is_downloaded", lambda: True)():
                return self._ensure_asr_usable()
            if self._asr_download_failed:
                return False

            shown = -1

            def progress(name: str, done: int, total: int) -> None:
                nonlocal shown
                if not total:
                    return
                percent = done * 100 // total
                if percent == shown:
                    return
                shown = percent
                bus.emit(
                    MODEL_DOWNLOAD,
                    {"file": name, "bytes": done, "total": total,
                     "percent": percent, "state": "downloading"},
                )

            log.info("Модель распознавания не найдена, качаем при первом обращении")
            self._asr_downloading = True
            try:
                self.downloader.run_blocking(progress)
            except DownloadBusy:
                # Второй экземпляр программы уже качает эти же веса.
                # Ждать здесь нечего: пусть докачает он, а мы попробуем
                # на следующей реплике. Главное — не лезть в его файл.
                log.info("Модель качает другой экземпляр, ждём его")
                bus.emit(MODEL_DOWNLOAD, {
                    "state": "error",
                    "message": self._msg("python.model.other_instance"),
                })
                return False
            except Exception as exc:
                self._asr_download_failed = True
                log.exception("Не удалось скачать модель распознавания")
                bus.emit(MODEL_DOWNLOAD, {"state": "error", "message": str(exc)})
                return False
            finally:
                self._asr_downloading = False

            if not getattr(transcriber, "is_downloaded", lambda: True)():
                self._asr_download_failed = True
                bus.emit(MODEL_DOWNLOAD, {
                    "state": "error",
                    "message": self._msg("python.model.incomplete"),
                })
                return False

            bus.emit(MODEL_DOWNLOAD, {"state": "ready", "message": ""})
            log.info("Модель распознавания готова")
            return self._ensure_asr_usable()

    def _ensure_asr_usable(self) -> bool:
        """Проверить, что скачанные веса действительно загружаются.

        Файлы на диске ещё не значат работающую модель: испорченная
        загрузка даёт файл верного размера, из которого onnxruntime
        ничего не собирает. Раньше это выглядело как полностью исправная
        программа с вечно пустым транскриптом, поэтому проверяем честной
        загрузкой, а битые веса выбрасываем и качаем заново.
        """
        transcriber = self.transcriber
        engine = getattr(transcriber, "russian", transcriber)
        load = getattr(engine, "load", None)
        if load is None:
            return True
        # Смотрим на настоящие файлы, а не на флаг готовности: флаг
        # выставляет загрузчик, и при подменённом загрузчике он говорит
        # «скачано», когда весов на диске нет. Проверять там нечего, а
        # попытка загрузки полезла бы в сеть за 220 МБ.
        model_dir = getattr(engine, "model_dir", None)
        if model_dir is not None and not all(
            (model_dir / name).exists() for name in MODEL_FILES
        ):
            return True
        try:
            load()
            return True
        except ModelBroken as exc:
            log.error("Веса повреждены: %s", exc)
        except ModelMissing:
            # Файлов нет вовсе. Это не порча: качать заново нечего, и
            # отдельная ветка загрузки разберётся с этим сама.
            return True
        except Exception:
            log.exception("Модель не загрузилась")
            return False

        if self._asr_repaired:
            # Перекачали и снова битая: дело не в загрузке. Молчать
            # нельзя, иначе человек так и будет смотреть в пустоту.
            bus.emit(MODEL_DOWNLOAD, {
                "state": "error",
                "message": self._msg("python.model.corrupted"),
            })
            return False

        self._asr_repaired = True
        bus.emit(MODEL_DOWNLOAD, {
            "state": "error",
            "message": self._msg("python.model.repairing"),
        })
        getattr(engine, "discard", lambda: None)()
        self._asr_download_failed = False
        return self._ensure_asr_model()

    def _recover_stale_recordings(self) -> None:
        """Чиним встречи, зависшие в записи или в разборе файла.

        Приложение могло упасть или быть убито во время записи. Тогда в базе
        остаётся встреча со статусом recording, которой уже никто не пишет,
        и в списке навсегда мигает красная точка.

        То же самое бывает с загруженным файлом: программу закрыли, пока
        он разбирался, и встреча навсегда остаётся «обрабатывается».
        Сама она из этого состояния не выйдет, потому что разбирать её
        больше некому, а человеку видно только вечное ожидание. Текст,
        который успели распознать, при этом на месте и никуда не денется.
        """
        зависшие = (MeetingStatus.RECORDING, MeetingStatus.PROCESSING)
        for meeting in self.store.list_meetings():
            if meeting.status not in зависшие:
                continue
            было = meeting.status
            ended = meeting.ended_at or meeting.started_at or meeting.created_at
            self.store.update_meeting(
                meeting.id, status=MeetingStatus.READY, ended_at=ended
            )
            log.info(
                "Восстановлена прерванная %s: %s",
                "запись" if было is MeetingStatus.RECORDING else "загрузка",
                meeting.id,
            )

    def _спасти_пустые_встречи(self) -> None:
        """Пометить к досчёту встречи, оставшиеся без расшифровки.

        Пометка пропусков при разборе файла чинит только будущее, а у
        человека уже лежат встречи, разобранные прежними версиями: звук
        цел, реплик нет, и в базе никто не отметил, что их надо
        досчитать (lamver/konspekt-releases#3). Сами они не оживут
        никогда, поэтому находим их по признаку «звук есть, а текста
        нет» и отдаём тому же досчёту.

        Отметку ставим сразу, до самого досчёта. Реплик может не быть и
        по честной причине - тишина или речь на языке, которого модель
        не знает, - и без отметки такую встречу перемалывали бы при
        каждом запуске впустую.
        """
        try:
            встречи = self.store.find_unrescued()
        except Exception:
            log.exception("Не удалось найти встречи без расшифровки")
            return
        if not встречи:
            return
        log.info("Встреч без расшифровки, к досчёту: %d", len(встречи))
        for meeting_id in встречи:
            try:
                spans = self._нарезать_на_куски(meeting_id)
                if spans:
                    self.store.add_missed_spans(meeting_id, spans)
                # Помечаем в любом случае, даже когда нарезать не вышло:
                # звука нет или файл удалён, и повторная попытка даст
                # ровно тот же результат.
                self.store.update_meeting(meeting_id, rescued=1)
            except Exception:
                log.exception("Не удалось подготовить к досчёту встречу %s", meeting_id)

    def _нарезать_на_куски(self, meeting_id: str) -> list[MissedSpan]:
        """Разбить дорожки встречи на куски по размеру окна распознавания.

        Целиком дорожку в очередь не отдать: часовая запись не влезет ни
        в память, ни в разумное ожидание. Режем тем же шагом, каким идёт
        разбор файла, и досчёт разберёт куски по одному.
        """
        куски: list[MissedSpan] = []
        for chunk in self.store.list_audio_chunks(meeting_id):
            длина = float(chunk.get("duration_s") or 0.0)
            начало = float(chunk.get("start_s") or 0.0)
            дорожка = chunk.get("track") or "them"
            if длина <= 0:
                continue
            смещение = 0.0
            while смещение < длина:
                шаг = min(ШАГ_ДОСЧЁТА, длина - смещение)
                if шаг <= 0:
                    break
                куски.append(
                    MissedSpan(meeting_id, дорожка, начало + смещение, шаг)
                )
                смещение += шаг
        return куски

    def _добрать_недосчитанное(self) -> None:
        """Досчитать то, что не успели в прошлый раз.

        Досчёт идёт фоном и на длинной встрече занимает минуты, а
        встреча к этому времени уже помечена готовой. Человек закрывает
        программу, не дожидаясь конца, и повода ждать у него нет.
        Звук при этом цел, пропадало только распознавание — значит
        досчитать можно и потом, просто на следующем запуске.

        Не задерживает старт: чтение списка дёшевое, а сам досчёт
        уходит в свой поток, как и после кнопки «стоп».
        """
        try:
            rows = self.store.list_missed_spans()
        except Exception:
            log.exception("Не удалось прочитать список недосчитанного")
            return
        if not rows:
            return
        по_встречам: dict[str, list[MissedSpan]] = {}
        for r in rows:
            по_встречам.setdefault(r["meeting_id"], []).append(
                MissedSpan(r["meeting_id"], r["speaker"], r["offset_s"], r["duration_s"])
            )
        log.info(
            "Осталось досчитать с прошлого запуска: %d кусков в %d встречах",
            len(rows), len(по_встречам),
        )
        for meeting_id, spans in по_встречам.items():
            threading.Thread(
                target=self._run_backfill, args=(meeting_id, spans),
                name="asr-backfill-старый", daemon=True,
            ).start()

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
        # Есть ли что переслушать. У встреч, загруженных файлом до появления
        # этой возможности, звук не сохранялся, и кнопка у их реплик только
        # обманывала бы: нажал, а в ответ «записи нет».
        data["has_audio"] = bool(self.store.list_audio_chunks(meeting_id))
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
        # Сначала файлы, потом запись в базе: если удаление файлов
        # упадёт, встреча останется на месте и человек попробует ещё
        # раз. Обратный порядок оставил бы аудио без владельца, и найти
        # его было бы уже нечем.
        self._delete_audio(meeting_id)
        self.store.delete_meeting(meeting_id)
        bus.emit(MEETINGS_CHANGED)

    def _save_audio_chunks(self, meeting_id: str) -> None:
        """Привязать записанные файлы к времени встречи.

        Молчим при ошибке: не сохранившаяся привязка стоит кнопки
        «переслушать», а потерянная из-за неё встреча — гораздо дороже.
        """
        chunks = getattr(self.capture, "last_chunks", None)
        if not chunks:
            return
        try:
            for track, path, duration in chunks:
                self.store.add_audio_chunk(
                    meeting_id, track, path, self.time_offset, duration
                )
        except Exception:
            log.exception("Не удалось запомнить куски записи встречи %s", meeting_id)

    def audio_clip(self, meeting_id: str, start: float, end: float,
                   track: str = "") -> dict[str, Any] | None:
        """Кусок записи вокруг реплики для прослушивания.

        Отдаём готовый WAV в base64, а не путь к файлу: во фронте нет
        доступа к диску, а поднимать ради этого HTTP-сервер значило бы
        открыть порт наружу в приложении, которое обещает приватность.
        """
        chunks = self.store.list_audio_chunks(meeting_id)
        if not chunks:
            return None

        # Реплику писали обе дорожки, но своя слышна чётче: у «them» голос
        # собеседника, у «me» свой. Берём дорожку реплики, а если её нет
        # (например, писали только систему) — любую доступную.
        wanted = [c for c in chunks if not track or c["track"] == track]
        pool = wanted or chunks
        # Нужный файл тот, внутрь которого попадает начало реплики.
        chunk = None
        for c in pool:
            if c["start_s"] <= start < c["start_s"] + c["duration_s"]:
                chunk = c
                break
        if chunk is None:
            # Записи старых встреч не привязаны ко времени: там файл один,
            # и он начинается с нуля.
            chunk = pool[0]

        path = Path(chunk["path"])
        if not path.exists():
            return None

        # Небольшой запас с обеих сторон: граница фразы у распознавания
        # неточная, и без него начало слова срезается.
        pad = 0.25
        offset = max(0.0, start - chunk["start_s"] - pad)
        length = max(0.3, (end - start) + pad * 2)
        try:
            data, rate = _read_wav_part(path, offset, length)
        except Exception:
            log.exception("Не удалось прочитать кусок записи %s", path)
            return None

        buf = io.BytesIO()
        with wave.open(buf, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(data)
        return {
            "wav": base64.b64encode(buf.getvalue()).decode("ascii"),
            "duration": len(data) / 2 / rate,
        }

    def retranscribe_segment(self, segment_id: str, lang: str) -> dict | None:
        """Пересчитать одну реплику на указанном языке.

        Защитная сетка на случай, когда автоматика всё же ошиблась с
        языком. Порог уверенности и сверка по тексту закрывают почти все
        случаи, но «почти» здесь недостаточно: одна испорченная фраза в
        часовой встрече заметна, а исправить её человеку было нечем.

        Звук берём из записи по времени реплики — тот же путь, что у
        кнопки «переслушать». Значит работает только там, где запись
        сохранена; у встреч без звука пересчитывать нечего.
        """
        seg = self.store.get_segment(segment_id)
        if seg is None:
            return None

        engine = self._pick_engine(lang)
        if engine is None:
            log.info("Нет модели для языка %s, пересчёт невозможен", lang)
            return None

        pcm, rate = self._segment_pcm(seg)
        if pcm is None:
            return None

        try:
            куски = list(engine.transcribe(
                pcm, sample_rate=rate, meeting_id=seg.meeting_id,
                offset=seg.start, speaker=seg.speaker.value, lang=lang,
            ))
        except TypeError:
            # У части распознавателей нет параметра языка: они его не
            # выбирают, а знают заранее.
            куски = list(engine.transcribe(
                pcm, sample_rate=rate, meeting_id=seg.meeting_id,
                offset=seg.start, speaker=seg.speaker.value,
            ))
        except Exception:
            log.exception("Пересчёт реплики %s не удался", segment_id)
            return None

        текст = " ".join(к.text.strip() for к in куски if к.text.strip()).strip()
        if not текст:
            log.info("Пересчёт реплики %s дал пустой текст, оставляем как было",
                     segment_id)
            return None

        self.store.update_segment(segment_id, текст, lang)
        log.info("Реплика %s пересчитана как %s", segment_id, lang)
        return {"id": segment_id, "text": текст, "lang": lang}

    def _pick_engine(self, lang: str):
        """Распознаватель под язык: русская модель или иноязычная.

        Обычно `transcriber` — это LanguageRouter, внутри которого две
        модели. Но он же может быть и одиночным распознавателем (когда
        иноязычной модели нет вовсе), и тогда выбирать не из чего.
        """
        russian = getattr(self.transcriber, "russian", None)
        if russian is None:
            return self.transcriber
        if lang in CYRILLIC_LANGS:
            return russian
        return getattr(self.transcriber, "foreign", None)

    def _segment_pcm(self, seg) -> tuple:
        """Звук реплики из записи встречи, как для прослушивания."""
        clip = self.audio_clip(seg.meeting_id, seg.start, seg.end,
                               seg.speaker.value)
        if not clip or not clip.get("wav"):
            log.info("Записи для реплики нет, пересчитывать нечего")
            return None, 0
        raw = base64.b64decode(clip["wav"])
        with wave.open(io.BytesIO(raw), "rb") as w:
            rate = w.getframerate()
            data = w.readframes(w.getnframes())
        return np.frombuffer(data, dtype=np.int16), rate

    def _delete_audio(self, meeting_id: str) -> None:
        """Убрать записи встречи с диска.

        Молчим при ошибке: занятый файл или права не должны мешать
        удалить саму встречу. Осиротевшее аудио потом подберёт уборка.
        """
        folder = paths.audio_dir() / meeting_id
        if not folder.exists():
            return
        try:
            shutil.rmtree(folder)
            log.info("Удалены записи встречи %s", meeting_id)
        except OSError:
            log.warning("Не удалось удалить записи встречи %s", meeting_id, exc_info=True)

    def cleanup_audio(self) -> dict[str, Any]:
        """Убрать записи, у которых больше нет встречи.

        Такие остаются после удаления встречи занятым файлом, после
        падения во время записи и от версий, которые аудио не убирали
        вовсе. Молча копить гигабайты в приложении про приватность
        неправильно.
        """
        root = paths.audio_dir()
        if not root.exists():
            return {"folders": 0, "bytes": 0}

        known = {m.id for m in self.store.list_meetings()}
        removed = 0
        freed = 0
        for folder in root.iterdir():
            if not folder.is_dir() or folder.name in known:
                continue
            size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
            try:
                shutil.rmtree(folder)
            except OSError:
                log.warning("Не удалось убрать %s", folder, exc_info=True)
                continue
            removed += 1
            freed += size

        if removed:
            log.info("Убрано %s папок с записями, %s байт", removed, freed)
        return {"folders": removed, "bytes": freed}

    def search(self, query: str) -> list[dict[str, Any]]:
        """Поиск по расшифровкам, сгруппированный по встречам.

        Плоский список реплик неудобен: одна встреча забивает выдачу
        десятком совпадений, и остальные не видно. Поэтому на встречу
        отдаём несколько лучших цитат и общее число совпадений.
        """
        rows = self.store.search(query)
        if not rows:
            return []

        by_meeting: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = by_meeting.setdefault(row["meeting_id"], {
                "meeting_id": row["meeting_id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "hits": 0,
                "quotes": [],
            })
            item["hits"] += 1
            # Три цитаты на встречу: больше не помещается в список и не
            # читается, а понять, та ли это встреча, хватает и одной.
            if len(item["quotes"]) < 3:
                item["quotes"].append({
                    "text": row["text"],
                    "start": row["start_s"],
                    "who": row["voice_label"] or ("Я" if row["speaker"] == "me" else "Собеседник"),
                })

        found = list(by_meeting.values())
        found.sort(key=lambda m: (-m["hits"], -(m["created_at"] or 0)))
        return found

    def storage_usage(self) -> dict[str, Any]:
        """Сколько занимают записи, база и модели.

        Модели считаем отдельно: они весят больше всего, но это не
        личные данные, и убирать их вместе со встречами неправильно.
        """
        def folder_size(path: Path) -> int:
            if not path.exists():
                return 0
            return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

        db = paths.db_path()
        audio_root = paths.audio_dir()
        known = {m.id for m in self.store.list_meetings()}

        orphan_bytes = 0
        orphan_folders = 0
        if audio_root.exists():
            for folder in audio_root.iterdir():
                if folder.is_dir() and folder.name not in known:
                    orphan_folders += 1
                    orphan_bytes += folder_size(folder)

        return {
            "meetings": len(known),
            "audio_bytes": folder_size(audio_root),
            "db_bytes": db.stat().st_size if db.exists() else 0,
            "models_bytes": folder_size(paths.models_dir()),
            "orphan_folders": orphan_folders,
            "orphan_bytes": orphan_bytes,
        }

    def cleanup_storage(self) -> dict[str, Any]:
        """Убрать записи без встреч и сжать базу."""
        result = self.cleanup_audio()
        result["db_bytes"] = self.store.vacuum()
        return result

    # --- запись ----------------------------------------------------------

    def start_recording(self, meeting_id: str | None = None) -> dict[str, Any] | None:
        if self.capture.is_recording:
            log.warning("Запись уже идёт, повторный старт проигнорирован")
            if not self.active_meeting_id:
                return None
            # Окно спросило «начать» во время записи, значит оно думает,
            # что записи нет. Расхождение чинится тут же: рассказываем
            # заново, что встреча пишется. Иначе кнопка так и останется
            # мёртвой до перезапуска программы.
            встреча = self.store.get_meeting(self.active_meeting_id)
            bus.emit(
                RECORDING_STARTED,
                {
                    "meeting_id": self.active_meeting_id,
                    "started_at": getattr(встреча, "started_at", None),
                },
            )
            return self.get_meeting(self.active_meeting_id)

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
        # Запоминаем, с какой секунды встречи начинается каждый файл.
        # time_offset хранит, сколько уже было записано до этого захода:
        # без него второй заход считался бы с нуля, и «переслушать» на
        # 40-й секунде попадало бы в начало второго файла.
        self._save_audio_chunks(meeting_id)
        # Последняя фраза ещё сидит внутри VAD: он ждал паузу, а её уже
        # не будет. Выталкиваем до остановки очереди, иначе потеряется.
        self.asr_queue.flush(meeting_id)
        # Даём распознаванию доделать хвост очереди: последние фразы
        # встречи важнее пары секунд ожидания.
        self.asr_queue.stop()
        # Что-то могло не поместиться в живую очередь при перегрузке.
        # Сам звук цел на диске (пишется отдельно от распознавания), и
        # теперь, когда спешить уже некуда, можно вырезать те же куски
        # из записи и досчитать их не торопясь.
        missed = self.asr_queue.take_missed(meeting_id)
        if missed:
            # Записываем до начала досчёта, а не после. Досчёт идёт
            # фоном и на длинной встрече занимает минуты, а встреча уже
            # помечена готовой: человек вправе закрыть программу, не
            # дожидаясь конца. Список в памяти ушёл бы вместе с ней,
            # и дыра в расшифровке осталась бы навсегда.
            try:
                self.store.add_missed_spans(meeting_id, missed)
            except Exception:
                log.exception("Не удалось запомнить пропущенные куски встречи %s", meeting_id)
            # Эталон голосов сохраняем только после докатки: иначе фразы,
            # которые она добавит, в счёт не попадут.
            threading.Thread(
                target=self._run_backfill, args=(meeting_id, missed),
                name="asr-backfill", daemon=True,
            ).start()
        else:
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
        # Окну важно знать, что расшифровка ещё дополняется: иначе оно
        # сразу отправит неполный транскрипт в заметки, а человек решит,
        # что куски речи просто потерялись.
        bus.emit(RECORDING_STOPPED,
                 {"meeting_id": meeting_id, "backfill": bool(missed)})
        bus.emit(MEETINGS_CHANGED)
        return self.get_meeting(meeting_id)

    def _run_backfill(self, meeting_id: str, missed: list) -> None:
        """Досчитать куски речи, не попавшие в живую очередь.

        Идёт в своём потоке и не спешит: встреча уже видна человеку как
        готовая, а докатка просто дозаполняет пропуски в транскрипте по
        мере готовности, как только распознавание до них доберётся.
        """
        total = len(missed)
        log.info("Докатываем %d пропущенных кусков речи встречи %s", total, meeting_id)
        bus.emit(RECOGNITION_BACKFILL,
                 {"meeting_id": meeting_id, "state": "start", "total": total})
        done = 0
        try:
            for span in missed:
                try:
                    pcm = self._read_missed_pcm(meeting_id, span)
                    if pcm is not None and pcm.size > 0:
                        # Вычёркиваем кусок не здесь, а когда он действительно
                        # распознан. Между постановкой в очередь и готовым
                        # текстом десятки кусков разницы, и если программу
                        # закроют в этот промежуток, всё стоявшее в очереди
                        # считалось бы сделанным и пропало бы молча.
                        self.asr_queue.submit_raw(
                            meeting_id, span.speaker, pcm, span.offset, block=True,
                            done=lambda s=span: self.store.drop_missed_span(
                                meeting_id, s.speaker, s.offset
                            ),
                        )
                    else:
                        # Звука для этого куска нет вовсе: файл удалён или
                        # запись оборвалась. Досчитать его не выйдет никогда,
                        # и держать его в списке значит пробовать снова при
                        # каждом запуске до конца времён.
                        self.store.drop_missed_span(
                            meeting_id, span.speaker, span.offset
                        )
                except Exception:
                    log.exception(
                        "Не удалось докатить кусок %s@%.1fс встречи %s",
                        span.speaker, span.offset, meeting_id,
                    )
                finally:
                    done += 1
                    bus.emit(RECOGNITION_BACKFILL, {
                        "meeting_id": meeting_id, "state": "progress",
                        "done": done, "total": total,
                    })
            # wait_idle, а не stop: докатка не должна гасить поток
            # распознавания, если человек уже начал следующую встречу.
            self.asr_queue.wait_idle()
        finally:
            self._remember_voices(meeting_id)
            bus.emit(RECOGNITION_BACKFILL,
                     {"meeting_id": meeting_id, "state": "done", "total": total})
            bus.emit(MEETING_UPDATED, {"meeting_id": meeting_id})

    def _read_missed_pcm(self, meeting_id: str, span) -> np.ndarray | None:
        """Вырезать звук пропущенного куска из уже записанного WAV.

        Тот же путь, что у кнопки «переслушать»: файл на диске цел, нужно
        только найти, какой из кусков записи покрывает нужное время.
        """
        chunks = self.store.list_audio_chunks(meeting_id)
        if not chunks:
            return None
        wanted = [c for c in chunks if c["track"] == span.speaker]
        pool = wanted or chunks
        chunk = None
        for c in pool:
            if c["start_s"] <= span.offset < c["start_s"] + c["duration_s"]:
                chunk = c
                break
        if chunk is None:
            return None
        path = Path(chunk["path"])
        if not path.exists():
            return None
        local_offset = max(0.0, span.offset - chunk["start_s"])
        try:
            raw, _rate = _read_wav_part(path, local_offset, span.duration)
        except Exception:
            log.exception("Не удалось прочитать пропущенный кусок записи %s", path)
            return None
        if not raw:
            return None
        return np.frombuffer(raw, dtype=np.int16)

    @property
    def is_recording(self) -> bool:
        return self.capture.is_recording

    def is_importing(self) -> bool:
        """Идёт ли разбор загруженных файлов.

        Нужно тихому обновлению: оборванный разбор придётся начинать
        заново, а он занимает минуты.
        """
        return bool(getattr(self.importer, "busy", False))

    def window_visible(self) -> bool:
        """Открыто ли окно. Проставляется окном при показе и скрытии.

        Подменять программу, пока человек в неё смотрит, нельзя: она
        просто исчезнет у него из-под рук.
        """
        return bool(self._window_visible)

    # --- чужая речь в системном звуке ------------------------------------

    def выключить_системный_звук(self) -> dict[str, Any]:
        """Ответ человека «это не собеседник»: перестать писать системный звук.

        Запись при этом продолжается с микрофона. Настройка тоже
        переключается, чтобы следующая встреча не начиналась с того же
        сюрприза, и сохраняется на диск: иначе после перезапуска всё
        вернулось бы, а человек уже сказал, чего хочет.
        """
        ответ: dict[str, Any] = {"ok": False}
        выключить = getattr(self.capture, "выключить_системный_звук", None)
        if выключить is not None:
            try:
                ответ = выключить()
            except Exception as exc:
                log.exception("Не удалось выключить системный звук")
                return {"ok": False, "причина": str(exc)}

        self.settings.audio.capture_system = False
        try:
            settings_mod.save(self.settings)
        except Exception:
            log.exception("Не удалось сохранить настройки после выключения звука")

        # Что уже записалось до этого мгновения, помечаем как сомнительное.
        # Тихо оставить нельзя: именно эти реплики и есть источник
        # испорченного саммари, ради которого человек нажал кнопку.
        # Удалять тоже нельзя: он мог ошибиться, а запись уже не вернуть.
        помечено = 0
        if self.active_meeting_id:
            try:
                помечено = self.store.mark_doubtful(
                    self.active_meeting_id,
                    Speaker.THEM.value,
                    self._last_segment_end(self.active_meeting_id) + 1.0,
                )
            except Exception:
                log.exception("Не удалось пометить реплики системного звука")
            if помечено:
                log.info("Помечено сомнительных реплик: %d", помечено)
                bus.emit(MEETING_UPDATED, {"meeting_id": self.active_meeting_id})
        ответ["помечено"] = помечено
        return ответ

    def включить_системный_звук(self) -> dict[str, Any]:
        """Ответ человека «это собеседник, пишите»: включить системный звук.

        Обратный случай: встреча началась без системного звука, а
        собеседник заговорил в Zoom. Дорожка поднимается на ходу, встречу
        останавливать не нужно.
        """
        ответ: dict[str, Any] = {"ok": False}
        включить = getattr(self.capture, "включить_системный_звук", None)
        if включить is not None:
            try:
                ответ = включить()
            except Exception as exc:
                log.exception("Не удалось включить системный звук")
                return {"ok": False, "причина": str(exc)}

        if ответ.get("ok"):
            self.settings.audio.capture_system = True
            try:
                settings_mod.save(self.settings)
            except Exception:
                log.exception("Не удалось сохранить настройки после включения звука")
        return ответ

    def не_спрашивать_про_системный_звук(self) -> dict[str, Any]:
        """Ответ человека «всё верно, это собеседник»: больше не спрашивать.

        Ничего не меняем, только затыкаем наблюдателя до конца записи.
        Повторный вопрос на той же встрече — это навязчивость, а такие
        уведомления закрывают не читая.
        """
        замолчать = getattr(self.capture, "замолчать_про_чужую_речь", None)
        if замолчать is not None:
            try:
                замолчать()
            except Exception:
                log.exception("Не удалось отключить вопрос про системный звук")
                return {"ok": False}
        return {"ok": True}

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
        # Заодно складываем звук рядом со встречей. Без этого у реплик из
        # загруженного файла нечего было переслушивать: сам файл лежит
        # где-то у человека и может быть переименован или удалён, а
        # расшифровка остаётся навсегда.
        self._write_import_audio(meeting_id, track, pcm)
        if not self.settings.asr.enabled:
            self._запомнить_неразобранное(meeting_id, track, pcm, offset)
            return
        # На чистой установке весов нет, и без этого разбор файла
        # тихо давал пустую расшифровку.
        if not self._ensure_asr_model():
            self._запомнить_неразобранное(meeting_id, track, pcm, offset)
            return
        # block=True: чтение файла идёт в десятки раз быстрее распознавания,
        # и без ожидания очередь переполняется, а речь молча теряется. На
        # живой 25-минутной записи так пропал 421 фрагмент речи.
        self.asr_queue.submit(meeting_id, track, pcm, offset, block=True)

    def _запомнить_неразобранное(
        self, meeting_id: str, track: str, pcm, offset: float
    ) -> None:
        """Пометить кусок файла, оставшийся без распознавания.

        Разбор файла молча пропускает распознавание в двух случаях: оно
        выключено в настройках и не скачалась модель. Звук при этом
        ложится на диск целым, пропадает только текст — то есть ровно та
        ситуация, для которой уже есть докатка. Живая запись свои дыры
        отдаёт через `take_missed`, а импорт до этой правки не оставлял
        следа вовсе, и встреча навсегда оставалась пустой: жалоба
        lamver/konspekt-releases#3.

        Пишем сразу, куском за куском, а не списком в конце разбора.
        Список в памяти ушёл бы вместе с закрытой программой, а причина
        пропуска (нет модели) как раз и означает, что человек, скорее
        всего, сейчас пойдёт её скачивать и перезапустится.
        """
        try:
            длина = len(np.asarray(pcm).reshape(-1)) / float(SAMPLE_RATE)
            if длина <= 0:
                return
            self.store.add_missed_spans(
                meeting_id, [MissedSpan(meeting_id, track, offset, длина)]
            )
        except Exception:
            # Разбор файла важнее пометки: звук уже сохранён, и человек
            # в худшем случае просто не получит досчёт.
            log.exception(
                "Не удалось запомнить неразобранный кусок встречи %s", meeting_id
            )

    def _write_import_audio(self, meeting_id: str, track: str, pcm) -> None:
        """Дописать кусок разбираемого файла в дорожку встречи.

        Пишем тем же WavWriter и в тот же каталог, что и живая запись,
        поэтому прослушивание, подсчёт места и удаление встречи работают
        с импортом ровно так же, как с записанным разговором.
        """
        try:
            writers = self._import_writers.setdefault(meeting_id, {})
            writer = writers.get(track)
            if writer is None:
                folder = paths.audio_dir() / meeting_id
                folder.mkdir(parents=True, exist_ok=True)
                writer = WavWriter(folder / f"import-{track}.wav")
                writers[track] = writer
            data = np.asarray(pcm)
            if data.dtype != np.int16:
                # Из файла звук приходит float32 [-1, 1], а на диск дорожки
                # кладём в int16, как и живую запись.
                data = float_to_int16(data)
            writer.write(data.reshape(-1))
        except Exception:
            # Расшифровка важнее возможности переслушать: молчим.
            log.exception("Не удалось сохранить звук импорта встречи %s", meeting_id)

    def _close_import_audio(self, meeting_id: str) -> None:
        """Закрыть дорожки импорта и привязать их ко времени встречи."""
        writers = self._import_writers.pop(meeting_id, None)
        if not writers:
            return
        for track, writer in writers.items():
            try:
                duration = writer.duration
                path = writer.path
                writer.close()
                if duration <= 0:
                    path.unlink(missing_ok=True)
                    continue
                # Файл импорта один и начинается с начала встречи.
                self.store.add_audio_chunk(meeting_id, track, str(path), 0.0, duration)
            except Exception:
                log.exception("Не удалось закрыть дорожку импорта %s", track)

    def _import_finished(self, meeting_id: str, duration: float) -> None:
        """Файл дочитан: дождаться распознавания и закрыть встречу."""
        self._close_import_audio(meeting_id)
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

    # --- саммари и чат ----------------------------------------------------

    def llm_status(self) -> dict[str, Any]:
        return self.llm.status()

    def download_llm(self) -> dict[str, Any]:
        """Скачать веса модели в фоне, отчитываясь в UI."""

        def progress(name: str, done: int, total: int) -> None:
            bus.emit(
                LLM_DOWNLOAD,
                {"file": name, "bytes": done, "total": total, "state": "downloading"},
            )

        def finished(error: str | None) -> None:
            bus.emit(
                LLM_DOWNLOAD,
                {"state": "error" if error else "ready", "message": error or ""},
            )

        self.llm.download(progress, finished)
        return self.llm.status()

    def cancel_llm_download(self) -> dict[str, Any]:
        self.llm.cancel_download()
        return self.llm.status()

    def _ensure_llm_model(self) -> None:
        """Дождаться весов, скачав их при необходимости.

        Первый запрос к модели упирается в полтора гигабайта загрузки.
        Молчать всё это время нельзя: без прогресса это читается как
        зависание, и человек закроет программу на полпути.
        """
        if self.llm.status().get("model_ready"):
            return
        shown = -1

        def progress(name: str, done: int, total: int) -> None:
            nonlocal shown
            if not total:
                return
            percent = done * 100 // total
            # Событие на каждые 64 КБ забьёт мост в окно бесполезной
            # работой: глазу хватает целых процентов.
            if percent == shown:
                return
            shown = percent
            bus.emit(
                LLM_DOWNLOAD,
                {"file": name, "bytes": done, "total": total,
                 "percent": percent, "state": "downloading"},
            )

        self.llm.ensure_model(progress)
        bus.emit(LLM_DOWNLOAD, {"state": "ready", "message": ""})

    def save_llm_settings(self, **fields: Any) -> dict[str, Any]:
        """Сохранить настройки модели.

        Смена бэкенда гасит локальный сервер: держать его в памяти после
        переезда в облако незачем, а при возврате он поднимется заново.
        """
        cfg = self.settings.llm
        backend_was = cfg.backend
        for key, value in fields.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        settings_mod.save(self.settings)
        if cfg.backend != backend_was:
            self.llm.shutdown()
        return self.llm_status()

    def check_llm(self) -> dict[str, Any]:
        """Проверить связь с моделью по кнопке в настройках."""
        try:
            self._ensure_llm_model()
            return self.llm.client().check()
        except LlmError as exc:
            return {"ok": False, "error": str(exc)}

    def transcript_text(self, meeting_id: str) -> str:
        """Расшифровка в виде «Кто: что».

        Имя говорящего важнее дорожки: модели нужно понять, кто на себя
        что взял, а «микрофон» и «системный звук» ей об этом не скажут.
        """
        lines: list[str] = []
        for seg in self.store.list_segments(meeting_id):
            text = seg.text.strip()
            if not text:
                continue
            if seg.doubtful:
                # Человек сказал, что это был чужой ролик, а не
                # собеседник. В расшифровке реплика остаётся, а в
                # пересказ не идёт: иначе модель добросовестно превратит
                # озвучку ролика в «решения встречи».
                continue
            who = seg.voice_label.strip()
            if not who:
                who = "Я" if seg.speaker == Speaker.ME else "Собеседник"
            lines.append(f"{who}: {text}")
        return "\n".join(lines)

    def generate_summary(self, meeting_id: str) -> dict[str, Any]:
        """Запустить синтез заметок. Возвращается сразу, текст идёт событиями."""
        if not self.llm.enabled:
            return {"ok": False, "error": self._msg("python.summary.disabled")}
        with self._llm_lock:
            if self._llm_busy:
                return {"ok": False, "error": self._msg("python.summary.busy")}
            self._llm_busy = True
            self._llm_cancel = False

        thread = threading.Thread(
            target=self._run_summary, args=(meeting_id,),
            name="summary", daemon=True,
        )
        thread.start()
        return {"ok": True, "started": True}

    def _run_summary(self, meeting_id: str) -> None:
        try:
            meeting = self.store.get_meeting(meeting_id)
            if meeting is None:
                bus.emit(SUMMARY_ERROR, {"meeting_id": meeting_id,
                                         "error": self._msg("python.summary.meeting_not_found")})
                return

            transcript = self.transcript_text(meeting_id)
            if not transcript.strip() and not meeting.notes.strip():
                bus.emit(SUMMARY_ERROR, {"meeting_id": meeting_id,
                                         "error": self._msg("python.summary.nothing")})
                return

            messages = summary_messages(
                title=meeting.title,
                transcript=transcript,
                notes=meeting.notes,
                template=self.settings.llm.template or meeting.template,
            )

            def on_chunk(piece: str) -> None:
                bus.emit(SUMMARY_CHUNK, {"meeting_id": meeting_id, "text": piece})

            self._ensure_llm_model()
            client = self.llm.client()
            if not fits(transcript, TRANSCRIPT_BUDGET):
                # Полуторачасовая встреча в окно не влезает, и сервер
                # отвечает отказом. Разбираем по частям, а потом сводим:
                # человеку это видно только как чуть более долгое ожидание.
                messages = self._summary_by_parts(
                    meeting, transcript, client, meeting_id
                )

            text = client.stream(
                messages, on_chunk=on_chunk, should_stop=lambda: self._llm_cancel
            )
            # Пустой результат не затирает прежнее саммари: человек мог
            # прервать генерацию, и терять готовый текст обиднее всего.
            if text.strip():
                self.store.update_meeting(meeting_id, summary=text.strip())
            bus.emit(SUMMARY_READY, {"meeting_id": meeting_id, "summary": text.strip()})
            bus.emit(MEETINGS_CHANGED)
        except LlmError as exc:
            bus.emit(SUMMARY_ERROR, {"meeting_id": meeting_id, "error": str(exc)})
        except Exception:
            log.exception("Синтез заметок упал")
            bus.emit(SUMMARY_ERROR, {"meeting_id": meeting_id,
                                     "error": self._msg("python.summary.error")})
        finally:
            with self._llm_lock:
                self._llm_busy = False

    def _summary_by_parts(self, meeting, transcript, client, meeting_id):
        """Разобрать длинную встречу по частям и вернуть запрос на сводку.

        Каждая часть превращается в черновик, и уже черновики сводятся в
        готовые заметки. Прогресс показываем словами: молчание на две
        минуты человек читает как зависание.
        """
        parts = split_transcript(transcript, TRANSCRIPT_BUDGET)
        log.info("Встреча длинная, разбираем по частям: %d", len(parts))
        drafts: list[str] = []
        for i, part in enumerate(parts, 1):
            if self._llm_cancel:
                break
            bus.emit(SUMMARY_STATUS, {
                "meeting_id": meeting_id,
                "text": f"Встреча длинная, разбираем часть {i} из {len(parts)}…",
            })
            drafts.append(client.complete(chunk_messages(part), max_tokens=700))

        bus.emit(SUMMARY_STATUS, {"meeting_id": meeting_id,
                                  "text": "Сводим части вместе…"})
        return merge_messages(
            title=meeting.title,
            drafts=drafts,
            notes=meeting.notes,
            template=self.settings.llm.template or meeting.template,
        )

    def _chat_transcript(self, meeting_id: str, summary: str) -> str:
        """Расшифровка для чата, урезанная под окно контекста.

        В чате места меньше, чем в саммари: туда же идут заметки и вся
        переписка. Если встреча не влезает, берём её конец: обычно
        спрашивают про договорённости, а они звучат ближе к концу.
        Начало при этом не теряется совсем, потому что саммари уже
        лежит в том же запросе.
        """
        text = self.transcript_text(meeting_id)
        budget = CHAT_BUDGET - estimate_tokens(summary or "")
        if fits(text, budget):
            return text
        parts = split_transcript(text, budget)
        log.info("Расшифровка не влезла в чат, берём последнюю часть из %d", len(parts))
        return ("(начало встречи опущено, оно есть в заметках выше)\n\n"
                + parts[-1])

    def stop_generation(self) -> dict[str, Any]:
        """Прервать генерацию: ответ уже не нужен или пошёл не туда."""
        self._llm_cancel = True
        return {"ok": True}

    def list_chat_messages(self, meeting_id: str) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.store.list_chat_messages(meeting_id)]

    def clear_chat(self, meeting_id: str) -> dict[str, Any]:
        self.store.clear_chat(meeting_id)
        return {"ok": True}

    def ask(self, meeting_id: str, question: str) -> dict[str, Any]:
        """Вопрос по встрече. Ответ приходит событиями по кускам."""
        question = (question or "").strip()
        if not question:
            return {"ok": False, "error": self._msg("python.chat.empty_question")}
        if not self.llm.enabled:
            return {"ok": False, "error": self._msg("python.chat.disabled")}
        with self._llm_lock:
            if self._llm_busy:
                return {"ok": False, "error": self._msg("python.chat.busy")}
            self._llm_busy = True
            self._llm_cancel = False

        # Вопрос кладём в базу сразу, до ответа: если модель не ответит,
        # человек хотя бы увидит, о чём спрашивал.
        asked = self.store.add_chat_message(
            ChatMessage(meeting_id=meeting_id, role="user", text=question)
        )
        answer = self.store.add_chat_message(
            ChatMessage(meeting_id=meeting_id, role="assistant", text="")
        )
        bus.emit(CHAT_MESSAGE, {"meeting_id": meeting_id, "message": asked.to_dict()})
        bus.emit(CHAT_MESSAGE, {"meeting_id": meeting_id, "message": answer.to_dict()})

        thread = threading.Thread(
            target=self._run_chat, args=(meeting_id, question, answer.id),
            name="chat", daemon=True,
        )
        thread.start()
        return {"ok": True, "message_id": answer.id}

    def _run_chat(self, meeting_id: str, question: str, answer_id: str) -> None:
        try:
            meeting = self.store.get_meeting(meeting_id)
            if meeting is None:
                bus.emit(CHAT_ERROR, {"meeting_id": meeting_id,
                                      "error": self._msg("python.chat.meeting_not_found")})
                return

            # История без нашей пустой заготовки под ответ: модель не
            # должна видеть пустую реплику ассистента в конце.
            history = [
                {"role": m.role, "content": m.text}
                for m in self.store.list_chat_messages(meeting_id)
                if m.id != answer_id and m.text.strip()
            ][:-1]

            messages = chat_messages(
                title=meeting.title,
                transcript=self._chat_transcript(meeting_id, meeting.summary),
                history=history,
                question=question,
                summary=meeting.summary,
            )

            def on_chunk(piece: str) -> None:
                bus.emit(CHAT_CHUNK, {"meeting_id": meeting_id,
                                      "message_id": answer_id, "text": piece})

            self._ensure_llm_model()
            text = self.llm.client().stream(
                messages, on_chunk=on_chunk, should_stop=lambda: self._llm_cancel
            )
            self.store.update_chat_message(answer_id, text.strip())
            bus.emit(CHAT_MESSAGE, {
                "meeting_id": meeting_id,
                "message": {"id": answer_id, "meeting_id": meeting_id,
                            "role": "assistant", "text": text.strip()},
                "done": True,
            })
        except LlmError as exc:
            self.store.update_chat_message(answer_id, "")
            bus.emit(CHAT_ERROR, {"meeting_id": meeting_id,
                                  "message_id": answer_id, "error": str(exc)})
        except Exception:
            log.exception("Ответ на вопрос по встрече упал")
            self.store.update_chat_message(answer_id, "")
            bus.emit(CHAT_ERROR, {"meeting_id": meeting_id,
                                  "message_id": answer_id,
                                  "error": self._msg("python.chat.error")})
        finally:
            with self._llm_lock:
                self._llm_busy = False

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

    def set_language(self, language: str) -> str:
        """Сохранить язык интерфейса."""
        if language not in ("ru", "en", "es", "sr"):
            language = "ru"
        self.settings.language = language
        settings_mod.save(self.settings)
        return language

    def get_i18n_dict(self, language: str) -> dict[str, Any]:
        """Отдать словарь переводов интерфейса.

        Фронт не может прочитать web/i18n/*.json сам: под pywebview
        страница открыта с file://, а fetch() к соседнему файлу там
        режется как кросс-origin запрос и тихо проваливается. Поэтому
        словарь идёт через мост, как и остальные данные.
        """
        import json as _json

        if language not in ("ru", "en", "es", "sr"):
            language = "ru"
        path = paths.web_dir() / "i18n" / f"{language}.json"
        try:
            return _json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            log.exception("Не удалось прочитать словарь перевода %s", language)
            return {}

    def _msg(self, key: str, **vars: object) -> str:
        """Перевести пользовательское сообщение на текущий язык интерфейса."""
        return _t(key, self.settings.language, **vars)

    def set_sidebar_width(self, width: int) -> int:
        """Запомнить ширину боковой колонки.

        Границы проверяем здесь тоже: файл настроек могли править
        руками, а колонка в три пикселя делает окно нерабочим.
        """
        value = max(150, min(420, int(width)))
        self.settings.window.sidebar_width = value
        settings_mod.save(self.settings)
        return value

    def set_check_updates(self, enabled: bool) -> bool:
        """Включить или выключить ежедневную проверку новой версии."""
        self.settings.check_updates = bool(enabled)
        settings_mod.save(self.settings)
        return self.settings.check_updates

    def mark_version_checked(self) -> None:
        """Отметить, что версию только что смотрели."""
        self.settings.last_version_check = time.time()
        settings_mod.save(self.settings)

    def set_auto_update(self, enabled: bool) -> bool:
        """Разрешить или запретить самостоятельную установку обновлений."""
        self.settings.auto_update = bool(enabled)
        settings_mod.save(self.settings)
        return self.settings.auto_update

    def save_window_geometry(self, x: int, y: int, width: int, height: int) -> None:
        self.settings.window.x = int(x)
        self.settings.window.y = int(y)
        self.settings.window.width = int(width)
        self.settings.window.height = int(height)
        settings_mod.save(self.settings)

    def shutdown(self) -> None:
        if self.capture.is_recording:
            self.stop_recording()
        # Клавиша диктовки перехватывает ввод глобально. Не сняв
        # перехват, мы оставили бы висеть чужой обработчик клавиатуры
        # после закрытия программы.
        self.остановить_диктовку()
        # Недосчитанный ответ всё равно некому показать.
        self._llm_cancel = True
        self.llm.shutdown()
        # Разбор файлов может идти долго: при выходе бросаем его, а
        # недоделанные встречи остаются с тем, что успели распознать.
        self.importer.stop()
        # Перепроверка версии больше не нужна: программа закрывается.
        self._version_checker.stop()
        settings_mod.save(self.settings)
        self.store.close()


def _read_wav_part(path: Path, offset: float, length: float) -> tuple[bytes, int]:
    """Прочитать кусок WAV с нужной секунды, не читая файл целиком.

    Двухчасовая запись весит сотни мегабайт, и читать её ради трёх секунд
    было бы и медленно, и по памяти расточительно. `setpos` переставляет
    чтение сразу на нужный кадр.
    """
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        total = w.getnframes()
        start = min(total, max(0, int(offset * rate)))
        count = max(0, min(total - start, int(length * rate)))
        w.setpos(start)
        raw = w.readframes(count)
    if channels > 1:
        # Дорожки пишем в моно, но чужой файл может оказаться стерео.
        data = np.frombuffer(raw, dtype=np.int16).reshape(-1, channels)
        raw = data.mean(axis=1).astype(np.int16).tobytes()
    return raw, rate


def _default_title() -> str:
    import datetime

    return "Встреча " + datetime.datetime.now().strftime("%d.%m %H:%M")
