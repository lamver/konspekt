# -*- coding: utf-8 -*-
"""Смена модели распознавания не оставляет мусора и не заставляет качать заново.

До 0.7.3 русскую речь распознавала CTC-сборка GigaAM: на трудной
записи она выдавала несуществующие слова («сумасодия» вместо «с ума
сойти»). Перешли на RNNT — тот же вес и та же скорость, но выдумок
нет. Файлы у сборок разные, поэтому старые нужно убрать: иначе у
каждого человека молча лежат мёртвые 214 МБ.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

tmp = Path(tempfile.mkdtemp(prefix="konspekt-модель-"))
os.environ["KONSPEKT_DATA_DIR"] = str(tmp)
os.environ["KONSPEKT_NO_PREFETCH"] = "1"

from app.asr import MODEL_DIR_NAME, MODEL_FILES, СТАРАЯ_ПАПКА_МОДЕЛИ
from app.audio import NullCapture
from app.core import paths
from app.core.service import AppService
from app.storage.db import Store

модели = paths.models_dir()
старая = модели / СТАРАЯ_ПАПКА_МОДЕЛИ
старая.mkdir(parents=True, exist_ok=True)
# Похоже на правду по весу: настоящая CTC-сборка занимала 214 МБ.
(старая / "v3_e2e_ctc.int8.onnx").write_bytes(b"x" * 4096)
(старая / "v3_e2e_ctc.yaml").write_bytes(b"y" * 64)

# Новая модель у человека уже скачана: её трогать нельзя ни в коем случае.
новая = модели / MODEL_DIR_NAME
новая.mkdir(parents=True, exist_ok=True)
for имя in MODEL_FILES:
    (новая / имя).write_bytes(b"z" * 32)

assert старая != новая, (
    "старая и новая папки совпали: тогда уборка снесёт рабочую модель"
)

AppService(store=Store(str(tmp / "konspekt.db")), capture=NullCapture())

assert not старая.exists(), (
    f"веса прошлой модели остались в {старая}: у каждого человека молча "
    f"лежат лишние 214 МБ"
)
print("[ok] веса прошлой модели убраны")

пропали = [и for и in MODEL_FILES if not (новая / и).exists()]
assert not пропали, (
    f"уборка задела рабочую модель, пропали файлы: {пропали}. "
    f"Человеку пришлось бы качать 214 МБ заново"
)
print(f"[ok] рабочая модель цела: {len(MODEL_FILES)} файлов")

# Второй запуск: убирать уже нечего, и падать на этом нельзя.
AppService(store=Store(str(tmp / "konspekt.db")), capture=NullCapture())
print("[ok] повторный запуск без прошлой модели проходит спокойно")

print("\nПереход на новую модель проходит чисто")
