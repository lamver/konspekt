"""Очередь распознавания: между захватом звука и моделью.

Зачем прослойка. Захват отдаёт чанки из потоков, которые обязаны
возвращаться к звуковой карте мгновенно: задержишься там на секунду
распознавания, и в записи появится дыра. Поэтому чанки складываются
в очередь, а разбирает её отдельный рабочий поток.

Очередь ограничена: если модель не успевает, лучше потерять кусок
и записать это в лог, чем съесть всю память и уронить приложение.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..core.models import TranscriptSegment
from .base import SAMPLE_RATE, Transcriber
from .embedder import VoiceEmbedder
from .vad import SpeechSegmenter
from .voices import VoiceRoster

log = logging.getLogger(__name__)

# Примерно две минуты речи в ожидании. Дальше отставание уже не догнать.
MAX_PENDING = 48
# Насколько занятой должна быть очередь, чтобы черновики не слались.
# Настоящие реплики всегда важнее: лучше показать текст на секунду
# позже, чем задержать то, что пойдёт в базу.
DRAFT_SKIP_AT = 2


@dataclass
class Job:
    meeting_id: str
    speaker: str
    pcm: np.ndarray
    offset: float
    # Черновик идущей речи: показать и забыть. В базу не идёт, говорящего
    # не ищет, и его всегда можно выбросить — настоящая фраза придёт
    # следом и заменит его целиком.
    draft: bool = False


@dataclass
class MissedSpan:
    """Кусок звука, который не влез в живую очередь.

    Сам звук никуда не делся — он лежит в WAV на диске, как и всегда.
    Пропало только распознавание. Эту запись достаточно, чтобы после
    остановки записи вырезать тот же интервал из файла и досчитать его,
    не торопясь: место в очереди уже не проблема, спешить некуда.
    """

    meeting_id: str
    speaker: str
    offset: float
    duration: float


class TranscriptionQueue:
    """Фоновое распознавание чанков по одному.

    Один поток, а не пул: onnxruntime и сам раскладывает работу по ядрам,
    а параллельные сессии только отнимали бы друг у друга процессор и
    ломали порядок сегментов.
    """

    def __init__(
        self,
        transcriber: Transcriber,
        on_segment: Callable[[TranscriptSegment], None],
        on_draft: Callable[[TranscriptSegment], None] | None = None,
        sample_rate: int = SAMPLE_RATE,
        use_vad: bool = True,
        embedder: VoiceEmbedder | None = None,
        roster: VoiceRoster | None = None,
        ensure_model: Callable[[], bool] | None = None,
    ) -> None:
        self.transcriber = transcriber
        self.on_segment = on_segment
        # Черновики показываются мимо базы. Не задан — программа работает
        # ровно как раньше, текст появляется после паузы.
        self.on_draft = on_draft
        self.sample_rate = sample_rate
        self.use_vad = use_vad
        # Веса могут быть ещё не скачаны. Ждём их здесь, в своём потоке:
        # поток захвата задерживать нельзя, там любая пауза это дыра в
        # записи, а чанки пока подождут в очереди.
        self.ensure_model = ensure_model
        # Кто говорит. Обе части необязательны: без них всё работает
        # ровно как раньше, только реплики остаются просто дорожками.
        self.embedder = embedder
        self.roster = roster
        # Своя нарезка на каждую дорожку: у микрофона и системного звука
        # разный уровень фона, общий порог был бы неверен для обоих.
        self._vad: dict[str, SpeechSegmenter] = {}
        # Дорожки приходят из разных потоков, а stop() из потока UI.
        self._vad_lock = threading.Lock()
        self._queue: queue.Queue[Job | None] = queue.Queue(maxsize=MAX_PENDING)
        self._thread: threading.Thread | None = None
        self._dropped = 0
        # Куда именно выпали чанки при переполнении. Список, а не только
        # счётчик: без него после «стоп» нечего было бы досчитывать, кроме
        # общего числа потерь, а звук на диске точно есть, только с какого
        # места его брать — было бы неизвестно.
        self._missed: list[MissedSpan] = []
        self._missed_lock = threading.Lock()
        # start() зовут из потоков захвата, stop() — из потока UI.
        # Без замка они рвут друг у друга _thread: то поток запускается
        # дважды, то stop() падает на уже обнулённой ссылке.
        self._lock = threading.Lock()

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def dropped(self) -> int:
        return self._dropped

    def take_missed(self, meeting_id: str) -> list[MissedSpan]:
        """Забрать и очистить список пропущенных кусков этой встречи.

        Зовут по кнопке «стоп», один раз: после этого докатка сама
        вырежет нужные интервалы из уже сохранённого WAV и досчитает их
        не спеша. «Забрать» вместо «посмотреть» — чтобы повторный вызов
        (например, из другой встречи) не досчитал те же куски дважды.
        """
        with self._missed_lock:
            keep: list[MissedSpan] = []
            taken: list[MissedSpan] = []
            for span in self._missed:
                (taken if span.meeting_id == meeting_id else keep).append(span)
            self._missed = keep
        return taken

    def start(self) -> None:
        with self._lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="asr-worker", daemon=True)
        self._thread.start()
        log.info("Очередь распознавания запущена")

    def submit(
        self,
        meeting_id: str,
        speaker: str,
        pcm: np.ndarray,
        offset: float,
        block: bool = False,
    ) -> None:
        """Положить чанк в очередь. Вызывается из потоков захвата.

        Здесь же ищем речь: копейка процессора на пару арифметических
        действий, зато модель не жуёт тишину, а фразы приходят целыми, а
        не разрезанными по границе чанка.

        block решает судьбу чанка, когда очередь забита. У живой записи
        выбора нет: поток обязан вернуться к звуковой карте немедленно, и
        лучше потерять кусок, чем порвать запись. А при разборе файла
        спешить некуда: там надо ждать, иначе чтение убегает вперёд
        распознавания и речь молча пропадает.
        """
        with self._lock:
            self._start_locked()

        if not self.use_vad:
            self._put(Job(meeting_id, speaker, pcm, offset), block)
            return

        try:
            with self._vad_lock:
                vad = self._vad.get(speaker)
                if vad is None:
                    vad = SpeechSegmenter(sample_rate=self.sample_rate)
                    self._vad[speaker] = vad
                pieces = vad.feed(self._as_float(pcm), offset)
        except Exception:
            # Поиск речи не должен ронять запись: в худшем случае отдаём
            # чанк как есть, как это было до появления VAD.
            log.exception("Поиск речи упал, отдаём чанк целиком")
            pieces = []
            self._put(Job(meeting_id, speaker, pcm, offset), block)
        for piece in pieces:
            self._put(Job(meeting_id, speaker, piece.pcm, piece.offset), block)

        # Человек всё ещё говорит: покажем, что уже сказано, чтобы экран
        # не стоял пустым. Только для живой записи: при разборе файла
        # показывать некому, а лишний счёт замедлит импорт вдвое.
        if self.on_draft is not None and not block:
            self._peek(meeting_id, speaker)

    def submit_raw(
        self,
        meeting_id: str,
        speaker: str,
        pcm: np.ndarray,
        offset: float,
        block: bool = True,
    ) -> None:
        """Положить уже готовый кусок речи, минуя VAD.

        Для докатки после «стоп»: кусок и так был вырезан VAD ещё в
        первый раз, до того как не поместился в очередь. Прогонять его
        через VAD второй раз незачем и рискованно — на короткой синтетике
        порог тишины может решить иначе, и кусок беззвучно потеряется
        снова, хотя звук на диске давно есть.
        """
        with self._lock:
            self._start_locked()
        self._put(Job(meeting_id, speaker, pcm, offset), block)


    def _peek(self, meeting_id: str, speaker: str) -> None:
        """Отправить черновик идущей речи, если пора.

        Очередь может быть занята настоящими фразами: тогда черновик
        просто пропускаем. Он не ценен — через секунду будет следующий,
        а вставать в очередь перед настоящей репликой ему нельзя.
        """
        try:
            with self._vad_lock:
                vad = self._vad.get(speaker)
                piece = vad.peek() if vad is not None else None
            if piece is None:
                return
            if self._queue.qsize() > DRAFT_SKIP_AT:
                return
            try:
                self._queue.put_nowait(
                    Job(meeting_id, speaker, piece.pcm, piece.offset, draft=True)
                )
            except queue.Full:
                pass
        except Exception:
            # Черновик — украшение. Ни одна его беда не стоит записи.
            log.exception("Не удалось показать черновик речи")

    @staticmethod
    def _as_float(pcm: np.ndarray) -> np.ndarray:
        """int16 из звуковой карты в float32 [-1, 1] для анализа."""
        arr = np.asarray(pcm)
        if arr.dtype == np.int16:
            return arr.astype(np.float32) / 32768.0
        return arr.astype(np.float32, copy=False)

    def _put(self, job: Job, block: bool = False) -> None:
        if block:
            # Разбор файла: ждём места сколько надо. Терять речь тут нельзя:
            # файл никуда не убежит, в отличие от живого звука.
            self._queue.put(job)
            return
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self._dropped += 1
            if not job.draft:
                # Черновики не считаем: они и так пересчитываются заново
                # на каждой следующей фразе, докатывать там нечего. А вот
                # настоящую реплику, которая не влезла, нужно запомнить,
                # чтобы позже вырезать этот же кусок из записанного WAV.
                duration = job.pcm.size / float(self.sample_rate)
                with self._missed_lock:
                    self._missed.append(
                        MissedSpan(job.meeting_id, job.speaker, job.offset, duration)
                    )
            log.warning(
                "Очередь распознавания переполнена, чанк %s@%.1fс пропущен (всего %d)",
                job.speaker,
                job.offset,
                self._dropped,
            )

    def flush(self, meeting_id: str, block: bool = False) -> None:
        """Дослать недоговорённые фразы. Зовём по кнопке «стоп».

        Без этого последняя фраза встречи оставалась бы внутри VAD и
        никогда не попала в транскрипт.
        """
        with self._vad_lock:
            items = list(self._vad.items())
            self._vad.clear()
        for speaker, vad in items:
            try:
                for piece in vad.flush():
                    self._put(Job(meeting_id, speaker, piece.pcm, piece.offset), block)
            except Exception:
                log.exception("Не удалось дослать хвост дорожки %s", speaker)

    def wait_idle(self, timeout: float = 600.0) -> bool:
        """Дождаться, пока очередь разберут, не останавливая поток.

        Нужно импорту файлов: там встречи идут одна за другой, и закрыть
        встречу можно только когда её последняя реплика уже распознана.
        Останавливать ради этого поток нельзя, следующий файл поднимал бы
        его заново. Ждём с потолком: если распознавание намертво встало,
        лучше закрыть встречу с тем, что есть, чем висеть вечно.
        """
        deadline = time.monotonic() + timeout
        while not self._queue.empty():
            if time.monotonic() > deadline:
                log.warning("Очередь не разобралась за %.0f с, идём дальше", timeout)
                return False
            time.sleep(0.05)
        # Последний чанк уже вынут из очереди, но ещё считается: без этой
        # паузы его реплики попали бы в следующую встречу.
        self._queue.join()
        return True

    def stop(self, timeout: float = 30.0) -> None:
        """Дождаться разбора очереди и остановить поток.

        Ждём осознанно: после кнопки «стоп» последние фразы встречи ещё
        лежат в очереди, и терять их обиднее всего.
        """
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is None:
            return
        self._queue.put(None)
        thread.join(timeout=timeout)
        if thread.is_alive():
            log.warning("Распознавание не успело завершиться за %.0f с", timeout)

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                break
            try:
                if self.ensure_model is not None and not self.ensure_model():
                    # Весов нет и скачать не вышло: молча выбрасывать чанк
                    # нельзя, но и делать с ним нечего. Сообщение об этом
                    # уже ушло в интерфейс из самого скачивания.
                    continue
                segments = self.transcriber.transcribe(
                    job.pcm,
                    sample_rate=self.sample_rate,
                    meeting_id=job.meeting_id,
                    offset=job.offset,
                    speaker=job.speaker,
                )
                for segment in segments:
                    if job.draft:
                        # Черновик: показать и забыть. Ни в базу, ни в
                        # поиск говорящего — отпечаток голоса по куску
                        # недоговорённой фразы всё равно ненадёжен.
                        if self.on_draft is not None:
                            self.on_draft(segment)
                        continue
                    self._identify(segment, job)
                    self.on_segment(segment)
            except Exception:
                # Один плохой чанк не должен останавливать распознавание.
                log.exception("Ошибка обработки чанка %s@%.1fс", job.speaker, job.offset)
            finally:
                self._queue.task_done()
        log.info("Очередь распознавания остановлена")

    def _identify(self, segment: TranscriptSegment, job: Job) -> None:
        """Проставить сегменту говорящего.

        Считаем отпечаток здесь, а не в потоке захвата: там нельзя
        задерживаться ни на миллисекунду, иначе в записи появится дыра.
        Любая осечка тут не должна стоить нам реплики, поэтому текст
        сохраняется в любом случае, просто без имени говорящего.
        """
        if self.embedder is None or self.roster is None:
            return
        try:
            vector = self.embedder.embed(job.pcm, self.sample_rate)
            voice = self.roster.assign(job.speaker, vector)
            if voice is None:
                return
            segment.voice_id = voice.id
            segment.voice_label = voice.label
            segment.person_id = voice.person_id
        except Exception:
            log.exception("Не удалось определить говорящего")
