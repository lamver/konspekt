"""Импорт файла: у реплик есть что переслушать.

Раньше загруженная запись оставляла только текст: звук нигде не
сохранялся, и кнопка «переслушать» у таких встреч молчала. Здесь
проверяем весь путь на настоящем файле и, главное, что отданный кусок
соответствует нужной секунде, а не началу записи.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import base64
import io
import math
import shutil
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

RATE = 16000


def make_file(path: Path) -> None:
    """Файл, у которого каждая секунда звучит по-своему.

    Тон растёт ступеньками, поэтому по частоте куска сразу видно, с
    какой секунды он взят. На обычной записи это было бы не проверить.
    """
    parts = []
    for i in range(10):
        t = np.arange(RATE, dtype=np.float32) / RATE
        parts.append(np.sin(2 * math.pi * (300 + i * 200) * t) * 0.5)
    data = (np.concatenate(parts) * 32000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())


def main_freq(pcm: np.ndarray) -> float:
    if pcm.size < 64:
        return 0.0
    spectrum = np.abs(np.fft.rfft(pcm.astype(np.float64) * np.hanning(pcm.size)))
    return float(np.fft.rfftfreq(pcm.size, 1 / RATE)[int(np.argmax(spectrum))])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-import-clip-"))
    try:
        from app.core import paths as paths_mod

        audio = tmp / "audio"
        audio.mkdir(parents=True, exist_ok=True)
        paths_mod.data_dir = lambda: tmp  # type: ignore[assignment]
        paths_mod.audio_dir = lambda: audio  # type: ignore[assignment]
        paths_mod.db_path = lambda: tmp / "konspekt.db"  # type: ignore[assignment]

        from app.audio import NullCapture
        from app.core.service import AppService
        from app.storage.db import Store

        src = tmp / "разговор.wav"
        make_file(src)

        service = AppService(store=Store(str(tmp / "konspekt.db")), capture=NullCapture())
        # Распознавание тут не нужно: проверяем сохранение звука.
        service.settings.asr.enabled = False

        tasks = service.import_files([str(src)])
        assert tasks, "файл не принят в импорт"

        # Ждём разбора: он идёт в фоновом потоке.
        deadline = time.time() + 60
        meeting_id = None
        while time.time() < deadline:
            status = service.import_status()
            items = status.get("tasks", status if isinstance(status, list) else [])
            done = [t for t in items if t.get("status") in ("done", "failed")]
            if done:
                meeting_id = done[0].get("meeting_id")
                assert done[0]["status"] == "done", f"импорт упал: {done[0]}"
                break
            time.sleep(0.2)
        assert meeting_id, "импорт не завершился за минуту"
        print(f"[ok] файл разобран, встреча {meeting_id}")

        chunks = service.store.list_audio_chunks(meeting_id)
        assert chunks, "у загруженной записи не сохранилось аудио — переслушать нечего"
        print(f"[ok] звук сохранён рядом со встречей: {len(chunks)} дорожка(и), "
              f"{chunks[0]['duration_s']:.1f}с")

        # Главное: кусок берётся с нужной секунды. Файл размечен так, что
        # шестая секунда звучит на 1500 Гц, а начало — на 300 Гц.
        clip = service.audio_clip(meeting_id, 6.0, 7.0, chunks[0]["track"])
        assert clip and clip["wav"], "кусок не отдался"
        with wave.open(io.BytesIO(base64.b64decode(clip["wav"]))) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        got = main_freq(pcm)
        assert abs(got - 1500) < 120, f"на 6-й секунде ждали ~1500 Гц, получили {got:.0f}"
        print(f"[ok] реплика на 6-й секунде звучит как {got:.0f} Гц, а не началом записи")

        # Контрольный опыт: начало файла звучит иначе, иначе проверка
        # выше проходила бы и у сломанной реализации.
        head = service.audio_clip(meeting_id, 0.0, 1.0, chunks[0]["track"])
        with wave.open(io.BytesIO(base64.b64decode(head["wav"]))) as w:
            first = main_freq(np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16))
        assert abs(first - got) > 120, (
            f"контрольный опыт бессмыслен: начало {first:.0f} Гц неотличимо от {got:.0f}"
        )
        print(f"[ok] контрольный опыт: начало записи звучит как {first:.0f} Гц")

        # Фронт прячет кнопку у встреч без записи, значит флаг обязан быть
        # честным: иначе кнопка либо пропадёт зря, либо обманет.
        assert service.get_meeting(meeting_id)["has_audio"] is True
        empty_id = service.create_meeting("Без записи")["id"]
        assert service.get_meeting(empty_id)["has_audio"] is False
        print("[ok] встреча со звуком помечена, пустая — нет")

        # Удаление встречи должно уносить и сохранённый звук.
        folder = audio / meeting_id
        assert folder.exists()
        service.delete_meeting(meeting_id)
        assert not folder.exists(), "звук импорта остался на диске после удаления встречи"
        print("[ok] удаление встречи уносит и сохранённый звук импорта")

        service.shutdown()
        print("\nУ загруженных файлов реплики можно переслушать.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
