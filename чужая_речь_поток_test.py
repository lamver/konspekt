# -*- coding: utf-8 -*-
"""Дорожка системного звука спрашивает человека, а не решает за него.

Проверяем питоновскую половину задачи №28: событие рождается из живого
потока захвата, а ответ человека доводится до дела.

Устройства поддельные, звуковая карта не нужна: дорожка работает с любым
объектом, у которого есть `record`, и это единственный способ проверить
поведение, не заводя настоящую встречу.
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


class Говорящий:
    """Устройство, из которого льётся похожая на речь дорожка."""

    name = "Чужой разговор"

    def __init__(self) -> None:
        self._поток = self._сделать_речь()
        self._поз = 0

    @staticmethod
    def _сделать_речь(секунд: float = 60.0) -> np.ndarray:
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


class Играющий(Говорящий):
    """Ровная музыка: спрашивать не о чем."""

    name = "Музыка"

    @staticmethod
    def _сделать_речь(секунд: float = 60.0) -> np.ndarray:
        t = np.arange(int(секунд * ЧАСТОТА), dtype=np.float32) / ЧАСТОТА
        основа = 0.18 * np.sin(2 * np.pi * 220 * t)
        вторая = 0.14 * np.sin(2 * np.pi * 277 * t)
        оболочка = 0.85 + 0.15 * np.sin(2 * np.pi * 0.5 * t)
        return ((основа + вторая) * оболочка).astype(np.float32)


def прогнать(устройство, имя=devices.TRACK_THEM, секунд=25.0):
    """Покрутить дорожку и вернуть пойманные вопросы."""
    пойманное: list[dict] = []
    отписаться = bus.on(RECORDING_FOREIGN_SPEECH, пойманное.append)
    файл = Path(__file__).parent / "_чужая.wav"
    дорожка = wasapi._Track(
        name=имя,
        opener=lambda: устройство,
        wav_path=файл,
        chunk_seconds=1.0,
        on_chunk=None,
    )
    дорожка.start()
    предел = time.monotonic() + секунд
    while time.monotonic() < предел and not пойманное:
        time.sleep(0.02)
    дорожка.stop()
    отписаться()
    файл.unlink(missing_ok=True)
    return пойманное, дорожка


def проверить_вопрос() -> None:
    пойманное, _ = прогнать(Говорящий())
    проверить(bool(пойманное),
              "о разговоре в системном звуке человека спрашивают")
    if пойманное:
        проверить(пойманное[0].get("track") == devices.TRACK_THEM,
                  "в событии указана дорожка системного звука")
        проверить("признаки" in пойманное[0],
                  "в событии есть признаки, по которым принято решение")


def проверить_молчание_на_музыке() -> None:
    """Ложная тревога хуже пропуска: она перебивает человека посреди встречи."""
    пойманное, _ = прогнать(Играющий(), секунд=20.0)
    проверить(not пойманное, f"на музыке человека не дёргают (было {len(пойманное)})")


def проверить_микрофон_не_трогаем() -> None:
    """В микрофоне речь — это и есть смысл записи, спрашивать нечего."""
    пойманное, _ = прогнать(Говорящий(), имя=devices.TRACK_ME, секунд=20.0)
    проверить(not пойманное,
              f"про речь в микрофоне не спрашивают (было {len(пойманное)})")


def проверить_ответ_человека() -> None:
    """Ответ человека затыкает вопрос, даже если тот ещё не прозвучал.

    Тонкость: после первого вопроса наблюдатель и так молчит, поэтому
    проверять «не повторился» бесполезно — оно верно само по себе.
    Затыкаем до того, как вопрос прозвучал: человек мог ответить на
    прошлой встрече или снять галочку в настройках. Вот тогда молчание
    что-то доказывает.
    """
    пойманное: list[dict] = []
    отписаться = bus.on(RECORDING_FOREIGN_SPEECH, пойманное.append)
    файл = Path(__file__).parent / "_чужая2.wav"
    дорожка = wasapi._Track(
        name=devices.TRACK_THEM,
        opener=lambda: Говорящий(),
        wav_path=файл,
        chunk_seconds=1.0,
        on_chunk=None,
    )
    # Затыкаем сразу, ещё до старта: вопроса быть не должно вовсе.
    дорожка.замолчать_про_чужую_речь()
    дорожка.start()
    предел = time.monotonic() + 25.0
    while time.monotonic() < предел:
        time.sleep(0.05)
    дорожка.stop()
    отписаться()
    файл.unlink(missing_ok=True)
    проверить(not пойманное,
              f"после ответа человека вопрос не задаётся (было {len(пойманное)})")


def проверить_выключение() -> None:
    """Выключение системного звука не должно останавливать встречу."""
    захват = wasapi.WasapiCapture(capture_mic=True, capture_system=True)
    захват._tracks = {
        devices.TRACK_ME: wasapi._Track(
            name=devices.TRACK_ME, opener=lambda: Говорящий(),
            wav_path=Path(__file__).parent / "_вык_me.wav",
            chunk_seconds=1.0, on_chunk=None,
        ),
        devices.TRACK_THEM: wasapi._Track(
            name=devices.TRACK_THEM, opener=lambda: Говорящий(),
            wav_path=Path(__file__).parent / "_вык_them.wav",
            chunk_seconds=1.0, on_chunk=None,
        ),
    }
    захват._paths = {}
    for д in захват._tracks.values():
        д.start()
    time.sleep(0.5)

    ответ = захват.выключить_системный_звук()
    проверить(ответ.get("ok") is True, f"системный звук выключился ({ответ})")
    проверить(devices.TRACK_THEM not in захват._tracks,
              "дорожка системного звука убрана из записи")
    проверить(devices.TRACK_ME in захват._tracks,
              "микрофон продолжает писаться: человек ответил честно, "
              "а не отказался от встречи")
    проверить(захват.capture_system is False,
              "следующая встреча не начнётся с того же сюрприза")

    # Файл уже записанного обязан остаться: по нему работает
    # прослушивание реплик, снести его — сломать кнопку «переслушать».
    путь = захват._paths.get(devices.TRACK_THEM)
    проверить(путь is not None and Path(путь).exists(),
              "записанное до выключения не пропало")

    захват._tracks[devices.TRACK_ME].stop()
    for имя in ("_вык_me.wav", "_вык_them.wav"):
        (Path(__file__).parent / имя).unlink(missing_ok=True)


def проверить_повторное_выключение() -> None:
    """Второй раз выключать нечего, и падать программа не должна."""
    захват = wasapi.WasapiCapture(capture_mic=True, capture_system=False)
    захват._tracks = {}
    захват._paths = {}
    ответ = захват.выключить_системный_звук()
    проверить(ответ.get("ok") is False,
              f"повторное выключение отвечает честно, а не падает ({ответ})")


def main() -> int:
    проверить_вопрос()
    проверить_молчание_на_музыке()
    проверить_микрофон_не_трогаем()
    проверить_ответ_человека()
    проверить_выключение()
    проверить_повторное_выключение()

    if БЕДЫ:
        print(f"\nНе прошло проверок: {len(БЕДЫ)}")
        return 1
    print("\nСистемный звук спрашивает, а не решает за человека.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
