"""Дымовой тест бэкенда: весь путь встречи без GUI.

Проверяет то, что нельзя проверить глазами в окне: что данные реально
доезжают до SQLite и что события шины летят в правильном порядке.
"""

import testenv  # noqa: F401  русский вывод в консоли Windows

import tempfile
import time
from pathlib import Path

from app.audio import NullCapture
from app.core.events import (
    MEETINGS_CHANGED,
    RECORDING_LEVEL,
    RECORDING_STARTED,
    RECORDING_STOPPED,
    bus,
)
from app.core.service import AppService
from app.storage import Store


def main() -> int:
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    service = AppService(store=Store(str(tmp)), capture=NullCapture())

    seen: list[str] = []
    levels: list[dict] = []
    for topic in (MEETINGS_CHANGED, RECORDING_STARTED, RECORDING_STOPPED):
        bus.on(topic, lambda p, t=topic: seen.append(t))
    bus.on(RECORDING_LEVEL, levels.append)

    assert service.list_meetings() == [], "новая база должна быть пустой"

    meeting = service.create_meeting("Тестовая встреча")
    mid = meeting["id"]
    assert meeting["title"] == "Тестовая встреча"
    assert meeting["status"] == "draft"
    print(f"[ok] встреча создана: {mid}")

    service.save_notes(mid, "первый тезис\nвторой тезис")
    assert service.get_meeting(mid)["notes"].startswith("первый")
    print("[ok] заметки сохранены и прочитаны")

    started = service.start_recording(mid)
    assert started["status"] == "recording"
    assert service.is_recording
    print("[ok] запись начата")

    time.sleep(0.35)
    assert levels, "заглушка обязана слать уровни звука"
    print(f"[ok] уровни звука приходят: {len(levels)} событий")

    line = service.add_note_line(mid, "тезис с таймстемпом")
    assert line["offset"] > 0, "во время записи смещение должно быть больше нуля"
    print(f"[ok] строка заметок с offset={line['offset']:.2f}с")

    stopped = service.stop_recording()
    assert stopped["status"] == "ready"
    assert stopped["duration"] > 0
    assert not service.is_recording
    print(f"[ok] запись остановлена, длительность {stopped['duration']:.2f}с")

    full = service.get_meeting(mid)
    assert len(full["note_lines"]) == 1
    assert full["segments"] == []
    print("[ok] встреча читается целиком")

    service.update_meeting(mid, title="Переименована")
    assert service.get_meeting(mid)["title"] == "Переименована"
    print("[ok] переименование работает")

    service.delete_meeting(mid)
    assert service.get_meeting(mid) is None
    assert service.list_meetings() == []
    print("[ok] удаление работает")

    expected = [MEETINGS_CHANGED, RECORDING_STARTED, MEETINGS_CHANGED]
    assert seen[:3] == expected, f"неверный порядок событий: {seen[:3]}"
    assert RECORDING_STOPPED in seen
    print(f"[ok] события шины в правильном порядке ({len(seen)} шт.)")

    service.shutdown()
    print("\nВсе проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
