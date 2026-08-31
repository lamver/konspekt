"""Хранилище на SQLite.

Схема нарочно плоская и версионируется простым `user_version`: миграции
понадобятся уже на этапе 3, когда поедут сегменты транскрипта.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

import numpy as np

from ..core import paths
from ..core.models import (
    ChatMessage,
    Meeting,
    MeetingStatus,
    NoteLine,
    Person,
    Speaker,
    TranscriptSegment,
    new_id,
    now,
)

log = logging.getLogger(__name__)

SCHEMA_VERSION = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    started_at  REAL,
    ended_at    REAL,
    status      TEXT NOT NULL DEFAULT 'draft',
    template    TEXT NOT NULL DEFAULT 'general',
    notes       TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    audio_path  TEXT
);

CREATE TABLE IF NOT EXISTS note_lines (
    id          TEXT PRIMARY KEY,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    text        TEXT NOT NULL DEFAULT '',
    offset_s    REAL NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS transcript_segments (
    id          TEXT PRIMARY KEY,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    speaker     TEXT NOT NULL DEFAULT 'them',
    text        TEXT NOT NULL DEFAULT '',
    start_s     REAL NOT NULL DEFAULT 0,
    end_s       REAL NOT NULL DEFAULT 0,
    lang        TEXT NOT NULL DEFAULT 'ru',
    voice_id    TEXT NOT NULL DEFAULT '',
    person_id   TEXT,
    voice_label TEXT NOT NULL DEFAULT ''
);

-- Голоса, знакомые между встречами. Вектор лежит сырыми байтами float32:
-- искать по нему всё равно только перебором, а людей в базе десятки.
CREATE TABLE IF NOT EXISTS people (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'other',  -- owner | other
    embedding   BLOB NOT NULL,
    samples     INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

-- Голоса конкретной встречи. Без них назвать говорящего можно было бы
-- только пока приложение не закрыли: состав участников жил в памяти, и
-- при открытии старой встречи отпечатка уже не существовало.
CREATE TABLE IF NOT EXISTS meeting_voices (
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    voice_id    TEXT NOT NULL,
    track       TEXT NOT NULL DEFAULT 'them',
    label       TEXT NOT NULL DEFAULT '',
    person_id   TEXT,
    embedding   BLOB NOT NULL,
    samples     INTEGER NOT NULL DEFAULT 1,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (meeting_id, voice_id)
);

-- Переписка по встрече. Храним и вопросы, и ответы, чтобы диалог
-- переживал перезапуск: человек возвращается к встрече через неделю
-- и должен видеть, о чём уже спрашивал.
CREATE TABLE IF NOT EXISTS chat_messages (
    id          TEXT PRIMARY KEY,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'user',  -- user | assistant
    text        TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_meeting ON chat_messages(meeting_id, created_at);

-- Куски записи встречи. Файлов у одной встречи бывает несколько: запись
-- останавливали и включали снова, и каждый заход пишет свою пару
-- дорожек. Время реплик при этом сквозное по встрече, поэтому без явной
-- привязки «файл начинается на такой-то секунде» переслушать реплику
-- нельзя: во втором заходе попадёшь в начало файла вместо нужного места.
CREATE TABLE IF NOT EXISTS audio_chunks (
    id          TEXT PRIMARY KEY,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    track       TEXT NOT NULL DEFAULT 'me',   -- me | them
    path        TEXT NOT NULL,
    start_s     REAL NOT NULL DEFAULT 0,      -- позиция начала файла во встрече
    duration_s  REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_chunks_meeting ON audio_chunks(meeting_id, start_s);
CREATE INDEX IF NOT EXISTS idx_notes_meeting ON note_lines(meeting_id);
CREATE INDEX IF NOT EXISTS idx_segments_meeting ON transcript_segments(meeting_id, start_s);
CREATE INDEX IF NOT EXISTS idx_meetings_created ON meetings(created_at DESC);
"""

# Поиск по расшифровкам.
#
# Отдельной схемой, потому что FTS5 есть не в каждой сборке SQLite: если
# его нет, приложение обязано работать дальше, просто искать медленнее
# перебором. Ронять запуск из-за поиска нельзя.
#
# Таблица внешняя (`content=`): текст уже лежит в transcript_segments, и
# хранить его вторую копию значит удвоить размер базы на пустом месте.
# Синхронизацию держат триггеры, иначе индекс разъедется молча.
#
# `unicode61 remove_diacritics 2` вместо стандартного токенизатора:
# стандартный не считает кириллицу буквами и русский текст не индексирует
# вовсе.
SEARCH_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text,
    content='transcript_segments',
    content_rowid='rowid',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS segments_fts_insert
AFTER INSERT ON transcript_segments BEGIN
    INSERT INTO segments_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER IF NOT EXISTS segments_fts_delete
AFTER DELETE ON transcript_segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text)
    VALUES ('delete', old.rowid, old.text);
END;

CREATE TRIGGER IF NOT EXISTS segments_fts_update
AFTER UPDATE ON transcript_segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text)
    VALUES ('delete', old.rowid, old.text);
    INSERT INTO segments_fts(rowid, text) VALUES (new.rowid, new.text);
END;
"""


class Store:
    """Потокобезопасная обёртка над SQLite.

    К базе ходят и UI-поток, и будущие фоновые потоки записи, поэтому
    соединение одно, а доступ сериализован блокировкой.
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path or str(paths.db_path())
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        """Привести схему к текущей версии.

        `CREATE TABLE IF NOT EXISTS` не трогает уже существующие таблицы,
        поэтому новые колонки приходится добавлять руками: у пользователя
        база с записанными встречами, и терять их из-за обновления нельзя.
        """
        with self._lock:
            was = self._conn.execute("PRAGMA user_version").fetchone()[0]
            self._conn.executescript(SCHEMA)
            if was < 2:
                self._add_columns(
                    "transcript_segments",
                    {
                        "voice_id": "TEXT NOT NULL DEFAULT ''",
                        "person_id": "TEXT",
                        "voice_label": "TEXT NOT NULL DEFAULT ''",
                    },
                )
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._conn.commit()
            if was < SCHEMA_VERSION:
                log.info("Схема базы обновлена с версии %d до %d", was, SCHEMA_VERSION)

        if was < 6:
            # У человека уже записаны встречи, а привязки файлов ко времени
            # нет: без этого кнопка «переслушать» молчала бы на всём архиве.
            self._index_existing_audio()

        self.search_ready = self._init_search(rebuild=was < 5)

    def _index_existing_audio(self) -> None:
        """Записать в базу файлы уже записанных встреч.

        Порядок восстанавливаем по имени файла: в нём стоит отметка
        времени старта записи, поэтому заходы выстраиваются правильно,
        даже если запись останавливали и включали снова.
        """
        import wave

        try:
            root = paths.audio_dir()
            if not root.exists():
                return
            known = {
                r["meeting_id"]
                for r in self._conn.execute("SELECT DISTINCT meeting_id FROM audio_chunks")
            }
            added = 0
            for folder in root.iterdir():
                if not folder.is_dir() or folder.name in known:
                    continue
                exists = self._conn.execute(
                    "SELECT 1 FROM meetings WHERE id=?", (folder.name,)
                ).fetchone()
                if not exists:
                    continue
                offset = 0.0
                # Файлы одного захода (me и them) начинаются в один момент,
                # поэтому сдвиг растёт по заходам, а не по файлам.
                by_stamp: dict[str, list] = {}
                for f in sorted(folder.glob("*.wav")):
                    stamp = f.stem.rsplit("-", 1)[0]
                    by_stamp.setdefault(stamp, []).append(f)
                for stamp in sorted(by_stamp):
                    longest = 0.0
                    for f in by_stamp[stamp]:
                        try:
                            with wave.open(str(f)) as w:
                                duration = w.getnframes() / float(w.getframerate())
                        except Exception:
                            continue
                        track = "me" if f.stem.endswith("-me") else "them"
                        self._conn.execute(
                            "INSERT INTO audio_chunks"
                            "(id, meeting_id, track, path, start_s, duration_s)"
                            " VALUES (?,?,?,?,?,?)",
                            (new_id(), folder.name, track, str(f), offset, duration),
                        )
                        added += 1
                        longest = max(longest, duration)
                    offset += longest
            self._conn.commit()
            if added:
                log.info("Записи старых встреч привязаны ко времени: %d файлов", added)
        except sqlite3.Error:
            log.warning("Не удалось привязать старые записи", exc_info=True)

    def _init_search(self, rebuild: bool) -> bool:
        """Поднять индекс поиска. Вернуть, получилось ли.

        Не роняем приложение, если FTS5 в сборке SQLite нет: поиск
        откатится на перебор, а записывать и расшифровывать встречи можно
        и без него.
        """
        try:
            with self._lock:
                self._conn.executescript(SEARCH_SCHEMA)
                if rebuild:
                    # У человека уже есть расшифровки: без этого поиск
                    # находил бы только то, что записано после обновления.
                    self._conn.execute(
                        "INSERT INTO segments_fts(segments_fts) VALUES ('rebuild')"
                    )
                    log.info("Индекс поиска построен по существующим расшифровкам")
                self._conn.commit()
            return True
        except sqlite3.Error:
            log.warning("Поиск по расшифровкам недоступен, ищем перебором", exc_info=True)
            return False

    def _add_columns(self, table: str, columns: dict[str, str]) -> None:
        """Добавить недостающие колонки, не трогая данные."""
        have = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name in have:
                continue
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            log.info("Добавлена колонка %s.%s", table, name)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- встречи ---------------------------------------------------------

    def create_meeting(self, meeting: Meeting | None = None) -> Meeting:
        meeting = meeting or Meeting()
        with self._lock:
            self._conn.execute(
                """INSERT INTO meetings
                   (id, title, created_at, started_at, ended_at, status,
                    template, notes, summary, audio_path)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    meeting.id, meeting.title, meeting.created_at,
                    meeting.started_at, meeting.ended_at, meeting.status.value,
                    meeting.template, meeting.notes, meeting.summary,
                    meeting.audio_path,
                ),
            )
            self._conn.commit()
        return meeting

    def update_meeting(self, meeting_id: str, **fields: Any) -> None:
        allowed = {
            "title", "started_at", "ended_at", "status",
            "template", "notes", "summary", "audio_path",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        if isinstance(updates.get("status"), MeetingStatus):
            updates["status"] = updates["status"].value
        clause = ", ".join(f"{k}=?" for k in updates)
        with self._lock:
            self._conn.execute(
                f"UPDATE meetings SET {clause} WHERE id=?",
                (*updates.values(), meeting_id),
            )
            self._conn.commit()

    def get_meeting(self, meeting_id: str) -> Meeting | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM meetings WHERE id=?", (meeting_id,)
            ).fetchone()
        return _row_to_meeting(row) if row else None

    def list_meetings(self, limit: int = 200) -> list[Meeting]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM meetings ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_meeting(r) for r in rows]

    def delete_meeting(self, meeting_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))
            self._conn.commit()

    def search(self, query: str, limit: int = 60) -> list[dict[str, Any]]:
        """Найти реплики по словам. Вернуть по встрече её лучшие совпадения.

        Ищем по расшифровке, а не только по заголовку и заметкам: человек
        помнит, что «Петров говорил про сроки», а не как он назвал встречу.

        Отдаём вместе с моментом времени: по нему потом можно будет
        включить воспроизведение с нужной секунды (этап 11).
        """
        words = _fts_query(query)
        if not words:
            return []

        if not getattr(self, "search_ready", False):
            return self._search_slow(query, limit)

        try:
            with self._lock:
                rows = self._conn.execute(
                    """SELECT s.meeting_id, s.text, s.start_s, s.voice_label,
                              s.speaker, m.title, m.created_at
                       FROM segments_fts f
                       JOIN transcript_segments s ON s.rowid = f.rowid
                       JOIN meetings m ON m.id = s.meeting_id
                       WHERE segments_fts MATCH ?
                       ORDER BY bm25(segments_fts), m.created_at DESC
                       LIMIT ?""",
                    (words, limit),
                ).fetchall()
        except sqlite3.Error:
            # Индекс мог не собраться на чужой сборке SQLite.
            log.warning("Поиск через индекс не сработал, ищем перебором", exc_info=True)
            return self._search_slow(query, limit)

        return [dict(r) for r in rows]

    def _search_slow(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Запасной поиск подстрокой, когда индекса нет.

        Медленнее и без ранжирования, зато работает всегда. Лучше найти
        не идеально, чем показать пустой экран.
        """
        like = f"%{query.strip().lower()}%"
        with self._lock:
            rows = self._conn.execute(
                """SELECT s.meeting_id, s.text, s.start_s, s.voice_label,
                          s.speaker, m.title, m.created_at
                   FROM transcript_segments s
                   JOIN meetings m ON m.id = s.meeting_id
                   WHERE lower(s.text) LIKE ?
                   ORDER BY m.created_at DESC
                   LIMIT ?""",
                (like, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def vacuum(self) -> int:
        """Сжать файл базы и вернуть, сколько байт освободилось.

        Без этого удалённые встречи остаются в файле: SQLite помечает
        страницы свободными, но размер не уменьшает. Для приложения,
        которое обещает удалять данные, это плохо: текст реплик так и
        лежит в файле, и его видно любым просмотрщиком.
        """
        path = Path(self._path)
        before = path.stat().st_size if path.exists() else 0
        with self._lock:
            # Сначала влить журнал в основной файл. В режиме WAL удалённые
            # реплики остаются лежать в `-wal`, и один VACUUM их не
            # трогает: текст удалённой встречи так и читается на диске.
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("VACUUM")
            # И ещё раз после: сам VACUUM пишет через журнал.
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.commit()
        after = path.stat().st_size if path.exists() else 0
        return max(0, before - after)

    # --- строки заметок --------------------------------------------------

    def add_note_line(self, line: NoteLine) -> NoteLine:
        with self._lock:
            self._conn.execute(
                """INSERT INTO note_lines (id, meeting_id, text, offset_s, created_at)
                   VALUES (?,?,?,?,?)""",
                (line.id, line.meeting_id, line.text, line.offset, line.created_at),
            )
            self._conn.commit()
        return line

    def list_note_lines(self, meeting_id: str) -> list[NoteLine]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM note_lines WHERE meeting_id=? ORDER BY offset_s",
                (meeting_id,),
            ).fetchall()
        return [
            NoteLine(
                id=r["id"], meeting_id=r["meeting_id"], text=r["text"],
                offset=r["offset_s"], created_at=r["created_at"],
            )
            for r in rows
        ]

    # --- чат по встрече --------------------------------------------------

    def add_chat_message(self, msg: ChatMessage) -> ChatMessage:
        with self._lock:
            self._conn.execute(
                """INSERT INTO chat_messages (id, meeting_id, role, text, created_at)
                   VALUES (?,?,?,?,?)""",
                (msg.id, msg.meeting_id, msg.role, msg.text, msg.created_at),
            )
            self._conn.commit()
        return msg

    def list_chat_messages(self, meeting_id: str) -> list[ChatMessage]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chat_messages WHERE meeting_id=? ORDER BY created_at",
                (meeting_id,),
            ).fetchall()
        return [
            ChatMessage(
                id=r["id"], meeting_id=r["meeting_id"], role=r["role"],
                text=r["text"], created_at=r["created_at"],
            )
            for r in rows
        ]

    def update_chat_message(self, message_id: str, text: str) -> None:
        """Дописать ответ, который собирался по кускам во время потока."""
        with self._lock:
            self._conn.execute(
                "UPDATE chat_messages SET text=? WHERE id=?", (text, message_id)
            )
            self._conn.commit()

    def clear_chat(self, meeting_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM chat_messages WHERE meeting_id=?", (meeting_id,)
            )
            self._conn.commit()

    # --- транскрипт (этап 3) ---------------------------------------------

    def add_segment(self, seg: TranscriptSegment) -> TranscriptSegment:
        with self._lock:
            self._conn.execute(
                """INSERT INTO transcript_segments
                   (id, meeting_id, speaker, text, start_s, end_s, lang,
                    voice_id, person_id, voice_label)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (seg.id, seg.meeting_id, seg.speaker.value, seg.text,
                 seg.start, seg.end, seg.lang,
                 seg.voice_id, seg.person_id, seg.voice_label),
            )
            self._conn.commit()
        return seg

    def list_segments(self, meeting_id: str) -> list[TranscriptSegment]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM transcript_segments WHERE meeting_id=? ORDER BY start_s",
                (meeting_id,),
            ).fetchall()
        return [
            TranscriptSegment(
                id=r["id"], meeting_id=r["meeting_id"],
                speaker=Speaker(r["speaker"]), text=r["text"],
                start=r["start_s"], end=r["end_s"], lang=r["lang"],
                voice_id=r["voice_id"], person_id=r["person_id"],
                voice_label=r["voice_label"],
            )
            for r in rows
        ]

    def relabel_segments(self, meeting_id: str, voice_id: str, label: str) -> int:
        """Переименовать участника во всех его репликах этой встречи."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE transcript_segments SET voice_label=? "
                "WHERE meeting_id=? AND voice_id=?",
                (label, meeting_id, voice_id),
            )
            self._conn.commit()
            return cur.rowcount

    def link_segments(self, meeting_id: str, voice_id: str, person_id: str) -> int:
        """Привязать реплики участника к человеку из базы голосов."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE transcript_segments SET person_id=? "
                "WHERE meeting_id=? AND voice_id=?",
                (person_id, meeting_id, voice_id),
            )
            self._conn.commit()
            return cur.rowcount

    # --- куски записи -----------------------------------------------------

    def add_audio_chunk(
        self, meeting_id: str, track: str, path: str,
        start_s: float, duration_s: float,
    ) -> None:
        """Запомнить файл записи и его место во времени встречи."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO audio_chunks(id, meeting_id, track, path, start_s, duration_s)"
                " VALUES (?,?,?,?,?,?)",
                (new_id(), meeting_id, track, path, start_s, duration_s),
            )
            self._conn.commit()

    def list_audio_chunks(self, meeting_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT track, path, start_s, duration_s FROM audio_chunks"
                " WHERE meeting_id=? ORDER BY start_s",
                (meeting_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- знакомые голоса --------------------------------------------------

    def save_person(self, person: Person) -> Person:
        """Создать или обновить человека. Вектор кладём сырыми байтами."""
        blob = np.asarray(person.embedding, dtype=np.float32).tobytes()
        person.updated_at = now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO people
                   (id, name, kind, embedding, samples, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, kind=excluded.kind,
                     embedding=excluded.embedding, samples=excluded.samples,
                     updated_at=excluded.updated_at""",
                (person.id, person.name, person.kind, blob, person.samples,
                 person.created_at, person.updated_at),
            )
            self._conn.commit()
        return person

    def list_people(self) -> list[Person]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM people ORDER BY kind DESC, name"
            ).fetchall()
        return [_row_to_person(r) for r in rows]

    def get_person(self, person_id: str) -> Person | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM people WHERE id=?", (person_id,)
            ).fetchone()
        return _row_to_person(row) if row else None

    def get_owner(self) -> Person | None:
        """Владелец программы: тот, чей голос записан эталоном."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM people WHERE kind='owner' ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return _row_to_person(row) if row else None

    def delete_person(self, person_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM people WHERE id=?", (person_id,))
            self._conn.execute(
                "UPDATE transcript_segments SET person_id=NULL WHERE person_id=?",
                (person_id,),
            )
            self._conn.commit()

    # --- голоса встречи ---------------------------------------------------

    def save_meeting_voice(
        self,
        meeting_id: str,
        voice_id: str,
        track: str,
        label: str,
        embedding: Any,
        samples: int,
        person_id: str | None = None,
    ) -> None:
        """Запомнить отпечаток участника встречи.

        Нужно, чтобы назвать говорящего можно было и через неделю, открыв
        старую встречу: имя закрепляется за голосом, а голос надо где-то
        держать.
        """
        blob = np.asarray(embedding, dtype=np.float32).tobytes()
        with self._lock:
            self._conn.execute(
                """INSERT INTO meeting_voices
                   (meeting_id, voice_id, track, label, person_id,
                    embedding, samples, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(meeting_id, voice_id) DO UPDATE SET
                     track=excluded.track, label=excluded.label,
                     person_id=excluded.person_id, embedding=excluded.embedding,
                     samples=excluded.samples, updated_at=excluded.updated_at""",
                (meeting_id, voice_id, track, label, person_id, blob, samples, now()),
            )
            self._conn.commit()

    def list_meeting_voices(self, meeting_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM meeting_voices WHERE meeting_id=? ORDER BY voice_id",
                (meeting_id,),
            ).fetchall()
        return [
            {
                "voice_id": r["voice_id"],
                "track": r["track"],
                "label": r["label"],
                "person_id": r["person_id"],
                "embedding": np.frombuffer(r["embedding"], dtype=np.float32),
                "samples": r["samples"],
            }
            for r in rows
        ]

    def get_meeting_voice(self, meeting_id: str, voice_id: str) -> dict[str, Any] | None:
        for voice in self.list_meeting_voices(meeting_id):
            if voice["voice_id"] == voice_id:
                return voice
        return None


def _row_to_person(row: sqlite3.Row) -> Person:
    return Person(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        embedding=np.frombuffer(row["embedding"], dtype=np.float32).tolist(),
        samples=row["samples"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _fts_query(query: str) -> str:
    """Превратить то, что напечатал человек, в запрос для FTS5.

    Подставлять текст в MATCH напрямую нельзя: кавычки, скобки и слова
    вроде `AND` или `NEAR` для FTS5 синтаксис, и любой апостроф уронил бы
    поиск ошибкой разбора. Поэтому режем на слова, выбрасываем всё, кроме
    букв и цифр, и склеиваем сами.

    Последнее слово получает `*`: человек печатает и ждёт подсказок, не
    дописав слово до конца.
    """
    words = re.findall(r"[\w]+", (query or "").lower(), re.UNICODE)
    if not words:
        return ""
    parts = [f'"{w}"' for w in words[:-1]]
    parts.append(f'"{words[-1]}"*')
    return " ".join(parts)


def _row_to_meeting(row: sqlite3.Row) -> Meeting:
    return Meeting(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        status=MeetingStatus(row["status"]),
        template=row["template"],
        notes=row["notes"],
        summary=row["summary"],
        audio_path=row["audio_path"],
    )
