"""Настоящий захват звука: микрофон и системный выход раздельно.

Две дорожки пишутся независимыми потоками в два WAV-файла. Смешивать их
нельзя: на этапе 3 распознавание должно знать, кто говорит, а разделение
по источнику даёт это бесплатно и точнее любой диаризации.

Отказ одной дорожки не роняет вторую: без микрофона встреча всё ещё
пишется с системного звука, и наоборот.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..core.events import (
    RECORDING_FOREIGN_SPEECH,
    RECORDING_LEVEL,
    RECORDING_SILENT,
    bus,
)
from ..core.paths import audio_dir
from . import devices
from .buffers import SAMPLE_RATE, ChunkBuffer, WavWriter, float_to_int16, rms_level
from .чужая_речь import Слушатель

log = logging.getLogger(__name__)

# Сколько кадров просим за раз: компромисс между отзывчивостью
# индикатора и нагрузкой на процессор.
BLOCK_FRAMES = 1600  # 0.1 с при 16 кГц

# Через сколько секунд ровного нуля на дорожке говорим человеку, что
# записывается пустота. Десять секунд: достаточно, чтобы не дёргать на
# паузе перед началом разговора, и достаточно рано, чтобы успеть
# переключить устройство и не потерять встречу.
ПОРОГ_ТИШИНЫ = 10.0

# Готовый чанк для распознавания.
ChunkCallback = Callable[[str, np.ndarray, float], None]


class _Track:
    """Одна дорожка записи в своём потоке."""

    def __init__(
        self,
        name: str,
        opener: Callable[[], Any],
        wav_path: Path,
        chunk_seconds: float,
        on_chunk: ChunkCallback | None,
    ) -> None:
        self.name = name
        self._opener = opener
        self._wav_path = wav_path
        self._on_chunk = on_chunk
        self._chunks = ChunkBuffer(chunk_seconds=chunk_seconds)
        self._writer: WavWriter | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._level = 0.0
        self._error: str | None = None
        self._started = threading.Event()
        # Сколько секунд подряд с дорожки идёт ровный ноль. Устройство
        # при этом «открыто» и ошибок не даёт, просто в нём нет звука:
        # так бывает, когда звук играет не на том выходе, который мы
        # слушаем. Молчать об этом нельзя, иначе человек час пишет
        # встречу, а в расшифровке получает обрывки из микрофона.
        self._тишина_секунд = 0.0
        self._было_слышно = False
        # Слушаем системный звук на предмет чужого разговора. Только его:
        # в микрофоне речь — это и есть смысл записи, а вот в системном
        # звуке она может оказаться роликом из соседней вкладки.
        self._чужая_речь = (
            Слушатель() if name == devices.TRACK_THEM else None
        )
        # Длительность записанного. Считаем её до закрытия файла: после
        # writer обнуляется, а знать, сколько секунд в файле, нужно, чтобы
        # потом переслушать нужную реплику.
        self._written_seconds = 0.0

    @property
    def level(self) -> float:
        return self._level

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def duration(self) -> float:
        return self._writer.duration if self._writer else self._written_seconds

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"capture-{self.name}", daemon=True)
        self._thread.start()
        # Ждём открытия устройства, чтобы ошибка всплыла сразу при старте.
        self._started.wait(timeout=3.0)

    def stop(self) -> Path | None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._level = 0.0
        if self._writer is None:
            return None
        # Хвост короче чанка тоже нужен распознаванию.
        tail = self._chunks.flush()
        if tail is not None and self._on_chunk is not None:
            chunk, offset = tail
            self._safe_chunk(chunk, offset)
        written = self._writer.frames
        self._written_seconds = self._writer.duration
        self._writer.close()
        path = self._writer.path
        self._writer = None
        if written == 0:
            # Пустой файл только мешает: удаляем.
            try:
                path.unlink(missing_ok=True)
            except Exception:
                log.exception("Не удалось удалить пустой файл %s", path)
            return None
        return path

    def _следить_за_тишиной(self, блок: np.ndarray) -> None:
        """Предупредить, если дорожка пишет ровный ноль.

        Проверяем именно ноль, а не тихий звук: тихая речь и пауза в
        разговоре — это нормально, а вот отсутствие даже шума значит,
        что устройство отдаёт пустоту. Предупреждаем один раз за заход,
        и только пока с дорожки вообще ничего не слышали: если звук был
        и просто настала пауза, тревожить человека незачем.
        """
        if self._было_слышно:
            return
        if float(np.abs(блок).max()) > 0.0:
            self._было_слышно = True
            self._тишина_секунд = 0.0
            return
        self._тишина_секунд += len(блок) / SAMPLE_RATE
        if self._тишина_секунд < ПОРОГ_ТИШИНЫ:
            return
        self._было_слышно = True  # больше не повторяемся
        куда = "микрофон" if self.name == devices.TRACK_ME else "системный звук"
        log.warning(
            "Дорожка %s молчит %.0f секунд подряд: устройство открылось, "
            "но звука в нём нет", self.name, self._тишина_секунд
        )
        bus.emit(
            RECORDING_SILENT,
            {
                "track": self.name,
                "silent": True,
                "message": (
                    f"Тишина на дорожке «{куда}»: за {int(self._тишина_секунд)} "
                    f"секунд не было ни звука. Проверьте, то ли устройство "
                    f"выбрано в настройках записи."
                ),
            },
        )

    def _safe_chunk(self, chunk: np.ndarray, offset: float) -> None:
        """Ошибка в распознавании не должна ронять запись."""
        try:
            if self._on_chunk:
                self._on_chunk(self.name, chunk, offset)
        except Exception:
            log.exception("Ошибка обработчика чанка на дорожке %s", self.name)

    def замолчать_про_чужую_речь(self) -> None:
        """Человек ответил на вопрос: больше не спрашиваем до конца записи."""
        if self._чужая_речь is not None:
            self._чужая_речь.замолчать()

    def _следить_за_чужой_речью(self, блок: np.ndarray) -> None:
        """Спросить человека, если в системном звуке идёт разговор.

        Ошибка здесь стоит дорого в обе стороны: промолчать — значит
        пустить чужой ролик в расшифровку и в саммари, а спросить зря —
        перебить человека посреди встречи. Поэтому решение принимает
        `Слушатель`, а он ждёт двух согласных окон подряд и говорит
        ровно один раз.
        """
        if self._чужая_речь is None:
            return
        try:
            if not self._чужая_речь.добавить(блок):
                return
        except Exception:
            # Слушатель — вспомогательная вещь: его поломка не имеет
            # права оборвать запись встречи.
            log.exception("Дорожка %s: сбой поиска чужой речи", self.name)
            return
        оценка = self._чужая_речь.последняя
        log.info(
            "В системном звуке слышен разговор (доля %.2f, переключений %.1f, "
            "тембр %.2f): спрашиваем человека",
            оценка.доля_звука, оценка.переключений, оценка.тембр,
        )
        bus.emit(
            RECORDING_FOREIGN_SPEECH,
            {"track": self.name, "признаки": оценка.to_dict()},
        )

    def _run(self) -> None:
        recorder = None
        try:
            # COM обязан быть поднят в том же потоке, где открываем устройство,
            # и оставаться поднятым до конца записи.
            devices.hold_com()
            device = self._opener()
            recorder = device.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK_FRAMES)
            recorder.__enter__()
            self._writer = WavWriter(self._wav_path)
            log.info("Дорожка %s: пишем с «%s»", self.name, getattr(device, "name", "?"))
        except Exception as exc:
            self._error = str(exc)
            log.exception("Дорожка %s: не удалось открыть устройство", self.name)
            self._started.set()
            return
        finally:
            self._started.set()

        try:
            while not self._stop.is_set():
                data = recorder.record(numframes=BLOCK_FRAMES)
                if data is None or len(data) == 0:
                    continue
                mono = data.reshape(-1) if data.ndim == 1 else data.mean(axis=1)
                self._level = rms_level(mono)
                self._следить_за_тишиной(mono)
                self._следить_за_чужой_речью(mono)
                pcm16 = float_to_int16(mono)
                if self._writer:
                    self._writer.write(pcm16)
                if self._on_chunk is not None:
                    for chunk, offset in self._chunks.push(pcm16):
                        self._safe_chunk(chunk, offset)
        except Exception as exc:
            self._error = str(exc)
            log.exception("Дорожка %s: запись прервана", self.name)
        finally:
            try:
                if recorder is not None:
                    recorder.__exit__(None, None, None)
            except Exception:
                log.exception("Дорожка %s: ошибка закрытия устройства", self.name)


class WasapiCapture:
    """Захват микрофона и системного звука двумя дорожками.

    Реализует тот же протокол `AudioCapture`, что и заглушка этапа 1,
    поэтому сервис и UI не меняются.
    """

    def __init__(
        self,
        mic_device_id: str | None = None,
        loopback_device_id: str | None = None,
        capture_mic: bool = True,
        capture_system: bool = True,
        chunk_seconds: float = 5.0,
        on_chunk: ChunkCallback | None = None,
    ) -> None:
        self.mic_device_id = mic_device_id
        self.loopback_device_id = loopback_device_id
        self.capture_mic = capture_mic
        self.capture_system = capture_system
        self.chunk_seconds = chunk_seconds
        self.on_chunk = on_chunk

        self._tracks: dict[str, _Track] = {}
        self._recording = False
        self._meeting_id: str | None = None
        self._level_thread: threading.Thread | None = None
        self._level_stop = threading.Event()
        self._paths: dict[str, str] = {}
        # Что записал последний заход: дорожка -> (файл, длительность).
        # Нужно, чтобы привязать файл к времени встречи.
        self._last_chunks: list[tuple[str, str, float]] = []

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def track_paths(self) -> dict[str, str]:
        """Пути к дорожкам последней записи: {"me": ..., "them": ...}."""
        return dict(self._paths)

    @property
    def last_chunks(self) -> list[tuple[str, str, float]]:
        """Файлы последнего захода: (дорожка, путь, длительность в секундах)."""
        return list(self._last_chunks)

    def errors(self) -> dict[str, str]:
        return {name: t.error for name, t in self._tracks.items() if t.error}

    def start(self, meeting_id: str) -> None:
        if self._recording:
            log.warning("Запись уже идёт")
            return

        self._meeting_id = meeting_id
        self._paths = {}
        self._tracks = {}
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = audio_dir() / meeting_id
        base.mkdir(parents=True, exist_ok=True)

        if self.capture_mic:
            self._tracks[devices.TRACK_ME] = _Track(
                name=devices.TRACK_ME,
                opener=lambda: devices.open_microphone(self.mic_device_id),
                wav_path=base / f"{stamp}-me.wav",
                chunk_seconds=self.chunk_seconds,
                on_chunk=self.on_chunk,
            )
        if self.capture_system:
            self._tracks[devices.TRACK_THEM] = _Track(
                name=devices.TRACK_THEM,
                opener=lambda: devices.open_loopback(self.loopback_device_id),
                wav_path=base / f"{stamp}-them.wav",
                chunk_seconds=self.chunk_seconds,
                on_chunk=self.on_chunk,
            )

        if not self._tracks:
            # Человек снял обе галочки в настройках: это не поломка,
            # но и записывать нечего.
            self._recording = False
            raise RuntimeError(
                "Не выбрано, что записывать: включите микрофон или системный звук"
            )

        for track in self._tracks.values():
            track.start()

        alive = [n for n, t in self._tracks.items() if t.error is None]
        if not alive:
            # Ни одна дорожка не открылась: честно сообщаем и не делаем вид,
            # что запись идёт.
            self._recording = False
            log.error("Запись не начата: ни одно устройство недоступно")
            raise RuntimeError(_errors_text(self.errors()))

        self._recording = True
        self._level_stop.clear()
        self._level_thread = threading.Thread(
            target=self._emit_levels, args=(meeting_id,), daemon=True
        )
        self._level_thread.start()
        log.info("Запись начата, дорожки: %s", ", ".join(alive))

    def stop(self) -> str | None:
        if not self._recording:
            return None
        self._recording = False
        self._level_stop.set()
        if self._level_thread:
            self._level_thread.join(timeout=1.0)
            self._level_thread = None

        paths: dict[str, str] = {}
        chunks: list[tuple[str, str, float]] = []
        for name, track in self._tracks.items():
            path = track.stop()
            if path is not None:
                paths[name] = str(path)
                chunks.append((name, str(path), track.duration))
        self._paths = paths
        self._last_chunks = chunks
        log.info("Запись остановлена, файлов: %d", len(paths))

        # В поле встречи кладём микрофон как основную дорожку,
        # а при её отсутствии — системную.
        return paths.get(devices.TRACK_ME) or paths.get(devices.TRACK_THEM)

    def _emit_levels(self, meeting_id: str) -> None:
        """Шлём уровни в UI с той же частотой, что и заглушка."""
        while not self._level_stop.wait(0.1):
            me = self._tracks.get(devices.TRACK_ME)
            them = self._tracks.get(devices.TRACK_THEM)
            bus.emit(
                RECORDING_LEVEL,
                {
                    "meeting_id": meeting_id,
                    "me": me.level if me else 0.0,
                    "them": them.level if them else 0.0,
                },
            )

    def замолчать_про_чужую_речь(self) -> None:
        """Человек ответил «это и есть собеседник»: вопрос снят до конца записи."""
        трек = self._tracks.get(devices.TRACK_THEM)
        if трек is not None:
            трек.замолчать_про_чужую_речь()

    def выключить_системный_звук(self) -> dict[str, Any]:
        """Перестать писать системный звук, не прерывая встречу.

        Человек ответил на вопрос «это чужой ролик». Останавливаем одну
        дорожку и оставляем вторую: остановить запись целиком значило бы
        наказать человека за честный ответ.

        Файл уже записанного не удаляем. Он привязан ко времени встречи,
        и по нему работает прослушивание реплик: снести его — значит
        сломать кнопку «переслушать» на всём, что было до выключения.
        Разбираться с уже распознанным будем в расшифровке, а не здесь.
        """
        трек = self._tracks.get(devices.TRACK_THEM)
        if трек is None:
            return {"ok": False, "причина": "системный звук и так не пишется"}

        путь = трек.stop()
        self._tracks.pop(devices.TRACK_THEM, None)
        # Чтобы следующая встреча не начиналась с сюрприза, настройку
        # тоже переключаем: человек уже сказал, чего хочет.
        self.capture_system = False
        if путь is not None:
            self._paths[devices.TRACK_THEM] = str(путь)
        log.info("Системный звук выключен посреди записи по просьбе человека")
        return {"ok": True, "остановлено": путь is not None}

def _errors_text(errors: dict[str, str]) -> str:
    if not errors:
        return "Аудиоустройства недоступны"
    human = {devices.TRACK_ME: "микрофон", devices.TRACK_THEM: "системный звук"}
    parts = [f"{human.get(k, k)}: {v}" for k, v in errors.items()]
    return "Не удалось начать запись — " + "; ".join(parts)
