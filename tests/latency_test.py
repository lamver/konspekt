"""Сквозная задержка: конец фразы -> готовый текст (issue #2).

Раньше звук из микрофона доходил до распознавания кусками по 5 секунд:
даже мгновенная короткая фраза ждала полного набора буфера, прежде чем
VAD вообще её видел. Проверяем сквозной путь целиком на настоящей
фразе из боевой записи, не гадаем по отдельным звеньям.
"""

import threading
import time
import wave

import numpy as np

import testenv  # noqa: F401

from app.asr.gigaam import GigaamTranscriber
from app.asr.queue import TranscriptionQueue
from app.audio.buffers import ChunkBuffer
from app.core import paths
from app.core.settings import AudioSettings

# Дефолт должен быть маленьким. Если кто-то вернёт 5.0 не задумываясь
# о задержке — тест должен упасть раньше, чем это увидит пользователь.
assert AudioSettings().chunk_seconds <= 1.0, (
    f"chunk_seconds={AudioSettings().chunk_seconds}: звук снова будет "
    "копиться секундами перед тем, как попасть в распознавание"
)
print(f"[ok] дефолт chunk_seconds={AudioSettings().chunk_seconds}с")

sr = 16000
модель = GigaamTranscriber(paths.models_dir() / "gigaam-v3-e2e")
if not модель.is_downloaded():
    print("[skip] модель GigaAM не скачана на этой машине")
    raise SystemExit(0)
модель.load()

audio_dir = paths.audio_dir() / "93b3a94f2bca4e39"
if not audio_dir.exists():
    print("[skip] боевая запись встречи 30.08 недоступна на этой машине")
    raise SystemExit(0)

files = sorted(audio_dir.glob("*-me.wav"))
with wave.open(str(files[0]), "rb") as w:
    rate = w.getframerate()
    pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)

# "Ну, транскрипция где?" — короткая настоящая фраза из разбора.
phrase = pcm[int(3.5 * rate):int(6.5 * rate)]
full = np.concatenate([phrase, np.zeros(int(1.5 * rate), dtype=np.int16)])

result = {}
готово = threading.Event()

def on_segment(seg):
    result["t"] = time.time()
    result["text"] = seg.text
    готово.set()

очередь = TranscriptionQueue(модель, on_segment, sample_rate=sr, use_vad=True)
буфер = ChunkBuffer(chunk_seconds=AudioSettings().chunk_seconds, sample_rate=sr)

BLOCK = 1600  # 0.1с, как реальный блок звуковой карты (BLOCK_FRAMES в wasapi.py)
t_last_speech = None
for i in range(0, len(full), BLOCK):
    block = full[i:i + BLOCK]
    if block.size == 0:
        continue
    if i < len(phrase):
        t_last_speech = time.time()
    for chunk, offset in буфер.push(block):
        очередь.submit("m-latency", "me", chunk, offset)
    time.sleep(0.1)
tail = буфер.flush()
if tail:
    очередь.submit("m-latency", "me", tail[0], tail[1])

assert готово.wait(30), "сегмент не пришёл вовсе"
задержка = result["t"] - t_last_speech
очередь.stop()

print(f"[ok] текст: {result['text']!r}")
print(f"[ok] задержка от конца речи до текста: {задержка:.2f}с")
# С chunk_seconds=5.0 было около 1.8с только на ожидание буфера, поверх
# него ещё VAD и инференс. 3с — щедрый потолок, лишь бы не вернулась
# многосекундная пауза.
assert задержка < 3.0, f"задержка {задержка:.2f}с слишком большая, регресс на медленный путь"

print("\nСквозная задержка в норме.")
