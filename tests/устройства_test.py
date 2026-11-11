"""Другой компьютер: два Конспекта на одной машине обмениваются встречами.

Проверяем то, что бьёт по человеку:
- код сопряжения одноразовый, а чужой секрет и чужое устройство ничего
  не получают, даже список встреч;
- тот, кто создал код, открывает свои встречи; тот, кто ввёл, в ответ
  ничего не открывает, пока не поставит галочку «и получить доступ»;
- встреча забирается копией целиком: расшифровка, заметки, итоги и
  записи, и ложится в папку с именем того компьютера;
- вторая копия той же встречи не заводится;
- имена файлов записей не выводят за папку встречи.

Сеть настоящая (iroh без ретрансляторов, прямые адреса на этой машине),
базы во временных папках.
"""
from __future__ import annotations

import testenv  # noqa: F401

import os
import tempfile
import time
import wave
from pathlib import Path

os.environ["KONSPEKT_P2P_MINIMAL"] = "1"

from app.core import устройства as у  # noqa: E402
from app.core.models import Meeting, MeetingStatus, NoteLine, Speaker, TranscriptSegment  # noqa: E402
from app.storage.db import Store  # noqa: E402

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ok] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


def компьютер(имя: str):
    корень = Path(tempfile.mkdtemp(prefix=f"konspekt-{имя}-"))
    store = Store(str(корень / "konspekt.db"))
    звук = корень / "audio"
    звук.mkdir()
    обмен = у.Обмен(store, звук)
    узел = у.Узел(обмен, data_dir=корень, имя=имя, минимально=True)
    узел.запустить()
    return store, звук, узел


def встреча_с_записью(store: Store, звук: Path, название: str) -> str:
    m = Meeting(title=название, status=MeetingStatus.READY, summary="Итоги: всё хорошо",
                started_at=1000.0, ended_at=1060.0, language="ru")
    store.create_meeting(m)
    store.add_segment(TranscriptSegment(meeting_id=m.id, speaker=Speaker.ME, text="Привет", start=0, end=1, voice_label="Я"))
    store.add_segment(TranscriptSegment(meeting_id=m.id, speaker=Speaker.THEM, text="Здравствуйте", start=1, end=2, voice_label="Собеседник"))
    store.add_note_line(NoteLine(meeting_id=m.id, text="тезис", offset=3.0))
    папка = звук / m.id
    папка.mkdir()
    путь = папка / "mic-0.wav"
    with wave.open(str(путь), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(os.urandom(16000 * 2 * 70))  # 70 секунд, больше одного куска нет, но файл не крошечный
    store.add_audio_chunk(m.id, "mic", str(путь), 0.0, 70.0)
    return m.id


store_a, звук_a, a = компьютер("Ноутбук")
store_b, звук_b, b = компьютер("Стол")
store_c, звук_c, c = компьютер("Чужой")
try:
    проверить(a.состояние()["running"] and b.состояние()["running"], "оба узла поднялись")
    своя = встреча_с_записью(store_a, звук_a, "Планёрка")
    встреча_с_записью(store_a, звук_a, "Чёрная")
    черновик = Meeting(title="Черновик", status=MeetingStatus.DRAFT)
    store_a.create_meeting(черновик)

    код = a.создать_код(взаимно=False)
    проверить(код.startswith("KS1-") and код.count("-") == 2, f"код выглядит как KS1-билет-секрет ({len(код)} знаков)")

    # Чужой с неверным секретом.
    билет, _ = у.разобрать_код(код)
    try:
        c.подключить(f"KS1-{билет}-neverno")
        проверить(False, "чужой секрет отвергнут")
    except у.Ошибка as e:
        проверить(e.код == "bad_code", f"чужой секрет отвергнут: {e.код}")

    итог = b.подключить(код)
    проверить(итог["name"] == "Ноутбук" and итог["remote_share"] == "view" and итог["share"] == "none",
              "Стол сопряжён: Ноутбук открыт ему, он Ноутбуку ничего не открыл")
    try:
        c.подключить(код)
        проверить(False, "код одноразовый")
    except у.Ошибка as e:
        проверить(e.код == "bad_code", f"код одноразовый: {e.код}")

    # Автозабор: после сопряжения встречи приезжают сами, без кнопки.
    for _ in range(100):
        if len(store_b.list_meetings()) >= 2:
            break
        time.sleep(0.1)
    проверить(len(store_b.list_meetings()) == 2, "после сопряжения обе готовые встречи приехали сами")
    список = b.встречи_у(a.id)
    проверить([m["title"] for m in список] == ["Чёрная", "Планёрка"] or sorted(m["title"] for m in список) == ["Планёрка", "Чёрная"],
              f"видны только готовые встречи: {[m['title'] for m in список]}")
    try:
        a.встречи_у(b.id)
        проверить(False, "Ноутбуку у Стола ничего не открыто")
    except у.Ошибка as e:
        проверить(e.код == "no_access", f"Ноутбуку у Стола ничего не открыто: {e.код}")

    наш = b.забрать(a.id, своя)  # уже забрана автозабором: вернёт ту же копию
    копия = store_b.get_meeting(наш)
    сегменты = store_b.list_segments(наш)
    записи = store_b.list_audio_chunks(наш)
    проверить(копия is not None and копия.title == "Планёрка" and копия.summary == "Итоги: всё хорошо"
              and копия.status == MeetingStatus.READY, "копия встречи с итогами")
    проверить([s.text for s in сегменты] == ["Привет", "Здравствуйте"] and сегменты[0].speaker == Speaker.ME,
              "расшифровка приехала целиком")
    проверить([n.text for n in store_b.list_note_lines(наш)] == ["тезис"], "заметки приехали")
    проверить(store_b.trial_used(0.5, 10) == 0, "копии не идут в счёт пробного периода")
    исходник = звук_a / своя / "mic-0.wav"
    проверить(len(записи) == 1 and Path(записи[0]["path"]).read_bytes() == исходник.read_bytes()
              and Path(записи[0]["path"]).parent == звук_b / наш, "запись скопирована байт в байт в папку копии")
    папки = {f["id"]: f["name"] for f in store_b.list_folders()}
    проверить(папки.get(store_b.meeting_folders().get(наш)) == "Ноутбук", "копия лежит в папке «Ноутбук»")
    проверить(b.забрать(a.id, своя) == наш and len(store_b.list_meetings()) == 2, "вторая копия не заводится")

    # Зеркало: у источника переименовали, дописали, перенесли в папку.
    проект = store_a.create_folder("Проект")
    store_a.update_meeting(своя, title="Планёрка (итог)", summary="Итоги: решили всё")
    store_a.add_segment(TranscriptSegment(meeting_id=своя, speaker=Speaker.ME, text="Добавка", start=2, end=3))
    store_a.set_meeting_folder(своя, проект["id"])
    проверить(b.забрать_новые(a.id) == 1, "изменённая встреча догнана как одна")
    копия = store_b.get_meeting(наш)
    папки_b = {f["id"]: f["name"] for f in store_b.list_folders()}
    проверить(копия.title == "Планёрка (итог)" and копия.summary == "Итоги: решили всё", "название и итоги догнали источник")
    проверить([s.text for s in store_b.list_segments(наш)] == ["Привет", "Здравствуйте", "Добавка"], "расшифровка дописалась")
    проверить(папки_b.get(store_b.meeting_folders().get(наш)) == "Ноутбук / Проект", "копия переехала в «Ноутбук / Проект»")
    проверить(len(store_b.list_audio_chunks(наш)) == 1, "запись не скачана второй раз")
    проверить(b.забрать_новые(a.id) == 0, "без изменений ничего не гоняем")

    # Заметки в обе стороны: с копии на источник сразу, строки сливаются.
    store_b.update_meeting(наш, notes="тест заметки")
    store_b.add_note_line(NoteLine(meeting_id=наш, text="строка с копии", offset=5.0))
    проверить(b.отправить_заметки(наш), "заметки копии поставлены к отправке")
    for _ in range(100):
        if store_a.get_meeting(своя).notes == "тест заметки":
            break
        time.sleep(0.1)
    проверить(store_a.get_meeting(своя).notes == "тест заметки", "текст заметок с копии на источнике")
    проверить("строка с копии" in [n.text for n in store_a.list_note_lines(своя)], "строка с копии на источнике")
    store_a.add_note_line(NoteLine(meeting_id=своя, text="строка источника", offset=6.0))
    проверить(b.забрать_новые(a.id) == 1, "источник изменился, копия догоняет")
    проверить(sorted(n.text for n in store_b.list_note_lines(наш)) == ["строка источника", "строка с копии", "тезис"],
              "у копии строки обеих сторон")
    проверить(store_b.get_meeting(наш).notes == "тест заметки", "текст заметок у копии не затёрт")
    проверить(b.отправить_заметки("нет-такой") is False, "заметки чужой встречи никуда не шлём")

    # Путь наружу.
    try:
        b._выполнить(b._вызвать(a.id, "audio", {"id": своя, "name": "..\\..\\konspekt.db", "offset": 0, "size": 100}))
        проверить(False, "имя файла с .. отвергнуто")
    except у.Ошибка as e:
        проверить(e.код == "not_found", f"имя файла с .. отвергнуто: {e.код}")

    for плохое in ("..", ".. ", "...", " a.wav", "a/b.wav", "a" + chr(92) + "b.wav", "c:a.wav", "", "." * 3 + " "):
        проверить(not у._безопасное_имя(плохое), f"имя файла {плохое!r} отвергнуто")
    проверить(у._безопасное_имя("mic-0.wav") and у._безопасное_имя("встреча 2.flac"), "обычные имена проходят")

    # «Забрать новые» берёт оставшуюся.
    проверить(b.забрать_новые(a.id) == 0 and len(store_b.list_meetings()) == 2, "«Проверить сейчас» ничего не дублирует")

    # Доступ в ответ: Стол открывает Ноутбуку, тот видит копии как готовые встречи.
    b.открыть(a.id, "view")
    проверить(len(a.встречи_у(b.id)) == 2, "после «открыть» Ноутбук видит встречи Стола")
    a.отвязать(b.id)
    try:
        a.встречи_у(b.id)
        проверить(False, "после отвязки запросы не идут")
    except у.Ошибка as e:
        проверить(e.код == "not_paired", f"после отвязки запросы не идут: {e.код}")
    try:
        b.встречи_у(a.id)
        проверить(False, "отвязанного не обслуживают")
    except у.Ошибка as e:
        проверить(e.код in ("not_paired", "offline"), f"отвязанного не обслуживают: {e.код}")

    # Взаимный код.
    код2 = a.создать_код(взаимно=True)
    итог2 = b.подключить(код2)
    проверить(итог2["share"] == "view" and a.состояние()["devices"][0]["remote_share"] == "view",
              "взаимный код открывает обе стороны")
    # Перезапуск узла: пары и ключ живут в файлах.
    data_dir = b.data_dir
    b.остановить()
    b2 = у.Узел(у.Обмен(store_b, звук_b), data_dir=data_dir, имя="Стол", минимально=True)
    b2.запустить()
    проверить(b2.id != "" and list(b2.устройства) == [a.id], "после перезапуска устройство помнит пару")
    проверить(len(b2.встречи_у(a.id)) == 2, "и с тем же ключом снова обслуживается")
    b2.остановить()
finally:
    for узел in (a, c):
        узел.остановить()
    try:
        b.остановить()
    except Exception:
        pass

print("БЕД:", len(БЕДЫ))
raise SystemExit(1 if БЕДЫ else 0)
