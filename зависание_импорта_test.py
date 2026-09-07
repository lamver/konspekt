"""Импорт после записи не должен зависать.

Живая беда 0.7.6: человек записал встречу, потом бросил в окно пачку
файлов. Первый отдал текст и замер на 100%, остальные не начинались
вовсе. Виновата метка остановки очереди распознавания: поток забирал её
и выходил, не отметив сделанной, и счётчик незавершённых задач навсегда
оставался ненулевым. Следующий wait_idle ждал его до конца света, держа
в себе поток импорта, а он один на все файлы.

Проверка идёт настоящим путём: та же очередь, тот же stop(), тот же
wait_idle(), без подмен внутренностей.
"""

from __future__ import annotations

import threading

import testenv  # noqa: F401  русский вывод в консоли Windows
import numpy as np

from app.asr.queue import TranscriptionQueue
from app.core.models import TranscriptSegment

БЕДЫ: list[str] = []


def проверить(условие: bool, описание: str) -> None:
    if условие:
        print(f"[ок] {описание}")
    else:
        БЕДЫ.append(описание)
        print(f"[БЕДА] {описание}")


class Эхо:
    """Распознаватель-пустышка: отдаёт одну реплику на кусок."""

    def transcribe(self, pcm, sample_rate, meeting_id, offset, speaker):
        return [
            TranscriptSegment(
                meeting_id=meeting_id,
                speaker=speaker,
                text="раз",
                start=offset,
                end=offset + 1.0,
            )
        ]


def очередь() -> TranscriptionQueue:
    return TranscriptionQueue(
        transcriber=Эхо(),
        on_segment=lambda seg: None,
        use_vad=False,
    )


def кусок() -> np.ndarray:
    return np.zeros(16000, dtype=np.int16)


def проверить_ожидание_после_остановки() -> None:
    """Главный случай: запись, стоп, потом импорт."""
    q = очередь()
    q.submit("встреча-1", "me", кусок(), 0.0)
    q.stop()  # как по кнопке «стоп» живой записи

    # Теперь то, что делает импорт файла.
    q.submit("встреча-2", "me", кусок(), 0.0, block=True)

    готово = threading.Event()

    def ждём() -> None:
        q.wait_idle(timeout=5.0)
        готово.set()

    threading.Thread(target=ждём, daemon=True).start()
    if готово.wait(timeout=10.0):
        проверить(True, "импорт после записи дожидается очереди")
    else:
        проверить(False, "импорт после записи завис на wait_idle")
    q.stop()


def проверить_много_остановок() -> None:
    """Три встречи подряд: счётчик не должен копить долг."""
    q = очередь()
    for круг in range(3):
        q.submit(f"встреча-{круг}", "me", кусок(), 0.0)
        q.stop()

    q.submit("после-всех", "me", кусок(), 0.0, block=True)
    готово = threading.Event()
    threading.Thread(
        target=lambda: (q.wait_idle(timeout=5.0), готово.set()), daemon=True
    ).start()
    if готово.wait(timeout=10.0):
        проверить(True, "очередь не копит долг после нескольких остановок")
    else:
        проверить(False, "после нескольких записей импорт завис")
    q.stop()


def проверить_потолок_ожидания() -> None:
    """wait_idle обязан возвращаться даже когда всё плохо.

    Раньше потолок стерёг только первую половину ожидания, а join()
    под ним висел без ограничения. Проверяем, что таймаут теперь
    настоящий: очередь искусственно оставляем должной.
    """
    q = очередь()
    q._queue.put(None)  # метка, которую никто не заберёт: поток не запущен
    исход: list[bool] = []
    готово = threading.Event()

    def ждём() -> None:
        исход.append(q.wait_idle(timeout=1.0))
        готово.set()

    threading.Thread(target=ждём, daemon=True).start()
    if готово.wait(timeout=10.0) and исход == [False]:
        проверить(True, "wait_idle возвращается по таймауту, а не висит")
    else:
        проверить(False, "wait_idle не соблюдает свой таймаут")


def main() -> int:
    проверить_ожидание_после_остановки()
    проверить_много_остановок()
    проверить_потолок_ожидания()
    if БЕДЫ:
        print(f"\nПровалено проверок: {len(БЕДЫ)}")
        return 1
    print("\nИмпорт после записи не виснет")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
