"""Захват звука."""

from .capture import AudioCapture, NullCapture
from .devices import TRACK_ME, TRACK_THEM, Device
from .wasapi import WasapiCapture

__all__ = [
    "AudioCapture",
    "NullCapture",
    "WasapiCapture",
    "Device",
    "TRACK_ME",
    "TRACK_THEM",
]
