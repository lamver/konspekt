"""Недосчитанное переживает закрытие программы.

Досчёт пропущенных кусков идёт фоновым потоком и на длинной встрече
занимает минуты. Встреча к этому времени уже помечена готовой, окно
показывает её как обычную, и повода ждать у человека нет: он закрывает
программу. Список пропусков жил только в памяти очереди, поэтому вместе
с процессом исчезал и он — дыра в расшифровке оставалась навсегда, хотя
звук на диске был цел.

Здесь проверяется, что пропуски записаны в базу и досчитываются при
следующем запуске.

Закрытие программы настоящее: первый этап идёт отдельным процессом,
который убивается посреди досчёта. Имитация «позвали shutdown()» ничего
не доказала бы — поток досчёта в том же процессе продолжил бы работать и
дописал бы всё сам.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import time
import wave
from pathlib import Path

import numpy as np

import testenv  # noqa: F401  русский вывод в консоли Windows

from app.storage.db import Store

RATE = 16000
БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


def write_wav(path: Path, seconds: float) -> None:
    t = np.arange(int(RATE * seconds), dtype=np.float32) / RATE
    signal = (np.sin(2 * np.pi * 220 * t) * 12000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(signal.tobytes())


# Первый запуск: встреча с пропусками, «стоп», и досчёт, который не успеет
# закончиться. Живёт отдельным процессом, чтобы его можно было честно убить.
ПЕРВЫЙ_ЗАПУСК = '''
import sys, wave
from pathlib import Path
import numpy as np

tmp = Path(sys.argv[1])
медленно = float(sys.argv[2])

# Берём `app` из того же дерева, что и сам тест, а не из установленного
# пакета. В .venv лежит editable-ссылка на боевой каталог, поэтому без
# этой строки дочерний процесс импортировал бы код из рабочей копии,
# даже когда тест запущен в отдельном git worktree, и проверка «сломай
# код, тест должен покраснеть» была бы обманом: он бы всегда зеленел.
sys.path.insert(0, sys.argv[3])

from app.core import paths as paths_mod
audio_root = tmp / "audio"
paths_mod.data_dir = lambda: tmp
paths_mod.audio_dir = lambda: audio_root
paths_mod.db_path = lambda: tmp / "konspekt.db"
paths_mod.models_dir = lambda: tmp / "models"
paths_mod.settings_path = lambda: tmp / "settings.json"

import time
from app.asr.queue import MissedSpan
from app.audio import NullCapture
from app.core.models import Speaker, TranscriptSegment
from app.core.service import AppService
from app.storage.db import Store


class МедленнаяМодель:
    """Не успевает — ровно тот случай, ради которого досчёт и написан."""
    name = "медленная"
    languages = ("ru",)

    def transcribe(self, pcm, sample_rate, meeting_id, offset, speaker, **_):
        time.sleep(медленно)
        return [TranscriptSegment(
            meeting_id=meeting_id, speaker=Speaker(speaker),
            text=f"фраза на {offset:.1f}с", start=offset,
            end=offset + len(pcm) / sample_rate,
        )]


service = AppService(
    store=Store(str(tmp / "konspekt.db")),
    capture=NullCapture(),
    transcriber=МедленнаяМодель(),
)
service.settings.asr.enabled = True
meeting = service.create_meeting("Встреча с пропусками")
meeting_id = meeting["id"]
print("ВСТРЕЧА", meeting_id, flush=True)

service.start_recording(meeting_id)

wav_path = audio_root / meeting_id / "запись-me.wav"
wav_path.parent.mkdir(parents=True, exist_ok=True)
t = np.arange(int(16000 * 30.0), dtype=np.float32) / 16000
signal = (np.sin(2 * np.pi * 220 * t) * 12000).astype(np.int16)
with wave.open(str(wav_path), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(signal.tobytes())
service.store.add_audio_chunk(meeting_id, "me", str(wav_path), 0.0, 30.0)

# Куски, которые не влезли в живую очередь: тот же список, что заполняет
# _put при переполнении.
for i in range(20):
    service.asr_queue._missed.append(MissedSpan(meeting_id, "me", float(i), 1.0))

service.stop_recording()
print("СТОП", flush=True)
# Не выходим сами: нас убьют посреди досчёта, как это делает человек,
# закрывающий программу.
time.sleep(600)
'''


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-restart-"))
    (tmp / "audio").mkdir(parents=True, exist_ok=True)
    скрипт = tmp / "первый_запуск.py"
    скрипт.write_text(textwrap.dedent(ПЕРВЫЙ_ЗАПУСК), encoding="utf-8")

    p = subprocess.Popen(
        [sys.executable, str(скрипт), str(tmp), "0.3",
         str(Path(__file__).resolve().parent)],
        cwd=str(Path(__file__).parent),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        # Дочерний процесс пишет по-русски, а вывод в трубу Windows по
        # умолчанию считает cp1252 и падает на первой же кириллице.
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    meeting_id = ""
    дошли_до_стопа = False
    начало = time.monotonic()
    while time.monotonic() - начало < 90:
        line = p.stdout.readline()
        if not line:
            break
        line = line.strip()
        if line.startswith("ВСТРЕЧА "):
            meeting_id = line.split()[1]
        elif line == "СТОП":
            дошли_до_стопа = True
            break
    проверить(дошли_до_стопа and bool(meeting_id),
              "первый запуск дошёл до кнопки «стоп» и начал досчёт")

    # Даём досчёту сделать часть работы, но не всю: 20 кусков по 0.3 с — это
    # 6 секунд, а мы ждём около двух. Убивать сразу нельзя, иначе проверка
    # выродится в «ничего не начиналось».
    time.sleep(2.0)
    p.kill()
    p.wait(timeout=30)

    остаток = Store(str(tmp / "konspekt.db"))
    # getattr, а не прямой вызов: на коде до этой правки хранилища пропусков
    # нет вовсе, и тест должен показать это провалом проверки по существу,
    # а не падением с AttributeError.
    список = getattr(остаток, "list_missed_spans", None)
    осталось = список(meeting_id) if список is not None else []
    успели = остаток.list_segments(meeting_id)
    остаток.close()
    print(f"       к моменту закрытия: досчитано {len(успели)}, "
          f"осталось в базе {len(осталось)}")

    # Без этой проверки тест обманывал бы сам себя с двух сторон: если
    # досчёт не начинался, «пережить закрытие» нечему, а если он всё успел,
    # то и второй запуск ничего не докажет.
    проверить(len(успели) > 0, "досчёт успел сделать часть работы до закрытия")
    проверить(len(осталось) > 0,
              f"недосчитанное осталось записанным в базе ({len(осталось)} кусков), "
              f"а не исчезло вместе с программой")

    # Второй запуск: та же папка данных, та же база. Ничего не делаем —
    # программа обязана добрать хвост сама.
    from app.core import paths as paths_mod
    paths_mod.data_dir = lambda: tmp  # type: ignore[assignment]
    paths_mod.audio_dir = lambda: tmp / "audio"  # type: ignore[assignment]
    paths_mod.db_path = lambda: tmp / "konspekt.db"  # type: ignore[assignment]
    paths_mod.models_dir = lambda: tmp / "models"  # type: ignore[assignment]
    paths_mod.settings_path = lambda: tmp / "settings.json"  # type: ignore[assignment]

    from app.audio import NullCapture
    from app.core.models import Speaker, TranscriptSegment
    from app.core.service import AppService

    class БыстраяМодель:
        name = "проба"
        languages = ("ru",)

        def transcribe(self, pcm, sample_rate, meeting_id, offset, speaker, **_):
            return [TranscriptSegment(
                meeting_id=meeting_id, speaker=Speaker(speaker),
                text=f"фраза на {offset:.1f}с", start=offset,
                end=offset + len(pcm) / sample_rate,
            )]

    service = AppService(
        store=Store(str(tmp / "konspekt.db")),
        capture=NullCapture(),
        transcriber=БыстраяМодель(),
    )
    try:
        # Ждём с опросом, а не фиксированной паузой: досчёт идёт своим
        # потоком, и на занятой машине он медленнее.
        крайний = time.monotonic() + 60
        while список is not None and time.monotonic() < крайний:
            if not service.store.list_missed_spans(meeting_id):
                break
            time.sleep(0.1)
        service.asr_queue.wait_idle(timeout=30)

        хвост = service.store.list_missed_spans(meeting_id) if список else []
        проверить(not хвост,
                  f"после второго запуска досчитывать больше нечего "
                  f"(осталось {len(хвост)})")

        segments = service.store.list_segments(meeting_id)
        места = sorted({round(s.start, 1) for s in segments})
        проверить(len(места) == 20,
                  f"в расшифровке все 20 кусков речи, включая недосчитанные "
                  f"в прошлый раз (нашлось {len(места)}: {места})")
    finally:
        service.shutdown()

    if БЕДЫ:
        print(f"\nПровалено проверок: {len(БЕДЫ)}")
        return 1
    print("\nНедосчитанное переживает закрытие программы и досчитывается потом")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
