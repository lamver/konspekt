"""Саммари и чат через AppService на настоящей локальной модели.

Проверка сквозная: от встречи в базе до текста, который увидит человек.
Без модели или движка тихо выходит.
"""

import testenv  # noqa: F401  русский вывод в консоли Windows

import tempfile
import threading
import time
from pathlib import Path

from app.core.events import (
    CHAT_CHUNK,
    CHAT_ERROR,
    CHAT_MESSAGE,
    SUMMARY_CHUNK,
    SUMMARY_ERROR,
    SUMMARY_READY,
    bus,
)
from app.core.models import Meeting, Speaker, TranscriptSegment
from app.core.service import AppService
from app.llm.local import default_binary, find_model
from app.storage.db import Store

REPLICAS = [
    (Speaker.ME, "Валерий", "Давайте решим по срокам, я предлагаю выкатить билд к пятнице."),
    (Speaker.THEM, "Дмитрий", "К пятнице не успею, у меня не закрыта задача с удалением данных."),
    (Speaker.ME, "Валерий", "Тогда перенесём на понедельник. Дмитрий, возьмёшь удаление на себя?"),
    (Speaker.THEM, "Дмитрий", "Да, беру, к понедельнику сделаю."),
    (Speaker.ME, "Валерий", "И неплохо бы когда-нибудь заняться поиском, но это не сейчас."),
    (Speaker.THEM, "Дмитрий", "Ещё непонятно, что делать с антивирусами, они ругаются на сборку."),
]


def _section(summary: str, name: str) -> str:
    """Достать раздел саммари по заголовку.

    Разбор нарочно грубый: модель ставит заголовки то жирными, то с
    двоеточием, и точный формат нам здесь неважен.
    """
    lines = summary.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        head = line.strip().strip("*# ").lower()
        if head.startswith(name.lower()):
            inside = True
            out.append(line.split("—", 1)[-1] if "—" in line else "")
            continue
        if inside and line.strip().startswith("**") and not head.startswith(name.lower()):
            break
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def main() -> int:
    if find_model() is None or not default_binary().exists():
        print("[skip] нет модели или движка, проверка пропущена")
        return 0

    # Своя база: боевую трогать нельзя, а встречу надо создать целиком.
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    service = AppService(store=Store(str(tmp)))
    try:
        meeting = service.store.create_meeting(Meeting(title="Планёрка по релизу"))
        mid = meeting.id
        service.store.update_meeting(mid, notes="решить по срокам билда")
        for i, (track, who, text) in enumerate(REPLICAS):
            service.store.add_segment(TranscriptSegment(
                meeting_id=mid, speaker=track, text=text,
                start=float(i * 10), end=float(i * 10 + 8), voice_label=who,
            ))

        transcript = service.transcript_text(mid)
        assert "Дмитрий: Да, беру" in transcript, f"расшифровка собралась не так:\n{transcript}"
        print("[ok] расшифровка собирается с именами говорящих")

        # --- саммари ------------------------------------------------------
        chunks: list[str] = []
        done = threading.Event()
        result: dict = {}

        stops = [
            bus.on(SUMMARY_CHUNK, lambda p: chunks.append(p["text"])),
            bus.on(SUMMARY_READY, lambda p: (result.update(p), done.set())),
            bus.on(SUMMARY_ERROR, lambda p: (result.update(p), done.set())),
        ]

        t0 = time.time()
        started = service.generate_summary(mid)
        assert started.get("ok"), f"синтез не запустился: {started}"

        # Пока модель занята, второй запрос должен получить отказ, а не
        # встать в очередь: локальный сервер от этого только тормозит.
        busy = service.generate_summary(mid)
        assert not busy.get("ok"), "второй запрос прошёл при занятой модели"
        print("[ok] параллельный запрос отклонён")

        assert done.wait(300), "саммари не дождались"
        for stop in stops:
            stop()

        assert "error" not in result, f"саммари упало: {result.get('error')}"
        summary = result.get("summary", "")
        assert summary.strip(), "саммари пустое"
        assert len(chunks) > 1, "текст пришёл одним куском, стриминг не работает"
        print(f"[ok] саммари за {time.time() - t0:.1f}с, кусков {len(chunks)}")

        saved = service.store.get_meeting(mid)
        assert saved.summary == summary.strip(), "саммари не сохранилось в базу"
        print("[ok] саммари сохранено во встрече")

        # Раздел задач не должен подбирать то, что никто на себя не брал.
        # Маленькие модели охотно делают задачу из «неплохо бы» и вешают
        # её на того, кто уже согласился на что-то другое.
        tasks = _section(summary, "Задачи")
        if tasks:
            for ghost in ("поиск", "антивирус"):
                assert ghost not in tasks.lower(), \
                    f"в задачи попало то, что никто не брал ({ghost}):\n{tasks}"
            # Проверяем суть, а не формулировку: модель одинаково честно
            # пишет «удалить данные», «убрать данные» и «зачистка данных».
            # Придираться к слову значит ловить не ошибку, а синоним.
            low = tasks.lower()
            assert "данн" in low, f"настоящая задача потерялась:\n{tasks}"
            assert "дмитрий" in low, f"задача осталась без исполнителя:\n{tasks}"
            print("[ok] в задачах только то, что человек взял на себя")

        print("-" * 60)
        print(summary.strip())
        print("-" * 60)

        # --- чат ----------------------------------------------------------
        answers: list[str] = []
        finished = threading.Event()
        chat_result: dict = {}

        def on_message(p):
            msg = p.get("message") or {}
            if p.get("done"):
                chat_result.update(msg)
                finished.set()

        stops = [
            bus.on(CHAT_CHUNK, lambda p: answers.append(p["text"])),
            bus.on(CHAT_MESSAGE, on_message),
            bus.on(CHAT_ERROR, lambda p: (chat_result.update(p), finished.set())),
        ]

        t0 = time.time()
        asked = service.ask(mid, "Кто отвечает за удаление данных и к какому сроку?")
        assert asked.get("ok"), f"вопрос не ушёл: {asked}"

        assert finished.wait(300), "ответа не дождались"
        for stop in stops:
            stop()

        assert "error" not in chat_result, f"чат упал: {chat_result.get('error')}"
        answer = chat_result.get("text", "")
        assert answer.strip(), "ответ пустой"
        assert len(answers) > 1, "ответ пришёл одним куском"
        print(f"[ok] ответ за {time.time() - t0:.1f}с, кусков {len(answers)}")

        low = answer.lower()
        assert "дмитрий" in low, f"модель не нашла исполнителя:\n{answer}"
        assert "понедельник" in low, f"модель не нашла срок:\n{answer}"
        print("[ok] ответ опирается на расшифровку: назван и человек, и срок")
        print("-" * 60)
        print(answer.strip())
        print("-" * 60)

        history = service.list_chat_messages(mid)
        assert [m["role"] for m in history] == ["user", "assistant"], \
            f"история чата собралась не так: {history}"
        assert history[1]["text"].strip() == answer.strip(), "ответ не лёг в базу"
        print("[ok] переписка сохранена и переживёт перезапуск")

        # Пустой вопрос не должен создавать записей в истории.
        assert not service.ask(mid, "   ").get("ok"), "пустой вопрос приняли"
        assert len(service.list_chat_messages(mid)) == 2, "пустой вопрос попал в историю"
        print("[ok] пустой вопрос отклоняется")

        проверить_длинную_встречу(service)

        print("\nСаммари и чат работают от базы до текста.")
        return 0
    finally:
        service.shutdown()


def проверить_длинную_встречу(service) -> None:
    """Часовая встреча не влезает в окно контекста и идёт по частям.

    Из-за чего написано. Разбиение по частям — самое хрупкое место
    синтеза: обычная встреча до него не доходит, поэтому поломка вылезет
    только у человека с длинной записью, когда чинить уже поздно. Здесь
    расшифровка нарочно длиннее окна, и проверяется, что заметки всё
    равно выходят и опираются на сказанное.
    """
    from app.core.events import SUMMARY_STATUS

    meeting = service.store.create_meeting(Meeting(title="Долгая планёрка"))
    mid = meeting.id

    # Наполнитель, чтобы перевалить за окно контекста, и одна приметная
    # реплика в самом конце: если разбиение теряет хвост, мы это увидим.
    # Реплики разные: одинаковые склеиваются при сборке расшифровки, и
    # набрать нужную длину повторами не выйдет.
    реплики = [
        (
            Speaker.THEM,
            "Дмитрий",
            f"Пункт {n}: посмотрели загрузку команды и текущие задачи, "
            f"ничего нового не решили, вернёмся к этому позже.",
        )
        for n in range(600)
    ]
    реплики.append(
        (Speaker.ME, "Валерий", "И самое главное: сервер переезжает в четверг.")
    )
    for i, (дорожка, кто, текст) in enumerate(реплики):
        service.store.add_segment(TranscriptSegment(
            meeting_id=mid, speaker=дорожка, text=текст,
            start=float(i * 10), end=float(i * 10 + 8), voice_label=кто,
        ))

    расшифровка = service.transcript_text(mid)
    from app.core.service import TRANSCRIPT_BUDGET
    from app.llm.chunking import fits

    assert not fits(расшифровка, TRANSCRIPT_BUDGET), \
        "встреча вышла короткой, разбиение по частям не сработает"

    состояния: list[str] = []
    готово = threading.Event()
    итог: dict = {}
    стоп = [
        bus.on(SUMMARY_STATUS, lambda p: состояния.append(p["text"])),
        bus.on(SUMMARY_READY, lambda p: (итог.update(p), готово.set())),
        bus.on(SUMMARY_ERROR, lambda p: (итог.update(p), готово.set())),
    ]
    t0 = time.time()
    assert service.generate_summary(mid).get("ok"), "синтез длинной встречи не запустился"
    assert готово.wait(900), "длинная встреча не досчиталась"
    for s in стоп:
        s()

    assert "error" not in итог, f"длинная встреча упала: {итог.get('error')}"
    заметки = итог.get("summary", "")
    assert заметки.strip(), "по длинной встрече заметки пустые"
    print(f"[ok] длинная встреча ({len(расшифровка)} символов) разобрана за {time.time() - t0:.1f}с")

    assert состояния, "человеку не сказали, что встреча длинная и идёт по частям"
    print(f"[ok] показан ход разбора: {состояния[0]}")

    assert "четверг" in заметки.lower(), \
        f"хвост встречи потерялся при разбиении:\n{заметки}"
    print("[ok] сказанное в самом конце не потерялось")


if __name__ == "__main__":
    raise SystemExit(main())
