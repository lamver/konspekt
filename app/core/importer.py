"""Импорт готовых записей: файл на входе, встреча с транскриптом на выходе.

Зачем отдельный модуль. Живая запись и импорт похожи только на первый
взгляд. У живой встречи звук идёт в реальном времени, и распознавание
обязано за ним поспевать; у файла всё наоборот: спешить некуда, зато
файлов сразу пачка, и человеку важно видеть, что происходит с каждым.

Как устроено. Один фоновый поток разбирает очередь файлов по одному.
Не пул: модели уже раскладывают работу по всем ядрам, а параллельный
разбор только отнимал бы у них процессор и перемешивал реплики разных
встреч. Зато очередь честная, её видно в интерфейсе, и любой файл можно
отменить, пока до него не дошли.

Ошибка одного файла не трогает остальные: в пачке из десятка записей
один битый файл не должен останавливать девять хороших.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..asr.audiofile import AudioInfo, UnsupportedAudio, decode, probe
from .models import new_id

log = logging.getLogger(__name__)

# Состояния файла в очереди. Ровно то, что видит человек.
WAITING = "waiting"      # ждёт очереди
RUNNING = "running"      # разбирается сейчас
DONE = "done"            # готово
FAILED = "failed"        # не смогли
CANCELLED = "cancelled"  # отменён до начала разбора


@dataclass
class ImportTask:
    """Один файл в очереди импорта."""

    id: str = field(default_factory=new_id)
    path: Path = Path()
    title: str = ""
    meeting_id: str | None = None
    status: str = WAITING
    error: str = ""
    duration: float = 0.0     # сколько звука в файле, секунд
    done: float = 0.0         # сколько уже разобрали, секунд
    segments: int = 0         # сколько реплик нашли
    stereo_split: bool = False

    def to_dict(self) -> dict[str, Any]:
        # Доля прогресса считается здесь, чтобы фронт не повторял эту
        # арифметику и не делил на ноль на файлах без длительности.
        progress = 0.0
        if self.duration > 0:
            progress = min(1.0, self.done / self.duration)
        elif self.status == DONE:
            progress = 1.0
        return {
            "id": self.id,
            "name": self.path.name,
            "path": str(self.path),
            "title": self.title,
            "meeting_id": self.meeting_id,
            "status": self.status,
            "error": self.error,
            "duration": round(self.duration, 1),
            "done": round(self.done, 1),
            "progress": round(progress, 3),
            "segments": self.segments,
            "stereo_split": self.stereo_split,
        }


class ImportQueue:
    """Очередь импорта файлов, разбираемая одним фоновым потоком.

    Вся работа с сервисом идёт через переданные функции обратного вызова,
    чтобы эта очередь ничего не знала ни про базу, ни про интерфейс.
    """

    def __init__(
        self,
        transcribe_chunk: Callable[[str, str, Any, float], None],
        create_meeting: Callable[[str], str],
        finish_meeting: Callable[[str, float], None],
        on_change: Callable[[ImportTask], None] | None = None,
        prepare_meeting: Callable[[str], None] | None = None,
        chunk_seconds: float = 30.0,
    ) -> None:
        self._transcribe = transcribe_chunk
        self._create_meeting = create_meeting
        self._finish_meeting = finish_meeting
        self._prepare_meeting = prepare_meeting
        self._on_change = on_change
        self._chunk_seconds = chunk_seconds

        self._queue: queue.Queue[ImportTask | None] = queue.Queue()
        self._tasks: dict[str, ImportTask] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._cancelled: set[str] = set()
        self._current: str | None = None

    # --- постановка в очередь --------------------------------------------

    def add(self, paths: list[str] | list[Path]) -> list[dict[str, Any]]:
        """Поставить файлы в очередь.

        Проверяем их сразу, ещё до разбора: человек бросил пачку и должен
        немедленно увидеть, что три файла из десяти вообще не звук, а не
        ждать этого через минуту.
        """
        added: list[dict[str, Any]] = []
        for raw in paths:
            path = Path(raw)
            task = ImportTask(path=path, title=path.stem or path.name)
            try:
                info: AudioInfo = probe(path)
                task.duration = info.duration
                task.title = info.title
                task.stereo_split = info.stereo_split
            except UnsupportedAudio as exc:
                task.status = FAILED
                task.error = str(exc)
                log.info("Файл %s не принят: %s", path.name, exc)
            except Exception as exc:
                task.status = FAILED
                task.error = f"Не удалось открыть файл: {exc}"
                log.exception("Ошибка проверки файла %s", path.name)

            with self._lock:
                self._tasks[task.id] = task
                self._order.append(task.id)
            added.append(task.to_dict())
            self._notify(task)
            if task.status == WAITING:
                self._queue.put(task)

        self._ensure_worker()
        return added

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name="import-worker", daemon=True
            )
            self._thread.start()

    # --- состояние --------------------------------------------------------

    def tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._tasks[i].to_dict() for i in self._order if i in self._tasks]

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._current is not None

    def cancel(self, task_id: str) -> bool:
        """Отменить файл.

        Уже начатый разбор дорабатывает текущий кусок и останавливается:
        рвать его посреди чанка незачем, это доли секунды.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status in (DONE, FAILED, CANCELLED):
                return False
            self._cancelled.add(task_id)
            if task.status == WAITING:
                task.status = CANCELLED
        self._notify(task)
        return True

    def clear_finished(self) -> int:
        """Убрать из списка всё, что уже отработало."""
        with self._lock:
            leaving = [
                i for i in self._order
                if self._tasks.get(i) and self._tasks[i].status in (DONE, FAILED, CANCELLED)
            ]
            for i in leaving:
                self._tasks.pop(i, None)
                self._order.remove(i)
            return len(leaving)

    def stop(self, timeout: float = 5.0) -> None:
        """Остановить разбор при выходе из приложения."""
        with self._lock:
            thread = self._thread
            self._thread = None
            # Всё, что ещё не начато, отменяем: доделывать это некому.
            self._cancelled.update(
                i for i in self._order
                if self._tasks.get(i) and self._tasks[i].status == WAITING
            )
        if thread is None:
            return
        self._queue.put(None)
        thread.join(timeout=timeout)

    # --- разбор -----------------------------------------------------------

    def _notify(self, task: ImportTask) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(task)
        except Exception:
            log.exception("Не удалось сообщить об изменении импорта")

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                break
            try:
                self._process(task)
            except Exception:
                log.exception("Импорт файла %s упал", task.path.name)
                task.status = FAILED
                if not task.error:
                    task.error = "Не удалось разобрать файл"
                self._notify(task)
            finally:
                with self._lock:
                    self._current = None
                self._queue.task_done()
        log.info("Поток импорта остановлен")

    def _process(self, task: ImportTask) -> None:
        with self._lock:
            if task.id in self._cancelled:
                task.status = CANCELLED
                self._notify(task)
                return
            self._current = task.id
            task.status = RUNNING
        self._notify(task)

        # Встречу заводим только теперь, когда точно начали разбор: иначе
        # отменённые файлы оставляли бы после себя пустые встречи.
        meeting_id = self._create_meeting(task.title)
        task.meeting_id = meeting_id
        if self._prepare_meeting is not None:
            self._prepare_meeting(meeting_id)
        self._notify(task)

        log.info(
            "Импорт %s -> встреча %s (%.1f c, %s)",
            task.path.name, meeting_id, task.duration,
            "два канала" if task.stereo_split else "одна дорожка",
        )

        last_offset = 0.0
        for track, pcm, offset in decode(
            task.path,
            chunk_seconds=self._chunk_seconds,
            split_channels=task.stereo_split,
        ):
            with self._lock:
                if task.id in self._cancelled:
                    task.status = CANCELLED
                    break
            self._transcribe(meeting_id, track, pcm, offset)
            last_offset = max(last_offset, offset + len(pcm) / 16000)
            task.done = last_offset
            # Длительность из заголовка бывает враньём, а полоска прогресса
            # не должна упираться в стену раньше времени.
            if task.duration and task.done > task.duration:
                task.duration = task.done
            self._notify(task)

        self._finish_meeting(meeting_id, last_offset)
        if task.status != CANCELLED:
            task.status = DONE
            task.done = task.duration or last_offset
        self._notify(task)
        log.info("Импорт %s завершён: %s", task.path.name, task.status)
