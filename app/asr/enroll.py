"""Запись эталона голоса.

Зачем отдельно от встреч. Дорожка «я» это микрофон, а не человек: за
компьютером может сидеть несколько людей, и все они попадают в одну
дорожку. Чтобы всегда узнавать владельца, нужен эталон, записанный
осознанно, в тишине и достаточной длины.

Как. Человек читает вслух пару фраз, мы режем поток на куски по паре
секунд и считаем отпечаток каждого, а в конце усредняем. Среднее по
нескольким кускам заметно устойчивее одного длинного: случайная
интонация или запинка не перекашивает эталон.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
# Сколько всего речи хотим услышать. Меньше десяти секунд эталон выходит
# ненадёжным, больше двадцати человек уже устаёт читать.
TARGET_SECONDS = 14.0
MIN_SECONDS = 8.0
# На такие куски режем поток для усреднения.
PIECE_SECONDS = 2.5
# Тише этого кусок считаем тишиной и в эталон не берём: иначе паузы между
# фразами размывают отпечаток.
SILENCE_RMS = 0.006

# Фразы подобраны так, чтобы в них встретились разные звуки русского языка.
PROMPTS = (
    "Сегодня хороший день, и я проверяю, как работает запись голоса.",
    "Мы обсудим сроки, бюджет и задачи на следующую неделю.",
    "Широкая электрификация южных губерний даст мощный толчок производству.",
)


class VoiceEnrollment:
    """Копит речь владельца и считает по ней эталон.

    Живёт ровно столько, сколько идёт запись эталона. Кормится теми же
    чанками, что и обычная запись, поэтому отдельного захвата не нужно.
    """

    def __init__(self, embedder, sample_rate: int = SAMPLE_RATE) -> None:
        self.embedder = embedder
        self.sample_rate = sample_rate
        self._buffer = np.zeros(0, dtype=np.float32)
        self._vectors: list[np.ndarray] = []
        self._speech = 0.0
        # Чанки идут из потока захвата, а состояние читает UI.
        self._lock = threading.Lock()

    @property
    def seconds(self) -> float:
        """Сколько речи уже услышали."""
        with self._lock:
            return self._speech

    @property
    def progress(self) -> float:
        return min(1.0, self.seconds / TARGET_SECONDS)

    @property
    def enough(self) -> bool:
        with self._lock:
            return self._speech >= MIN_SECONDS and len(self._vectors) >= 3

    def feed(self, pcm: np.ndarray) -> None:
        """Принять кусок звука с микрофона."""
        chunk = _to_float32(pcm)
        if chunk.size == 0:
            return
        piece_len = int(PIECE_SECONDS * self.sample_rate)
        with self._lock:
            self._buffer = np.concatenate([self._buffer, chunk])
            while self._buffer.size >= piece_len:
                piece = self._buffer[:piece_len]
                self._buffer = self._buffer[piece_len:]
                self._consume(piece)

    def _consume(self, piece: np.ndarray) -> None:
        """Посчитать отпечаток куска, если в нём есть речь."""
        rms = float(np.sqrt(np.mean(np.square(piece)))) if piece.size else 0.0
        if rms < SILENCE_RMS:
            return
        try:
            vector = self.embedder.embed(piece, self.sample_rate)
        except Exception:
            log.exception("Не удалось посчитать отпечаток куска эталона")
            return
        if vector is None:
            return
        self._vectors.append(vector)
        self._speech += piece.size / float(self.sample_rate)

    def result(self) -> np.ndarray | None:
        """Усреднённый эталон или None, если речи не хватило."""
        with self._lock:
            # Хвост короче куска тоже пригодится, если речи впритык.
            if self._buffer.size > int(1.0 * self.sample_rate):
                self._consume(self._buffer)
                self._buffer = np.zeros(0, dtype=np.float32)
            if len(self._vectors) < 3 or self._speech < MIN_SECONDS:
                return None
            mean = np.mean(np.stack(self._vectors), axis=0)
        norm = float(np.linalg.norm(mean))
        if norm < 1e-6:
            return None
        return (mean / norm).astype(np.float32)

    def samples(self) -> int:
        with self._lock:
            return len(self._vectors)


def _to_float32(pcm) -> np.ndarray:
    if isinstance(pcm, (bytes, bytearray)):
        pcm = np.frombuffer(pcm, dtype=np.int16)
    data = np.asarray(pcm)
    if data.dtype == np.int16:
        return (data.astype(np.float32) / 32768.0).reshape(-1)
    return data.astype(np.float32).reshape(-1)
