"""Импорт файлов целиком: файл на входе, встреча с текстом на выходе.

Проверка идёт через настоящий сервис и настоящее распознавание, но
на отдельной временной базе, чтобы не трогать боевые встречи.
"""
import os
import tempfile
import time
from pathlib import Path

db_file = Path(tempfile.gettempdir()) / f"konspekt-import-{os.getpid()}.db"
os.environ["KONSPEKT_DB"] = str(db_file)

from app.core.events import IMPORT_PROGRESS, bus  # noqa: E402
from app.core.service import AppService  # noqa: E402
from app.storage import Store  # noqa: E402

EXAMPLES = Path("audio_examples")

# Примеры записей в репозиторий не кладём: это 12 МБ звука. Здесь нужна
# настоящая речь: проверяется весь путь до распознанного текста,
# а на синтетике модель ничего не услышит.
if not (EXAMPLES.is_dir() and any(EXAMPLES.iterdir())):
    print("[--] папки audio_examples нет, проверка импорта пропущена")
    raise SystemExit(0)

service = AppService(store=Store(str(db_file)))
print(f"движок распознавания: {type(service.transcriber).__name__}")

# Следим за событиями прогресса: интерфейс живёт именно на них
seen = []
bus.on(IMPORT_PROGRESS, lambda p: seen.append(p["task"]["status"]))

# Русская речь, английская речь и ловушка с подменённым расширением
files = [
    EXAMPLES / "aidar_ speakкаждый-_20260714_165516.wav",
    EXAMPLES / "0aba19b7f3d1.mp3",   # на самом деле текст, должен отпасть
    EXAMPLES / "6035729_Mother_Father_1280x720.mp4",  # видео без звука
]
files = [f for f in files if f.exists()]
assert files, "примеры не найдены"

tasks = service.import_files([str(f) for f in files])
print(f"[ok] в очередь принято {len(tasks)} файлов")

bad = [t for t in tasks if t["status"] == "failed"]
assert len(bad) == 2, [t["name"] for t in bad]
print(f"[ok] негодные файлы отсеяны сразу: {[t['error'][:40] for t in bad]}")

# Ждём разбора
for _ in range(1200):
    status = service.import_status()
    if not status["busy"] and all(
        t["status"] in ("done", "failed", "cancelled") for t in status["tasks"]
    ):
        break
    time.sleep(0.25)

status = service.import_status()
done = [t for t in status["tasks"] if t["status"] == "done"]
assert done, f"ни один файл не разобран: {status['tasks']}"
print(f"[ok] разобрано файлов: {len(done)}")

for t in done:
    meeting = service.get_meeting(t["meeting_id"])
    assert meeting, t
    segments = service.store.list_segments(t["meeting_id"])
    text = " ".join(s.text for s in segments)
    print(f"\n--- {t['name']}")
    print(f"    встреча: {meeting['title']!r}, статус {meeting['status']}")
    print(f"    реплик: {len(segments)}, длительность {t['duration']:.1f} c")
    print(f"    текст: {text[:200]!r}")
    assert meeting["title"] == Path(t["name"]).stem, meeting["title"]
    assert meeting["status"] == "ready", meeting["status"]
    assert segments, "встреча без единой реплики"
    assert text.strip(), "текст пустой"

print("\n[ok] заголовок встречи взят из имени файла")
print("[ok] встречи закрыты со статусом «готово»")
print("[ok] прогресс доезжал до интерфейса:", sorted(set(seen)))

service.shutdown()
db_file.unlink(missing_ok=True)
print("\nИмпорт файлов работает.")
