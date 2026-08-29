"""Интерфейс распознавания речи.

Контракт специально сделан потоковым (чанк на входе, сегменты на выходе),
потому что именно так работает GigaAM CTC в ONNX: ему скармливают
короткие отрезки, нарезанные VAD. Под этот же интерфейс ложится
и Whisper для сербского, и облачные провайдеры.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from ..core.models import TranscriptSegment


@runtime_checkable
class Transcriber(Protocol):
    name: str

    def supports(self, lang: str) -> bool:
        """Знает ли движок этот язык. Нужно роутеру: ru → GigaAM, sr → Whisper."""
        ...

    def transcribe(
        self, pcm: bytes, sample_rate: int, meeting_id: str, offset: float, speaker: str
    ) -> Iterable[TranscriptSegment]:
        ...


class NullTranscriber:
    """Заглушка этапа 1: молча ничего не распознаёт."""

    name = "null"

    def supports(self, lang: str) -> bool:
        return True

    def transcribe(
        self, pcm: bytes, sample_rate: int, meeting_id: str, offset: float, speaker: str
    ) -> Iterable[TranscriptSegment]:
        return []
