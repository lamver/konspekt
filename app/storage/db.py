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

SCHEMA_VERSION = 10

SCHEMA = """
-- Комментарии внутри CREATE TABLE не писать: SQLite до 3.44 хранит
-- определение вместе с ними и потом спотыкается на ALTER TABLE DROP
-- COLUMN («incomplete input»). Сервер сборки как раз с такой версией.
--
-- meetings.rescued — пробовали ли досчитать встречу, оставшуюся без
-- расшифровки. Без отметки тишину и речь на чужом языке пересчитывали
-- бы при каждом запуске: на архиве в сотню встреч это минуты впустую.
--
-- Значения полей, которые раньше стояли комментариями в строках:
-- people.kind — owner или other; chat_messages.role — user или
-- assistant; audio_chunks.track и transcript_segments.speaker — me или
-- them; audio_chunks.start_s — начало файла во встрече, а
-- transcript_segments.offset_s — место реплики во времени встречи.
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
    audio_path  TEXT,
    rescued     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS note_lines (
    id          TEXT PRIMARY KEY,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    text        TEXT NOT NULL DEFAULT '',
    offset_s    REAL NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);

-- Про `doubtful` в таблице ниже. Это пометка «реплика под сомнением»:
-- человек сказал, что в системном звуке был чужой ролик, а не
-- собеседник. Удалять такое нельзя (вдруг он ошибся, а запись уже не
-- вернёшь), и тихо оставить тоже нельзя: именно оно портит саммари.
-- Комментарий вынесен над таблицей намеренно: внутри CREATE TABLE он
-- ломает DROP COLUMN на старом SQLite.
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
    voice_label TEXT NOT NULL DEFAULT '',
    doubtful   INTEGER NOT NULL DEFAULT 0
);

-- Голоса, знакомые между встречами. Вектор лежит сырыми байтами float32:
-- искать по нему всё равно только перебором, а людей в базе десятки.
CREATE TABLE IF NOT EXISTS people (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'other',
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
    role        TEXT NOT NULL DEFAULT 'user',
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
    track       TEXT NOT NULL DEFAULT 'me',
    path        TEXT NOT NULL,
    start_s     REAL NOT NULL DEFAULT 0,
    duration_s  REAL NOT NULL DEFAULT 0
);

-- Куски речи, которые не успели распознать.
--
-- Звук их цел в WAV, пропало только распознавание, и досчёт после
-- «стоп» берёт тот же интервал из файла. В памяти этот список держать
-- нельзя: досчёт идёт фоном и может занять минуты, а встреча уже помечена
-- готовой, и человек вправе закрыть программу. Запись в базе значит, что
-- недосчитанное досчитается при следующем запуске, а не пропадёт молча.
CREATE TABLE IF NOT EXISTS missed_spans (
    id          TEXT PRIMARY KEY,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    speaker     TEXT NOT NULL DEFAULT 'me',
    offset_s    REAL NOT NULL DEFAULT 0,
    duration_s  REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_missed_meeting ON missed_spans(meeting_id, offset_s);
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

# Поиск по смыслу.
#
# Ищем не по отдельной реплике, а по куску из нескольких соседних: в
# реплике в среднем 47 знаков, «Вот.» и «Завтра вечер.» смысла сами по
# себе не несут, а в куске из соседних они складываются в разговор.
#
# Вектор лежит сырыми байтами float32, как и у голосов: искать по нему
# всё равно только перебором, а кусков в архиве полторы тысячи.
#
# meaning_state помнит, по какой версии расшифровки посчитаны куски
# встречи. Реплики дописываются во время записи, пересчитываются при
# смене языка, а сама модель может смениться с обновлением. Без этой
# отметки указатель либо пересчитывался бы целиком на каждом запуске,
# либо тихо отставал от текста.
#
# Удаление встречи уносит и её куски (ON DELETE CASCADE): иначе текст
# удалённой встречи продолжал бы лежать в базе и находиться поиском.
MEANING_SCHEMA = """
CREATE TABLE IF NOT EXISTS meaning_chunks (
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    start_s     REAL NOT NULL DEFAULT 0,
    who         TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL,
    vector      BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meaning_meeting ON meaning_chunks(meeting_id);

CREATE TABLE IF NOT EXISTS meaning_state (
    meeting_id  TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    signature   TEXT NOT NULL
);
"""

# Разборы встречи в разных разрезах: итоги, SPIN, STAR, SOAP и другие
# (llm/lenses.py). У каждого разреза свой результат, и один разбор не
# затирает другой: человек сделал разбор по SPIN, потом по STAR, и оба
# лежат, пока встреча не удалена.
#
# signature — отпечаток расшифровки на момент разбора (тот же, что у
# поиска по смыслу). Встреча дополнилась после разбора — отпечаток не
# сходится, и карточка честно говорит, что разбор устарел.
#
# Таблица только добавляется, без повышения версии схемы: прежняя версия
# программы поверх такой базы работает как работала.
ANALYSES_SCHEMA = """
CREATE TABLE IF NOT EXISTS meeting_analyses (
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    text        TEXT NOT NULL DEFAULT '',
    signature   TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    PRIMARY KEY (meeting_id, kind)
);
"""

# Папки встреч. Папка — группа встреч в списке: внутри можно начать
# встречу как обычно, встречу можно перенести в папку и вынуть обратно.
# Потом у папки появится источник (своя папка на диске, SFTP, телефония),
# отсюда поле source: пустое — обычная папка.
#
# Привязка отдельной таблицей, а не колонкой встречи: прежняя версия
# программы поверх такой базы работает как работала, просто не видя
# папок. Удалили папку — привязки уходят каскадом, встречи остаются и
# возвращаются в общий список. Удалили встречу — уходит и привязка.
FOLDERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    source      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS meeting_folders (
    meeting_id  TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    folder_id   TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_meeting_folders ON meeting_folders(folder_id);
-- Какие файлы папки-источника уже взяты (core/источники.py). По отпечатку
-- содержимого, а не по пути: переименование и перенос не дают дублей.
CREATE TABLE IF NOT EXISTS source_files (
    folder_id   TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    path        TEXT NOT NULL DEFAULT '',
    taken_at    REAL NOT NULL,
    PRIMARY KEY (folder_id, fingerprint)
);
"""

# Пробный период: первые встречи работают целиком, дальше только просмотр.
#
# Считаем встречи, а не дни: поставил и неделю не пользовался — ничего не
# сгорело. Встреча считается один раз и навсегда (trial_counted): иначе
# удалил встречу — вернул себе пробную. Встречи, записанные до появления
# пробного периода, в счёт не идут (trial_legacy): те, кто поверил в
# программу первыми, не должны получить «только просмотр» в день
# обновления.
#
# По времени ничего не сверяем: перевод часов назад пробный период не
# продлевает, потому что считать тут нечего, кроме самих встреч.
TRIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS trial_state (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trial_legacy (
    meeting_id  TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS trial_counted (
    meeting_id  TEXT PRIMARY KEY,
    counted_at  REAL NOT NULL
);
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
            # Таблицы поиска по смыслу без повышения версии схемы: они
            # только добавляются, и прежняя версия программы поверх такой
            # базы работает как работала, просто не замечая их.
            self._conn.executescript(MEANING_SCHEMA)
            self._conn.executescript(ANALYSES_SCHEMA)
            self._conn.executescript(TRIAL_SCHEMA)
            self._conn.executescript(FOLDERS_SCHEMA)
            self._начать_пробный_период()
            if was < 2:
                self._add_columns(
                    "transcript_segments",
                    {
                        "voice_id": "TEXT NOT NULL DEFAULT ''",
                        "person_id": "TEXT",
                        "voice_label": "TEXT NOT NULL DEFAULT ''",
                    },
                )
            if was < 9:
                # База человека старше пометок о досчёте. Ставим ноль:
                # его старые пустые встречи как раз и надо попробовать
                # спасти, по одному разу каждую.
                self._add_columns(
                    "meetings", {"rescued": "INTEGER NOT NULL DEFAULT 0"}
                )
            if was < 10:
                # Пометка «реплика под сомнением». В старом архиве таких
                # нет: человека тогда никто не спрашивал, и разметить
                # задним числом нечего.
                self._add_columns(
                    "transcript_segments",
                    {"doubtful": "INTEGER NOT NULL DEFAULT 0"},
                )
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._conn.commit()
            if was < SCHEMA_VERSION:
                log.info("Схема базы обновлена с версии %d до %d", was, SCHEMA_VERSION)

        if was < 6:
            # У человека уже записаны встречи, а привязки файлов ко времени
            # нет: без этого кнопка «переслушать» молчала бы на всём архиве.
            self._index_existing_audio()

        if was < 7:
            # В архиве остались реплики с ярлыком «УК» или «БЕ», хотя
            # текст у них русский. Определитель языка часто слышит в
            # русской речи украинскую, а отправить её всё равно некуда,
            # кроме русской модели: что бы ни послышалось, текст выходит
            # русским. Ярлык при этом сохранялся тот, что послышался, и
            # человек видел в расшифровке язык, которого там нет.
            self._исправить_ложные_языки()

        self.search_ready = self._init_search(rebuild=was < 5)

    def _начать_пробный_период(self) -> None:
        """Запомнить встречи, записанные до пробного периода. Один раз."""
        if self._conn.execute("SELECT 1 FROM trial_state WHERE key='started'").fetchone():
            return
        self._conn.execute("INSERT INTO trial_legacy(meeting_id) SELECT id FROM meetings")
        self._conn.execute(
            "INSERT INTO trial_state(key, value) VALUES ('started', ?)", (str(now()),)
        )

    def trial_used(self, min_speech: float, limit: int) -> int:
        """Сколько встреч пробного периода уже использовано.

        Встреча идёт в счёт, когда в ней набралось min_speech секунд
        распознанной речи: случайное «Запись» — «Стоп» пробную встречу не
        съедает. Посчитанная встреча остаётся посчитанной и после удаления.

        Больше limit не считаем: посчитанной встрече модель открыта
        навсегда, и одиннадцатая встреча, попавшая в счёт, стала бы
        пробной задним числом.
        """
        with self._lock:
            было = int(self._conn.execute("SELECT COUNT(*) FROM trial_counted").fetchone()[0])
            if было >= limit:
                return было
            self._conn.execute(
                """INSERT OR IGNORE INTO trial_counted(meeting_id, counted_at)
                   SELECT meeting_id, ? FROM transcript_segments
                   WHERE meeting_id NOT IN (SELECT meeting_id FROM trial_legacy)
                     AND meeting_id NOT IN (SELECT meeting_id FROM trial_counted)
                   GROUP BY meeting_id
                   HAVING SUM(MAX(end_s - start_s, 0)) >= ?
                   ORDER BY MIN(rowid)
                   LIMIT ?""",
                (now(), float(min_speech), limit - было),
            )
            self._conn.commit()
            return int(self._conn.execute("SELECT COUNT(*) FROM trial_counted").fetchone()[0])

    # --- папки ------------------------------------------------------------

    def create_folder(self, name: str) -> dict[str, Any]:
        folder = {"id": new_id(), "name": name, "created_at": now(), "source": ""}
        with self._lock:
            self._conn.execute(
                "INSERT INTO folders(id, name, created_at, source) VALUES (?,?,?,?)",
                (folder["id"], folder["name"], folder["created_at"], folder["source"]),
            )
            self._conn.commit()
        return folder

    def list_folders(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT f.id, f.name, f.created_at, f.source,
                          (SELECT COUNT(*) FROM meeting_folders mf WHERE mf.folder_id = f.id) AS count
                   FROM folders f ORDER BY f.name COLLATE NOCASE, f.created_at"""
            ).fetchall()
        return [dict(r) for r in rows]

    def set_folder_source(self, folder_id: str, source: str) -> bool:
        """Каталог на диске, из которого папка берёт записи. Пусто — не следит."""
        with self._lock:
            cur = self._conn.execute("UPDATE folders SET source=? WHERE id=?", (source, folder_id))
            self._conn.commit()
        return cur.rowcount > 0

    def source_taken(self, folder_id: str, fingerprint: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM source_files WHERE folder_id=? AND fingerprint=?",
                (folder_id, fingerprint),
            ).fetchone()
        return row is not None

    def mark_source_taken(self, folder_id: str, fingerprint: str, path: str) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO source_files(folder_id, fingerprint, path, taken_at) VALUES (?,?,?,?)",
                    (folder_id, fingerprint, path, now()),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                pass  # папку удалили, пока шёл обход

    def rename_folder(self, folder_id: str, name: str) -> bool:
        with self._lock:
            cur = self._conn.execute("UPDATE folders SET name=? WHERE id=?", (name, folder_id))
            self._conn.commit()
        return cur.rowcount > 0

    def delete_folder(self, folder_id: str) -> bool:
        """Удалить папку. Встречи остаются и возвращаются в общий список."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM folders WHERE id=?", (folder_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def set_meeting_folder(self, meeting_id: str, folder_id: str | None) -> bool:
        """Перенести встречу в папку, None — вынуть из папки."""
        with self._lock:
            try:
                if folder_id:
                    self._conn.execute(
                        """INSERT INTO meeting_folders(meeting_id, folder_id) VALUES (?,?)
                           ON CONFLICT(meeting_id) DO UPDATE SET folder_id=excluded.folder_id""",
                        (meeting_id, folder_id),
                    )
                else:
                    self._conn.execute("DELETE FROM meeting_folders WHERE meeting_id=?", (meeting_id,))
                self._conn.commit()
            except sqlite3.IntegrityError:
                # Встречи или папки уже нет: переносить некуда.
                self._conn.rollback()
                return False
        return True

    def meeting_folders(self) -> dict[str, str]:
        """Какая встреча в какой папке: {встреча: папка}."""
        with self._lock:
            rows = self._conn.execute("SELECT meeting_id, folder_id FROM meeting_folders").fetchall()
        return {r["meeting_id"]: r["folder_id"] for r in rows}

    def trial_legacy(self, meeting_id: str) -> bool:
        """Записана ли встреча до появления пробного периода."""
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM trial_legacy WHERE meeting_id=?", (meeting_id,)
            ).fetchone() is not None

    def trial_counts(self, meeting_id: str) -> bool:
        """Посчитана ли встреча в пробный период."""
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM trial_counted WHERE meeting_id=?", (meeting_id,)
            ).fetchone() is not None

    def _исправить_ложные_языки(self) -> None:
        """Снять ярлык чужого языка с реплик, которые распознала русская модель.

        Кириллические соседи русского (uk, be, bg, mk, sr) уходят в
        GigaAM, а он говорит только по-русски. Значит текст такой
        реплики русский, и ярлык нужно поправить. Латиницу не трогаем:
        там реплику и правда распознавал Whisper, и его ответ осмыслен.
        """
        with self._lock:
            похожие = ("uk", "be", "bg", "mk", "sr")
            вопросы = ",".join("?" * len(похожие))
            сколько = self._conn.execute(
                f"SELECT COUNT(*) FROM transcript_segments WHERE lang IN ({вопросы})",
                похожие,
            ).fetchone()[0]
            if not сколько:
                return
            self._conn.execute(
                f"UPDATE transcript_segments SET lang='ru' WHERE lang IN ({вопросы})",
                похожие,
            )
            self._conn.commit()
        log.info("Поправлен язык у %d реплик: их распознавала русская модель", сколько)

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
            "template", "notes", "summary", "audio_path", "rescued",
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

    def search_stems(self, stems: list[str], limit: int = 400) -> list[dict[str, Any]]:
        """Реплики, где есть хотя бы одна из основ, по началу слова.

        В отличие от search() здесь достаточно одного слова: человек
        пишет «выручка за август что там по продажам», а в разговоре
        сказано «перспективы по продажам, по выручке». Какие реплики
        лучше, решает ранжирование: там считается, сколько основ
        сошлось. Отсюда отдаём с запасом, потому что по одному общему
        слову находится много, а нужные могут быть не первыми по BM25.
        """
        stems = [s for s in (re.sub(r"[^\w]", "", s) for s in stems) if s]
        if not stems:
            return []
        if not getattr(self, "search_ready", False):
            return self._search_stems_slow(stems, limit)
        match = " OR ".join(f'"{s}"*' for s in stems)
        try:
            with self._lock:
                rows = self._conn.execute(
                    """SELECT s.meeting_id, s.text, s.start_s, s.voice_label,
                              s.speaker, m.title, m.created_at,
                              bm25(segments_fts) AS rank
                       FROM segments_fts f
                       JOIN transcript_segments s ON s.rowid = f.rowid
                       JOIN meetings m ON m.id = s.meeting_id
                       WHERE segments_fts MATCH ?
                       ORDER BY rank
                       LIMIT ?""",
                    (match, limit),
                ).fetchall()
        except sqlite3.Error:
            log.warning("Поиск по основам через индекс не сработал", exc_info=True)
            return self._search_stems_slow(stems, limit)
        return [dict(r) for r in rows]

    def _search_stems_slow(self, stems: list[str], limit: int) -> list[dict[str, Any]]:
        """То же перебором, когда индекса нет."""
        условие = " OR ".join("lower(s.text) LIKE ?" for _ in stems)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT s.meeting_id, s.text, s.start_s, s.voice_label,
                           s.speaker, m.title, m.created_at, 0.0 AS rank
                    FROM transcript_segments s
                    JOIN meetings m ON m.id = s.meeting_id
                    WHERE {условие}
                    ORDER BY m.created_at DESC
                    LIMIT ?""",
                (*(f"%{s}%" for s in stems), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- поиск по смыслу -------------------------------------------------

    def meaning_signature(self, meeting_id: str) -> str:
        """Отпечаток расшифровки: изменилась ли она с прошлого подсчёта.

        Число реплик, сумма длин и время последней. Дописанная реплика
        меняет число, правка языка меняет длину. Хеш всего текста был бы
        точнее, но его пришлось бы считать по всему архиву на каждом
        запуске, а этого хватает с запасом.
        """
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*), COALESCE(SUM(LENGTH(text)), 0),
                          COALESCE(MAX(start_s), 0)
                   FROM transcript_segments WHERE meeting_id=?""",
                (meeting_id,),
            ).fetchone()
        return f"{row[0]}:{row[1]}:{row[2]:.2f}"

    # --- разборы встречи ------------------------------------------------

    def save_analysis(
        self, meeting_id: str, kind: str, text: str, signature: str, model: str = "",
    ) -> None:
        """Сохранить разбор встречи в одном разрезе, заменив прежний."""
        with self._lock:
            try:
                self._conn.execute(
                    """INSERT INTO meeting_analyses
                       (meeting_id, kind, text, signature, model, created_at)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(meeting_id, kind) DO UPDATE SET
                         text=excluded.text, signature=excluded.signature,
                         model=excluded.model, created_at=excluded.created_at""",
                    (meeting_id, kind, text, signature, model, now()),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                # Встречу удалили, пока шёл разбор: сохранять некуда.
                self._conn.rollback()

    def list_analyses(self, meeting_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM meeting_analyses WHERE meeting_id=?", (meeting_id,)
            ).fetchall()
        return [
            {"kind": r["kind"], "text": r["text"], "signature": r["signature"],
             "model": r["model"], "created_at": r["created_at"]}
            for r in rows
        ]

    def meetings_needing_meaning(self, model: str) -> list[str]:
        """Встречи, чьи куски не посчитаны или посчитаны по старому тексту.

        Сначала свежие: человек ищет обычно то, о чём говорили недавно, и
        пока архив досчитывается, найтись должна прежде всего вчерашняя
        встреча, а не прошлогодняя.

        Встречи в записи не пропускаем. Так было в первой версии, и
        встреча, оставшаяся в «идёт запись» после падения программы,
        не находилась бы по смыслу никогда. Идущую запись пересчитать
        лишний раз дёшево: отпечаток изменится, и после «стоп» она
        посчитается заново целиком.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT m.id, s.model, s.signature FROM meetings m
                   LEFT JOIN meaning_state s ON s.meeting_id = m.id
                   ORDER BY m.created_at DESC"""
            ).fetchall()
        need = []
        for r in rows:
            if r["model"] != model or r["signature"] != self.meaning_signature(r["id"]):
                need.append(r["id"])
        return need

    def save_meaning(
        self, meeting_id: str, model: str, signature: str,
        chunks: list[dict[str, Any]], vectors: np.ndarray,
    ) -> None:
        """Заменить куски встречи целиком, одним махом.

        Одна транзакция: поиск в соседнем потоке не должен увидеть
        встречу наполовину старой, наполовину новой.
        """
        with self._lock:
            try:
                self._conn.execute("DELETE FROM meaning_chunks WHERE meeting_id=?", (meeting_id,))
                self._conn.executemany(
                    """INSERT INTO meaning_chunks
                       (meeting_id, start_s, who, text, model, vector)
                       VALUES (?,?,?,?,?,?)""",
                    [
                        (meeting_id, c["start"], c["who"], c["text"], model,
                         np.asarray(v, dtype=np.float32).tobytes())
                        for c, v in zip(chunks, vectors)
                    ],
                )
                self._conn.execute(
                    """INSERT INTO meaning_state (meeting_id, model, signature)
                       VALUES (?,?,?)
                       ON CONFLICT(meeting_id) DO UPDATE SET
                         model=excluded.model, signature=excluded.signature""",
                    (meeting_id, model, signature),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                # Встречу удалили, пока считали её куски. Не беда: считать
                # больше нечего.
                self._conn.rollback()

    def meeting_meaning(self, meeting_id: str, model: str) -> tuple[list[float], np.ndarray]:
        """Начала кусков одной встречи и их векторы, по порядку.

        Для чата: отрывки к вопросу ищутся внутри одной встречи, и
        грузить ради этого весь архив незачем.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT start_s, vector FROM meaning_chunks
                   WHERE meeting_id=? AND model=? ORDER BY start_s""",
                (meeting_id, model),
            ).fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32)
        starts = [float(r["start_s"]) for r in rows]
        vectors = np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
        return starts, vectors

    def list_meaning(self, model: str) -> tuple[list[dict[str, Any]], np.ndarray]:
        """Все куски указателя и их векторы одной матрицей."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT c.meeting_id, c.start_s, c.who, c.text, c.vector,
                          m.title, m.created_at
                   FROM meaning_chunks c JOIN meetings m ON m.id = c.meeting_id
                   WHERE c.model=?""",
                (model,),
            ).fetchall()
        items = [
            {"meeting_id": r["meeting_id"], "start_s": r["start_s"],
             "who": r["who"], "text": r["text"],
             "title": r["title"], "created_at": r["created_at"]}
            for r in rows
        ]
        if not rows:
            return items, np.zeros((0, 0), dtype=np.float32)
        vectors = np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
        return items, vectors

    def meaning_progress(self, model: str) -> tuple[int, int]:
        """Сколько встреч с текстом уже посчитано и сколько всего."""
        with self._lock:
            total = self._conn.execute(
                """SELECT COUNT(DISTINCT meeting_id) FROM transcript_segments"""
            ).fetchone()[0]
            done = self._conn.execute(
                """SELECT COUNT(*) FROM meaning_state s
                   WHERE s.model=? AND EXISTS (
                     SELECT 1 FROM transcript_segments t
                     WHERE t.meeting_id = s.meeting_id)""",
                (model,),
            ).fetchone()[0]
        return done, total

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
                doubtful=bool(r["doubtful"]),
            )
            for r in rows
        ]

    def update_segment(self, segment_id: str, text: str, lang: str) -> None:
        """Заменить текст и язык одной реплики.

        Нужно для ручной правки языка: человек говорит, на каком языке
        фраза была на самом деле, и она пересчитывается заново. Индекс
        поиска обновляется триггером вместе с текстом.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE transcript_segments SET text=?, lang=? WHERE id=?",
                (text, lang, segment_id),
            )
            self._conn.commit()

    def get_segment(self, segment_id: str) -> TranscriptSegment | None:
        """Одна реплика по её номеру."""
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM transcript_segments WHERE id=?", (segment_id,)
            ).fetchone()
        if r is None:
            return None
        return TranscriptSegment(
            id=r["id"], meeting_id=r["meeting_id"],
            speaker=Speaker(r["speaker"]), text=r["text"],
            start=r["start_s"], end=r["end_s"], lang=r["lang"],
            voice_id=r["voice_id"], person_id=r["person_id"],
            voice_label=r["voice_label"],
            doubtful=bool(r["doubtful"]),
        )

    def mark_doubtful(self, meeting_id: str, speaker: str, until: float) -> int:
        """Пометить реплики дорожки как сомнительные.

        Человек сказал, что в системном звуке был чужой ролик.
        Всё, что успело записаться до этого мгновения, помечаем:
        удалять нельзя (вдруг человек ошибся), а тихо оставить тоже:
        именно эти реплики попадут в саммари и превратятся в «решения
        встречи», которых никто не принимал.

        Возвращает число помеченных реплик.
        """
        with self._lock:
            курсор = self._conn.execute(
                "UPDATE transcript_segments SET doubtful=1 "
                "WHERE meeting_id=? AND speaker=? AND start_s<=?",
                (meeting_id, speaker, until),
            )
            self._conn.commit()
            return курсор.rowcount or 0

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

    def find_unrescued(self) -> list[str]:
        """Встречи со звуком, но без единой реплики, ещё не пробованные.

        Такие остались от версий, которые молча пропускали
        распознавание: звук сохранён, текста нет, и в missed_spans про
        них никто не написал. Отметка rescued нужна, чтобы попытка была
        одна: у встречи может не быть реплик и по честной причине -
        тишина или речь на языке, которого модель не знает.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT m.id FROM meetings m"
                " JOIN audio_chunks a ON a.meeting_id = m.id"
                " WHERE m.rescued = 0"
                " AND NOT EXISTS ("
                "   SELECT 1 FROM transcript_segments t WHERE t.meeting_id = m.id"
                " )"
                " AND NOT EXISTS ("
                "   SELECT 1 FROM missed_spans s WHERE s.meeting_id = m.id"
                " )"
            ).fetchall()
        return [r["id"] for r in rows]

    # --- недосчитанные куски речи -------------------------------------

    def add_missed_spans(self, meeting_id: str, spans: list) -> None:
        """Запомнить куски, которые ещё предстоит досчитать.

        Пишется до начала досчёта, а не после: если программу
        закроют посреди досчёта, список уже должен лежать на диске.
        """
        if not spans:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO missed_spans(id, meeting_id, speaker, offset_s, duration_s)"
                " VALUES (?,?,?,?,?)",
                [(new_id(), meeting_id, s.speaker, s.offset, s.duration) for s in spans],
            )
            self._conn.commit()

    def drop_missed_span(self, meeting_id: str, speaker: str, offset: float) -> None:
        """Вычеркнуть досчитанный кусок.

        По одному, а не все сразу в конце: досчёт могут прервать
        на любом месте, и тогда в базе должно остаться ровно то, что
        ещё не сделано. Сравнение времени с допуском: в базе REAL,
        и точное равенство дробных чисел ненадёжно.
        """
        with self._lock:
            self._conn.execute(
                "DELETE FROM missed_spans WHERE meeting_id=? AND speaker=?"
                " AND abs(offset_s - ?) < 0.001",
                (meeting_id, speaker, offset),
            )
            self._conn.commit()

    def list_missed_spans(self, meeting_id: str | None = None) -> list[dict[str, Any]]:
        """Что осталось досчитать. Без встречи — по всем сразу."""
        with self._lock:
            if meeting_id is None:
                rows = self._conn.execute(
                    "SELECT meeting_id, speaker, offset_s, duration_s FROM missed_spans"
                    " ORDER BY meeting_id, offset_s"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT meeting_id, speaker, offset_s, duration_s FROM missed_spans"
                    " WHERE meeting_id=? ORDER BY offset_s",
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
