"""Распознавание нерусской речи через Whisper base в ONNX.

Зачем отдельная модель. GigaAM знает только русский, а на встречах
случаются английские фразы и целые англоязычные участники. Whisper base
знает 99 языков, в int8 весит около 80 МБ вместе с декодером и на
процессоре работает достаточно быстро для коротких фраз.

Не только английский. Язык мы модели не навязываем: Whisper сам слышит,
что звучит, и немецкая или французская реплика распознаётся так же
хорошо, как английская. Код языка для пометки приходит снаружи, от
роутера, который всё равно спрашивал определителя языка.

Почему base, а не small. Small точнее, но весит 250 МБ в int8 и на
процессоре обрабатывает фразу в несколько раз дольше. Для реплики на
чужом языке посреди русской встречи важнее, чтобы она вообще появилась в
транскрипте латиницей, а не была записана кириллицей как «холло дис из».

Грузится лениво, при первой нерусской фразе: на встрече целиком по-русски
он не нужен вовсе.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from ..core.models import Speaker, TranscriptSegment
from .base import SAMPLE_RATE

log = logging.getLogger(__name__)

MODEL_NAME = "whisper"
MODEL_REPO = "onnx-community/whisper-base"
MODEL_DIR_NAME = "whisper-base"
QUANTIZATION = "int8"
MODEL_TOTAL_BYTES = 79_000_000

MODEL_FILES = (
    "onnx/encoder_model_int8.onnx",
    "onnx/decoder_model_merged_int8.onnx",
    "vocab.json",
    "added_tokens.json",
    "config.json",
    "generation_config.json",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "normalizer.json",
    "merges.txt",
)

# Whisper обучен на кусках по 30 секунд и короткий вход дополняет сам,
# но совсем крошечные обрывки лучше не подавать: он начинает выдумывать.
MIN_INPUT_SECONDS = 1.0


class WhisperMissing(RuntimeError):
    """Весов нет на диске."""


class WhisperTranscriber:
    """Распознавание нерусской речи."""

    name = "whisper-base"
    # Что модель умеет разбирать уверенно. Список ничего не ограничивает:
    # Whisper знает 99 языков и определяет язык сам, это просто те, что
    # встречаются чаще всего и на которых base держится прилично.
    languages = ("en", "de", "fr", "es", "it", "pt", "nl", "pl", "tr", "zh", "ja")

    def __init__(self, model_dir: Path, auto_load: bool = True) -> None:
        self.model_dir = Path(model_dir)
        self.auto_load = auto_load
        self._model: Any = None
        self._lock = threading.Lock()
        self._load_failed: str | None = None

    def is_downloaded(self) -> bool:
        return all((self.model_dir / f).exists() for f in MODEL_FILES)

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def supports(self, lang: str) -> bool:
        return not lang.lower().startswith("ru")

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            if not self.is_downloaded():
                raise WhisperMissing(f"Whisper не скачан в {self.model_dir}")
            import onnx_asr

            log.info("Загружаем Whisper из %s", self.model_dir)
            self._model = onnx_asr.load_model(
                MODEL_NAME, str(self.model_dir), quantization=QUANTIZATION
            )
            log.info("Whisper загружен")

    def unload(self) -> None:
        with self._lock:
            self._model = None

    def transcribe(
        self,
        pcm: np.ndarray | bytes,
        sample_rate: int = SAMPLE_RATE,
        meeting_id: str = "",
        offset: float = 0.0,
        speaker: str = "them",
        lang: str = "",
    ) -> Iterable[TranscriptSegment]:
        if self._model is None:
            if not self.auto_load or self._load_failed:
                return []
            try:
                self.load()
            except Exception as exc:
                self._load_failed = str(exc)
                log.error("Whisper не загрузился: %s", exc)
                return []

        waveform = _to_float32(pcm)
        if waveform.size < int(MIN_INPUT_SECONDS * sample_rate):
            return []
        duration = waveform.size / float(sample_rate)

        try:
            text = self._model.recognize(waveform, sample_rate=sample_rate)
        except Exception:
            log.exception("Ошибка распознавания через Whisper")
            return []

        text = (text or "").strip()
        if not text:
            return []
        text = _clean(text)
        if not text:
            return []

        return [
            TranscriptSegment(
                meeting_id=meeting_id,
                speaker=Speaker(speaker) if speaker in ("me", "them") else Speaker.THEM,
                text=text,
                start=offset,
                end=offset + duration,
                lang=lang or "",
            )
        ]


def _to_float32(pcm: np.ndarray | bytes) -> np.ndarray:
    if isinstance(pcm, (bytes, bytearray)):
        pcm = np.frombuffer(pcm, dtype=np.int16)
    data = np.asarray(pcm)
    if data.dtype == np.int16:
        return (data.astype(np.float32) / 32768.0).reshape(-1)
    return data.astype(np.float32).reshape(-1)


def _clean(text: str) -> str:
    """Убрать зацикливание и мусор.

    Whisper на тишине и обрывках уходит в петлю: «In In In In In» или
    «Thank you thank you thank you». В транскрипте это хуже, чем ничего,
    потому что выглядит настоящей репликой. Ищем повторы не только
    одного слова, но и коротких групп: именно группами модель зацикливается
    чаще всего.
    """
    words = text.split()
    if len(words) < 4:
        return text

    cleaned = _drop_repeats(words)
    # Если после чистки осталась четверть исходного, это была петля.
    if len(cleaned) * 4 < len(words):
        return ""
    result = " ".join(cleaned).strip()
    # Строка из одних знаков препинания смысла не несёт.
    if not any(ch.isalnum() for ch in result):
        return ""
    return result


def _drop_repeats(words: list[str], max_group: int = 4) -> list[str]:
    """Схлопнуть подряд идущие повторы групп слов.

    Две одинаковые группы подряд оставляем: живая речь бывает такой.
    Всё, что дальше, отбрасываем. Из нескольких подходящих длин группы
    берём ту, что покрывает больше слов: «thank you thank you thank you»
    это петля из пары, а не из шести отдельных слов.
    """
    lower = [w.lower() for w in words]
    out: list[str] = []
    i = 0
    while i < len(words):
        best_size = 0
        best_count = 0
        for size in range(1, max_group + 1):
            if i + size * 2 > len(words):
                break
            group = lower[i:i + size]
            count = 1
            j = i + size
            while j + size <= len(words) and lower[j:j + size] == group:
                count += 1
                j += size
            # Трижды подряд это уже петля, дважды ещё живая речь.
            if count >= 3 and count * size > best_size * best_count:
                best_size, best_count = size, count

        if best_size:
            out.extend(words[i:i + best_size * 2])   # оставляем две копии
            i += best_size * best_count
        else:
            out.append(words[i])
            i += 1
    return out
