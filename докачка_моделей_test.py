# -*- coding: utf-8 -*-
"""Чистая установка докачивает Whisper, определитель языка и модель голосов.

Беда, от которой это сторожит (найдена 04.10, исправлена в 0.15.1):
программа сама качала только русскую модель распознавания. Whisper и
определитель языка не качал никто, у разработчика они лежали в папке с
весны. У нового пользователя английская, испанская и сербская речь
уходила в русскую модель и выходила кириллической кашей, а снаружи всё
выглядело рабочим.

Проверяем на пустом каталоге моделей и маленьком своём «зеркале»:
после докачки распознавание само становится многоязычным.
"""
from __future__ import annotations

import testenv  # noqa: F401  русский вывод и песочница вместо боевого профиля

import http.server
import os
import shutil
import tempfile
import threading
from pathlib import Path

from app.asr import embedder, langid, whisper
from app.asr.router import LanguageRouter
from app.audio import NullCapture
from app.core import paths
from app.core.service import AppService
from app.storage.db import Store

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


корень = Path(tempfile.mkdtemp(prefix="konspekt-докачка-"))
зеркало = корень / "зеркало"
модели = корень / "models"
модели.mkdir()

# Зеркало с фальшивыми весами в раскладке «репозиторий/файл», как у CDN.
запросы: list[str] = []
for repo, files in ((langid.MODEL_REPO, langid.MODEL_FILES),
                    (whisper.SIZES["small"]["repo"], whisper.MODEL_FILES),
                    (embedder.MODEL_REPO, embedder.MODEL_FILES)):
    for имя in files:
        путь = зеркало / repo / имя
        путь.parent.mkdir(parents=True, exist_ok=True)
        путь.write_bytes(b"weights:" + имя.encode())


class Зеркало(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(зеркало), **k)

    def log_message(self, *a):
        запросы.append(self.path)


сервер = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Зеркало)
threading.Thread(target=сервер.serve_forever, daemon=True).start()

paths.models_dir = lambda: модели  # type: ignore[assignment]
os.environ["KONSPEKT_MODELS_BASE"] = f"http://127.0.0.1:{сервер.server_address[1]}"
# Пробная загрузка при старте здесь не нужна: зовём докачку сами.
os.environ["KONSPEKT_NO_PREFETCH"] = "1"

сервис = AppService(store=Store(str(корень / "k.db")), capture=NullCapture(), transcriber=None)
сервис.settings.asr.enabled = True
сервис.settings.asr.detect_language = True
сервис.settings.asr.whisper_size = "small"

проверить(not isinstance(сервис._build_transcriber(), LanguageRouter),
          "на пустом каталоге распознавание только русское (так и было у новых людей)")

# Как при обычном запуске: без KONSPEKT_NO_PREFETCH.
os.environ.pop("KONSPEKT_NO_PREFETCH")
сервис._докачать_остальные_модели()

проверить(langid.LanguageDetector(модели / langid.MODEL_DIR_NAME).is_downloaded(),
          "определитель языка скачан")
проверить(whisper.WhisperTranscriber(модели / whisper.SIZES["small"]["dir"]).is_downloaded(),
          "Whisper small скачан")
проверить(embedder.VoiceEmbedder(модели / embedder.MODEL_DIR_NAME).is_downloaded(),
          "модель голосов скачана")
проверить(isinstance(сервис.transcriber, LanguageRouter),
          "после докачки распознавание само стало многоязычным, без перезапуска")

было = len(запросы)
сервис._докачать_остальные_модели()
проверить(len(запросы) == было, "второй запуск ничего не качает заново")

# Зеркало пропало: программа не падает, просто попробует в следующий раз.
shutil.rmtree(модели / embedder.MODEL_DIR_NAME)
сервер.shutdown()
os.environ["KONSPEKT_MODELS_BASE"] = "http://127.0.0.1:9"
try:
    import app.asr.download as загрузка
    старые = загрузка.HF_BASE
    загрузка.HF_BASE = "http://127.0.0.1:9/{repo}/{name}"
    загрузка.RETRIES, было_повторов = 1, загрузка.RETRIES
    сервис._докачать_остальные_модели()
    проверить(True, "нет сети: докачка молча откладывается, программа работает")
except Exception as e:  # noqa: BLE001
    проверить(False, f"нет сети: докачка уронила программу ({e!r})")
finally:
    загрузка.HF_BASE = старые
    загрузка.RETRIES = было_повторов

сервис.settings.asr.enabled = False
сервис._докачать_остальные_модели()
проверить(True, "распознавание выключено: ничего не качаем")

os.environ["KONSPEKT_NO_PREFETCH"] = "1"
сервис.shutdown()
shutil.rmtree(корень, ignore_errors=True)

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Чистая установка докачивает Whisper, определитель языка и модель голосов.")
