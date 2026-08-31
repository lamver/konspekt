"""Миграция схемы на копии настоящей базы пользователя."""

import testenv  # noqa: F401  русский вывод в консоли Windows

import shutil
import sqlite3
import tempfile
from pathlib import Path

import numpy as np

from app.core import paths
from app.core.models import ChatMessage, Person, Speaker, TranscriptSegment
from app.storage.db import SCHEMA_VERSION, Store

src = paths.db_path()
print("боевая база:", src, src.exists())

tmp = Path(tempfile.mkdtemp()) / "copy.db"
shutil.copy(src, tmp)

# Что было до миграции
con = sqlite3.connect(tmp)
before_ver = con.execute("PRAGMA user_version").fetchone()[0]
before_meetings = con.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
before_segs = con.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0]
before_notes = con.execute("SELECT COUNT(*) FROM note_lines").fetchone()[0]
sample = con.execute(
    "SELECT id, text FROM transcript_segments ORDER BY rowid LIMIT 3").fetchall()
con.close()
print(f"до: версия={before_ver}, встреч={before_meetings}, "
      f"сегментов={before_segs}, заметок={before_notes}")

store = Store(str(tmp))

con = sqlite3.connect(tmp)
after_ver = con.execute("PRAGMA user_version").fetchone()[0]
after_meetings = con.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
after_segs = con.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0]
after_notes = con.execute("SELECT COUNT(*) FROM note_lines").fetchone()[0]
cols = {r[1] for r in con.execute("PRAGMA table_info(transcript_segments)")}
after_sample = con.execute(
    "SELECT id, text FROM transcript_segments ORDER BY rowid LIMIT 3").fetchall()
con.close()
print(f"после: версия={after_ver}, встреч={after_meetings}, "
      f"сегментов={after_segs}, заметок={after_notes}")

assert after_ver == SCHEMA_VERSION, f"версия схемы {after_ver}"
assert (after_meetings, after_segs, after_notes) == (before_meetings, before_segs, before_notes), \
    "миграция потеряла данные"
print("[ok] ни одна запись не потеряна")
assert sample == after_sample, "тексты сегментов изменились"
print("[ok] тексты старых реплик на месте")
assert {"voice_id", "person_id", "voice_label"} <= cols, f"колонок нет: {cols}"
print("[ok] новые колонки добавлены к существующей таблице")

# Старые сегменты читаются, и говорящий у них ровно тот, что лежит в
# базе. Раньше здесь стояло «у первой реплики говорящий пуст», но в
# боевой базе давно есть встречи с распознанными голосами, и проверка
# падала не от поломки, а от того, что жизнь ушла вперёд.
mid = None
con = sqlite3.connect(tmp)
row = con.execute("SELECT meeting_id FROM transcript_segments LIMIT 1").fetchone()
labels = dict(con.execute(
    "SELECT id, COALESCE(voice_label, '') FROM transcript_segments").fetchall())
con.close()
if row:
    mid = row[0]
    segs = store.list_segments(mid)
    assert segs, "старые сегменты не читаются"
    for s in segs:
        assert s.voice_label == labels[s.id], \
            f"говорящий разошёлся с базой: {s.voice_label!r} вместо {labels[s.id]!r}"
    empty = sum(1 for s in segs if not s.voice_label)
    print(f"[ok] старые реплики читаются ({len(segs)} шт.), "
          f"говорящий совпадает с базой, пустых {empty}")

# Голос берём с заведомо своим идентификатором: в боевой базе уже есть
# встречи с them-1, и тест ловил бы чужие реплики вместо своей.
if mid:
    seg = TranscriptSegment(
        meeting_id=mid, speaker=Speaker.THEM, text="проверка",
        start=1.0, end=2.0, voice_id="test-voice",
        person_id="p-1", voice_label="Собеседник 1")
    store.add_segment(seg)
    got = [s for s in store.list_segments(mid) if s.id == seg.id][0]
    assert got.voice_label == "Собеседник 1" and got.voice_id == "test-voice"
    assert got.person_id == "p-1"
    print("[ok] новая реплика сохраняет говорящего")

    assert store.relabel_segments(mid, "test-voice", "Анна") == 1
    got = [s for s in store.list_segments(mid) if s.id == seg.id][0]
    assert got.voice_label == "Анна"
    print("[ok] переименование участника меняет его реплики")

# База знакомых голосов. Считаем от того, что уже есть в копии боевой
# базы: там могут лежать реальные люди, и жёсткое число ломало тест.
people_before = len(store.list_people())
vec = np.random.default_rng(1).normal(size=256).astype(np.float32)
vec /= np.linalg.norm(vec)
p = Person(name="Валерий", kind="owner", embedding=vec.tolist(), samples=5)
store.save_person(p)
back = store.get_person(p.id)
assert back is not None and back.name == "Валерий"
assert np.allclose(back.embedding, vec, atol=1e-6), "вектор исказился при хранении"
print(f"[ok] голос сохранён и прочитан без искажений ({len(back.embedding)} чисел)")

owner = store.get_owner()
assert owner is not None, "владелец не нашёлся"
print("[ok] владелец находится по типу")

p.name = "Валерий Петрович"
p.samples = 9
store.save_person(p)
again = store.get_person(p.id)
assert again.name == "Валерий Петрович" and again.samples == 9
assert len(store.list_people()) == people_before + 1, "обновление создало второго человека"
print("[ok] повторное сохранение обновляет, а не дублирует")

store.delete_person(p.id)
assert store.get_person(p.id) is None
assert len(store.list_people()) == people_before, "удаление задело чужих"
print("[ok] удаление человека работает")

# Переписка по встрече переживает перезапуск: ради этого она и лежит
# в базе, а не в памяти вкладки.
if mid:
    assert store.list_chat_messages(mid) == [], "чат новой встречи не пуст"
    store.add_chat_message(ChatMessage(meeting_id=mid, role="user", text="кто что обещал?"))
    a = store.add_chat_message(ChatMessage(meeting_id=mid, role="assistant", text=""))
    store.update_chat_message(a.id, "Дмитрий обещал удаление данных.")
    got = store.list_chat_messages(mid)
    assert [m.role for m in got] == ["user", "assistant"], "порядок реплик сбился"
    assert got[1].text == "Дмитрий обещал удаление данных.", "ответ не дописался"
    print("[ok] чат сохраняется, ответ дописывается по кускам")

    store.clear_chat(mid)
    assert store.list_chat_messages(mid) == [], "очистка чата не сработала"
    assert store.list_segments(mid), "очистка чата задела расшифровку"
    print("[ok] очистка чата не трогает расшифровку")

# Повторное открытие уже мигрированной базы ничего не ломает
store.close()
store2 = Store(str(tmp))
assert len(store2.list_segments(mid)) if mid else True
store2.close()
print("[ok] повторная миграция идемпотентна")

print("\nМиграция прошла без потерь.")
