"""Проверка: подробный журнал действительно объясняет молчание.

Диагностику я добавил, но не убедился, что она доходит до файла. Это
ровно та ошибка, на которой в 0.7.0 сгорел выпуск: код писался, а до
человека ничего не доезжало.

Гоняем настоящую дорожку захвата с музыкой (программа обязана
промолчать) и требуем, чтобы в журнале осталось объяснение почему.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

import testenv  # noqa: F401  русский вывод в консоли Windows

from app.audio import devices, wasapi

ЧАСТОТА = 16000
БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


class Играющий:
    """Ровная музыка: программа обязана промолчать."""

    def __init__(self) -> None:
        t = np.arange(int(60 * ЧАСТОТА), dtype=np.float32) / ЧАСТОТА
        основа = 0.18 * np.sin(2 * np.pi * 220 * t)
        вторая = 0.14 * np.sin(2 * np.pi * 277 * t)
        оболочка = 0.85 + 0.15 * np.sin(2 * np.pi * 0.5 * t)
        self._поток = ((основа + вторая) * оболочка).astype(np.float32)
        self._поз = 0

    def recorder(self, **_):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def record(self, numframes):
        time.sleep(0.001)
        конец = self._поз + numframes
        if конец > len(self._поток):
            self._поз = 0
            конец = numframes
        кусок = self._поток[self._поз:конец]
        self._поз = конец
        return кусок.copy()


def main() -> int:
    # Ловим журнал в память, как это сделал бы файл.
    записи: list[str] = []

    class Ловец(logging.Handler):
        def emit(self, запись: logging.LogRecord) -> None:
            записи.append(запись.getMessage())

    ловец = Ловец()
    ловец.setLevel(logging.DEBUG)
    логгер = logging.getLogger("app.audio.wasapi")
    прежний = логгер.level
    логгер.setLevel(logging.DEBUG)
    логгер.addHandler(ловец)

    файл = Path(__file__).parent / "_диагностика.wav"
    дорожка = wasapi._Track(
        name=devices.TRACK_THEM,
        opener=lambda: Играющий(),
        wav_path=файл,
        chunk_seconds=1.0,
        on_chunk=None,
    )
    дорожка.start()
    # Ждём, пока наберётся хотя бы одно окно разбора.
    предел = time.monotonic() + 30.0
    while time.monotonic() < предел:
        if any("Системный звук, окно" in з for з in записи):
            break
        time.sleep(0.1)
    дорожка.stop()
    логгер.removeHandler(ловец)
    логгер.setLevel(прежний)
    файл.unlink(missing_ok=True)

    разборы = [з for з in записи if "Системный звук, окно" in з]
    проверить(bool(разборы),
              "в подробном журнале есть разбор окна: молчание программы "
              "теперь можно объяснить, а не гадать")
    if разборы:
        первый = разборы[0]
        for что in ("доля=", "переключений=", "тембр="):
            проверить(что in первый,
                      f"в разборе есть {что.rstrip('=')} — без него причина не видна")
        проверить("речь=False" in первый,
                  f"на музыке разбор честно говорит «речь=False» ({первый[:90]})")

    вопросы = [з for з in записи if "слышен разговор" in з]
    проверить(not вопросы,
              f"на музыке вопрос не задавался (было {len(вопросы)})")

    if БЕДЫ:
        print(f"\nНе прошло проверок: {len(БЕДЫ)}")
        return 1
    print("\nПодробный журнал объясняет, почему программа промолчала.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
