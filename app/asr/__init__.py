"""Распознавание речи."""

from .base import NullTranscriber, Transcriber, SAMPLE_RATE
from .download import ModelDownloader
from .gigaam import (
    MODEL_DIR_NAME,
    СТАРАЯ_ПАПКА_МОДЕЛИ,
    MODEL_FILES,
    MODEL_REPO,
    MODEL_TOTAL_BYTES,
    GigaamTranscriber,
    ModelMissing,
)
from .queue import MissedSpan, TranscriptionQueue

__all__ = [
    "Transcriber",
    "NullTranscriber",
    "GigaamTranscriber",
    "TranscriptionQueue",
    "MissedSpan",
    "ModelDownloader",
    "ModelMissing",
    "MODEL_DIR_NAME",
    "СТАРАЯ_ПАПКА_МОДЕЛИ",
    "MODEL_REPO",
    "MODEL_FILES",
    "MODEL_TOTAL_BYTES",
    "SAMPLE_RATE",
]
