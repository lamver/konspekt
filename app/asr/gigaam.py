"""Распознавание русской речи через GigaAM v3 CTC в ONNX.

Почему именно так:
- `onnx-asr` тянет за собой только numpy и onnxruntime. Ни torch, ни NeMo,
  ни ffmpeg, поэтому всё это потом влезет в один бинарник.
- Берём квантованную в int8 версию: 224 МБ против 885 МБ у fp32 при
  почти том же качестве. На слабом интернете это решающий довод.
- CTC, а не RNN-T: заметно быстрее на процессоре и состоит из одного
  файла, который проще качать и проверять.
- Вариант e2e: та же модель, но сразу с пунктуацией, заглавными
  буквами и числами цифрами. Весит столько же, а транскрипт после
  него читается глазами, а не расшифровывается из сплошного потока слов.

Модель загружается лениво, при первом чанке. Иначе запуск приложения
ждал бы несколько секунд ради подсистемы, которая может не понадобиться.
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

# Имя модели в каталоге onnx-asr и репозиторий на Hugging Face.
MODEL_NAME = "gigaam-v3-e2e-ctc"
MODEL_REPO = "istupakov/gigaam-v3-onnx"
QUANTIZATION = "int8"
# Каталог с весами внутри models_dir(). Держим рядом с именем модели:
# иначе при смене модели легко забыть поправить путь в сервисе.
MODEL_DIR_NAME = "gigaam-v3-e2e"
# Короче этого модель отдаёт пустую строку. Дополняем тишиной до предела.
MIN_INPUT_SECONDS = 6.0

# Файлы, без которых модель не заработает. Список нужен и для скачивания,
# и для проверки «а всё ли уже на диске».
MODEL_FILES = (
    "v3_e2e_ctc.int8.onnx",
    "v3_e2e_ctc.yaml",
    "v3_e2e_ctc_vocab.txt",
    "config.json",
)

# Общий вес всех файлов. Нужен только чтобы показать честные проценты
# до того, как сервер ответит с Content-Length.
MODEL_TOTAL_BYTES = 224_724_330


class ModelMissing(RuntimeError):
    """Весов нет на диске. Не ошибка, а повод предложить скачивание."""


class GigaamTranscriber:
    """Русский ASR. Один чанк на входе, один сегмент на выходе."""

    name = "gigaam-v3-e2e-ctc"
    languages = ("ru",)

    def __init__(self, model_dir: Path, auto_load: bool = True) -> None:
        self.model_dir = Path(model_dir)
        self.auto_load = auto_load
        self._model: Any = None
        # Модель грузится долго, а чанки могут прийти сразу с двух дорожек:
        # замок не даёт начать загрузку дважды.
        self._lock = threading.Lock()
        self._load_failed: str | None = None

    # --- готовность ------------------------------------------------------

    def is_downloaded(self) -> bool:
        return all((self.model_dir / f).exists() for f in MODEL_FILES)

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def supports(self, lang: str) -> bool:
        return lang.lower().startswith("ru")

    def load(self) -> None:
        """Поднять модель в память. Идемпотентно."""
        with self._lock:
            if self._model is not None:
                return
            if not self.is_downloaded():
                raise ModelMissing(
                    f"Модель не скачана: положите файлы {', '.join(MODEL_FILES)} в {self.model_dir}"
                )
            import onnx_asr  # импорт внутри: без модели он не нужен

            log.info("Загружаем %s из %s", MODEL_NAME, self.model_dir)
            self._model = onnx_asr.load_model(
                MODEL_NAME, str(self.model_dir), quantization=QUANTIZATION
            )
            log.info("Модель загружена")

    def unload(self) -> None:
        with self._lock:
            self._model = None

    # --- распознавание ---------------------------------------------------

    def transcribe(
        self,
        pcm: np.ndarray | bytes,
        sample_rate: int = SAMPLE_RATE,
        meeting_id: str = "",
        offset: float = 0.0,
        speaker: str = "them",
    ) -> Iterable[TranscriptSegment]:
        if self._model is None:
            if not self.auto_load:
                return []
            if self._load_failed:
                # Один раз не смогли — не долбимся в это на каждом чанке.
                return []
            try:
                self.load()
            except Exception as exc:
                self._load_failed = str(exc)
                log.error("Модель не загрузилась: %s", exc)
                return []

        waveform = _to_float32(pcm)
        if waveform.size == 0:
            return []
        duration = waveform.size / float(sample_rate)

        # Куски короче примерно пяти секунд GigaAM возвращает пустыми:
        # свёрточный энкодер рассчитан на длинный вход, и коротким просто
        # не хватает кадров. Проверено: одна и та же фраза длиной 4 с даёт
        # пустоту, а дополненная тишиной до 6 с распознаётся целиком.
        # Раньше это не мешало, потому что захват всегда слал куски
        # фиксированной длины, а с поиском речи фразы стали короткими.
        need = int(MIN_INPUT_SECONDS * sample_rate) - waveform.size
        if need > 0:
            waveform = np.concatenate(
                [waveform, np.zeros(need, dtype=np.float32)]
            )

        try:
            text = self._model.recognize(waveform, sample_rate=sample_rate)
        except Exception:
            log.exception("Ошибка распознавания чанка")
            return []

        text = (text or "").strip()
        if not text:
            # Тишина или шум: пустой сегмент в транскрипте не нужен.
            return []

        return [
            TranscriptSegment(
                meeting_id=meeting_id,
                speaker=Speaker(speaker) if speaker in ("me", "them") else Speaker.THEM,
                text=text,
                start=offset,
                end=offset + duration,
                lang="ru",
            )
        ]


def _to_float32(pcm: np.ndarray | bytes) -> np.ndarray:
    """int16 из захвата → float32 [-1, 1], которого ждёт модель."""
    if isinstance(pcm, (bytes, bytearray)):
        pcm = np.frombuffer(pcm, dtype=np.int16)
    data = np.asarray(pcm)
    if data.dtype == np.int16:
        return (data.astype(np.float32) / 32768.0).reshape(-1)
    return data.astype(np.float32).reshape(-1)
