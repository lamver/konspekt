"""Распознавание речи."""

from .base import NullTranscriber, Transcriber, SAMPLE_RATE
from .download import ModelDownloader
from .gigaam import (
    MODEL_FILES,
    MODEL_REPO,
    MODEL_TOTAL_BYTES,
    GigaamTranscriber,
    ModelMissing,
)
from .queue import TranscriptionQueue

__all__ = [
    "Transcriber",
    "NullTranscriber",
    "GigaamTranscriber",
    "TranscriptionQueue",
    "ModelDownloader",
    "ModelMissing",
    "MODEL_REPO",
    "MODEL_FILES",
    "MODEL_TOTAL_BYTES",
    "SAMPLE_RATE",
]
