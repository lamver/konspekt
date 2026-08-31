"""Удаление данных: встреча уходит целиком, вместе с аудио и из файла базы.

Приложение обещает приватность, поэтому «удалил» должно значить именно
удалил, а не «спрятал из списка». Проверяем три вещи, каждую из которых
легко потерять молча:

1. Аудиофайлы встречи исчезают с диска. Раньше они оставались: за месяц
   работы так накопилось 16 папок от удалённых встреч.
2. Реплики, заметки и переписка уходят каскадом, а не висят сиротами.
3. Текст реплик исчезает из самого файла базы. Без `VACUUM` SQLite
   только помечает страницы свободными, и удалённый разговор читается
   в файле обычным просмотрщиком.
"""

from __future__ import annotations

import testenv  # noqa: F401  русский вывод в консоли Windows

import sqlite3
import tempfile
from pathlib import Path

from app.core import paths
from app.core.models import ChatMessage, Meeting, NoteLine, Speaker, TranscriptSegment
from app.storage.db import Store

# Слово-маркер: ищем именно его в сыром файле базы после удаления.
SECRET = "ПАРОЛЬОТСЕЙФАВЕРБЛЮД"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-delete-"))
    db_path = tmp / "test.db"
    store = Store(str(db_path))

    meeting = store.create_meeting(Meeting(title="Секретная встреча"))
    mid = meeting.id

    # Кладём заметный текст во все таблицы, связанные со встречей.
    for i in range(200):
        store.add_segment(TranscriptSegment(
            meeting_id=mid, text=f"{SECRET} реплика {i}",
            speaker=Speaker.ME, start=float(i), end=float(i) + 1,
        ))
    store.add_note_line(NoteLine(meeting_id=mid, text=f"{SECRET} заметка", offset=1.0))
    store.add_chat_message(ChatMessage(meeting_id=mid, role="user", text=f"{SECRET} вопрос"))

    assert len(store.list_segments(mid)) == 200, "реплики не сохранились"
    print("[ok] встреча заполнена: 200 реплик, заметка и переписка")

    # База в режиме WAL: свежие записи лежат в отдельном журнале, пока
    # он не влит в основной файл. Ищем в обоих, иначе проверка врёт.
    def db_bytes() -> bytes:
        data = db_path.read_bytes()
        wal = db_path.with_name(db_path.name + "-wal")
        if wal.exists():
            data += wal.read_bytes()
        return data

    assert SECRET.encode() in db_bytes(), "текст не дошёл до базы, тест бессмысленный"
    print("[ok] текст лежит в файле базы, есть что удалять")

    # --- удаление -------------------------------------------------------
    store.delete_meeting(mid)

    assert store.get_meeting(mid) is None, "встреча осталась в базе"
    assert store.list_segments(mid) == [], "реплики пережили удаление встречи"
    assert store.list_note_lines(mid) == [], "заметки пережили удаление"
    assert store.list_chat_messages(mid) == [], "переписка пережила удаление"
    print("[ok] встреча, реплики, заметки и переписка удалены каскадом")

    # --- текст из файла --------------------------------------------------
    freed = store.vacuum()
    assert SECRET.encode() not in db_bytes(), (
        "текст удалённой встречи всё ещё читается в файле базы: "
        "VACUUM не сработал"
    )
    print(f"[ok] текст исчез из файла базы (освободилось {freed / 1024:.0f} КБ)")

    # --- аудиофайлы ------------------------------------------------------
    # Полный путь удаления идёт через сервис: он один знает про диск.
    from app.core.service import AppService

    # Подменяем папку аудио на временную: тест не должен даже случайно
    # задеть настоящие записи пользователя.
    audio_root = tmp / "audio"
    audio_root.mkdir()
    paths.audio_dir = lambda: audio_root

    service = AppService.__new__(AppService)
    service.store = store
    service.active_meeting_id = None

    other = store.create_meeting(Meeting(title="Остаётся"))
    victim = store.create_meeting(Meeting(title="Уходит"))

    made = []
    for meeting_id in (other.id, victim.id, "нет-такой-встречи"):
        folder = audio_root / meeting_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "запись.wav").write_bytes(b"\0" * 1024)
        made.append(folder)

    AppService._delete_audio(service, victim.id)
    assert not made[1].exists(), "аудио удалённой встречи осталось на диске"
    assert made[0].exists(), "удаление задело чужие записи"
    print("[ok] удаление встречи уносит её записи и не трогает соседние")

    # --- уборка осиротевшего ---------------------------------------------
    result = AppService.cleanup_audio(service)
    assert not made[2].exists(), "осиротевшая папка осталась"
    assert made[0].exists(), "уборка снесла записи живой встречи"
    assert result["folders"] >= 1, f"уборка ничего не нашла: {result}"
    word = "папку" if result["folders"] == 1 else "папок"
    print(f"[ok] уборка убрала {result['folders']} {word} без встречи")

    print("\nУдаление уносит данные целиком: и с диска, и из файла базы.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
