"""Кому отдать фразу: русский в GigaAM, остальное в Whisper.

Зачем. GigaAM отлично знает русский и ничего не знает про другие языки,
но английскую речь он не отвергает, а записывает кириллицей: «hello this
is the first sentence» превращается в «холло дис из зе фест сентинс». На
встрече с иностранным участником такой транскрипт бесполезен.

Как. Перед распознаванием спрашиваем у определителя языка, что звучит во
фразе, и отправляем её подходящей модели. Whisper поднимается лениво, при
первой нерусской фразе: на встречах целиком по-русски он не понадобится
вовсе, а памяти занимает немало.

Осторожность. Если язык определить не удалось (короткая фраза, шум,
неуверенный ответ), берём язык предыдущей фразы того же говорящего:
человек редко переключает язык посреди разговора. А если и этого нет,
считаем речь русской, потому что программа прежде всего русская.
"""

from __future__ import annotations

import logging
import threading
from typing import Iterable

import numpy as np

from ..core.models import TranscriptSegment
from .base import SAMPLE_RATE, Transcriber
from .langid import LanguageDetector

log = logging.getLogger(__name__)


class LanguageRouter:
    """Распознаватель, который сам выбирает модель под язык фразы."""

    name = "router"

    def __init__(
        self,
        russian: Transcriber,
        detector: LanguageDetector | None = None,
        foreign: Transcriber | None = None,
    ) -> None:
        self.russian = russian
        self.detector = detector
        self.foreign = foreign
        # Последний язык каждой дорожки: им подменяем неуверенные ответы.
        self._last: dict[str, bool] = {}
        self._lock = threading.Lock()

    @property
    def languages(self) -> tuple[str, ...]:
        return ("ru",) if self.foreign is None else ("ru", "en")

    def is_downloaded(self) -> bool:
        return getattr(self.russian, "is_downloaded", lambda: True)()

    @property
    def is_loaded(self) -> bool:
        return bool(getattr(self.russian, "is_loaded", False))

    def supports(self, lang: str) -> bool:
        if lang.lower().startswith("ru"):
            return True
        return self.foreign is not None

    def reset(self) -> None:
        """Забыть языки дорожек. Зовём при старте новой встречи."""
        with self._lock:
            self._last.clear()

    def transcribe(
        self,
        pcm: np.ndarray | bytes,
        sample_rate: int = SAMPLE_RATE,
        meeting_id: str = "",
        offset: float = 0.0,
        speaker: str = "them",
    ) -> Iterable[TranscriptSegment]:
        russian = self._decide(pcm, sample_rate, speaker)
        engine = self.russian if russian else (self.foreign or self.russian)

        segments = list(engine.transcribe(
            pcm, sample_rate=sample_rate, meeting_id=meeting_id,
            offset=offset, speaker=speaker,
        ))
        # Помечаем язык честно: по нему потом строится саммари, и знать,
        # что реплика была не по-русски, полезно.
        for segment in segments:
            segment.lang = "ru" if russian else "en"
        return segments

    def _decide(self, pcm, sample_rate: int, speaker: str) -> bool:
        """Русская ли это речь. Ошибаться в сторону русского безопаснее."""
        if self.detector is None or self.foreign is None:
            return True
        try:
            verdict = self.detector.is_russian(pcm, sample_rate)
        except Exception:
            log.exception("Определение языка упало, считаем речь русской")
            return True

        with self._lock:
            if verdict is None:
                # Не разобрали: продолжаем на языке прошлой фразы.
                return self._last.get(speaker, True)
            self._last[speaker] = verdict
        return verdict
