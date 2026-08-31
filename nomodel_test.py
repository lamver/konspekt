"""Чистая установка: расшифровка не должна молча не появляться.

Жалоба первого пользователя: скачал, установил, бросил файл — и получил
встречу без единой реплики. Причина в том, что весов распознавания на
чистой машине нет, а приложение об этом не говорило: импорт завершался
успешно, ошибок не было, в журнале лежала строчка, которую никто не
читает.

Проверяем оба пути (файл и живая запись) с ненастоящей загрузкой:
качать 220 МБ в тесте нельзя, поэтому скачивание подменяем, но сам
факт обращения к нему проверяем строго.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import shutil
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

RATE = 16000


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-nomodel-"))
    try:
        from app.core import paths as paths_mod

        models = tmp / "models"   # пусто, как на свежей машине
        models.mkdir(parents=True, exist_ok=True)
        audio = tmp / "audio"
        audio.mkdir(parents=True, exist_ok=True)
        paths_mod.data_dir = lambda: tmp  # type: ignore[assignment]
        paths_mod.audio_dir = lambda: audio  # type: ignore[assignment]
        paths_mod.db_path = lambda: tmp / "konspekt.db"  # type: ignore[assignment]
        paths_mod.models_dir = lambda: models  # type: ignore[assignment]
        paths_mod.settings_path = lambda: tmp / "settings.json"  # type: ignore[assignment]

        from app.audio import NullCapture
        from app.core.events import MODEL_DOWNLOAD, bus
        from app.core.models import TranscriptSegment
        from app.core.service import AppService
        from app.storage.db import Store

        src = tmp / "запись.wav"
        t = np.arange(RATE * 3, dtype=np.float32) / RATE
        with wave.open(str(src), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes((np.sin(2 * np.pi * 220 * t) * 12000).astype(np.int16).tobytes())

        service = AppService(store=Store(str(tmp / "konspekt.db")), capture=NullCapture())
        # Подменяем загрузку сразу: иначе первый же чанк уйдёт качать
        # настоящие 220 МБ, и тест превратится в проверку интернета.
        called = {"n": 0}
        fake_files = {}

        def fake_download(on_progress=None):
            called["n"] += 1
            if on_progress:
                on_progress("v3_e2e_ctc.int8.onnx", 50, 100)
                on_progress("v3_e2e_ctc.int8.onnx", 100, 100)
            fake_files["done"] = True

        service.downloader.run_blocking = fake_download
        # После «скачивания» распознаватель обязан отвечать, что готов,
        # иначе сервис честно сообщит о неудаче.
        service.transcriber.is_downloaded = lambda: bool(fake_files.get("done"))
        # Настоящего распознавания тут нет: проверяем не качество текста,
        # а что до модели вообще дошла очередь.
        service.transcriber.transcribe = lambda *a, **k: []

        # --- состояние до всего: модели нет, и это видно в интерфейсе
        st = service.model_status()
        assert st["downloaded"] is False, "тест бессмыслен: модель уже на месте"
        print("[ok] на чистой установке модель не скачана, интерфейс это знает")

        events = []
        bus.on(MODEL_DOWNLOAD, events.append)

        # --- путь 1: человек бросает файл
        service.import_files([str(src)])
        deadline = time.time() + 60
        task = None
        while time.time() < deadline:
            items = service.import_status().get("tasks", [])
            done = [t for t in items if t.get("status") in ("done", "failed")]
            if done:
                task = done[0]
                break
            time.sleep(0.1)
        assert task, "импорт не завершился"
        assert called["n"] >= 1, (
            "при разборе файла модель не скачивалась — это и есть та поломка, "
            "из-за которой человек получал пустую расшифровку"
        )
        print(f"[ok] брошенный файл сам запускает загрузку модели ({called['n']} раз)")

        states = [e.get("state") for e in events]
        assert "downloading" in states, "интерфейсу не сообщили, что идёт загрузка"
        assert "ready" in states, "интерфейсу не сообщили, что модель готова"
        print(f"[ok] интерфейс получил события: {', '.join(dict.fromkeys(states))}")

        # --- повторный разбор не качает второй раз
        before = called["n"]
        service.import_files([str(src)])
        deadline = time.time() + 60
        while time.time() < deadline:
            items = service.import_status().get("tasks", [])
            if len([t for t in items if t.get("status") in ("done", "failed")]) >= 2:
                break
            time.sleep(0.1)
        assert called["n"] == before, "готовая модель качается повторно"
        print("[ok] второй файл повторную загрузку не запускает")

        # --- путь 2: живая запись идёт тем же путём
        fake_files.clear()
        service._asr_download_failed = False
        service.transcriber.is_downloaded = lambda: bool(fake_files.get("done"))
        before = called["n"]
        mid = service.create_meeting("Живая запись")["id"]
        service.active_meeting_id = mid
        # Тишина не пройдёт поиск речи, и до модели дело не дойдёт:
        # отдаём громкий сигнал, как настоящую реплику.
        speech = (np.sin(2 * np.pi * 200 * np.arange(RATE * 2) / RATE) * 9000).astype(np.int16)
        service.asr_queue.submit(mid, "me", speech, 0.0, block=True)
        service.asr_queue.flush(mid, block=True)
        service.asr_queue.wait_idle(timeout=30)
        assert called["n"] > before, "запись с микрофона не запускает загрузку модели"
        print("[ok] запись с микрофона тоже сама качает модель")

        # --- неудача загрузки: сообщаем, а не молчим
        fake_files.clear()
        service._asr_download_failed = False
        events.clear()

        def broken_download(on_progress=None):
            raise RuntimeError("сеть недоступна")

        service.downloader.run_blocking = broken_download
        service.transcriber.is_downloaded = lambda: False
        assert service._ensure_asr_model() is False
        assert any(e.get("state") == "error" for e in events), (
            "загрузка упала, а человеку никто не сказал"
        )
        print("[ok] при неудаче загрузки человек получает сообщение, а не тишину")

        # --- вторая попытка не долбит сеть на каждом чанке
        tries = {"n": 0}

        def counting(on_progress=None):
            tries["n"] += 1
            raise RuntimeError("сеть недоступна")

        service.downloader.run_blocking = counting
        for _ in range(5):
            service._ensure_asr_model()
        assert tries["n"] == 0, (
            f"после неудачи снова полезли в сеть {tries['n']} раз"
        )
        print("[ok] после неудачи не долбим сеть на каждой реплике")

        service.shutdown()
        print("\nНа чистой установке расшифровка не пропадает молча.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
