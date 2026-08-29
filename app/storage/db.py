"""Хранилище на SQLite.

Схема нарочно плоская и версионируется простым `user_version`: миграции
понадобятся уже на этапе 3, когда поедут сегменты транскрипта.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from typing import Any

from ..core import paths
from ..core.models import Meeting, MeetingStatus, NoteLine, Speaker, TranscriptSegment

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

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
    lang        TEXT NOT NULL DEFAULT 'ru'
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
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._conn.commit()

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
                   (id, meeting_id, speaker, text, start_s, end_s, lang)
                   VALUES (?,?,?,?,?,?,?)""",
                (seg.id, seg.meeting_id, seg.speaker.value, seg.text,
                 seg.start, seg.end, seg.lang),
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
            )
            for r in rows
        ]


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
