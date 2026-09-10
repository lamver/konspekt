# -*- coding: utf-8 -*-
"""Обратный случай: собеседник заговорил, а системный звук выключен.

Из задачи №28: «человек начал встречу без звука в системе, а через
минуту собеседник заговорил в Zoom — это как раз тот случай, когда
второй канал нужен, и предложить его стоит так же».

Тонкость, ради которой всё и написано: чтобы заметить собеседника,
звук надо слышать. Но записывать его без спроса нельзя — человек ведь
отказался. Поэтому дорожка открывается в режиме «слушаю, но не пишу»:
ни файла, ни расшифровки, только вопрос.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

import testenv  # noqa: F401  русский вывод в консоли Windows

from app.audio import devices, wasapi
from app.core.events import RECORDING_FOREIGN_SPEECH, bus

ЧАСТОТА = 16000
БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


def _речь(секунд: float = 60.0) -> np.ndarray:
    rng = np.random.default_rng(7)
    куски: list[np.ndarray] = []
    время = 0.0
    while время < секунд:
        длина = rng.uniform(0.12, 0.22)
        n = int(длина * ЧАСТОТА)
        ог = np.hanning(n).astype(np.float32)
        if rng.random() > 0.35:
            t = np.arange(n, dtype=np.float32) / ЧАСТОТА
            ядро = np.sin(2 * np.pi * rng.uniform(90, 220) * t).astype(np.float32)
        else:
            ядро = rng.normal(0, 1.0, n).astype(np.float32)
        куски.append((0.25 * ядро * ог).astype(np.float32))
        время += длина
        пауза = rng.uniform(0.04, 0.10) if rng.random() > 0.15 else rng.uniform(0.35, 0.6)
        куски.append(np.zeros(int(пауза * ЧАСТОТА), dtype=np.float32))
        время += пауза
    return np.concatenate(куски)


class Карта:
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


def проверить_слушающую_дорожку() -> None:
    """Слушает, спрашивает, но ничего не записывает."""
    речь = _речь()
    вопросы: list[dict] = []
    отписаться = bus.on(RECORDING_FOREIGN_SPEECH, вопросы.append)
    куски: list[tuple] = []
    файл = Path(__file__).parent / "_обратный.wav"

    дорожка = wasapi._Track(
        name=devices.TRACK_THEM,
        opener=lambda: Карта(речь),
        wav_path=файл,
        chunk_seconds=1.0,
        on_chunk=lambda *а: куски.append(а),
        только_слушать=True,
    )
    дорожка.start()
    # Пока дорожка жива, смотрим, не завела ли она файл. После stop()
    # пустой файл удаляется сам, и по итогу проверить это уже нельзя:
    # «писала, но стёрла» и «не писала вовсе» выглядят одинаково.
    time.sleep(0.5)
    завела_файл = дорожка._writer is not None
    предел = time.monotonic() + 40.0
    while time.monotonic() < предел and not вопросы:
        time.sleep(0.05)
    путь = дорожка.stop()
    отписаться()

    проверить(bool(вопросы),
              "слушающая дорожка заметила собеседника и спросила")
    if вопросы:
        проверить(вопросы[0].get("пишется") is False,
                  f"в вопросе сказано, что звук не пишется ({вопросы[0].get('пишется')})")

    # Главное: человек отказался писать системный звук, и мы его не пишем.
    проверить(not завела_файл,
              "файл на запись даже не открывался: человек отказался, "
              "и звук на диск не попадает")
    проверить(путь is None, f"файла записи не появилось (получили {путь})")
    проверить(not файл.exists(), "на диске ничего не осталось")
    проверить(not куски,
              f"в расшифровку ничего не ушло (кусков: {len(куски)})")
    файл.unlink(missing_ok=True)


def проверить_включение() -> None:
    """Ответ «писать собеседника» поднимает дорожку на ходу."""
    речь = _речь()
    было_мик, было_луп = devices.open_microphone, devices.open_loopback
    devices.open_microphone = lambda _id: Карта(речь)
    devices.open_loopback = lambda _id: Карта(речь)
    try:
        захват = wasapi.WasapiCapture(
            capture_mic=True, capture_system=False, chunk_seconds=1.0,
        )
        захват.start("встреча-обратная")
        time.sleep(1.0)

        трек = захват._tracks.get(devices.TRACK_THEM)
        проверить(трек is not None and трек._только_слушать,
                  "при выключенном звуке дорожка всё равно слушает")

        ответ = захват.включить_системный_звук()
        проверить(ответ.get("ok") is True, f"звук включился на ходу ({ответ})")

        новый = захват._tracks.get(devices.TRACK_THEM)
        проверить(новый is not None and not новый._только_слушать,
                  "дорожка теперь пишет, а не только слушает")
        проверить(захват.capture_system is True,
                  "настройка отражает включённый звук")

        time.sleep(1.5)
        проверить(новый.duration > 0,
                  f"в дорожку пошёл звук ({новый.duration:.1f}с)")

        # Повторное включение не должно ломать уже идущую запись.
        ещё = захват.включить_системный_звук()
        проверить(ещё.get("ok") is False,
                  f"повторное включение отвечает честно ({ещё})")

        захват.stop()
        проверить(not захват.is_recording, "запись остановилась штатно")
    finally:
        devices.open_microphone, devices.open_loopback = было_мик, было_луп


def проверить_слушающая_не_запись() -> None:
    """Слушающая дорожка не должна выдавать себя за запись.

    Если микрофон не открылся, а слушающая открылась, встречи всё равно
    нет. Сказать «запись идёт» здесь значит потерять встречу молча.
    """
    речь = _речь()
    было_мик, было_луп = devices.open_microphone, devices.open_loopback

    def мёртвый(_id):
        raise RuntimeError("микрофон занят")

    devices.open_microphone = мёртвый
    devices.open_loopback = lambda _id: Карта(речь)
    try:
        захват = wasapi.WasapiCapture(
            capture_mic=True, capture_system=False, chunk_seconds=1.0,
        )
        упало = False
        try:
            захват.start("встреча-без-микрофона")
        except RuntimeError:
            упало = True
        проверить(упало,
                  "без микрофона запись честно не начинается, хотя "
                  "слушающая дорожка и открылась")
        проверить(not захват.is_recording,
                  "программа не делает вид, что запись идёт")
    finally:
        devices.open_microphone, devices.open_loopback = было_мик, было_луп


def main() -> int:
    проверить_слушающую_дорожку()
    проверить_включение()
    проверить_слушающая_не_запись()

    if БЕДЫ:
        print(f"\nНе прошло проверок: {len(БЕДЫ)}")
        return 1
    print("\nСобеседник в выключенном звуке замечен, и звук включается на ходу.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
