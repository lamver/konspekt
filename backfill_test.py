"""Пропущенные при перегрузке чанки не теряются навсегда.

Живая очередь распознавания ограничена (MAX_PENDING): если модель не
успевает, лишний чанк выбрасывается сразу, чтобы не заблокировать поток
захвата звука. До этой правки на этом всё и заканчивалось — реплика
пропадала из транскрипта насовсем, хотя сам звук лежал на диске в целости.

Проверяем: как только очередь переполняется, потерянный интервал
запоминается (TranscriptionQueue.take_missed), а по кнопке «стоп»
AppService вырезает тот же кусок из записанного WAV и досчитывает его
отдельно, block=True, не спеша. В итоговом транскрипте текст должен
появиться, даже если он не успел прийти вовремя.
"""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import numpy as np

import testenv  # noqa: F401  русский вывод в консоли Windows

from app.asr.queue import MAX_PENDING, Job, MissedSpan
from app.audio import NullCapture
from app.core.events import RECOGNITION_BACKFILL, bus
from app.core.models import Speaker, TranscriptSegment
from app.core.service import AppService
from app.storage.db import Store

RATE = 16000
БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ok] " if ок else "[FAIL] ") + что)
    if not ок:
        БЕДЫ.append(что)


class МодельЗаглушка:
    """Отдаёт текст с офсетом внутри — по нему видно, какой кусок дошёл."""

    name = "проба"
    languages = ("ru",)

    def transcribe(self, pcm, sample_rate, meeting_id, offset, speaker, **_):
        return [TranscriptSegment(
            meeting_id=meeting_id, speaker=Speaker(speaker),
            text=f"фраза на {offset:.1f}с", start=offset,
            end=offset + len(pcm) / sample_rate,
        )]


def write_wav(path: Path, seconds: float) -> None:
    import wave

    t = np.arange(int(RATE * seconds), dtype=np.float32) / RATE
    signal = (np.sin(2 * np.pi * 220 * t) * 12000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(signal.tobytes())


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-backfill-"))
    from app.core import paths as paths_mod

    audio_root = tmp / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    paths_mod.data_dir = lambda: tmp  # type: ignore[assignment]
    paths_mod.audio_dir = lambda: audio_root  # type: ignore[assignment]
    paths_mod.db_path = lambda: tmp / "konspekt.db"  # type: ignore[assignment]
    paths_mod.models_dir = lambda: tmp / "models"  # type: ignore[assignment]
    paths_mod.settings_path = lambda: tmp / "settings.json"  # type: ignore[assignment]

    service = AppService(
        store=Store(str(tmp / "konspekt.db")),
        capture=NullCapture(),
        transcriber=МодельЗаглушка(),
    )
    try:
        service.settings.asr.enabled = True
        meeting = service.create_meeting("Проверка докатки")
        meeting_id = meeting["id"]

        # «Запись» через NullCapture — active_meeting_id проставляется
        # так же, как при настоящем захвате.
        service.start_recording(meeting_id)

        # Кусок звука на диске так же, как это делает настоящий захват:
        # файл дорожки плюс привязка к времени встречи в базе.
        wav_path = audio_root / meeting_id / "запись-me.wav"
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        write_wav(wav_path, 10.0)
        service.store.add_audio_chunk(meeting_id, "me", str(wav_path), 0.0, 10.0)

        # Переполняем очередь напрямую (без VAD и без настоящего потока
        # захвата): живая ситуация в миниатюре — модель не успевает и
        # честный дроп должен оставить след, а не молча съесть кусок.
        # Поток-потребитель ещё не запущен (submit его поднимет), поэтому
        # очередь предсказуемо забивается доверху.
        for i in range(MAX_PENDING):
            pcm = np.zeros(RATE, dtype=np.int16)
            service.asr_queue._queue.put_nowait(Job(meeting_id, "me", pcm, float(i)))

        # Пропущенный при переполнении кусок кладём в список "потерь" так
        # же, как это делает _put при queue.Full: сам факт срабатывания
        # дропа в submit() уже проверен в draft_test.py и queue-тестах,
        # здесь важно поведение системы после потери, а не сама потеря.
        service.asr_queue._missed.append(MissedSpan(meeting_id, "me", 2.0, 2.0))

        # Освобождаем очередь, чтобы рабочий поток не завис на стопе.
        while not service.asr_queue._queue.empty():
            try:
                service.asr_queue._queue.get_nowait()
                service.asr_queue._queue.task_done()
            except Exception:
                break

        backfill_done = threading.Event()

        def on_backfill(payload):
            if payload.get("state") == "done":
                backfill_done.set()

        stop_sub = bus.on(RECOGNITION_BACKFILL, on_backfill)

        result = service.stop_recording()
        проверить(result is not None, "stop_recording вернул встречу")

        проверить(backfill_done.wait(15), "докатка завершилась за разумное время")
        stop_sub()
        service.asr_queue.wait_idle(timeout=15)

        segments = service.store.list_segments(meeting_id)
        тексты = [s.text for s in segments]
        нашли = any("на 2.0с" in t for t in тексты)
        проверить(нашли,
                  f"пропущенный кусок 2.0с догнал транскрипт (реплики: {тексты})")

        проверить(not service.asr_queue.take_missed(meeting_id),
                  "после докатки список потерь этой встречи пуст")

    finally:
        service.shutdown()

    if БЕДЫ:
        print(f"\nПровалено проверок: {len(БЕДЫ)}")
        return 1
    print("\nПропущенные при перегрузке куски всё равно доходят до транскрипта")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
