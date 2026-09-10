# -*- coding: utf-8 -*-
"""Приёмка задачи №28 на настоящей программе, а не на её частях.

Зачем отдельно от других проверок. Всё, что было до этого, проверяло
части поодиночке: детектор отдельно, дорожку отдельно, окно отдельно.
Ровно так в 0.7.0 и вышла беда — каждая часть работала, а собранная
программа не делала ничего, потому что дорога между частями была
разорвана.

Здесь всё настоящее: `AppService`, `WasapiCapture`, `Api` — тот самый
мост, который зовёт окно. Подменяем единственное, что подменить
обязаны: драйвер звуковой карты. Настоящую карту в проверке не завести,
и это ограничение честно записано, а не спрятано.

Сценарий человеческий, от начала до конца:
  1. Человек создал встречу и начал запись.
  2. В системном ��вуке заиграл чужой ролик.
  3. Программа спросила.
  4. Человек нажал «не писать».
  5. Встреча продолжает писаться с микрофона, файл прошлого цел,
     расшифровка на месте, а следующая встреча системный звук уже не
     трогает.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import numpy as np

# Свой каталог данных, и обязательно до импорта самой программы: иначе
# проверка запишет выключенный системный звук в настоящие настройки
# человека и он потом будет гадать, почему собеседник перестал
# записываться.
_ПЕСОЧНИЦА = Path(tempfile.mkdtemp(prefix="konspekt-приёмка-"))
os.environ["KONSPEKT_DATA_DIR"] = str(_ПЕСОЧНИЦА)

import testenv  # noqa: F401  русский вывод в консоли Windows

from app.audio import devices, wasapi
from app.core import settings as settings_mod
from app.core.events import RECORDING_FOREIGN_SPEECH, bus
from app.core.service import AppService
from app.storage import Store
from app.ui.api import Api

ЧАСТОТА = 16000
БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


def _речь(секунд: float = 90.0) -> np.ndarray:
    """Разговор: слоги двух видов вперемежку с паузами."""
    rng = np.random.default_rng(7)
    куски: list[np.ndarray] = []
    время = 0.0
    while время < секунд:
        длина = rng.uniform(0.12, 0.22)
        n = int(длина * ЧАСТОТА)
        ог = np.hanning(n).astype(np.float32)
        if rng.random() > 0.35:
            t = np.arange(n, dtype=np.float32) / ЧАСТОТА
            основа = rng.uniform(90, 220)
            ядро = np.sin(2 * np.pi * основа * t).astype(np.float32)
        else:
            ядро = rng.normal(0, 1.0, n).astype(np.float32)
        куски.append((0.25 * ядро * ог).astype(np.float32))
        время += длина
        пауза = rng.uniform(0.04, 0.10) if rng.random() > 0.15 else rng.uniform(0.35, 0.6)
        куски.append(np.zeros(int(пауза * ЧАСТОТА), dtype=np.float32))
        время += пауза
    return np.concatenate(куски)


class ПоддельнаяКарта:
    """Единственная подмена: сама звуковая карта.

    Всё выше по течению — настоящий код программы.
    """

    def __init__(self, поток: np.ndarray) -> None:
        self._поток = поток
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
    речь = _речь()
    # Подменяем открытие устройств на уровне драйвера: сама программа
    # об этом не знает и работает как обычно.
    было_мик, было_луп = devices.open_microphone, devices.open_loopback
    devices.open_microphone = lambda _id: ПоддельнаяКарта(речь)
    devices.open_loopback = lambda _id: ПоддельнаяКарта(речь)

    tmp = Path(tempfile.mkdtemp())
    служба = AppService(store=Store(str(tmp / "test.db")))
    # Распознавание выключаем: проверяем поведение звука, а не модель.
    служба.settings.asr.enabled = False
    служба.settings.audio.capture_mic = True
    служба.settings.audio.capture_system = True
    служба.capture = wasapi.WasapiCapture(
        capture_mic=True, capture_system=True, chunk_seconds=1.0,
    )
    мост = Api(служба)

    вопросы: list[dict] = []
    отписаться = bus.on(RECORDING_FOREIGN_SPEECH, вопросы.append)

    try:
        встреча = служба.create_meeting("Приёмка системного звука")
        mid = встреча["id"]
        служба.start_recording(mid)
        проверить(служба.is_recording, "запись началась")

        # Ждём вопроса от настоящей программы.
        предел = time.monotonic() + 40.0
        while not вопросы and time.monotonic() < предел:
            time.sleep(0.05)

        проверить(bool(вопросы),
                  "настоящая программа спросила про чужую речь в системном звуке")
        if not вопросы:
            return 1

        # Человек нажал «не писать» — ровно тем же вызовом, что и окно.
        ответ = мост.turn_off_system_audio()
        проверить(ответ.get("ok") is True,
                  f"кнопка «не писать» отработала через мост окна ({ответ})")

        проверить(служба.is_recording,
                  "встреча продолжает писаться: человек ответил честно, "
                  "а не потерял запись")

        # Микрофон обязан продолжать писать: проверяем ростом файла.
        время_до = служба.capture._tracks[devices.TRACK_ME].duration
        time.sleep(1.5)
        время_после = служба.capture._tracks[devices.TRACK_ME].duration
        проверить(время_после > время_до,
                  f"микрофон пишет дальше ({время_до:.1f}с → {время_после:.1f}с)")

        # Настройка сохранена на диск: следующая встреча без сюрприза.
        проверить(служба.settings.audio.capture_system is False,
                  "настройка «писать системный звук» выключена")
        заново = settings_mod.load()
        проверить(заново.audio.capture_system is False,
                  "выбор человека пережил перезапуск программы")

        # Записанное до выключения на месте: по нему работает
        # прослушивание реплик.
        файл = служба.capture._paths.get(devices.TRACK_THEM)
        проверить(файл is not None and Path(файл).exists(),
                  "запись системного звука до выключения цела")

        итог = служба.stop_recording()
        проверить(итог is not None, "встреча остановилась штатно")
        проверить(not служба.is_recording, "запись действительно кончилась")

        готовая = служба.get_meeting(mid)
        проверить(готовая is not None, "встреча читается из базы после всего")

        # Вторая встреча: системный звук больше не открывается вовсе.
        вопросы.clear()
        служба.capture = wasapi.WasapiCapture(
            capture_mic=служба.settings.audio.capture_mic,
            capture_system=служба.settings.audio.capture_system,
            chunk_seconds=1.0,
        )
        вторая = служба.create_meeting("Следующая встреча")
        служба.start_recording(вторая["id"])
        time.sleep(2.0)
        проверить(devices.TRACK_THEM not in служба.capture._tracks,
                  "следующая встреча системный звук уже не пишет")
        служба.stop_recording()

    finally:
        try:
            if служба.is_recording:
                служба.stop_recording()
        except Exception:
            pass
        отписаться()
        devices.open_microphone, devices.open_loopback = было_мик, было_луп

    if БЕДЫ:
        print(f"\nНе прошло проверок: {len(БЕДЫ)}")
        return 1
    print("\nПриёмка пройдена: настоящая программа спрашивает и слушается ответа.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
