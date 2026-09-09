"""Импорт без распознавания: файл не должен остаться пустым навсегда.

Жалоба lamver/konspekt-releases#3: «если по какой-то причине файлы не
были обработаны, они остаются висеть без транскрипции». В 0.8.0
починили только вид: встреча перестала висеть в «обрабатывается» и
стала помечаться готовой. Готовой с пустым транскриптом — то есть
внешне исправной, и человек уже не понимает, что расшифровки нет не
по его вине.

Причина глубже. `_import_chunk` в двух случаях молча пропускает
распознавание: выключено в настройках и не скачалась модель. Звук при
этом ложится на диск целым. У живой записи такие дыры попадают в
`missed_spans`, и досчёт при следующем запуске их подбирает, а импорт
шёл мимо этого механизма и следа не оставлял.

Проверяем оба пути и то, что видит пользователь: файлы разобраны без
распознавания, программа перезапущена с рабочей моделью — текст
появился сам, без единого его действия.

Настоящего распознавания тут нет: движок подменён, потому что
проверяем не качество текста, а что до него вообще дошла очередь.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import os
import shutil
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

RATE = 16000


def сделать_запись(путь: Path, секунд: float = 3.0) -> None:
    """Файл с громким сигналом: тишину поиск речи отбросит."""
    t = np.arange(int(RATE * секунд), dtype=np.float32) / RATE
    сигнал = (np.sin(2 * np.pi * 220 * t) * 12000).astype(np.int16)
    with wave.open(str(путь), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(сигнал.tobytes())


def дождаться_импорта(service, сколько: int, предел: float = 60.0) -> None:
    край = time.time() + предел
    while time.time() < край:
        items = service.import_status().get("tasks", [])
        готово = [t for t in items if t.get("status") in ("done", "failed")]
        if len(готово) >= сколько:
            return
        time.sleep(0.1)
    raise AssertionError(f"импорт не завершился за {предел} с")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-повтор-"))
    os.environ["KONSPEKT_NO_PREFETCH"] = "1"
    try:
        from app.core import paths as paths_mod

        models = tmp / "models"
        models.mkdir(parents=True, exist_ok=True)
        audio = tmp / "audio"
        audio.mkdir(parents=True, exist_ok=True)
        paths_mod.data_dir = lambda: tmp  # type: ignore[assignment]
        paths_mod.audio_dir = lambda: audio  # type: ignore[assignment]
        paths_mod.db_path = lambda: tmp / "konspekt.db"  # type: ignore[assignment]
        paths_mod.models_dir = lambda: models  # type: ignore[assignment]
        paths_mod.settings_path = lambda: tmp / "settings.json"  # type: ignore[assignment]

        from app.audio import NullCapture
        from app.core.models import Speaker, TranscriptSegment
        from app.core.service import AppService
        from app.storage.db import Store

        выключено = tmp / "выключено.wav"
        без_модели = tmp / "без-модели.wav"
        сделать_запись(выключено)
        сделать_запись(без_модели)

        путь_бд = str(tmp / "konspekt.db")
        service = AppService(store=Store(путь_бд), capture=NullCapture())

        # --- путь 1: распознавание выключено в настройках
        service.settings.asr.enabled = False
        service.import_files([str(выключено)])
        дождаться_импорта(service, 1)

        # --- путь 2: распознавание включено, но модели нет и не скачать.
        # Это состояние чистой установки без интернета, и оно тоже вело
        # к пустой встрече навсегда.
        service.settings.asr.enabled = True
        service._asr_download_failed = False
        service.transcriber.is_downloaded = lambda: False
        service.downloader.run_blocking = lambda on_progress=None: (_ for _ in ()).throw(
            RuntimeError("сеть недоступна")
        )
        service.import_files([str(без_модели)])
        дождаться_импорта(service, 2)

        встречи = service.list_meetings()
        assert len(встречи) == 2, f"ждали две встречи, получили {len(встречи)}"

        for м in встречи:
            mid = м["id"]
            название = м.get("title") or mid
            assert not service.store.list_segments(mid), (
                f"«{название}»: распознавания не было, а реплики откуда-то взялись"
            )
            # Звук обязан лежать на диске: без него досчитывать нечего,
            # и вся починка невозможна в принципе.
            assert list((audio / mid).glob("*.wav")), (
                f"«{название}»: звук импорта не сохранён, досчитать будет нечего"
            )
            # Вот оно, главное. Пропуск обязан быть записан в базу, иначе
            # никто и никогда не узнает, что встречу надо досчитать.
            assert service.store.list_missed_spans(mid), (
                f"«{название}»: импорт не оставил следа о пропущенном "
                "распознавании — встреча навсегда останется пустой, "
                "ровно как в жалобе konspekt-releases#3"
            )
        print("[ok] оба пути: звук сохранён, пропуск распознавания записан в базу")

        # --- перезапуск: модель появилась, человек ничего не делал
        service.shutdown()

        def досчитать(pcm, sample_rate, meeting_id, offset, speaker, **_):
            return [TranscriptSegment(
                meeting_id=meeting_id, speaker=Speaker(speaker),
                text=f"досчитано на {offset:.1f}с", start=offset,
                end=offset + len(pcm) / sample_rate,
            )]

        class Рабочий:
            """Распознаватель, который наконец работает."""

            name = "проба"
            languages = ("ru",)

            def is_downloaded(self) -> bool:
                return True

            def transcribe(self, *a, **k):
                return досчитать(*a, **k)

        # Распознаватель отдаём в конструктор, а не подменяем после:
        # досчёт запускается прямо при создании службы, как при обычном
        # запуске программы. Звать его руками значило бы проверять
        # метод, а не то, что программа сама всё доделывает.
        service2 = AppService(
            store=Store(путь_бд), capture=NullCapture(), transcriber=Рабочий()
        )

        край = time.time() + 90
        while time.time() < край:
            if all(service2.store.list_segments(м["id"]) for м in встречи):
                break
            time.sleep(0.1)

        for м in встречи:
            mid = м["id"]
            название = м.get("title") or mid
            сегменты = service2.store.list_segments(mid)
            assert сегменты, (
                f"«{название}»: модель появилась, а встреча так и осталась "
                "без текста — повторная обработка не работает"
            )
        print("[ok] после запуска с рабочей моделью встречи досчитались сами")

        # Досчитанное вычёркивается, иначе программа будет пересчитывать
        # одно и то же при каждом запуске до конца времён.
        край = time.time() + 30
        while time.time() < край and service2.store.list_missed_spans():
            time.sleep(0.1)
        assert not service2.store.list_missed_spans(), (
            "досчитанные куски остались в списке: их пересчитают снова"
        )
        print("[ok] досчитанное вычеркнуто, повторно считаться не будет")

        service2.shutdown()
        print("\nПовторная обработка загруженных файлов работает.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
