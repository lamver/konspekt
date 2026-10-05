# -*- coding: utf-8 -*-
"""Сербская речь идёт в свою модель, и качается она только тем, кому нужна.

Зачем (замер 04.10 на речи в парламенте ParlaSpeech-RS): обычный Whisper
small ошибается в сербском в 31 % слов, дообученный на сербском — в 16 %
при том же размере и скорости. Но это ещё 290 МБ, и русскому человеку
без сербских собеседников они не нужны.
"""
from __future__ import annotations

import testenv  # noqa: F401  русский вывод и песочница вместо боевого профиля

import http.server
import os
import shutil
import tempfile
import threading
from pathlib import Path

import numpy as np

from app.asr import embedder, langid, whisper
from app.asr.router import LanguageRouter
from app.audio import NullCapture
from app.core import paths
from app.core.models import TranscriptSegment
from app.core.service import AppService
from app.storage.db import Store

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


звук = np.zeros(16000 * 3, dtype=np.float32)


# --- Роутер ----------------------------------------------------------------

class Распознаватель:
    def __init__(self, имя):
        self.имя = имя
        self.подсказки: list = []

    def transcribe(self, pcm, sample_rate=16000, meeting_id="", offset=0.0,
                   speaker="them", lang=None):
        self.подсказки.append(lang)
        return [TranscriptSegment(meeting_id=meeting_id, text=self.имя, speaker=speaker)]


class Определитель:
    ответ = ("sr", 0.95)

    def detect(self, pcm, sample_rate=16000):
        return self.ответ


ru, общий, сербский = Распознаватель("ru"), Распознаватель("общий"), Распознаватель("сербский")
р = LanguageRouter(ru, Определитель(), общий, serbian=сербский)
куски = list(р.transcribe(звук))
проверить(куски[0].text == "сербский" and сербский.подсказки == ["sr"],
          "сербская фраза ушла в сербскую модель с подсказкой языка")
Определитель.ответ = ("de", 0.95)
list(р.transcribe(звук))
проверить(общий.подсказки == ["de"] and len(сербский.подсказки) == 1,
          "немецкая фраза по-прежнему в общий Whisper")

просили: list[str] = []
Определитель.ответ = ("sr", 0.95)
ru, общий = Распознаватель("ru"), Распознаватель("общий")
р = LanguageRouter(ru, Определитель(), общий, нужна_модель=просили.append)
куски = list(р.transcribe(звук))
проверить(куски[0].text == "общий" and общий.подсказки == ["sr"],
          "сербской модели нет: фраза в общий Whisper, как раньше")
проверить(просили == ["sr"], f"и программу попросили докачать сербскую модель: {просили}")


# --- Заглавная буква -------------------------------------------------------

class Модель:
    def recognize(self, waveform, sample_rate=16000, language=None):
        return "главна религија у молдавији је православно хришћанство."


w = whisper.WhisperTranscriber(Path("_нет"), auto_load=False)
w._model = Модель()
текст = w.transcribe(звук, lang="sr")[0].text
проверить(текст == "Glavna religija u moldaviji je pravoslavno hrišćanstvo.",
          f"сербская фраза с заглавной буквы и латиницей: {текст!r}")


# --- Докачка только тем, кому нужно ---------------------------------------

корень = Path(tempfile.mkdtemp(prefix="konspekt-сербский-"))
зеркало = корень / "зеркало"
модели = корень / "models"
модели.mkdir()
запросы: list[str] = []
for repo, files in ((langid.MODEL_REPO, langid.MODEL_FILES),
                    (whisper.SIZES["small"]["repo"], whisper.MODEL_FILES),
                    (whisper.SERBIAN["repo"], whisper.MODEL_FILES),
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

сервис = AppService(store=Store(str(корень / "k.db")), capture=NullCapture(), transcriber=None)
сервис.settings.asr.enabled = True
сервис.settings.asr.detect_language = True
сервис.settings.asr.whisper_size = "small"
сервис.settings.language = "ru"
сервис.settings.asr.language = "ru"
сербская_папка = модели / whisper.SERBIAN["dir"]

os.environ.pop("KONSPEKT_NO_PREFETCH")
try:
    сервис._докачать_остальные_модели()
    проверить(not сербская_папка.exists(),
              "русскому человеку без сербской речи сербская модель не качается")
    проверить(isinstance(сервис.transcriber, LanguageRouter) and сервис.transcriber.serbian is None,
              "распознавание многоязычное, но без сербской модели")

    # Роутер услышал сербский: просьба ставит флаг и качает в фоне.
    сервис._нужна_модель_языка("sr")
    сервис._нужна_модель_языка("sr")  # вторая фраза не запускает вторую загрузку
    for _ in range(100):
        if isinstance(сервис.transcriber, LanguageRouter) and сервис.transcriber.serbian is not None:
            break
        threading.Event().wait(0.1)
    проверить(whisper.WhisperTranscriber(сербская_папка).is_downloaded(),
              "услышали сербскую речь — сербская модель скачалась")
    проверить(isinstance(сервис.transcriber, LanguageRouter) and сервис.transcriber.serbian is not None,
              "и распознавание само переключилось на неё, без перезапуска")
    проверить(сервис._pick_engine("sr") is сервис.transcriber.serbian,
              "пересчёт фразы на сербском тоже идёт в сербскую модель")
    сербских = [з for з in запросы if whisper.SERBIAN["repo"] in з]
    проверить(len(сербских) == len(whisper.MODEL_FILES),
              f"каждый файл сербской модели скачан один раз: {len(сербских)}")

    # Сербский интерфейс: модель нужна сразу, без ожидания сербской речи.
    shutil.rmtree(сербская_папка)
    сервис._сербский_услышан = False
    проверить(not сервис._нужен_сербский(), "флаг сброшен — не нужна")
    сервис.settings.language = "sr"
    проверить(сервис._нужен_сербский(), "сербский интерфейс — нужна")
    сервис.settings.language = "ru"
    сервис.settings.asr.language = "sr"
    проверить(сервис._нужен_сербский(), "сербский основной язык встреч — нужна")
finally:
    os.environ["KONSPEKT_NO_PREFETCH"] = "1"
    сервер.shutdown()
    сервис.shutdown()
    shutil.rmtree(корень, ignore_errors=True)

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Сербская речь идёт в свою модель, и качается она только тем, кому нужна.")
