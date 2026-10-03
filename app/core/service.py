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
import re
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
from ..search import meaning as meaning_mod
from ..search import ranking
from ..search.model import MODEL_DIR_NAME as MEANING_DIR_NAME
from ..search.model import MODEL_FILES as MEANING_FILES
from ..search.model import MODEL_REPO as MEANING_REPO
from ..search.model import MODEL_TOTAL_BYTES as MEANING_TOTAL_BYTES
from ..search.model import MeaningModel
from ..audio import AudioCapture, NullCapture, WasapiCapture
from ..audio import devices as audio_devices
from ..audio.buffers import WavWriter, float_to_int16
from ..audio.сторож import Сторож
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
    TELEGRAM_CHANGED,
    MEETINGS_CHANGED,
    MEETING_UPDATED,
    MEANING_STATE,
    MODEL_DOWNLOAD,
    NEW_VERSION,
    RECOGNITION_BACKFILL,
    RECORDING_ERROR,
    RECORDING_STARTED,
    RECORDING_STOPPED,
    SUMMARY_CHUNK,
    ANALYSIS_CHUNK,
    ANALYSIS_ERROR,
    ANALYSIS_READY,
    TRIAL_BLOCKED,
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
from ..llm import context as chat_context
from . import personal
from ..llm.local import tier_or_default
from ..llm.chunking import fits, split_transcript
from ..llm.prompts import chunk_messages, merge_messages
from ..llm import calc, lenses
from . import analysis as analysis_mod
from ..storage import Store

log = logging.getLogger(__name__)


def total_ram_gb() -> float:
    """Сколько памяти у компьютера, в гигабайтах. 0 — не удалось узнать.

    Нужна, чтобы честно предупредить: мощная модель на восьми гигабайтах
    не поднимется или загонит компьютер в подкачку.
    """
    try:
        if os.name == "nt":
            import ctypes

            class _Mem(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            m = _Mem()
            m.dwLength = ctypes.sizeof(_Mem)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return round(m.ullTotalPhys / (1 << 30), 1)
            return 0.0
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1 << 30), 1)
    except Exception:
        return 0.0

# Сколько токенов отдаём под расшифровку. Окно модели 32k, но в нём
# живёт ещё и ответ, и промпт, и заметки человека. Цифры взяты с
# запасом: отказ сервера хуже, чем лишняя часть при разборе.
TRANSCRIPT_BUDGET = 24000
# Как спрашивать свою модель в чате. Два открытия на копии живой базы.
#
# Qwen3 сначала «думает» вслух и только потом отвечает: в чате это
# десять секунд тишины на «1+6». Без раздумий ответ приходит сразу.
#
# С прежней температурой 0.3 и без штрафа за повтор модель на длинном
# тексте повторяла вопрос эхом: «кто что взял на себя?» → «Кто что взял
# на себя?». С параметрами, которые советуют сами авторы Qwen3 для
# режима без раздумий, она отвечает по существу. Только для своей
# модели: чужой сервер незнакомые поля может и отвергнуть.
CHAT_LOCAL_OPTIONS = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "presence_penalty": 1.5,
    "chat_template_kwargs": {"enable_thinking": False},
}

# Пределы масштаба интерфейса. Мельче 70% буквы уже не читаются, крупнее
# 200% в окно при самом себе не влезает список встреч с заметками.
# Те же числа в web/app.js (ZOOM_MIN, ZOOM_MAX).
UI_ZOOM_MIN = 0.7
UI_ZOOM_MAX = 2.0


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
        # Сторож: слушает системный звук, пока запись не идёт. Ради
        # самой обидной потери — забыл нажать «запись», поговорил час.
        # Ничего не пишет на диск: только замечает разговор и предлагает.
        # Поднимается не здесь, а после старта окна: до него некому
        # показать вопрос, да и открывать звуковую карту раньше времени
        # незачем.
        self.сторож = Сторож(
            loopback_device_id=self.settings.audio.loopback_device_id or None,
            включён=self.settings.внимание.сторож,
        )
        # Разбор готовых записей. Поток поднимается только когда человек
        # действительно бросит файлы в окно.
        self.importer = ImportQueue(
            transcribe_chunk=self._import_chunk,
            create_meeting=self._import_meeting,
            finish_meeting=self._import_finished,
            prepare_meeting=self._prepare_voices,
            on_change=self._on_import_change,
            fetch_link=self._fetch_link,
        )
        self.downloader = ModelDownloader(
            MODEL_REPO, MODEL_FILES, paths.models_dir() / MODEL_DIR_NAME
        )
        self._убрать_прошлую_модель()
        # Поиск по смыслу. Модель своя, 135 МБ, и качается отдельно от
        # распознавания: без неё поиск по словам работает как прежде.
        self.meaning_model = MeaningModel(paths.models_dir() / MEANING_DIR_NAME)
        self.meaning = meaning_mod.MeaningIndex(
            self.store, self.meaning_model, who=self._кто_сказал,
            on_done=lambda: bus.emit(MEANING_STATE, self.meaning_status()),
        )
        self.meaning_downloader = ModelDownloader(
            MEANING_REPO, MEANING_FILES, paths.models_dir() / MEANING_DIR_NAME
        )
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
        # Смысл готовим следом за распознаванием и тоже фоном: модель
        # качается один раз, а указатель досчитывает только изменившееся.
        self._prepare_meaning()
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

    # --- диктовка в само окно Konspekt ------------------------------------
    #
    # Обычная диктовка печатает в чужое приложение: человек стоит в поле
    # Telegram и говорит. Здесь наоборот — текст нужен нашему же окну, в
    # поле вопроса к встрече. Вставлять его через клавиатуру нельзя:
    # webview примет ввод только если окно в фокусе, а человек в этот
    # момент может смотреть на Zoom. Поэтому распознаём и возвращаем
    # строку, а положит её в поле само окно (задача №26).

    def начать_диктовку_в_окно(self) -> dict:
        """Начать слушать для поля ввода. Причина отказа — в ответе."""
        служба = self._служба_диктовки()
        if служба is None:
            return {"ok": False, "причина": "диктовка выключена в настройках"}
        беда = служба._почему_нельзя()
        if беда:
            # Честно говорим, почему нельзя. Мёртвая кнопка без
            # объяснения читается как поломка программы.
            return {"ok": False, "причина": беда}
        return {"ok": bool(служба.начать())}

    def закончить_диктовку_в_окно(self) -> dict:
        """Остановить, распознать и вернуть текст окну, не вставляя его."""
        служба = self._служба_диктовки()
        if служба is None:
            return {"ok": False, "текст": "", "причина": "диктовка не запущена"}
        текст = служба.распознать_для_окна()
        return {"ok": True, "текст": текст}

    def отменить_диктовку_в_окно(self) -> dict:
        """Бросить запись: человек передумал."""
        служба = self._служба_диктовки()
        if служба is not None:
            служба.отменить()
        return {"ok": True}

    def _служба_диктовки(self):
        """Служба диктовки, подняв её при необходимости.

        Диктовка в окно не зависит от горячей клавиши: человек мог
        выключить клавишу, но кнопка в поле ввода должна работать.
        """
        if self.диктовка is not None:
            return self.диктовка
        настройки = getattr(self.settings, "dictation", None)
        if настройки is None or not настройки.enabled:
            return None
        from ..dictation.служба import СлужбаДиктовки

        self.диктовка = СлужбаДиктовки(self)
        return self.диктовка

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

    def _prepare_meaning(self) -> None:
        """Скачать модель смысла, если её нет, и поднять пересчёт указателя.

        Качаем без спроса, как и модель распознавания: поиск по смыслу
        это часть обычного поиска, а не отдельная функция, которую
        человек должен найти и включить. Отказаться можно тем же
        KONSPEKT_NO_PREFETCH, что и у распознавания: проверкам сеть не
        нужна.
        """
        if os.environ.get("KONSPEKT_NO_PREFETCH") == "1":
            # Если модель уже на диске, указатель поднимаем и без сети.
            if self.meaning_model.is_downloaded():
                self.meaning.start()
            return

        def run() -> None:
            if not self.meaning_model.is_downloaded():
                try:
                    self._download_meaning()
                except Exception:
                    # Не вышло — не беда: поиск по словам работает, а
                    # скачать попробуем при следующем запуске.
                    log.exception("Модель поиска по смыслу не скачалась")
                    return
            self.meaning.start()
            bus.emit(MEANING_STATE, self.meaning_status())

        threading.Thread(target=run, name="meaning-prefetch", daemon=True).start()

    def _download_meaning(self) -> None:
        shown = -1

        def progress(name: str, done: int, total: int) -> None:
            nonlocal shown
            percent = done * 100 // MEANING_TOTAL_BYTES
            if percent != shown:
                shown = percent
                bus.emit(MEANING_STATE, {**self.meaning_status(), "percent": percent})

        log.info("Качаем модель поиска по смыслу")
        try:
            self.meaning_downloader.run_blocking(progress)
        except DownloadBusy:
            log.info("Модель смысла качает другой экземпляр программы")
            raise
        log.info("Модель поиска по смыслу скачана")

    def meaning_status(self) -> dict[str, Any]:
        """Готов ли поиск по смыслу: окну нужно это, чтобы не обещать зря."""
        return {
            **self.meaning.status(),
            "downloading": self.meaning_downloader.is_running
                           or (not self.meaning_model.is_downloaded()
                               and self.meaning_downloader.downloaded_bytes() > 0),
            "bytes": self.meaning_downloader.downloaded_bytes(),
            "total_bytes": MEANING_TOTAL_BYTES,
        }

    def _кто_сказал(self, seg: TranscriptSegment) -> str:
        """Подпись говорящего у цитаты: та же, что у поиска по словам."""
        return seg.voice_label or ("Я" if seg.speaker == Speaker.ME else "Собеседник")

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
        папки = self.store.meeting_folders()
        out = []
        for m in self.store.list_meetings():
            d = m.to_dict()
            d["folder_id"] = папки.get(m.id)
            out.append(d)
        return out

    def create_meeting(self, title: str | None = None, folder_id: str | None = None) -> dict[str, Any]:
        """Новая встреча. С folder_id — сразу в папке: «+» у папки."""
        meeting = Meeting(title=title or _default_title())
        self.store.create_meeting(meeting)
        d = meeting.to_dict()
        d["folder_id"] = None
        if folder_id and self.store.set_meeting_folder(meeting.id, folder_id):
            d["folder_id"] = folder_id
        bus.emit(MEETINGS_CHANGED)
        return d

    # --- папки ------------------------------------------------------------

    def list_folders(self) -> list[dict[str, Any]]:
        return self.store.list_folders()

    @staticmethod
    def _folder_name(name: str | None) -> str:
        # Имя не пустое и без переносов: оно стоит строкой в списке.
        return " ".join((name or "").split())[:80]

    def create_folder(self, name: str | None = None) -> dict[str, Any]:
        folder = self.store.create_folder(self._folder_name(name) or self._msg("python.folder.new"))
        folder["count"] = 0
        bus.emit(MEETINGS_CHANGED)
        return folder

    def rename_folder(self, folder_id: str, name: str) -> dict[str, Any]:
        имя = self._folder_name(name)
        if not имя:
            return {"ok": False}
        ok = self.store.rename_folder(folder_id, имя)
        if ok:
            bus.emit(MEETINGS_CHANGED)
        return {"ok": ok, "name": имя}

    def delete_folder(self, folder_id: str) -> dict[str, Any]:
        """Удалить папку. Встречи не удаляются, а возвращаются в общий список."""
        ok = self.store.delete_folder(folder_id)
        if ok:
            bus.emit(MEETINGS_CHANGED)
        return {"ok": ok}

    def move_meeting(self, meeting_id: str, folder_id: str | None) -> dict[str, Any]:
        """Перенести встречу в папку или вынуть из неё (folder_id пустой)."""
        ok = self.store.set_meeting_folder(meeting_id, folder_id or None)
        if ok:
            bus.emit(MEETINGS_CHANGED)
        return {"ok": ok, "folder_id": folder_id or None}

    def set_collapsed_folders(self, ids: list[str]) -> list[str]:
        """Какие папки свёрнуты. Помним между запусками, как свёрнутые заметки."""
        self.settings.collapsed_folders = [str(i) for i in (ids or [])][:500]
        settings_mod.save(self.settings)
        return self.settings.collapsed_folders

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
        data["folder_id"] = self.store.meeting_folders().get(meeting_id)
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
        # Куски встречи ушли из базы вместе с ней, а матрица в памяти
        # ещё помнит их: без пересчёта удалённое продолжало бы находиться.
        self.meaning.forget(meeting_id)
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
        """Поиск по расшифровкам: по словам и по смыслу, одним списком.

        Раньше встреча находилась, только если в одной реплике были все
        слова запроса, включая «за» и «что». На «выручка за август что
        там по продажам» не находилось ничего, хотя разговор о выручке и
        продажах был. Теперь хватает одного значимого слова, а порядок
        решает, сколько слов сошлось и насколько встреча близка по
        смыслу (app/search/ranking.py).

        На встречу отдаём несколько лучших цитат: одна встреча не должна
        забивать выдачу десятком совпадений.
        """
        осн = ranking.основы(query)
        строки = self.store.search_stems(осн) if осн else []
        for строка in строки:
            строка["who"] = строка["voice_label"] or (
                "Я" if строка["speaker"] == "me" else "Собеседник")
        # Смысл только добавляет: сломайся модель, человек получит то,
        # что нашлось словами.
        try:
            by_meaning = self.meaning.search(query)
        except Exception:
            log.exception("Поиск по смыслу не удался, отдаём найденное словами")
            by_meaning = []
        return ranking.rank(осн, строки, by_meaning)

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

        if not self._can_record(meeting_id):
            # Пробный период кончился. Пишем дальше только встречу, которая
            # уже в счёте: её дописывают, а не начинают новую.
            bus.emit(TRIAL_BLOCKED, {
                "action": "record",
                "message": self._msg("python.trial.record", limit=self._trial_limit()),
            })
            return None

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
        # Сторож отпускает звуковую карту: дальше за системным звуком
        # следит сама дорожка записи. Два потока, читающих одно
        # устройство, мешают друг другу, а вопрос «не записать ли» во
        # время записи звучал бы просто глупо.
        try:
            self.сторож.остановить()
        except Exception:
            log.exception("Не удалось остановить сторожа перед записью")
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
            # Запись не пошла, значит сторожу снова есть что слушать.
            self.поднять_сторожа()
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
        # Новую встречу должно быть видно и поиску по смыслу, а не только
        # поиску по словам, который узнаёт о репликах сам, триггером.
        self.meaning.refresh()
        # Встреча кончилась, карта свободна: сторож снова слушает, не
        # начался ли следующий разговор. Иначе слежка работала бы ровно
        # до первой записи за запуск программы.
        self.поднять_сторожа()
        return self.get_meeting(meeting_id)

    def поднять_сторожа(self) -> None:
        """Вернуть слежку за системным звуком, если она включена.

        Отдельным методом, потому что зовётся из трёх мест: после
        остановки записи, после неудачного старта и при изменении
        настроек. Поломка сторожа не имеет права мешать записи, поэтому
        всё внутри try.
        """
        try:
            if self.settings.внимание.сторож:
                self.сторож.запустить()
        except Exception:
            log.exception("Не удалось поднять сторожа системного звука")

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

        Ничего не меняем, только затыкаем наблюдателя. Не навсегда: он
        замолчит до тех пор, пока этот звук не сменится тишиной. Ответ
        относится к тому, что играет сейчас, а не ко всему, что зазвучит
        до конца встречи.
        """
        замолчать = getattr(self.capture, "замолчать_про_чужую_речь", None)
        if замолчать is not None:
            try:
                замолчать()
            except Exception:
                log.exception("Не удалось отключить вопрос про системный звук")
                return {"ok": False}
        return {"ok": True}

    # --- сторож: разговор при выключенной записи ---------------------------

    def настройки_внимания(self) -> dict[str, Any]:
        """Что сейчас со слежкой за звуком и с уведомлениями."""
        в = self.settings.внимание
        return {
            "сторож": в.сторож,
            "уведомления": в.уведомления,
            # Включить мало: карту мог занять кто-то другой. Молчать об
            # этом нельзя, иначе человек будет думать, что слежка
            # работает, и понадеется на неё.
            "работает": bool(getattr(self.сторож, "работает", False)),
            "проблема": getattr(self.сторож, "ошибка", None),
        }

    def сохранить_внимание(self, **поля: Any) -> dict[str, Any]:
        """Сохранить настройки внимания и применить их сразу.

        Именно сразу: человек снял галочку, чтобы программа перестала
        лезть, и ждать перезапуска ради этого было бы издевательством.
        """
        в = self.settings.внимание
        for ключ, значение in поля.items():
            if hasattr(в, ключ):
                setattr(в, ключ, bool(значение))
        try:
            settings_mod.save(self.settings)
        except Exception:
            log.exception("Не удалось сохранить настройки внимания")

        try:
            # Во время записи сторож обязан молчать в любом случае:
            # карта занята дорожкой.
            self.сторож.настроить(
                включён=в.сторож and not self.capture.is_recording,
                loopback_device_id=self.settings.audio.loopback_device_id or None,
            )
        except Exception:
            log.exception("Не удалось применить настройки сторожа")
        return self.настройки_внимания()

    def записать_замеченный_разговор(self) -> dict[str, Any]:
        """Ответ «да, записывай»: начать встречу прямо сейчас.

        Сторож заметил разговор при выключенной записи. Человек согласился,
        и дальше всё как при обычном нажатии кнопки: заводим встречу и
        пишем. Момент начала разговора уже упущен — звук мы не сохраняли,
        и делать вид, что первые полминуты у нас есть, нечестно.
        """
        try:
            self.сторож.замолчать()
        except Exception:
            log.exception("Не удалось успокоить сторожа")
        встреча = self.start_recording()
        if встреча is None:
            return {"ok": False}
        return {"ok": True, "meeting": встреча}

    def не_записывать_замеченный_разговор(self) -> dict[str, Any]:
        """Ответ «не надо»: молчим, пока этот разговор не сменится тишиной.

        Не навсегда. Человек отказался записывать нынешний подкаст, а не
        отменил слежку: следующая встреча через час — это другой разговор,
        и промолчать о ней значило бы вернуть ровно ту потерю, ради
        которой сторож и заведён. Совсем выключается галочкой в настройках.
        """
        try:
            self.сторож.замолчать()
        except Exception:
            log.exception("Не удалось успокоить сторожа")
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

    def import_files(self, paths: list[str], folder_id: str | None = None) -> list[dict[str, Any]]:
        """Поставить готовые записи в очередь разбора.

        На каждый файл заводится своя встреча, названная по имени файла:
        пачка записей превращается в пачку встреч, а не в одну кашу.
        """
        if not paths:
            return []
        свободно = self._trial_left()
        if свободно is not None and свободно < len(paths):
            # Загружаем столько, сколько осталось пробных встреч, и честно
            # говорим, сколько не влезло. Молча проглотить пачку файлов
            # хуже: человек будет ждать, что они появятся.
            всего = len(paths)
            paths = list(paths)[:свободно]
            bus.emit(TRIAL_BLOCKED, {
                "action": "import",
                "message": (self._msg("python.trial.import_part", n=len(paths), total=всего)
                            if paths else self._msg("python.trial.record", limit=self._trial_limit())),
            })
            if not paths:
                return []
        tasks = self.importer.add(paths, folder_id)
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

    def _import_meeting(self, title: str, url: str = "", folder_id: str | None = None) -> str:
        """Встреча под импортируемый файл.

        Помечаем её как идущую обработку: в списке сразу видно, что
        транскрипт ещё дописывается. У записи по ссылке ссылка ложится
        в пометки: откуда запись, должно быть видно и через год.
        """
        meeting = Meeting(title=title or _default_title(), status=MeetingStatus.PROCESSING)
        meeting.started_at = now()
        if url:
            meeting.notes = url
        self.store.create_meeting(meeting)
        if folder_id:
            self.store.set_meeting_folder(meeting.id, folder_id)
        bus.emit(MEETINGS_CHANGED)
        return meeting.id

    @staticmethod
    def _fetch_link(url: str, папка, прогресс, стоп):
        from . import link as link_mod

        return link_mod.fetch(url, папка, прогресс, стоп)

    def import_link(self, text: str, folder_id: str | None = None) -> dict[str, Any]:
        """Расшифровать запись по ссылке: поставить её в очередь разбора."""
        from . import link as link_mod

        url = link_mod.normalize(text)
        if url is None:
            return {"ok": False, "error": self._msg("python.link.not_link")}
        if self._trial_left() == 0:
            сообщение = self._msg("python.trial.record", limit=self._trial_limit())
            bus.emit(TRIAL_BLOCKED, {"action": "import", "message": сообщение})
            return {"ok": False, "error": сообщение, "trial": True}
        task = self.importer.add_link(url, folder_id or None)
        bus.emit(IMPORT_CHANGED, {"tasks": self.importer.tasks()})
        return {"ok": True, "task": task}

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
        # Загруженную запись тоже должно быть видно поиску по смыслу.
        self.meaning.refresh()

    def _on_import_change(self, task: Any) -> None:
        bus.emit(IMPORT_PROGRESS, {"task": task.to_dict()})

    # --- саммари и чат ----------------------------------------------------

    def llm_status(self) -> dict[str, Any]:
        return {**self.llm.status(), "ram_gb": total_ram_gb()}

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
        tier_was = cfg.local_model
        for key, value in fields.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        # Незнакомую модель не записываем: фронт другой версии или ручная
        # правка не должны оставить программу без модели.
        cfg.local_model = tier_or_default(cfg.local_model)
        settings_mod.save(self.settings)
        if cfg.backend != backend_was or cfg.local_model != tier_was:
            # Сервер держит в памяти прежнюю модель: гасим, следующий
            # вопрос поднимет выбранную.
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
        if not self._can_use_model(meeting_id):
            return {"ok": False, "error": self._msg("python.trial.llm"), "trial": True}
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
                self._for_model(messages), on_chunk=on_chunk,
                should_stop=lambda: self._llm_cancel,
            )
            # Пустой результат не затирает прежнее саммари: человек мог
            # прервать генерацию, и терять готовый текст обиднее всего.
            if text.strip():
                self.store.update_meeting(meeting_id, summary=text.strip())
                # Итоги — одна из карточек разбора: там видно, когда они
                # сделаны и не устарели ли с тех пор.
                self.store.save_analysis(
                    meeting_id, "summary", text.strip(),
                    self.store.meaning_signature(meeting_id), self._model_label(),
                )
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

    # --- разборы по разрезам -------------------------------------------------

    def _model_label(self) -> str:
        """Кто сделал разбор: своя модель по уровню или модель сервера."""
        if self.llm.backend == "remote":
            return f"remote:{self.settings.llm.model or ''}"
        return f"local:{self.llm.tier}"

    def _segments_for_analysis(self, meeting_id: str) -> list[dict[str, Any]]:
        return [
            {"text": s.text, "start": s.start, "end": s.end,
             "who": self._кто_сказал(s), "track": s.speaker.value}
            for s in self.store.list_segments(meeting_id)
            if s.text.strip() and not s.doubtful
        ]

    def list_analyses(self, meeting_id: str) -> list[dict[str, Any]]:
        """Карточки разбора встречи по порядку, с результатами, где они есть.

        Разборы без модели (разговор, тон) считаются тут же: это десятки
        миллисекунд даже на трёхчасовой встрече, и хранить их незачем,
        пока расшифровка та же. Сохраняем, чтобы у карточки было время.
        """
        signature = self.store.meaning_signature(meeting_id)
        готовые = {a["kind"]: a for a in self.store.list_analyses(meeting_id)}
        meeting = self.store.get_meeting(meeting_id)
        # Старые встречи: саммари лежит в поле встречи, а карточки ещё не было.
        if meeting is not None and meeting.summary.strip() and "summary" not in готовые:
            готовые["summary"] = {"kind": "summary", "text": meeting.summary,
                                  "signature": "", "model": "", "created_at": meeting.created_at}
        сегменты = None
        out = []
        for kind in lenses.ПОРЯДОК:
            р = lenses.РАЗРЕЗЫ[kind]
            a = готовые.get(kind)
            if р.engine == "счёт" and (a is None or a["signature"] != signature):
                if сегменты is None:
                    сегменты = self._segments_for_analysis(meeting_id)
                text = self._count_analysis(kind, сегменты)
                if text:
                    self.store.save_analysis(meeting_id, kind, text, signature, "счёт")
                    a = {"kind": kind, "text": text, "signature": signature,
                         "model": "счёт", "created_at": time.time()}
            out.append({
                "kind": kind,
                "engine": р.engine,
                "text": (a or {}).get("text", ""),
                "created_at": (a or {}).get("created_at"),
                "model": (a or {}).get("model", ""),
                # Встреча дополнилась после разбора. У старых саммари без
                # отпечатка не знаем — и не пугаем зря.
                "stale": bool(a and a["signature"] and a["signature"] != signature),
            })
        return out

    @staticmethod
    def _count_analysis(kind: str, segments: list[dict[str, Any]]) -> str:
        if kind == "talk":
            return analysis_mod.разговор(segments)["markdown"]
        if kind == "tone":
            return analysis_mod.тон(segments)["markdown"]
        return ""

    def run_analysis(self, meeting_id: str, kind: str) -> dict[str, Any]:
        """Сделать разбор встречи в разрезе. Итоги идут прежней дорогой."""
        if kind not in lenses.РАЗРЕЗЫ:
            return {"ok": False, "error": self._msg("python.analysis.unknown")}
        if kind == "summary":
            return self.generate_summary(meeting_id)
        р = lenses.РАЗРЕЗЫ[kind]
        if р.engine == "счёт":
            self.list_analyses(meeting_id)
            return {"ok": True, "done": True}
        if not self._can_use_model(meeting_id):
            return {"ok": False, "error": self._msg("python.trial.llm"), "trial": True}
        if not self.llm.enabled:
            return {"ok": False, "error": self._msg("python.summary.disabled")}
        with self._llm_lock:
            if self._llm_busy:
                return {"ok": False, "error": self._msg("python.summary.busy")}
            self._llm_busy = True
            self._llm_cancel = False
        threading.Thread(
            target=self._run_analysis, args=(meeting_id, kind),
            name=f"analysis-{kind}", daemon=True,
        ).start()
        return {"ok": True, "started": True}

    def _run_analysis(self, meeting_id: str, kind: str) -> None:
        try:
            meeting = self.store.get_meeting(meeting_id)
            if meeting is None:
                bus.emit(ANALYSIS_ERROR, {"meeting_id": meeting_id, "kind": kind,
                                          "error": self._msg("python.summary.meeting_not_found")})
                return
            signature = self.store.meaning_signature(meeting_id)
            transcript = self.transcript_text(meeting_id)
            if not transcript.strip():
                bus.emit(ANALYSIS_ERROR, {"meeting_id": meeting_id, "kind": kind,
                                          "error": self._msg("python.summary.nothing")})
                return
            self._ensure_llm_model()
            client = self.llm.client()
            if not fits(transcript, TRANSCRIPT_BUDGET):
                # Длинная встреча: сначала выжимка по частям, как у итогов,
                # и уже по ней разбор в нужном разрезе.
                parts = split_transcript(transcript, TRANSCRIPT_BUDGET)
                drafts = []
                for i, part in enumerate(parts, 1):
                    if self._llm_cancel:
                        break
                    bus.emit(ANALYSIS_CHUNK, {"meeting_id": meeting_id, "kind": kind, "status":
                                              self._msg("python.analysis.part", i=i, n=len(parts))})
                    drafts.append(client.complete(self._for_model(chunk_messages(part)), max_tokens=700))
                transcript = "\n\n".join(f"Часть {i + 1}:\n{d.strip()}" for i, d in enumerate(drafts))

            def on_chunk(piece: str) -> None:
                bus.emit(ANALYSIS_CHUNK, {"meeting_id": meeting_id, "kind": kind, "text": piece})

            text = client.stream(
                self._for_model(lenses.messages(kind, meeting.title, transcript, meeting.notes)),
                on_chunk=on_chunk, should_stop=lambda: self._llm_cancel,
            )
            if text.strip():
                self.store.save_analysis(meeting_id, kind, text.strip(), signature, self._model_label())
            bus.emit(ANALYSIS_READY, {"meeting_id": meeting_id, "kind": kind, "text": text.strip()})
        except LlmError as exc:
            bus.emit(ANALYSIS_ERROR, {"meeting_id": meeting_id, "kind": kind, "error": str(exc)})
        except Exception:
            log.exception("Разбор %s упал", kind)
            bus.emit(ANALYSIS_ERROR, {"meeting_id": meeting_id, "kind": kind,
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
            drafts.append(client.complete(self._for_model(chunk_messages(part)), max_tokens=700))

        bus.emit(SUMMARY_STATUS, {"meeting_id": meeting_id,
                                  "text": "Сводим части вместе…"})
        return merge_messages(
            title=meeting.title,
            drafts=drafts,
            notes=meeting.notes,
            template=self.settings.llm.template or meeting.template,
        )

    def _chat_context(
        self, meeting_id: str, question: str, history: list[dict[str, Any]],
    ) -> chat_context.ChatContext:
        """Что из встречи показать модели в ответ на вопрос.

        Не вся расшифровка, а отрывки к вопросу (см. llm/context.py).
        Векторы кусков берём из указателя поиска по смыслу: они там уже
        посчитаны. Нет модели смысла — ищем по словам.
        """
        segments = [
            {"text": s.text, "start": s.start, "who": self._кто_сказал(s)}
            for s in self.store.list_segments(meeting_id)
            if s.text.strip() and not s.doubtful
        ]
        vectors = None
        embed = None
        if self.meaning_model.is_downloaded():
            vectors = self.store.meeting_meaning(meeting_id, self.meaning_model.name)
            embed = self.meaning_model.query
        ctx = chat_context.build(segments, question, history, vectors, embed)
        log.info(
            "Чат: %s, %d знаков встречи, %d сообщений переписки",
            ("без встречи" if not ctx.attach
             else "вся встреча" if ctx.whole else f"отрывков {len(ctx.starts)}"),
            len(ctx.transcript), len(ctx.history),
        )
        return ctx

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
        if not self._can_use_model(meeting_id) and calc.ответ(question, self._last_answer(meeting_id)) is None:
            # Калькулятор модели не требует: считать можно и без лицензии.
            return {"ok": False, "error": self._msg("python.trial.llm"), "trial": True}
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

            # История без нашей пустой заготовки под ответ и без только
            # что заданного вопроса: он пойдёт отдельно, последним.
            history = [
                {"role": m.role, "content": m.text}
                for m in self.store.list_chat_messages(meeting_id)
                if m.id != answer_id and m.text.strip()
            ][:-1]

            # Пример считаем сами, и знаками, и словами («минус 400»):
            # модель тут угадывает (см. llm/calc.py), а калькулятор верен
            # и мгновенен. Всё, что не пример, calc вернёт None. Считаем
            # до поиска по встрече: для примера он не нужен.
            прошлый = next((h["content"] for h in reversed(history)
                            if h["role"] == "assistant"), "")
            посчитано = calc.ответ(question, прошлый)
            if посчитано is not None:
                bus.emit(CHAT_CHUNK, {"meeting_id": meeting_id,
                                      "message_id": answer_id, "text": посчитано})
                self.store.update_chat_message(answer_id, посчитано)
                bus.emit(CHAT_MESSAGE, {
                    "meeting_id": meeting_id,
                    "message": {"id": answer_id, "meeting_id": meeting_id,
                                "role": "assistant", "text": посчитано},
                    "done": True,
                })
                return
            ctx = self._chat_context(meeting_id, question, history)
            messages = chat_messages(
                title=meeting.title,
                transcript=ctx.transcript,
                history=ctx.history,
                question=question,
                summary=meeting.summary,
                whole=ctx.whole,
                attach=ctx.attach,
            )

            def on_chunk(piece: str) -> None:
                bus.emit(CHAT_CHUNK, {"meeting_id": meeting_id,
                                      "message_id": answer_id, "text": piece})

            self._ensure_llm_model()
            text = self.llm.client().stream(
                self._for_model(messages), on_chunk=on_chunk,
                should_stop=lambda: self._llm_cancel,
                **(CHAT_LOCAL_OPTIONS if self.llm.backend == "local" else {}),
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

    def set_ui_zoom(self, zoom: float) -> float:
        """Запомнить масштаб интерфейса.

        Границы и здесь: файл настроек правят руками, а масштаб 0.1
        делает окно нечитаемым, и вернуть его обратно уже нечем.
        """
        try:
            value = float(zoom)
        except (TypeError, ValueError):
            value = 1.0
        if value != value:  # NaN из битого файла
            value = 1.0
        value = round(max(UI_ZOOM_MIN, min(UI_ZOOM_MAX, value)), 2)
        self.settings.ui_zoom = value
        settings_mod.save(self.settings)
        return value

    def find_personal(self, texts: list[str]) -> list[list[dict[str, Any]]]:
        """Личные данные в каждом тексте, по порядку (см. core/personal.py)."""
        return [
            [н.to_dict() for н in personal.find(т if isinstance(т, str) else "")]
            for т in (texts or [])[:5000]
        ]

    def mask_personal(self, text: str) -> str:
        return personal.mask(text if isinstance(text, str) else "")

    def _for_model(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """Сообщения перед отправкой в модель.

        Своя модель на этом компьютере видит всё: текст никуда не уходит.
        Свой сервер — это уже чужой компьютер, и номера карт, телефоны и
        паспорта туда уходить не должны, если человек не разрешил.

        Скрываем на готовых сообщениях, а не на расшифровке: в запрос
        попадают ещё заметки человека, переписка и прежнее саммари, и
        любое из них может нести тот же номер карты.
        """
        if self.llm.backend != "remote" or not self.settings.llm.mask_personal_remote:
            return messages
        return [{**m, "content": personal.mask(m.get("content") or "")} for m in messages]

    def set_copy_mode(self, mode: str) -> str:
        """Запомнить вид копирования заметок по главной кнопке.

        Незнакомое значение не записываем как есть: фронт другой версии
        или ручная правка файла не должны оставить кнопку без вида.
        """
        value = mode if mode in ("markdown", "plain", "masked") else "markdown"
        self.settings.copy_mode = value
        settings_mod.save(self.settings)
        return value

    def set_summary_collapsed(self, collapsed: bool) -> bool:
        """Запомнить, свёрнуты ли заметки над перепиской."""
        self.settings.summary_collapsed = bool(collapsed)
        settings_mod.save(self.settings)
        return self.settings.summary_collapsed

    # --- лицензия ----------------------------------------------------------

    def _licensed(self) -> bool:
        from . import license as license_mod

        key = self.settings.license_key
        if not key:
            return False
        try:
            license_mod.parse(key)
        except license_mod.LicenseError:
            return False
        return True

    def _trial_limit(self) -> int:
        from . import license as license_mod

        return license_mod.TRIAL_MEETINGS

    def _trial_left(self) -> int | None:
        """Сколько пробных встреч осталось. None — лицензия есть, счёта нет."""
        if self._licensed():
            return None
        from . import license as license_mod

        used = self.store.trial_used(license_mod.TRIAL_MIN_SPEECH_S, license_mod.TRIAL_MEETINGS)
        return max(0, license_mod.TRIAL_MEETINGS - used)

    def _can_record(self, meeting_id: str | None) -> bool:
        """Можно ли начать запись в эту встречу.

        После пробного периода нельзя начать новую, но встречу, которая уже
        в счёте, дописать можно: человек поставил на паузу десятую встречу,
        и отнимать у него её конец было бы нечестно.
        """
        left = self._trial_left()
        if left is None or left > 0:
            return True
        return bool(meeting_id) and self.store.trial_counts(meeting_id)

    def _can_use_model(self, meeting_id: str) -> bool:
        """Можно ли звать модель: заметки, разборы, вопросы.

        Встречи пробного периода работают целиком навсегда: за них человек
        уже «заплатил» пробой, и отнимать у них пересборку заметок незачем.
        Закрываются только встречи сверх пробного периода, а у старых, до
        его появления, всё как было.
        """
        left = self._trial_left()
        if left is None or left > 0:
            return True
        return self.store.trial_counts(meeting_id) or self.store.trial_legacy(meeting_id)

    def _last_answer(self, meeting_id: str) -> str:
        for m in reversed(self.store.list_chat_messages(meeting_id)):
            if m.role == "assistant" and m.text.strip():
                return m.text
        return ""

    def trial_state(self) -> dict[str, Any]:
        """Пробный период для окна: сколько встреч прошло и сколько осталось."""
        from . import license as license_mod

        left = self._trial_left()
        return {
            "licensed": left is None,
            "limit": license_mod.TRIAL_MEETINGS,
            "left": left,
            "over": left == 0,
        }

    def license_state(self) -> dict[str, Any]:
        """Есть ли лицензия и кому выдана. Проверяется подписью, без сети."""
        from . import license as license_mod

        key = self.settings.license_key
        trial = self.trial_state()
        if not key:
            return {"licensed": False, "buy_url": license_mod.BUY_URL, "trial": trial}
        try:
            lic = license_mod.parse(key)
        except license_mod.LicenseError as err:
            # Ключ был, но больше не подходит: истёк срок или файл
            # настроек правили руками. Не стираем: вдруг человек
            # захочет посмотреть, что было вставлено.
            log.warning("Сохранённый ключ лицензии не подошёл: %s", err.code)
            return {"licensed": False, "error": err.code, "detail": err.detail,
                    "buy_url": license_mod.BUY_URL, "trial": trial}
        return {"licensed": True, "license": lic.to_dict(), "buy_url": license_mod.BUY_URL,
                "trial": trial}

    def activate_license(self, key: str) -> dict[str, Any]:
        """Проверить вставленный ключ и, если подошёл, запомнить."""
        from . import license as license_mod

        try:
            lic = license_mod.parse(key)
        except license_mod.LicenseError as err:
            # Неподошедший ключ не записываем: иначе испорченная вставка
            # затёрла бы рабочий ключ, и плашка вернулась бы.
            log.info("Ключ лицензии не принят: %s", err.code)
            return {"ok": False, "error": err.code, "detail": err.detail}
        self.settings.license_key = license_mod.clean(key)
        settings_mod.save(self.settings)
        log.info("Лицензия принята, номер %s", lic.id or "без номера")
        return {"ok": True, **self.license_state()}

    def remove_license(self) -> dict[str, Any]:
        """Убрать ключ с этого компьютера, например перед переносом на другой."""
        self.settings.license_key = ""
        settings_mod.save(self.settings)
        log.info("Ключ лицензии убран")
        return self.license_state()

    # --- свой бот в Telegram ----------------------------------------------

    _бот = None

    def telegram_folder_id(self) -> str | None:
        """Папка «Telegram» для встреч из бота: найти или завести."""
        for папка in self.store.list_folders():
            if папка.get("name") == "Telegram":
                return папка["id"]
        return self.create_folder("Telegram").get("id")

    def telegram_can_summarize(self) -> bool:
        """Сделает ли модель итоги прямо сейчас, ничего не скачивая молча."""
        if not self.llm.enabled:
            return False
        состояние = self.llm.status()
        return состояние.get("backend") != "local" or bool(состояние.get("model_ready"))

    def _собрать_бота(self, токен: str):
        from . import telegram_bot as бот_mod

        def привязали(chat_id: int, имя: str) -> None:
            self.settings.telegram.chat_id = int(chat_id)
            self.settings.telegram.chat_name = имя
            settings_mod.save(self.settings)
            bus.emit(TELEGRAM_CHANGED, self.telegram_state())

        return бот_mod.ТелеграмБот(
            self, токен, хозяин=self.settings.telegram.chat_id,
            при_привязке=привязали, папка=paths.data_dir() / "telegram",
            доступ=self._telegram_allowed, при_сообщении=self._telegram_seen,
        )

    def _telegram_allowed(self, chat_id: int) -> bool:
        настройки = self.settings.telegram
        if настройки.access == "all":
            return True
        return any(int(u.get("id", 0)) == int(chat_id) and u.get("allowed") for u in настройки.users)

    def _telegram_seen(self, chat_id: int, имя: str, ник: str) -> None:
        """Человек написал боту: занести в список или обновить имя."""
        настройки = self.settings.telegram
        for u in настройки.users:
            if int(u.get("id", 0)) == int(chat_id):
                if u.get("name") == имя and u.get("username") == ник:
                    return
                u["name"], u["username"] = имя, ник
                break
        else:
            # Новичку доступа нет, пока хозяин не отметит: молча пускать
            # всех, кто нашёл бота по имени, нельзя.
            настройки.users.append({"id": int(chat_id), "name": имя, "username": ник, "allowed": False})
        settings_mod.save(self.settings)
        bus.emit(TELEGRAM_CHANGED, self.telegram_state())

    def telegram_set_access(self, mode: str) -> dict[str, Any]:
        """Кому можно пользоваться ботом: chosen — отмеченным, all — всем."""
        self.settings.telegram.access = "all" if mode == "all" else "chosen"
        settings_mod.save(self.settings)
        return self.telegram_state()

    def telegram_set_user(self, chat_id: int, allowed: bool) -> dict[str, Any]:
        for u in self.settings.telegram.users:
            if int(u.get("id", 0)) == int(chat_id):
                u["allowed"] = bool(allowed)
        settings_mod.save(self.settings)
        return self.telegram_state()

    def запустить_телеграм(self) -> None:
        """Поднять бота, если он включён и токен есть. Зовётся при старте."""
        from . import секрет

        настройки = self.settings.telegram
        if not настройки.enabled or self._бот is not None:
            return
        токен = секрет.достать(настройки.token)
        if not токен:
            return
        try:
            self._бот = self._собрать_бота(токен)
            self._бот.запустить()
        except Exception:
            log.exception("Не удалось поднять бота Telegram")
            self._бот = None

    def остановить_телеграм(self) -> None:
        бот, self._бот = self._бот, None
        if бот is not None:
            бот.остановить()

    def telegram_state(self) -> dict[str, Any]:
        настройки = self.settings.telegram
        бот = self._бот.состояние() if self._бот is not None else {}
        return {
            "enabled": настройки.enabled,
            "has_token": bool(настройки.token),
            "linked": bool(настройки.chat_id),
            "chat_name": настройки.chat_name,
            "bot_name": бот.get("имя", ""),
            "running": бот.get("работает", False),
            "error": бот.get("ошибка", ""),
            "code": бот.get("код", ""),
            "access": настройки.access,
            "users": [
                {"id": int(u.get("id", 0)), "name": u.get("name", ""),
                 "username": u.get("username", ""), "allowed": bool(u.get("allowed"))}
                for u in настройки.users
            ],
        }

    def telegram_set_token(self, token: str) -> dict[str, Any]:
        """Проверить токен у Telegram и, если подошёл, включить бота."""
        from . import секрет
        from . import telegram_bot as бот_mod

        токен = "".join((token or "").split())
        if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{30,}", токен):
            return {"ok": False, "error": "format"}
        проба = бот_mod.ТелеграмБот(self, токен)
        try:
            имя = проба.проверить_токен()
        except бот_mod.ОшибкаТелеграма as беда:
            return {"ok": False, "error": "bad_token" if беда.код in (401, 404) else "api"}
        except Exception:
            return {"ok": False, "error": "network"}
        finally:
            проба.остановить()
        self.остановить_телеграм()
        настройки = self.settings.telegram
        настройки.token = секрет.спрятать(токен)
        настройки.enabled = True
        # Новый бот — новая привязка: прежний хозяин и список могли быть у
        # другого бота.
        настройки.chat_id = 0
        настройки.chat_name = ""
        настройки.users = []
        настройки.access = "chosen"
        settings_mod.save(self.settings)
        self.запустить_телеграм()
        return {"ok": True, "bot_name": имя, **self.telegram_state()}

    def telegram_set_enabled(self, enabled: bool) -> dict[str, Any]:
        self.settings.telegram.enabled = bool(enabled)
        settings_mod.save(self.settings)
        if enabled:
            self.запустить_телеграм()
        else:
            self.остановить_телеграм()
        return self.telegram_state()

    def telegram_forget(self) -> dict[str, Any]:
        """Отключить бота от программы: токен и привязка стираются."""
        self.остановить_телеграм()
        self.settings.telegram = settings_mod.TelegramSettings()
        settings_mod.save(self.settings)
        return self.telegram_state()

    def share_telegram(self, text: str) -> dict[str, Any]:
        """Открыть Telegram с заметками: человек сам выберет, кому отправить."""
        from . import telegram as telegram_mod

        return telegram_mod.открыть(text)

    def open_buy_page(self) -> bool:
        """Открыть страницу покупки в браузере человека.

        Своим окном не открываем: оплата в чужом для человека окне без
        адресной строки выглядит как ловушка, и правильно выглядит.
        """
        import webbrowser

        from . import license as license_mod

        try:
            return bool(webbrowser.open(license_mod.BUY_URL))
        except Exception:
            log.exception("Не удалось открыть страницу покупки")
            return False

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
        """Запомнить, где стоит окно.

        Свёрнутое окно Windows отвечает координатами -32000, -32000: это
        не место на экране, а условный «угол» для минимизированных окон.
        Записав их, мы при следующем запуске ставили окно туда же, и
        человек видел значок в трее, но не находил самого окна.
        """
        if not settings_mod.геометрия_годится(x, y, width, height):
            log.debug("Не запоминаем положение окна x=%s y=%s: оно вне экрана", x, y)
            return
        self.settings.window.x = int(x)
        self.settings.window.y = int(y)
        self.settings.window.width = int(width)
        self.settings.window.height = int(height)
        settings_mod.save(self.settings)

    def shutdown(self) -> None:
        if self.capture.is_recording:
            self.stop_recording()
        # Сторож держит открытой звуковую карту в своём потоке. Не
        # остановив его, мы оставили бы устройство занятым после
        # закрытия программы.
        try:
            self.сторож.остановить()
        except Exception:
            log.exception("Не удалось остановить сторожа при выходе")
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
        # Бот спрашивает Telegram в своём потоке: без остановки соединение
        # висело бы ещё до 25 секунд после выхода.
        self.остановить_телеграм()
        # Перепроверка версии больше не нужна: программа закрывается.
        self._version_checker.stop()
        # Пересчёт указателя пишет в базу, а её мы сейчас закроем.
        self.meaning.stop()
        self.meaning_downloader.cancel()
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
