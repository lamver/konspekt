"""Поиск по расшифровкам.

Человек помнит не название встречи, а фразу: «Петров говорил про сроки».
Поэтому искать надо по тексту реплик, а не только по заголовку.

Проверяем то, что ломается молча:

1. Поиск находит слово в расшифровке и не находит того, чего не было.
2. Запрос со скобками, кавычками и словом `AND` не роняет поиск: для
   FTS5 это синтаксис, и любой апостроф в запросе стал бы ошибкой.
3. Индекс живёт вместе с данными: новая реплика сразу находится, а
   удалённая встреча перестаёт, иначе поиск начнёт показывать то, чего
   уже нет.
4. Старая база доиндексируется при обновлении, иначе поиск нашёл бы
   только встречи, записанные после обновления.
"""

from __future__ import annotations

import testenv  # noqa: F401  русский вывод в консоли Windows

import sqlite3
import tempfile
from pathlib import Path

from app.core.models import Meeting, Speaker, TranscriptSegment
from app.storage.db import Store, _fts_query

PHRASES = [
    "Петров обещал прислать смету к пятнице",
    "Обсудили сроки поставки оборудования",
    "Договорились созвониться после праздников",
]


def _fill(store: Store, title: str) -> str:
    meeting = store.create_meeting(Meeting(title=title))
    for i, text in enumerate(PHRASES):
        store.add_segment(TranscriptSegment(
            meeting_id=meeting.id, text=text, speaker=Speaker.ME,
            start=float(i * 10), end=float(i * 10 + 5),
        ))
    return meeting.id


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-search-"))
    store = Store(str(tmp / "search.db"))
    assert store.search_ready, "FTS5 недоступен, поиск не построился"
    print("[ok] индекс поиска построен")

    mid = _fill(store, "Планёрка по проекту")

    # --- обычный поиск ---------------------------------------------------
    found = store.search("смету")
    assert found, "не нашлось слово, которое точно есть в расшифровке"
    assert found[0]["meeting_id"] == mid, "нашлась не та встреча"
    assert "смету" in found[0]["text"].lower(), f"нашлась не та реплика: {found[0]['text']}"
    print(f"[ok] слово из расшифровки находится ({len(found)} совпадений)")

    # Ищем по началу слова: человек печатает и не дописывает до конца.
    assert store.search("постав"), "поиск по началу слова не работает"
    print("[ok] находит по началу слова, пока человек ещё печатает")

    # Два слова: нужны обе, а не любое из них.
    assert store.search("сроки поставки"), "поиск по двум словам не работает"
    assert not store.search("сроки вертолёта"), \
        "нашлось по одному слову из двух: ищем ИЛИ вместо И"
    print("[ok] несколько слов ищутся вместе, а не по отдельности")

    assert not store.search("велосипед"), "нашлось то, чего в расшифровке нет"
    print("[ok] несуществующее слово не находится")

    # --- запросы, которые ломают FTS5 ------------------------------------
    # Для FTS5 это синтаксис: без экранирования запрос падал бы ошибкой.
    for nasty in ('смету"', "AND OR NOT", "(скобка", "*", "^ NEAR/2", "'", '""'):
        store.search(nasty)  # падать не должно
    print("[ok] запросы со спецсимволами не роняют поиск")

    assert _fts_query("") == "", "пустой запрос должен давать пустую строку"
    assert _fts_query("   ") == "", "пробелы должны считаться пустым запросом"
    print("[ok] пустой запрос обрабатывается")

    # --- индекс не отстаёт от данных -------------------------------------
    # Реплики приходят по ходу встречи, и найтись они должны сразу, а не
    # после перезапуска приложения.
    store.add_segment(TranscriptSegment(
        meeting_id=mid, text="Ещё вспомнили про командировку в Новосибирск",
        speaker=Speaker.THEM, start=100.0, end=105.0,
    ))
    assert store.search("командировку"), "новая реплика не попала в индекс"
    print("[ok] новая реплика находится сразу")

    store.delete_meeting(mid)
    assert not store.search("поставки"), "удалённая встреча всё ещё находится"
    assert not store.search("командировку"), "реплики удалённой встречи находятся"
    print("[ok] удалённое перестаёт находиться")

    # --- старая база доиндексируется -------------------------------------
    # Самое важное: у человека уже есть архив, и после обновления поиск
    # обязан находить в нём, а не только в новых встречах.
    old = tmp / "old.db"
    store2 = Store(str(old))
    kept = _fill(store2, "Старая встреча")
    store2._conn.close()

    # Откатываем версию схемы, как будто база от прежней сборки, и
    # сносим индекс: ровно так выглядит база до обновления.
    conn = sqlite3.connect(old)
    conn.executescript(
        "DROP TRIGGER IF EXISTS segments_fts_insert;"
        "DROP TRIGGER IF EXISTS segments_fts_delete;"
        "DROP TRIGGER IF EXISTS segments_fts_update;"
        "DROP TABLE IF EXISTS segments_fts;"
        "PRAGMA user_version=4;"
    )
    conn.commit()
    conn.close()

    store3 = Store(str(old))
    found = store3.search("смету")
    assert found, "после обновления поиск не видит старые расшифровки"
    assert found[0]["meeting_id"] == kept, "нашлась не та встреча"
    print("[ok] старый архив доиндексирован при обновлении")

    print("\nПоиск идёт по расшифровкам и не отстаёт от данных.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
