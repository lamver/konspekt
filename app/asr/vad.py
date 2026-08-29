"""Поиск речи в потоке звука.

Зачем. Захват режет звук на куски равной длины, и модели достаётся всё
подряд: тишина, щелчки, дыхание. На тишине распознавание выдумывает
слова, а фразы рвутся ровно посередине, потому что граница куска не
знает ничего про паузы. Здесь мы копим звук и отдаём его только тогда,
когда человек договорил.

Почему без нейросети. Silero VAD весит всего пару мегабайт, но это ещё
одна модель, которую надо скачать и держать в памяти. Для нашей задачи
хватает энергии сигнала: нужно отличить речь от тишины, а не речь от
шума улицы. Порог не фиксированный, а считается от текущего фона, иначе
гул кулера или тихий микрофон ломают любую константу.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

FRAME_MS = 20                 # шаг анализа
SILENCE_TAIL = 0.6            # сколько тишины считаем концом фразы, с
MIN_SPEECH = 0.35             # короче этого — щелчок, а не речь, с
MAX_SEGMENT = 18.0            # принудительная резка, чтобы не копить вечно, с
PAD = 0.15                    # прихватываем немного до и после, с
NOISE_MARGIN = 3.0            # во сколько раз речь громче фона
ABS_FLOOR = 0.004             # ниже этого молчим даже в полной тишине
NOISE_CEILING = 0.05          # выше этого «фон» уже не фон, а голос
NOISE_WINDOW = 250            # окно оценки фона, кадров (5 секунд)
NOISE_WARMUP = 15             # кадров на первичную оценку фона (0.3 с)


@dataclass
class Speech:
    """Кусок речи: сам звук и его место на оси времени встречи."""

    pcm: np.ndarray
    offset: float


@dataclass
class SpeechSegmenter:
    """Копит звук одной дорожки и отдаёт фразы целиком.

    На каждую дорожку нужен свой экземпляр: у микрофона и системного
    звука разный уровень фона, общий порог был бы неверен для обоих.
    """

    sample_rate: int = 16000
    silence_tail: float = SILENCE_TAIL
    min_speech: float = MIN_SPEECH
    max_segment: float = MAX_SEGMENT

    _buf: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    _buf_offset: float = 0.0          # время начала буфера от начала встречи
    _noise: float = 0.0               # текущая оценка фона
    _quiet: deque = field(default_factory=lambda: deque(maxlen=NOISE_WINDOW))
    _seen: int = 0                    # сколько кадров уже видели
    _in_speech: bool = False
    _speech_start: int = 0            # индекс кадра, где началась речь
    _silence_run: int = 0             # подряд идущих тихих кадров

    # --- внутреннее ------------------------------------------------------

    @property
    def _frame(self) -> int:
        return max(1, int(self.sample_rate * FRAME_MS / 1000))

    def _energy(self, pcm: np.ndarray) -> np.ndarray:
        """Громкость по кадрам, RMS."""
        n = self._frame
        count = len(pcm) // n
        if count == 0:
            return np.zeros(0, dtype=np.float32)
        frames = pcm[: count * n].reshape(count, n)
        return np.sqrt(np.mean(frames.astype(np.float32) ** 2, axis=1))

    def _threshold(self, energy: float) -> bool:
        """Речь ли это, и заодно подстройка оценки фона.

        Фон это не среднее, а нижний перцентиль последних пяти секунд.
        Среднее подтягивается к голосу и рвёт фразу посередине, а
        перцентиль держится за самые тихие кадры, то есть за настоящий
        фон. Пока человек говорит, окно заморожено: иначе затяжная
        реплика сама себя превратила бы в «фон».
        """
        # Разогрев: первые доли секунды слушаем молча. Иначе ровный гул
        # с первого же кадра принимается за речь, ведь сравнивать ещё
        # не с чем, а фон по определению не может быть речью.
        warming = len(self._quiet) < NOISE_WARMUP
        if warming:
            self._quiet.append(float(energy))
            self._noise = float(np.percentile(self._quiet, 10))

        # Потолок не даёт ослепнуть, если запись включили посреди громкой
        # реплики: без него уровень голоса записался бы в фон, и дальше
        # речь не нашлась бы уже никогда.
        floor = max(min(self._noise, NOISE_CEILING) * NOISE_MARGIN, ABS_FLOOR)
        if warming:
            return False
        loud = energy > floor
        # Только тихие кадры и только вне фразы: иначе первое же слово
        # запишется в фон, порог подскочит и речь больше не найдётся.
        if not loud and not self._in_speech:
            self._quiet.append(float(energy))
            self._noise = float(np.percentile(self._quiet, 10))
        return loud

    def _cut(self, start_frame: int, end_frame: int) -> Speech | None:
        """Вырезать кусок буфера по номерам кадров, с запасом по краям."""
        n = self._frame
        pad = int(PAD * self.sample_rate)
        a = max(0, start_frame * n - pad)
        b = min(len(self._buf), end_frame * n + pad)
        if b - a < int(self.min_speech * self.sample_rate):
            return None
        return Speech(
            pcm=self._buf[a:b].copy(),
            offset=self._buf_offset + a / self.sample_rate,
        )

    def _drop_before(self, frame: int) -> None:
        """Забыть всё до кадра: обработанное держать незачем."""
        n = self._frame
        cut = max(0, frame * n - int(PAD * self.sample_rate))
        if cut <= 0:
            return
        self._buf = self._buf[cut:]
        self._buf_offset += cut / self.sample_rate
        shift = cut // n
        self._seen = max(0, self._seen - shift)
        self._speech_start = max(0, self._speech_start - shift)

    # --- публичное -------------------------------------------------------

    def feed(self, pcm: np.ndarray, offset: float) -> list[Speech]:
        """Добавить кусок звука. Возвращает готовые фразы, если они есть."""
        if pcm is None or len(pcm) == 0:
            return []
        pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)

        if len(self._buf) == 0:
            self._buf_offset = float(offset)
            self._seen = 0
        self._buf = np.concatenate([self._buf, pcm])

        out: list[Speech] = []
        tail_frames = max(1, int(self.silence_tail * 1000 / FRAME_MS))
        max_frames = max(1, int(self.max_segment * 1000 / FRAME_MS))

        total = len(self._buf) // self._frame
        energies = self._energy(self._buf[self._seen * self._frame: total * self._frame])

        # Границы найденных фраз в номерах кадров. Резать буфер прямо в
        # цикле нельзя: обрезка сдвигает нумерацию, и остаток разбора
        # уезжает на чужие кадры.
        found: list[tuple[int, int]] = []

        for i, e in enumerate(energies):
            idx = self._seen + i
            loud = self._threshold(float(e))

            if not self._in_speech:
                if loud:
                    self._in_speech = True
                    self._speech_start = idx
                    self._silence_run = 0
                continue

            if loud:
                self._silence_run = 0
            else:
                self._silence_run += 1

            long_enough = idx - self._speech_start >= max_frames
            if self._silence_run >= tail_frames or long_enough:
                end = idx - self._silence_run if self._silence_run else idx
                found.append((self._speech_start, end))
                self._in_speech = False
                self._silence_run = 0

        self._seen = total

        for start, end in found:
            piece = self._cut(start, end)
            if piece is not None:
                out.append(piece)

        # Всё до конца последней фразы больше не нужно.
        if found:
            self._drop_before(found[-1][1] + 1)

        # Тишину копить незачем: держим только хвост под будущую фразу.
        if not self._in_speech:
            keep = int((self.silence_tail + PAD) * self.sample_rate)
            if len(self._buf) > keep:
                self._drop_before((len(self._buf) - keep) // self._frame)
        return out

    def flush(self) -> list[Speech]:
        """Отдать недоговорённое. Зовём по кнопке «стоп»."""
        out: list[Speech] = []
        if self._in_speech:
            piece = self._cut(self._speech_start, len(self._buf) // self._frame)
            if piece is not None:
                out.append(piece)
        self.reset()
        return out

    def reset(self) -> None:
        """Забыть всё: новая встреча начинается с чистого листа."""
        self._buf = np.zeros(0, dtype=np.float32)
        self._buf_offset = 0.0
        self._seen = 0
        self._in_speech = False
        self._silence_run = 0
        self._speech_start = 0
        self._noise = 0.0
        self._quiet.clear()
