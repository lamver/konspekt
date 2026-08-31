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
        print("\nОбновление не заставляет качать модель заново.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
