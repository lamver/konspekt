"""Повторный старт записи: тот самый 0x800401f0.

Баг вылезал не сразу: soundcard поднимал COM временным объектом, а в его
деструкторе стоит CoUninitialize. Пока объект жив, всё работает; стоит
сборщику мусора до него добраться, и COM в потоке гаснет. Поэтому здесь
мы не просто перезапускаем запись, а ещё и дёргаем gc между заходами.
"""

import testenv  # noqa: F401  русский вывод в консоли Windows

import gc
import time

from app.audio import devices
from app.audio.wasapi import WasapiCapture

chunks = {"n": 0}
cap = WasapiCapture(on_chunk=lambda *a: chunks.__setitem__("n", chunks["n"] + 1))

ROUNDS = 5
for i in range(1, ROUNDS + 1):
    cap.start(f"meeting-{i}")
    time.sleep(1.2)
    errs = cap.errors()
    assert not errs, f"заход {i} упал: {errs}"
    assert cap.is_recording, f"заход {i}: запись не идёт"
    cap.stop()
    # Ровно та ситуация, что ломала: чистка мусора между записями.
    gc.collect()
    time.sleep(0.3)
    print(f"[ok] заход {i} записал без ошибок")

print(f"[ok] {ROUNDS} заходов подряд, чанков получено: {chunks['n']}")
assert chunks["n"] > 0, "звук не шёл ни разу"

# Список устройств из свежего потока: тот же COM, другой путь к нему.
import threading

res = {}
def probe():
    gc.collect()
    res["mics"] = len(devices.list_microphones())
    res["spk"] = len(devices.list_speakers())

for i in range(3):
    t = threading.Thread(target=probe)
    t.start()
    t.join()
    assert res["mics"] > 0 or res["spk"] > 0, f"поток {i}: устройства не видны"
print(f"[ok] устройства видны из свежих потоков: микрофонов {res['mics']}, выходов {res['spk']}")

print("\nПовторный старт записи чинён.")
