"""Миграция схемы на копии настоящей базы пользователя."""

import testenv  # noqa: F401  русский вывод в консоли Windows

import sqlite3
import tempfile
from pathlib import Path

import numpy as np

from app.core import paths
from app.core.models import ChatMessage, Person, Speaker, TranscriptSegment
from app.storage.db import SCHEMA_VERSION, Store

src = paths.db_path()
print("боевая база:", src, src.exists())

# Смысл теста — проверить миграцию на настоящей базе. Если программой
# ещё не пользовались (чистая машина, CI), мигрировать нечего.
#
# Существования файла мало: соседний тест мог оставить пустую заготовку
# базы, где ещё нет ни одной таблицы. Спрашиваем саму базу, что в ней
# есть, а не файловую систему.
def таблицы_есть(путь) -> bool:
    if not путь.exists() or путь.stat().st_size == 0:
        return False
    try:
        con = sqlite3.connect(f"file:{путь}?mode=ro", uri=True)
        try:
            есть = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='meetings'"
            ).fetchone()
        finally:
            con.close()
        return есть is not None
    except sqlite3.Error:
        return False


if not таблицы_есть(src):
    print("[пропуск] боевой базы нет, миграцию проверять не на чем")
    raise SystemExit(0)

tmp = Path(tempfile.mkdtemp()) / "copy.db"
# Копируем базу средствами sqlite, а не файловой системой. SQLite здесь
# работает в режиме WAL: свежие записи лежат в соседнем konspekt.db-wal,
# и обычное копирование одного файла берёт только то, что успело
# слиться на диск. На боевой базе это давало заниженные цифры «до
# миграции», а на свежей — копию вообще без единой таблицы.
источник = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
приёмник = sqlite3.connect(str(tmp))
with приёмник:
    источник.backup(приёмник)
приёмник.close()
источник.close()

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

# --- Подъём со старой версии --------------------------------------------
# Выше база уже была текущей версии, и мигрировать в ней было нечего:
# проверка «версия стала правильной» проходила сама собой, даже если
# миграция вообще ничего не делает. Поэтому отдельно берём копию,
# объявляем её старой и смотрим, что схему действительно подняли.
# Копию берём с боевой базы, а не с tmp: tmp уже прошла через Store, то
# есть починку языков к ней успели применить. Снимок «до» с такой копии
# показывал бы уже починенное, и поломка «починка трогает латиницу»
# пряталась: латиница пропадала одинаково и «до», и «после».
старая = tmp.parent / "старая.db"
источник = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
приёмник = sqlite3.connect(str(старая))
with приёмник:
    источник.backup(приёмник)
приёмник.execute("PRAGMA user_version=1")
# Заодно возвращаем в копию ложные ярлыки языка. Боевая база уже могла
# быть починена прошлым запуском, и тогда проверять было бы нечего:
# «ярлыков не осталось» выполнялось бы само собой. Портим нарочно,
# ровно так, как выглядел архив до починки.
приёмник.execute(
    "UPDATE transcript_segments SET lang='uk' "
    "WHERE lang='ru' AND rowid % 7 = 0"
)
приёмник.commit()
приёмник.close()
источник.close()

похожие = ("uk", "be", "bg", "mk", "sr")
вопросы = ",".join("?" * len(похожие))

# Снимок «до»: обязательно с самой старой копии и обязательно до того,
# как её тронет Store. Раньше он снимался с уже мигрированной базы, и
# сравнение получалось само с собой: проверка проходила, даже если
# починка ярлыков стирала тексты или трогала латиницу.
con = sqlite3.connect(str(старая))
было_встреч = con.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
ложных_до = con.execute(
    f"SELECT COUNT(*) FROM transcript_segments WHERE lang IN ({вопросы})",
    похожие).fetchone()[0]
латиница_до = con.execute(
    "SELECT COUNT(*) FROM transcript_segments WHERE lang NOT IN "
    f"({вопросы}) AND lang != 'ru'", похожие).fetchone()[0]
тексты_до = con.execute(
    "SELECT id, text FROM transcript_segments ORDER BY rowid").fetchall()
con.close()

старый_store = Store(str(старая))
con = sqlite3.connect(старая)
стало_версия = con.execute("PRAGMA user_version").fetchone()[0]
стало_встреч = con.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
con.close()

assert стало_версия == SCHEMA_VERSION, (
    f"база версии 1 осталась на версии {стало_версия}: обновление у "
    f"пользователя не довело схему до {SCHEMA_VERSION}"
)
assert стало_встреч == было_встреч, (
    f"при подъёме со старой версии встреч стало {стало_встреч} вместо {было_встреч}"
)
print(f"[ok] база версии 1 поднята до {стало_версия}, встречи целы")

# --- Ложные ярлыки языка снимаются --------------------------------------
# Определитель языка часто слышит в русской речи украинскую или
# белорусскую. Отправить её всё равно некуда, кроме русской модели, и
# текст выходит русским — а ярлык оставался чужим, и человек видел в
# расшифровке язык, которого там нет. Обновление должно это починить,
# не тронув ни тексты, ни реплики, распознанные на латинице.
con = sqlite3.connect(старая)
осталось = con.execute(
    f"SELECT COUNT(*) FROM transcript_segments WHERE lang IN ({вопросы})",
    похожие).fetchone()[0]
латиница_после = con.execute(
    "SELECT COUNT(*) FROM transcript_segments WHERE lang NOT IN "
    f"({вопросы}) AND lang != 'ru'", похожие).fetchone()[0]
тексты_после = con.execute(
    "SELECT id, text FROM transcript_segments ORDER BY rowid").fetchall()
con.close()

assert ложных_до > 0, (
    "в базе нет ни одной реплики с чужим кириллическим ярлыком: "
    "проверка ничего не проверяет"
)
assert осталось == 0, (
    f"{осталось} реплик остались с ярлыком чужого языка, хотя их "
    f"распознавала русская модель"
)
assert тексты_до == тексты_после, "починка ярлыков изменила тексты реплик"
assert латиница_до == латиница_после, (
    f"реплики на латинице тронуты: было {латиница_до}, стало {латиница_после}"
)
print(f"[ok] снято {ложных_до} ложных ярлыков, тексты и латиница ({латиница_после}) целы")

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
