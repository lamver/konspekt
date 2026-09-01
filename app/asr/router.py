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

Сверка по тексту (issue #2). Акустика иногда уверенно ошибается: на
боевой записи 4-секундный кусок был опознан как литовский с уверенностью
0.93, и дальше вся дорожка застряла в литовском до конца часовой встречи
— память о языке ничем не ограничена и не перепроверяется. Как это чинит
Handy (cjpais/Handy, `audio_toolkit/lang_id.rs`): язык там определяется
не только по звуку, а сверяется с языком уже готового текста через
`whatlang`. Текст несёт больше сигнала, чем 4 секунды акустики: «Ну так,
сначала, ну все» текстовым анализатором с русским не спутать, а голосом
можно. Мы делаем так же: если текст фразы (от двух слов, короче
ненадёжно и текстом, и звуком — проверено на боевых репликах) явно
кириллический, а акустика отправила фразу в Whisper — переписываем язык
сегмента и память дорожки на кириллический. Так одна акустическая ошибка
живёт максимум одну фразу, а не до конца встречи, независимо от того,
какой язык оказался ошибочным: ничего не привязано к русскому жёстко.
"""

from __future__ import annotations

import logging
import threading
from typing import Iterable

import numpy as np
import py3langid as langid

from ..core.models import TranscriptSegment
from .base import SAMPLE_RATE, Transcriber
from .langid import CYRILLIC_LANGS, LanguageDetector

log = logging.getLogger(__name__)

# Короче этого текстовая сверка не надёжнее акустики: на «Да.», «Ну.»
# py3langid угадывает почти случайно (проверено на боевых репликах),
# а от двух слов ни разу не ошибся на настоящей русской речи пользователя.
MIN_WORDS_FOR_TEXT_CHECK = 2


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
        #
        # Но кириллические соседи русского (uk, be, bg...) — особый
        # случай. Отправлять их некуда, кроме GigaAM, а GigaAM говорит
        # только по-русски: что бы ни услышала акустика, на выходе будет
        # русский текст. Ставить на такую реплику ярлык «УК» значит
        # врать человеку — он видит в расшифровке украинский, которого
        # там нет. На боевой базе так помечено 596 реплик из 4294 (13%),
        # и у 592 из них текст оказался русским.
        #
        # Поэтому язык берём по тому, кто на самом деле распознавал:
        # раз это русская модель, то и язык русский.
        итоговый = self.fallback_lang if russian else lang
        for segment in segments:
            segment.lang = итоговый
            self._recheck_by_text(segment, speaker)
        return segments

    def _recheck_by_text(self, segment: TranscriptSegment, speaker: str) -> None:
        """Поймать акустическую ошибку по уже готовому тексту.

        Только в одну сторону: GigaAM физически не выдаёт латиницу, а
        Whisper на кириллической фразе, которую акустика ошибочно увела
        к нему, честно пытается расслышать что-то нерусское. Если сам
        текст оказался явно кириллическим, акустика соврала — чиним и
        сегмент, и память дорожки, чтобы ошибка не тянулась дальше.
        """
        if segment.lang in CYRILLIC_LANGS:
            return  # уже в GigaAM, сверять не с чем
        words = segment.text.split()
        if len(words) < MIN_WORDS_FOR_TEXT_CHECK:
            return  # короткий текст текстом определяется не надёжнее звука
        try:
            text_lang, _ = langid.classify(segment.text)
        except Exception:
            log.exception("Текстовая сверка языка упала, оставляем как есть")
            return
        if text_lang not in CYRILLIC_LANGS:
            return
        log.info(
            "Текст фразы кириллический (%s), а акустика отправила её в %s: "
            "поправляю дорожку %s",
            text_lang, segment.lang, speaker,
        )
        segment.lang = text_lang
        with self._lock:
            self._last[speaker] = text_lang

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
