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
человек редко переключает язык посреди разговора. А если и этого нет
(самая первая фраза встречи), берём язык распознавания по умолчанию из
настроек. Раньше тут было жёстко зашитое «ru» — верно только пока у
Konspekt один пользователь, говорящий по-русски. Испаноязычному
человеку такой отказ ломает первую же фразу.

Пометка языка. Whisper многоязычный: он одинаково распознаёт немецкую,
французскую и любую другую речь, сам определяя язык по звуку. Поэтому в
сегменте пишем то, что услышал определитель языка (de, fr, es...), а не
одно и то же «en» на всё нерусское. Иначе саммари английской и немецкой
реплики выглядят одинаково, а по транскрипту нельзя понять, что звучало.
"""

from __future__ import annotations

import logging
import threading
from typing import Iterable

import numpy as np

from ..core.models import TranscriptSegment
from .base import SAMPLE_RATE, Transcriber
from .langid import CYRILLIC_LANGS, LanguageDetector

log = logging.getLogger(__name__)


class LanguageRouter:
    """Распознаватель, который сам выбирает модель под язык фразы."""

    name = "router"

    def __init__(
        self,
        russian: Transcriber,
        detector: LanguageDetector | None = None,
        foreign: Transcriber | None = None,
        fallback_lang: str = "ru",
    ) -> None:
        self.russian = russian
        self.detector = detector
        self.foreign = foreign
        # Язык на случай, когда определить нечего и вспомнить нечего:
        # самая первая фраза встречи. Берётся из настроек распознавания,
        # а не зашит намертво, иначе не по-русски говорящий человек
        # ломает себе первую же фразу.
        self.fallback_lang = fallback_lang or "ru"
        # Последний язык каждой дорожки: им подменяем неуверенные ответы.
        self._last: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def languages(self) -> tuple[str, ...]:
        if self.foreign is None:
            return ("ru",)
        return ("ru",) + tuple(getattr(self.foreign, "languages", ("en",)))

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
        lang = self._decide(pcm, sample_rate, speaker)
        russian = lang in CYRILLIC_LANGS
        engine = self.russian if russian else (self.foreign or self.russian)

        segments = list(engine.transcribe(
            pcm, sample_rate=sample_rate, meeting_id=meeting_id,
            offset=offset, speaker=speaker,
        ))
        # Помечаем язык честно: по нему потом строится саммари, и знать,
        # что реплика была немецкой, а не просто «нерусской», полезно.
        for segment in segments:
            segment.lang = lang
        return segments

    def _decide(self, pcm, sample_rate: int, speaker: str) -> str:
        """Код языка фразы. Ошибаться в сторону настроенного языка безопаснее."""
        if self.detector is None or self.foreign is None:
            return self.fallback_lang
        try:
            verdict = self.detector.detect(pcm, sample_rate)
        except Exception:
            log.exception("Определение языка упало, берём язык по умолчанию")
            return self.fallback_lang

        with self._lock:
            if verdict is None:
                # Не разобрали: продолжаем на языке прошлой фразы дорожки,
                # а если её ещё не было — на языке по умолчанию.
                return self._last.get(speaker, self.fallback_lang)
            lang = verdict[0]
            self._last[speaker] = lang
        return lang
