"""Разовая справка: что в базе установленной программы."""

import sqlite3
from pathlib import Path

база = Path.home() / "AppData" / "Roaming" / "Konspekt" / "konspekt.db"
строки = [f"база: {база} ({база.stat().st_size // 1024} КБ)"]

# Только чтение: копия, чтобы не мешать работающей программе.
соединение = sqlite3.connect(f"file:{база}?mode=ro", uri=True)
соединение.row_factory = sqlite3.Row

таблицы = [
    ряд[0]
    for ряд in соединение.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )
]
строки.append("таблицы: " + ", ".join(таблицы))

# Имя таблицы с репликами разное в разных версиях схемы.
реплики = next(
    (имя for имя in ("segments", "transcript", "transcript_segments") if имя in таблицы),
    None,
)
if реплики is None:
    Path("_res.txt").write_text("\n".join(строки), encoding="utf-8")
    raise SystemExit(0)

встречи = соединение.execute(
    "SELECT id, title, status, created_at FROM meetings"
    " ORDER BY created_at DESC LIMIT 6"
).fetchall()

for встреча in встречи:
    отрезки = соединение.execute(
        f"SELECT text FROM {реплики} WHERE meeting_id = ? LIMIT 4",
        (встреча["id"],),
    ).fetchall()
    сколько = соединение.execute(
        f"SELECT count(*) FROM {реплики} WHERE meeting_id = ?", (встреча["id"],)
    ).fetchone()[0]
    строки.append(
        f"\n{встреча['created_at']} | {встреча['title']!r} | {встреча['status']}"
        f" | реплик: {сколько}"
    )
    for отрезок in отрезки:
        строки.append(f"    {отрезок['text'][:120]}")

соединение.close()
Path("_res.txt").write_text("\n".join(строки), encoding="utf-8")
