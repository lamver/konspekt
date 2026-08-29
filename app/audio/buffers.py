"""Кольцевой буфер и запись WAV.

Вынесено из движка захвата, чтобы эту логику можно было проверить без
живого микрофона: на вход подаются numpy-массивы, на выходе файл и чанки.
"""

from __future__ import annotations

import logging
import threading
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000  # то, что ждёт GigaAM; ресемплинг делает soundcard
SAMPLE_WIDTH = 2     # int16


def float_to_int16(data: np.ndarray) -> np.ndarray:
    """float32 [-1, 1] → int16 с защитой от клиппинга."""
    clipped = np.clip(data, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16)


def rms_level(data: np.ndarray) -> float:
    """Уровень для индикатора: 0..1, слегка поджатый под восприятие."""
    if data.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(data.astype(np.float64)))))
    # Корень растягивает тихую часть шкалы, иначе полоска почти не шевелится.
    return float(min(1.0, rms ** 0.5 * 2.2))


class WavWriter:
    """Потокобезопасная дозапись в WAV.

    Заголовок WAV хранит длину, поэтому файл дописывается по кускам, а
    корректный размер проставляется при закрытии.
    """

    def __init__(self, path: Path, sample_rate: int = SAMPLE_RATE) -> None:
        self.path = path
        self.sample_rate = sample_rate
        self._lock = threading.Lock()
        self._frames = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(SAMPLE_WIDTH)
        self._wav.setframerate(sample_rate)

    @property
    def frames(self) -> int:
        return self._frames

    @property
    def duration(self) -> float:
        return self._frames / float(self.sample_rate)

    def write(self, pcm16: np.ndarray) -> None:
        with self._lock:
            if self._wav is None:
                return
            self._wav.writeframes(pcm16.tobytes())
            self._frames += int(pcm16.size)

    def close(self) -> None:
        with self._lock:
            if self._wav is None:
                return
            try:
                self._wav.close()
            except Exception:
                log.exception("Не удалось закрыть WAV %s", self.path)
            self._wav = None


class ChunkBuffer:
    """Накопитель PCM, отдающий куски фиксированной длины.

    Распознаванию нужны отрезки в секундах, а звуковая карта отдаёт блоки
    произвольного размера. Здесь одно превращается в другое.
    """

    def __init__(self, chunk_seconds: float = 5.0, sample_rate: int = SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self.chunk_size = int(chunk_seconds * sample_rate)
        self._buf = np.zeros(0, dtype=np.int16)
        self._consumed = 0  # сколько сэмплов уже отдано, нужно для offset

    def push(self, pcm16: np.ndarray) -> list[tuple[np.ndarray, float]]:
        """Добавить данные и забрать готовые чанки с их смещением в секундах."""
        self._buf = np.concatenate([self._buf, pcm16.reshape(-1)])
        out: list[tuple[np.ndarray, float]] = []
        while self._buf.size >= self.chunk_size:
            chunk = self._buf[: self.chunk_size]
            self._buf = self._buf[self.chunk_size :]
            offset = self._consumed / float(self.sample_rate)
            self._consumed += self.chunk_size
            out.append((chunk, offset))
        return out

    def flush(self) -> tuple[np.ndarray, float] | None:
        """Остаток в конце записи: последняя фраза не должна пропасть."""
        if self._buf.size == 0:
            return None
        chunk = self._buf
        offset = self._consumed / float(self.sample_rate)
        self._consumed += chunk.size
        self._buf = np.zeros(0, dtype=np.int16)
        return chunk, offset
