"""Хранилище на SQLite.

Схема нарочно плоская и версионируется простым `user_version`: миграции
понадобятся уже на этапе 3, когда поедут сегменты транскрипта.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from typing import Any

import numpy as np

from ..core import paths
from ..core.models import (
    Meeting,
    MeetingStatus,
    NoteLine,
    Person,
    Speaker,
    TranscriptSegment,
    now,
)

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

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

CREATE INDEX IF NOT EXISTS idx_notes_meeting ON note_lines(meeting_id);
CREATE INDEX IF NOT EXISTS idx_segments_meeting ON transcript_segments(meeting_id, start_s);
CREATE INDEX IF NOT EXISTS idx_meetings_created ON meetings(created_at DESC);
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
