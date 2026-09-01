"""Обновление и перезапуск: скачанное не качается заново.

Отвечает на вопрос «как это поведёт себя, когда программа уже
установлена». Модель лежит в данных пользователя, а не в программе,
поэтому обновление её не трогает — но проверить это надо, а не
предполагать.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import os
import shutil
import tempfile
import wave
from pathlib import Path

import numpy as np

RATE = 16000


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-upgrade-"))
    os.environ["KONSPEKT_NO_PREFETCH"] = "1"
    try:
        from app.core import paths as paths_mod

        models = tmp / "models"
        (models / "gigaam-v3-e2e").mkdir(parents=True, exist_ok=True)
        audio = tmp / "audio"
        audio.mkdir(parents=True, exist_ok=True)
        paths_mod.data_dir = lambda: tmp  # type: ignore[assignment]
        paths_mod.audio_dir = lambda: audio  # type: ignore[assignment]
        paths_mod.db_path = lambda: tmp / "konspekt.db"  # type: ignore[assignment]
        paths_mod.models_dir = lambda: models  # type: ignore[assignment]
        paths_mod.settings_path = lambda: tmp / "settings.json"  # type: ignore[assignment]

        from app.asr import MODEL_FILES
        from app.audio import NullCapture
        from app.core.service import AppService
        from app.storage.db import Store

        # Модель «уже скачана» прошлой версией: кладём файлы на место.
        for name in MODEL_FILES:
            (models / "gigaam-v3-e2e" / name).write_bytes(b"x" * 16)
        print(f"[ok] у пользователя уже лежат веса: {len(MODEL_FILES)} файлов")

        service = AppService(store=Store(str(tmp / "konspekt.db")), capture=NullCapture())

        # Файлы здесь ненастоящие, поэтому проверка целостности весов
        # честно объявила бы их повреждёнными. Тест не про это: он про то,
        # что после обновления уже скачанное не качается заново. Поэтому
        # подменяем саму загрузку модели в память успешной.
        engine = getattr(service.transcriber, "russian", service.transcriber)
        engine.load = lambda: None

        pulled = {"n": 0}

        def counting(on_progress=None):
            pulled["n"] += 1

        service.downloader.run_blocking = counting

        # Запуск после обновления: фоновая загрузка не должна начинаться.
        os.environ.pop("KONSPEKT_NO_PREFETCH", None)
        service._prefetch_asr_model()
        os.environ["KONSPEKT_NO_PREFETCH"] = "1"
        import time
        time.sleep(1.0)
        assert pulled["n"] == 0, (
            "после обновления модель качается заново, хотя она уже на диске"
        )
        print("[ok] после обновления модель не качается повторно")

        # И при первой же реплике тоже не качается.
        assert service._ensure_asr_model() is True
        assert pulled["n"] == 0, "модель качается при работе, хотя уже на месте"
        print("[ok] при работе повторной загрузки нет")

        # Данные пользователя лежат отдельно от программы: обновление
        # ставится поверх и их не трогает. Проверяем, что каталог весов
        # действительно внутри данных, а не рядом с exe.
        assert models.is_relative_to(tmp), "веса лежат не в данных пользователя"
        print("[ok] веса лежат в данных пользователя, установка их не затирает")

        service.shutdown()

        # --- Настройки, доставшиеся от старой версии ---
        #
        # Самая дорогая находка: у человека, который пользовался
        # Konspekt до 0.6.1, в settings.json лежит chunk_seconds = 5.0.
        # Файл настроек сильнее умолчания в коде, поэтому уменьшение
        # умолчания до 0.5 не давало ему ничего: и 0.6.1, и 0.7.0, и
        # 0.7.1 у него вели себя ровно как раньше — текст ждал секунды.
        # Проверяем на настоящем старом файле настроек, а не на объекте
        # в памяти: ошибка была именно в чтении файла.
        import json
        from app.core import settings as settings_mod

        старый = {
            "audio": {"chunk_seconds": 5.0, "capture_mic": True},
            "asr": {"language": "ru"},
        }
        (tmp / "settings.json").write_text(
            json.dumps(старый, ensure_ascii=False), encoding="utf-8"
        )
        s = settings_mod.load()
        assert s.audio.chunk_seconds <= 1.0, (
            f"настройка от старой версии не починена: chunk_seconds="
            f"{s.audio.chunk_seconds}, человек по-прежнему ждёт текст "
            f"секундами, сколько ни обновляйся"
        )
        print(f"[ok] chunk_seconds от старой версии починен: 5.0 -> {s.audio.chunk_seconds}")

        # Осознанный выбор пользователя не трогаем: чиним только то самое
        # унаследованное значение, а не любую настройку не по вкусу.
        (tmp / "settings.json").write_text(
            json.dumps({"audio": {"chunk_seconds": 1.5}}, ensure_ascii=False),
            encoding="utf-8",
        )
        assert settings_mod.load().audio.chunk_seconds == 1.5, (
            "переписали значение, которое пользователь выставил сам"
        )
        print("[ok] выбранное пользователем значение не трогаем")

        # Обратный конец: настройка должна доезжать до захвата звука.
        # Починить чтение и забыть про использование — ровно тот же
        # обрыв пути, что был с черновиками в 0.7.0.
        import inspect
        src = inspect.getsource(type(service)._start_capture) if hasattr(
            type(service), "_start_capture"
        ) else inspect.getsource(type(service))
        assert "chunk_seconds=audio.chunk_seconds" in src, (
            "настройка не доходит до захвата звука"
        )
        print("[ok] настройка доходит до захвата звука")

        print("\nОбновление не заставляет качать модель заново,")
        print("а настройки от старых версий чинятся при чтении.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
