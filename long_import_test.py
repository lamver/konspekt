"""Длинная запись не теряет речь при импорте.

Настоящий баг: чтение файла идёт в десятки раз быстрее распознавания,
очередь переполнялась, и на 25-минутной записи молча пропал 421
фрагмент речи из ~475. Здесь проверяем, что этого больше нет.

Длинную запись собираем сами, склеивая живую речь в цикле: так проверка
не зависит от того, есть ли под рукой чужая получасовая запись.
"""
import os
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

db_file = Path(tempfile.gettempdir()) / f"konspekt-long-{os.getpid()}.db"
os.environ["KONSPEKT_DB"] = str(db_file)

from app.asr.queue import MAX_PENDING  # noqa: E402
from app.core.service import AppService  # noqa: E402
from app.storage import Store  # noqa: E402

EXAMPLES = Path("audio_examples")
source = EXAMPLES / "aidar_ speakкаждый-_20260714_165516.wav"
if not source.exists():
    print("[--] нет примера речи, проверка длинной записи пропущена")
    raise SystemExit(0)

with wave.open(str(source), "rb") as w:
    rate, chans = w.getframerate(), w.getnchannels()
    raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
if chans > 1:
    raw = raw.reshape(-1, chans)[:, 0]

# Копий должно быть заведомо больше, чем влезает в очередь: только так
# проверка ловит именно переполнение, а не что-то другое.
COPIES = MAX_PENDING + 20
pause = np.zeros(int(rate * 0.8), dtype=np.int16)
long_wave = np.concatenate([np.concatenate([raw, pause]) for _ in range(COPIES)])

long_file = Path("_t_long.wav")
with wave.open(str(long_file), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(long_wave.tobytes())

minutes = len(long_wave) / rate / 60
print(f"собрана запись на {minutes:.1f} мин из {COPIES} повторов "
      f"(очередь вмещает {MAX_PENDING})")

service = AppService(store=Store(str(db_file)))
started = time.monotonic()
service.import_files([str(long_file)])

for _ in range(4000):
    st = service.import_status()
    if not st["busy"] and all(t["status"] in ("done", "failed") for t in st["tasks"]):
        break
    time.sleep(0.25)

task = service.import_status()["tasks"][0]
elapsed = time.monotonic() - started
print(f"разобрано за {elapsed:.0f} c, статус {task['status']}")
assert task["status"] == "done", task

dropped = service.asr_queue.dropped
print(f"потеряно фрагментов: {dropped}")
assert dropped == 0, f"речь потеряна: {dropped} фрагментов"
print("[ok] ни один фрагмент речи не потерян")

segments = service.store.list_segments(task["meeting_id"])
print(f"реплик в транскрипте: {len(segments)}")
assert len(segments) >= COPIES * 0.8, \
    f"реплик слишком мало: {len(segments)} на {COPIES} повторов"
print(f"[ok] распознано {len(segments)} реплик на {COPIES} повторов речи")

# Транскрипт покрывает всю запись, а не только начало
last = max((s.end for s in segments), default=0.0)
total = len(long_wave) / rate
print(f"транскрипт доходит до {last:.0f} c из {total:.0f} c "
      f"({last / total * 100:.0f}%)")
assert last > total * 0.9, f"конец записи не распознан: {last:.0f} из {total:.0f}"
print("[ok] транскрипт покрывает запись до конца")

# А живая запись по-прежнему обязана ронять чанки, а не ждать: поток
# захвата не может задерживаться, иначе в записи появится дыра.
import queue as _queue  # noqa: E402

from app.asr.queue import Job, TranscriptionQueue  # noqa: E402

q = TranscriptionQueue(service.transcriber, lambda _s: None)
q._queue = _queue.Queue(maxsize=1)
q._put(Job("m", "them", np.zeros(10, dtype=np.float32), 0.0))
q._put(Job("m", "them", np.zeros(10, dtype=np.float32), 1.0))   # места уже нет
assert q.dropped == 1, q.dropped
print("[ok] живая запись по-прежнему роняет чанк, а не тормозит захват")

service.shutdown()
long_file.unlink(missing_ok=True)
db_file.unlink(missing_ok=True)
print("\nДлинная запись разбирается без потерь.")
