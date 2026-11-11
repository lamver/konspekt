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

    # Папки зеркалятся и пустые; переименованная пустая папка не остаётся.
    пустая = store_a.create_folder("Пустая")
    b.забрать_новые(a.id)
    имена_b = lambda: {f["name"] for f in store_b.list_folders()}
    проверить("Ноутбук / Пустая" in имена_b(), "пустая папка источника появилась у копии")
    store_a.rename_folder(пустая["id"], "Переименованная")
    b.забрать_новые(a.id)
    проверить("Ноутбук / Переименованная" in имена_b() and "Ноутбук / Пустая" not in имена_b(),
              "переименование пустой папки доехало, старой нет")
    store_a.delete_folder(пустая["id"])
    b.забрать_новые(a.id)
    проверить("Ноутбук / Переименованная" not in имена_b() and "Ноутбук / Проект" in имена_b(),
              "удалённая пустая папка ушла, папка со встречами осталась")

    # Живая встреча: идущая запись видна копии как «обработка», растёт по
    # мере расшифровки, а звук приезжает, когда запись остановили.
    живая = Meeting(title="Прямо сейчас", status=MeetingStatus.RECORDING, started_at=2000.0)
    store_a.create_meeting(живая)
    store_a.add_segment(TranscriptSegment(meeting_id=живая.id, speaker=Speaker.ME, text="первая", start=0, end=1))
    b.забрать_новые(a.id)
    копия_живой = store_b.get_meeting(b.устройства[a.id].taken.get(живая.id, ""))
    проверить(копия_живой is not None and копия_живой.status == MeetingStatus.PROCESSING
              and [s.text for s in store_b.list_segments(копия_живой.id)] == ["первая"],
              "идущая запись приехала как «обработка» с первой репликой")
    store_a.add_segment(TranscriptSegment(meeting_id=живая.id, speaker=Speaker.THEM, text="вторая", start=1, end=2))
    b.забрать_новые(a.id)
    проверить([s.text for s in store_b.list_segments(копия_живой.id)] == ["первая", "вторая"], "реплика дописалась у копии")
    папка_ж = звук_a / живая.id; папка_ж.mkdir()
    (папка_ж / "mic-0.wav").write_bytes(os.urandom(4000))
    store_a.add_audio_chunk(живая.id, "mic", str(папка_ж / "mic-0.wav"), 0.0, 2.0)
    store_a.update_meeting(живая.id, status=MeetingStatus.READY, ended_at=2002.0)
    b.забрать_новые(a.id)
    копия_живой = store_b.get_meeting(копия_живой.id)
    проверить(копия_живой.status == MeetingStatus.READY and len(store_b.list_audio_chunks(копия_живой.id)) == 1,
              "после остановки копия готова и звук приехал")
    проверить(not store_b.find_unrescued(), "копии не попадают в спасение без реплик")

    # Вложения: с источника копии, с копии источнику, удаление у источника.
    from app.core import вложения as вл
    большое = os.urandom(5 * 1024 * 1024 + 123)
    вл_a = вл.сохранить(store_a, своя, "схема.png", большое)
    b.забрать_новые(a.id)
    вл_b = {a["id"]: a for a in store_b.list_attachments(наш)}
    проверить(вл_a["id"] in вл_b and Path(вл_b[вл_a["id"]]["path"]).read_bytes() == большое
              and вл_b[вл_a["id"]]["origin"] == a.id, "вложение приехало на копию целиком, кусками")
    моё = вл.сохранить(store_b, наш, "с копии.txt", b"privet" * 1000)
    b.отправить_заметки(наш)
    for _ in range(100):
        if store_a.get_attachment(моё["id"]):
            break
        time.sleep(0.1)
    у_a = store_a.get_attachment(моё["id"])
    проверить(у_a is not None and Path(у_a["path"]).read_bytes() == b"privet" * 1000 and у_a["origin"] == b.id,
              "вложение с копии приехало источнику")
    # Общее вложение, убранное на копии, уходит и у источника; и наоборот.
    проверить(store_b.get_attachment(моё["id"])["origin"] == a.id, "уехавшее вложение стало общим с источником")
    store_b.delete_attachment(моё["id"]); Path(моё["path"]).unlink()
    b.вложение_убрано(наш, моё["id"], a.id)
    for _ in range(50):
        if store_a.get_attachment(моё["id"]) is None:
            break
        time.sleep(0.1)
    проверить(store_a.get_attachment(моё["id"]) is None, "убранное на копии ушло у источника")
    ещё = вл.сохранить(store_b, наш, "ещё.txt", b"x" * 10)
    b.отправить_заметки(наш)
    for _ in range(50):
        if store_a.get_attachment(ещё["id"]):
            break
        time.sleep(0.1)
    a_ещё = store_a.get_attachment(ещё["id"])
    store_a.delete_attachment(ещё["id"]); Path(a_ещё["path"]).unlink()
    a.вложение_убрано(своя, ещё["id"], b.id)
    for _ in range(50):
        if store_b.get_attachment(ещё["id"]) is None:
            break
        time.sleep(0.1)
    проверить(store_b.get_attachment(ещё["id"]) is None, "убранное у источника ушло с копии по сообщению")
    моё = вл.сохранить(store_b, наш, "с копии.txt", b"privet" * 1000)
    store_a.delete_attachment(вл_a["id"]); Path(вл_a["path"]).unlink()
    сколько = b.забрать_новые(a.id)
    остались = {a["id"]: a["origin"] for a in store_b.list_attachments(наш)}
    проверить(вл_a["id"] not in остались and моё["id"] in остались,
              f"удалённое у источника ушло с копии, своё осталось (обновлено {сколько}, у B {остались}, у A {[x['id'] for x in store_a.list_attachments(своя)]}, вл_a {вл_a['id']}, моё {моё['id']})")

    # «Печатает»: набор с копии летит источнику без базы, и обратно.
    набор: list[tuple[str, str, str]] = []
    a.обмен.на_набор = lambda m, текст, кто: набор.append((m, текст, кто))
    b.печатает(наш, "печа")
    b.печатает(наш, "печатаю")
    for _ in range(50):
        if any(т == "печатаю" for _, т, _ in набор):
            break
        time.sleep(0.1)
    проверить(any((m, т) == (своя, "печатаю") for m, т, _ in набор) and набор[-1][2] == "Стол",
              f"набор с копии дошёл до источника последним текстом: {набор[-1:]}")
    проверить(store_a.get_meeting(своя).notes != "печатаю", "набор в базу не пишется")
    набор_b: list[tuple[str, str, str]] = []
    b.обмен.на_набор = lambda m, текст, кто: набор_b.append((m, текст, кто))
    a.печатает(своя, "ответ")
    for _ in range(50):
        if набор_b:
            break
        time.sleep(0.1)
    проверить(набор_b and набор_b[-1][0] == наш and набор_b[-1][1] == "ответ", f"набор с источника дошёл до копии: {набор_b[-1:]}")

    # Толчок в одну сторону: Стол Ноутбуку ничего не открыл, но толчок от Ноутбука принимает.
    store_a.create_folder("Односторонний толчок")
    a.толкнуть()
    for _ in range(100):
        if "Ноутбук / Односторонний толчок" in имена_b():
            break
        time.sleep(0.1)
    проверить("Ноутбук / Односторонний толчок" in имена_b(), "толчок доходит и когда получатель ничего не открыл в ответ")

    # Взаимная связь не отражает копии обратно: у Ноутбука нет «Стол / Ноутбук / …».
    b.открыть(a.id, "view")
    встреча_с_записью(store_b, звук_b, "Своя у Стола")
    проверить([m["title"] for m in a.встречи_у(b.id)] == ["Своя у Стола"], "Стол отдаёт только своё, копии Ноутбука не отдаёт")
    a.забрать_новые(b.id)
    имена_a = {f["name"] for f in store_a.list_folders()}
    проверить(not any(n.startswith("Стол / Ноутбук") for n in имена_a), f"копии и зеркальные папки не отдаются обратно: {sorted(имена_a)}")
    проверить(all(m.title != "Планёрка (итог)" or m.id == своя for m in store_a.list_meetings()), "своя встреча не вернулась копией")

    # Толчок: источник изменился и сам сказал копии, без опроса и кнопки.
    store_a.create_folder("Толчок")
    a.толкнуть()
    for _ in range(100):
        if "Ноутбук / Толчок" in имена_b():
            break
        time.sleep(0.1)
    проверить("Ноутбук / Толчок" in имена_b(), "папка приехала по толчку, без кнопки и опроса")

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

    # Код руководителя: многоразовый, сотрудники отдают ему встречи, он им ничего.
    код_р = a.создать_код_руководителя()["code"]
    проверить(код_р.startswith("KS2-"), "код руководителя с приставкой KS2")
    c.подключить(код_р)
    d_store, d_звук, d = компьютер("Второй сотрудник")
    try:
        d.подключить(код_р)
        проверить(c.устройства[a.id].share == "view" and c.устройства[a.id].remote_share == "none",
                  "сотрудник отдаёт руководителю, в ответ ничего")
        проверить(a.устройства[c.id].share == "none" and a.устройства[c.id].remote_share == "view"
                  and a.устройства[d.id].remote_share == "view", "руководитель видит обоих, сам не отдаёт")
        try:
            c.встречи_у(a.id)
            проверить(False, "сотруднику встречи руководителя закрыты")
        except у.Ошибка as e:
            проверить(e.код == "no_access", f"сотруднику встречи руководителя закрыты: {e.код}")
        # Живая запись у сотрудника видна руководителю в сводке.
        живая_c = Meeting(title="Звонок клиенту", status=MeetingStatus.RECORDING, started_at=3000.0)
        store_c.create_meeting(живая_c)
        a.обновить(c.id)
        сводка = a.состояние()["recording"]
        проверить(any(r["device"] == "Чужой" and r["title"] == "Звонок клиенту" for r in сводка),
                  f"в сводке руководителя видно, что сотрудник в записи: {сводка}")
        a.отозвать_код_руководителя()
        e_store, e_звук, e = компьютер("Опоздавший")
        try:
            e.подключить(код_р)
            проверить(False, "отозванный код не работает")
        except у.Ошибка as err:
            проверить(err.код == "bad_code", f"отозванный код не работает: {err.код}")
        finally:
            e.остановить()
    finally:
        d.остановить()

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
    было = len(store_b.list_meetings())
    проверить(b.забрать_новые(a.id) == 0 and len(store_b.list_meetings()) == было, "«Проверить сейчас» ничего не дублирует")

    # Доступ в ответ: Стол открывает Ноутбуку, тот видит копии как готовые встречи.
    b.открыть(a.id, "view")
    проверить(len(a.встречи_у(b.id)) == 1, "после «открыть» Ноутбук видит встречи Стола")
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
    у_a = [m["title"] for m in b2.встречи_у(a.id)]
    проверить(sorted(у_a) == ["Планёрка (итог)", "Прямо сейчас", "Чёрная"], f"и с тем же ключом снова обслуживается: {у_a}")
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
