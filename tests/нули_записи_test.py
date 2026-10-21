"""Запись ждёт кадры от устройства, а не подменяет их нулями.

Библиотека soundcard при задержке в четыре периода устройства (около
40 мс) возвращала нули за время ожидания, а следом и настоящие кадры за
то же время: запись удлинялась, внутри фраз появлялись провалы. Здесь
устройство подменено заглушкой, которая отдаёт кадры с задержкой, и
проверяется, что за секунду стены набирается секунда звука.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import time

import numpy as np

from app.audio import devices

RATE = 16000


class Устройство:
    """Заглушка записи soundcard: кадры появляются порциями по расписанию."""

    def __init__(self, задержка_с: float, порция: int) -> None:
        self.samplerate = RATE
        self.channelmap = [0]
        self._idle_start_time = None
        self._задержка = задержка_с
        self._порция = порция
        self._готово = time.perf_counter() + задержка_с
        self.отдано = 0

    def _capture_available_frames(self) -> int:
        return self._порция if time.perf_counter() >= self._готово else 0

    def _record_chunk(self):
        # Как в soundcard: вызывается, когда кадры есть.
        assert self._capture_available_frames() > 0
        self._готово = time.perf_counter() + self._задержка
        self.отдано += self._порция
        return np.ones(self._порция, dtype="float32")


def main() -> int:
    # --- кадры приходят раз в 120 мс: раньше это считалось молчанием
    у = Устройство(0.12, int(RATE * 0.12))
    devices.терпеливая_запись(у)
    t0 = time.perf_counter()
    кадров = 0
    нулей = 0
    while time.perf_counter() - t0 < 1.0:
        блок = у._record_chunk()
        кадров += блок.size
        нулей += int((блок == 0).sum())
    стена = time.perf_counter() - t0
    assert нулей == 0, f"в записи появились нули ({нулей}) при живом устройстве"
    assert abs(кадров / RATE - стена) < 0.2, f"за {стена:.2f} с стены записано {кадров / RATE:.2f} с"
    print(f"[ok] задержка 120 мс не даёт нулей: {кадров / RATE:.2f} с звука за {стена:.2f} с")

    # --- устройство молчит всерьёз: тишина идёт вровень с часами
    у = Устройство(10.0, 1)
    devices.терпеливая_запись(у, порог_с=0.2)
    t0 = time.perf_counter()
    блок = у._record_chunk()
    стена = time.perf_counter() - t0
    assert блок.size > 0 and not блок.any(), "молчащее устройство должно дать тишину"
    assert abs(блок.size / RATE - стена) < 0.05, f"тишины {блок.size / RATE:.2f} с за {стена:.2f} с"
    print(f"[ok] молчащее устройство даёт тишину по часам: {блок.size / RATE:.2f} с за {стена:.2f} с")

    # --- чужая реализация (macOS) не трогается
    class Другая:
        pass

    д = Другая()
    assert devices.терпеливая_запись(д) is д and not hasattr(д, "_record_chunk")
    print("[ok] запись без WASAPI-внутренностей остаётся как есть")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
