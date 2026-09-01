"""Приём брошенных файлов: разбор события drop от pywebview.

Само перетаскивание мышью через OLE из другого процесса не вызвать,
поэтому проверяем то, что в нашей власти: обработчик получает событие
ровно в том виде, в каком его формирует pywebview (webview/util.py
дописывает в каждый файл поле pywebviewFullPath), и делает с ним то,
что нужно.
"""

import time
import testenv  # noqa: F401  русский вывод в консоли Windows

import os
import tempfile
import wave
from pathlib import Path

import numpy as np

db_file = Path(tempfile.gettempdir()) / f"konspekt-drop-{os.getpid()}.db"
os.environ["KONSPEKT_DB"] = str(db_file)

from app.core.service import AppService  # noqa: E402
from app.storage import Store  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

# Свой файл вместо примера из папки: здесь проверяется разбор
# события, а не распознавание, и живая речь не нужна.
good = Path("_t_dropped.wav").resolve()
tone = (0.2 * np.sin(np.arange(16000 * 2) * 0.05) * 32767).astype(np.int16)
with wave.open(str(good), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(tone.tobytes())

service = AppService(store=Store(str(db_file)))
window = MainWindow(service)

# Событие ровно такой формы приходит из pywebview при броске файлов
event = {
    "type": "drop",
    "dataTransfer": {
        "files": [
            {"name": good.name, "pywebviewFullPath": str(good)},
            # Файл без пути: так бывает с содержимым архивов и облачных
            # папок, которых физически нет на диске.
            {"name": "из-архива.wav"},
        ]
    },
}

window._on_files_dropped(event)

tasks = service.import_status()["tasks"]
assert len(tasks) == 1, tasks
assert tasks[0]["name"] == good.name, tasks
print(f"[ok] файл с путём принят: {tasks[0]['name']}")
print("[ok] файл без пути пропущен, а не уронил обработчик")

# Пустое событие и мусор не должны ронять окно: оно приходит из браузера
for junk in ({}, None, {"dataTransfer": None}, {"dataTransfer": {"files": None}}):
    window._on_files_dropped(junk)
print("[ok] пустое и битое событие обработчик переживает")

before = len(service.import_status()["tasks"])
window._on_files_dropped({"dataTransfer": {"files": []}})
assert len(service.import_status()["tasks"]) == before
print("[ok] бросок без файлов ничего не добавляет")

service.importer.stop()
service.shutdown()

# Windows не даёт удалить файл, пока его кто-то держит. Ввоз мог не
# успеть отпустить наш образец, и уборка роняла уже пройденный тест.
def убрать(путь):
    for _ in range(20):
        try:
            путь.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.1)

убрать(db_file)
убрать(good)
print("\nПриём брошенных файлов работает.")
