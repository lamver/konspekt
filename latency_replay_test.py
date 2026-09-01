"""Замер задержки на настоящей встрече пользователя, а не на синтетике.

Зачем. latency_test.py гоняет одну короткую фразу через TTS на тихой
машине — это доказывает, что искусственная 5-секундная задержка убрана,
но не отвечает, "моментальна" ли расшифровка на реальном потоке из
десятков фраз подряд, как в жизни. Здесь берём настоящий wav-файл
встречи, кормим его в тот же VAD + очередь распознавания, что работает
вживую, и меряем время от конца каждой фразы до появления её текста.

Что считаем. Для каждой фразы: t_speech_end (конец речи по VAD) и
t_text_ready (когда сегмент долетел до on_segment). Разница и есть
ощущаемая пользователем задержка. Плюс общая пропускная способность:
успевает ли распознавание за темпом речи, или очередь копится.

Инструмент расследования, не регулярный тест. Путь ниже — личная запись
конкретного пользователя с его машины, её нет ни у кого другого и её
не может быть в репозитории (это чья-то реальная встреча). Поэтому
скрипт не входит в run_tests.py и молча пропускается, если файла нет.
"""

import sys
import time
import wave
from pathlib import Path

import testenv  # noqa: F401  русский вывод в консоли Windows
import numpy as np

from app.asr.gigaam import MODEL_DIR_NAME as GIGAAM_DIR
from app.asr.gigaam import GigaamTranscriber
from app.asr.langid import MODEL_DIR_NAME as LANGID_DIR
from app.asr.langid import LanguageDetector
from app.asr.queue import TranscriptionQueue
from app.asr.router import LanguageRouter
from app.asr.whisper import MODEL_DIR_NAME as WHISPER_DIR
from app.asr.whisper import WhisperTranscriber
from app.core import paths

# Настоящая встреча пользователя: 285 МБ дорожки, живой разговор.
WAV = Path.home() / "AppData/Roaming/Konspekt/audio/1160feead0d54753/20260829-200417-me.wav"
LIMIT_SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0


def read_pcm(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
        d = np.frombuffer(raw, dtype=np.int16)
        if w.getnchannels() == 2:
            d = d.reshape(-1, 2).mean(axis=1).astype(np.int16)
    return d, rate


if not WAV.exists():
    print(f"[пропуск] нет личной записи {WAV}, замер только у автора находки")
    raise SystemExit(0)

pcm, rate = read_pcm(WAV)
total_seconds = len(pcm) / rate
n = int(min(LIMIT_SECONDS, total_seconds) * rate)
pcm = pcm[:n]
print(f"[ok] взято {n / rate:.1f}с из {total_seconds:.1f}с настоящей встречи")

root = paths.models_dir()
russian = GigaamTranscriber(root / GIGAAM_DIR)
detector = LanguageDetector(root / LANGID_DIR)
foreign = WhisperTranscriber(root / WHISPER_DIR)
transcriber = LanguageRouter(russian, detector, foreign) if (
    detector.is_downloaded() and foreign.is_downloaded()
) else russian
print(f"[ok] движок: {transcriber.name}")

# Симулируем реальный захват: чанки по 0.5с (как в проде после правки),
# отданные ровно в темпе живой речи, а не все разом.
CHUNK = 0.5
chunk_n = int(CHUNK * rate)

results: list[dict] = []
speech_end_wall: dict[float, float] = {}   # offset -> момент, когда чанк ушёл в очередь


def on_segment(segment) -> None:
    now = time.monotonic()
    results.append({
        "offset": segment.start,
        "end": segment.end,
        "text": segment.text,
        "lang": segment.lang,
        "wall_ready": now,
    })


queue = TranscriptionQueue(transcriber, on_segment, sample_rate=rate)

t_start = time.monotonic()
offset = 0.0
fed = 0
while fed < len(pcm):
    piece = pcm[fed: fed + chunk_n]
    fed += chunk_n
    queue.submit("bench", "me", piece, offset, block=True)
    offset += CHUNK
    # Живой темп: не кормим быстрее, чем реально говорили.
    target_wall = t_start + offset
    now = time.monotonic()
    if target_wall > now:
        time.sleep(target_wall - now)

queue.flush("bench", block=True)
queue.wait_idle(timeout=120)
queue.stop()

print(f"\n[ok] подано {offset:.1f}с речи в реальном темпе, распознано фраз: {len(results)}")
if not results:
    print("[FAIL] ни одной фразы не распознано")
    raise SystemExit(1)

# Задержка = когда текст готов (по настенному времени) минус когда
# закончилась сама фраза (offset конца фразы + t_start).
delays = []
for r in sorted(results, key=lambda x: x["offset"]):
    speech_end_wall = t_start + r["end"]
    delay = r["wall_ready"] - speech_end_wall
    delays.append(delay)
    print(f"[{r['offset']:7.1f}-{r['end']:7.1f}] +{delay:5.2f}с  "
          f"{r['lang']:3} {r['text'][:60]}")

import statistics
print(f"\nзадержка от конца фразы до текста: "
      f"медиана {statistics.median(delays):.2f}с, "
      f"среднее {statistics.mean(delays):.2f}с, "
      f"максимум {max(delays):.2f}с, "
      f"минимум {min(delays):.2f}с")
доля_отставших = sum(1 for d in delays if d > 2.0) / len(delays)
print(f"доля фраз с задержкой > 2с: {доля_отставших:.0%}")
print(f"фраз потеряно (переполнение очереди): {queue.dropped}")
