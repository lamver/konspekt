"""Прослушивание реплики: кусок берётся из нужного места записи.

Главная ловушка здесь в том, что проверка «вернулся непустой WAV»
зелёная и у сломанной реализации, которая всегда отдаёт начало первого
файла. Поэтому заходы записи размечены разными тонами: 440 Гц в первом,
880 Гц во втором, 1320 Гц в третьем. По частоте отданного куска сразу
видно, туда ли попали, и наивная реализация тест не проходит.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import base64
import io
import math
import shutil
import tempfile
import wave
from pathlib import Path

import numpy as np

RATE = 16000


def tone(path: Path, freq: float, seconds: float) -> None:
    """Записать WAV с чистым тоном: по частоте потом узнаем файл."""
    t = np.arange(int(RATE * seconds), dtype=np.float32) / RATE
    wave_data = (np.sin(2 * math.pi * freq * t) * 16000).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(wave_data.tobytes())


def main_freq(data: bytes) -> float:
    """Основная частота куска: чем его записали, тем и звучит."""
    arr = np.frombuffer(data, dtype=np.int16).astype(np.float64)
    if arr.size < 64:
        return 0.0
    spectrum = np.abs(np.fft.rfft(arr * np.hanning(arr.size)))
    return float(np.fft.rfftfreq(arr.size, 1 / RATE)[int(np.argmax(spectrum))])


def clip_freq(clip: dict) -> float:
    raw = base64.b64decode(clip["wav"])
    with wave.open(io.BytesIO(raw)) as w:
        assert w.getframerate() == RATE, "частота дискретизации не сохранена"
        return main_freq(w.readframes(w.getnframes()))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-clip-"))
    try:
        from app.core import paths as paths_mod

        # Уводим данные в свою папку: тест не должен трогать настоящий архив.
        paths_mod.data_dir = lambda: tmp  # type: ignore[assignment]
        audio = tmp / "audio"
        audio.mkdir(parents=True, exist_ok=True)
        paths_mod.audio_dir = lambda: audio  # type: ignore[assignment]
        paths_mod.db_path = lambda: tmp / "konspekt.db"  # type: ignore[assignment]

        from app.core.models import Meeting, Speaker, TranscriptSegment
        from app.core.service import AppService
        from app.storage.db import Store

        store = Store(str(tmp / "konspekt.db"))
        meeting = Meeting(title="Проверка прослушивания")
        store.create_meeting(meeting)

        # Три захода записи подряд: так бывает, когда запись ставят на
        # паузу. Время реплик при этом сквозное по встрече.
        plan = [(440.0, 10.0, 0.0), (880.0, 10.0, 10.0), (1320.0, 10.0, 20.0)]
        for freq, seconds, start in plan:
            for track in ("me", "them"):
                f = audio / meeting.id / f"2026-{int(start):02d}-{track}.wav"
                tone(f, freq, seconds)
                store.add_audio_chunk(meeting.id, track, str(f), start, seconds)

        service = AppService.__new__(AppService)
        service.store = store

        # --- главное: кусок берётся из того захода, куда попадает реплика
        cases = [(2.0, 440.0, "первый заход"),
                 (13.0, 880.0, "второй заход"),
                 (25.0, 1320.0, "третий заход")]
        for start, expect, name in cases:
            clip = service.audio_clip(meeting.id, start, start + 2.0, "me")
            assert clip, f"{name}: кусок не вернулся"
            got = clip_freq(clip)
            assert abs(got - expect) < 30, (
                f"{name}: ждали {expect:.0f} Гц, получили {got:.0f} Гц — "
                "кусок взят не из того файла"
            )
            print(f"[ok] {name}: реплика на {start:.0f}с звучит как {got:.0f} Гц")

        # Контрольный опыт: если бы всегда отдавали начало первого файла,
        # проверка выше провалилась бы. Убеждаемся, что это правда так.
        naive = service.audio_clip(meeting.id, 0.0, 2.0, "me")
        assert abs(clip_freq(naive) - 440.0) < 30
        assert abs(clip_freq(naive) - 1320.0) > 30, (
            "контрольный опыт бессмыслен: начало записи неотличимо от третьего захода"
        )
        print("[ok] контрольный опыт: начало записи и поздняя реплика звучат по-разному")

        # --- дорожка: своя реплика берётся со своей дорожки
        for track in ("me", "them"):
            clip = service.audio_clip(meeting.id, 13.0, 15.0, track)
            assert clip, f"дорожка {track}: куска нет"
        print("[ok] обе дорожки отдают звук")

        # --- длительность куска соответствует реплике
        clip = service.audio_clip(meeting.id, 12.0, 15.0, "me")
        assert 3.0 <= clip["duration"] <= 4.0, f"длина куска {clip['duration']:.2f}с"
        print(f"[ok] длина куска {clip['duration']:.2f}с при реплике в 3с (с запасом по краям)")

        # --- край записи не роняет: реплика у самого конца
        clip = service.audio_clip(meeting.id, 29.5, 31.0, "me")
        assert clip and clip["duration"] > 0, "у конца записи кусок не отдался"
        print(f"[ok] реплика у самого конца отдаётся ({clip['duration']:.2f}с)")

        # --- встреча без записи: тихий отказ, а не падение
        empty = Meeting(title="Без записи")
        store.create_meeting(empty)
        assert service.audio_clip(empty.id, 1.0, 2.0, "me") is None
        print("[ok] у встречи без записи кнопка молчит, а не роняет приложение")

        # --- пропавший файл: молчим, а не падаем
        for f in (audio / meeting.id).glob("*-me.wav"):
            f.unlink()
        gone = service.audio_clip(meeting.id, 2.0, 4.0, "me")
        assert gone is None or clip_freq(gone) > 0
        print("[ok] удалённый с диска файл не роняет прослушивание")

        # --- старая встреча без привязки ко времени
        old = Meeting(title="Старая запись")
        store.create_meeting(old)
        f = audio / old.id / "20260101-000000-me.wav"
        tone(f, 660.0, 5.0)
        store2 = Store(str(tmp / "konspekt.db"))
        store2._index_existing_audio()
        service.store = store2
        clip = service.audio_clip(old.id, 1.0, 3.0, "me")
        assert clip, "запись старой встречи не подхватилась при обновлении"
        assert abs(clip_freq(clip) - 660.0) < 30
        print("[ok] записи старых встреч привязываются ко времени при обновлении")

        print("\nПрослушивание реплики отдаёт нужный кусок записи.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
