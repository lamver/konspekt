"""Приёмочная проверка беды 0.7.6: запись, потом пачка файлов.

Отличие от «зависание_импорта_test.py»: там проверялась одна очередь
распознавания, здесь — весь путь целиком, как у человека. Настоящий
сервис, настоящая база, настоящие файлы на диске, настоящая очередь
импорта в своём потоке.

Важна именно последовательность: сначала живая запись со «стопом»,
и только потом файлы. Без записи беда не воспроизводилась, поэтому
проверки импорта её и не замечали.
"""

from __future__ import annotations

import os
import tempfile
import threading
import wave
from pathlib import Path

import testenv  # noqa: F401  русский вывод в консоли Windows
import numpy as np

БЕДЫ: list[str] = []


def проверить(условие: bool, описание: str) -> None:
    if условие:
        print(f"[ок] {описание}")
    else:
        БЕДЫ.append(описание)
        print(f"[БЕДА] {описание}")


def сделать_wav(путь: Path, секунд: float = 2.0) -> None:
    """Короткая запись с настоящим звуком, а не тишиной.

    Тишину VAD честно выбросит, и проверка перестанет проверять то,
    ради чего написана.
    """
    частота = 16000
    t = np.linspace(0, секунд, int(частота * секунд), endpoint=False)
    волна = (np.sin(2 * np.pi * 220 * t) * 8000).astype(np.int16)
    with wave.open(str(путь), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(частота)
        w.writeframes(волна.tobytes())


def main() -> int:
    сюда = Path(tempfile.mkdtemp(prefix="konspekt-import-"))
    os.environ["KONSPEKT_DATA_DIR"] = str(сюда)

    from app.core.service import AppService
    from app.core import settings as settings_mod

    служба = AppService()
    # Распознавание отключаем: нас занимает очередь и её счётчик, а не
    # качество текста. Модели весят полгигабайта, и тянуть их сюда
    # значит превратить быструю проверку в получасовую.
    служба.settings.asr.enabled = False

    # 1. Живая запись со «стопом» — то, что оставляло долг в очереди.
    служба.asr_queue.submit("живая", "me", np.zeros(16000, dtype=np.int16), 0.0)
    служба.asr_queue.stop()

    # 2. Пачка файлов, как перетаскиванием в окно.
    файлы = []
    for номер in range(3):
        путь = сюда / f"запись-{номер}.wav"
        сделать_wav(путь)
        файлы.append(str(путь))

    служба.importer.add(файлы)

    # Ждём, пока очередь импорта разберётся. Потолок щедрый: важно
    # отличить «медленно» от «навсегда».
    дошло = threading.Event()

    def ждём() -> None:
        import time
        предел = time.monotonic() + 60
        while time.monotonic() < предел:
            задачи = служба.importer.tasks()
            if задачи and all(з["status"] in ("done", "failed", "cancelled") for з in задачи):
                дошло.set()
                return
            time.sleep(0.2)

    threading.Thread(target=ждём, daemon=True).start()
    успели = дошло.wait(timeout=70)

    задачи = служба.importer.tasks()
    проверить(успели, "все три файла импорта дошли до конца")
    проверить(
        len(задачи) == 3 and all(з["status"] == "done" for з in задачи),
        f"каждый файл в состоянии done (сейчас: {[з['status'] for з in задачи]})",
    )
    проверить(
        all(з["progress"] == 1.0 for з in задачи),
        "полоска прогресса дошла до конца у всех",
    )

    # Встречи закрыты, а не висят в обработке: именно это человек видел
    # как «100%, но ничего не происходит».
    встречи = служба.list_meetings()
    незакрытые = [в for в in встречи if str(в.get("status", "")).lower() == "processing"]
    проверить(
        not незакрытые,
        f"ни одна встреча не осталась в обработке (висит: {len(незакрытые)})",
    )

    # Отдельно: встреча, застрявшая в обработке от прошлого запуска,
    # должна чиниться при старте. Иначе тот, кто уже поймал зависание,
    # остался бы с вечным «обрабатывается» даже после перезапуска.
    from app.core.models import Meeting, MeetingStatus

    застрявшая = Meeting(title="от прошлого раза", status=MeetingStatus.PROCESSING)
    служба.store.create_meeting(застрявшая)
    служба._recover_stale_recordings()
    после = служба.store.get_meeting(застрявшая.id)
    проверить(
        после is not None and после.status is MeetingStatus.READY,
        "встреча, застрявшая в обработке, чинится при запуске",
    )

    try:
        служба.shutdown()
    except Exception:
        pass

    if БЕДЫ:
        print(f"\nПровалено проверок: {len(БЕДЫ)}")
        return 1
    print("\nЗапись и следом пачка файлов проходят целиком")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
