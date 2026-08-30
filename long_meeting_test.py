"""Длинная встреча: нарезка и разбор по частям.

Полуторачасовой разговор не влезает в окно контекста, и без нарезки
сервер отвечает отказом именно на той встрече, ради которой всё
затевалось. Здесь проверяется, что этого не происходит.
"""

import tempfile
import threading
import time
from pathlib import Path

from app.core.events import SUMMARY_ERROR, SUMMARY_READY, SUMMARY_STATUS, bus
from app.core.models import Meeting, Speaker, TranscriptSegment
from app.core.service import TRANSCRIPT_BUDGET, AppService
from app.llm.chunking import estimate_tokens, fits, split_transcript
from app.llm.local import default_binary, find_model
from app.storage.db import Store


def test_chunking() -> None:
    # Короткий текст не трогаем вовсе.
    short = "Валерий: привет\nДмитрий: привет"
    assert split_transcript(short, 1000) == [short], "короткий текст зачем-то порезали"
    print("[ok] короткая встреча не режется")

    # Длинный режется, и каждый кусок влезает в бюджет.
    lines = [f"Говорящий {i % 3}: реплика номер {i} с каким-то текстом" for i in range(4000)]
    long = "\n".join(lines)
    budget = 2000
    parts = split_transcript(long, budget)
    assert len(parts) > 1, "длинный текст не порезался"
    for p in parts:
        assert fits(p, budget), f"кусок не влез в бюджет: {estimate_tokens(p)} токенов"
    print(f"[ok] длинная встреча порезана на {len(parts)} частей, все влезают")

    # Ни одна реплика не потерялась и не разорвалась.
    assert "\n".join(parts) == long, "нарезка потеряла или испортила текст"
    print("[ok] нарезка не теряет ни одной реплики")

    # Реплика длиннее бюджета целиком: человек говорил без пауз.
    monster = "Валерий: " + ("слово " * 20000)
    parts = split_transcript(monster, 500)
    assert len(parts) > 1, "гигантская реплика не порезалась"
    for p in parts:
        assert fits(p, 500), "кусок гигантской реплики не влез"
    print("[ok] реплика длиннее окна режется по словам, а не теряется")


def test_real_meeting() -> int:
    if find_model() is None or not default_binary().exists():
        print("[skip] нет модели или движка, разбор не проверен")
        return 0

    # Настоящая длинная встреча из боевой базы: синтетика не показала бы,
    # как ведёт себя живая расшифровка с обрывками и повторами.
    prod = AppService(store=Store())
    try:
        meetings = prod.store.list_meetings()
        source = max(meetings, key=lambda m: len(prod.transcript_text(m.id)), default=None)
        transcript = prod.transcript_text(source.id) if source else ""
    finally:
        prod.shutdown()

    tokens = estimate_tokens(transcript)
    print(f"самая длинная встреча в базе: {tokens} токенов")
    if tokens <= TRANSCRIPT_BUDGET:
        print("[skip] в базе нет встречи длиннее окна контекста")
        return 0

    tmp = Path(tempfile.mkdtemp()) / "t.db"
    service = AppService(store=Store(str(tmp)))
    try:
        meeting = service.store.create_meeting(Meeting(title="Длинная встреча"))
        mid = meeting.id
        for i, line in enumerate(transcript.split("\n")):
            who, _, text = line.partition(": ")
            service.store.add_segment(TranscriptSegment(
                meeting_id=mid, speaker=Speaker.THEM, text=text or line,
                start=float(i * 5), end=float(i * 5 + 4), voice_label=who,
            ))

        statuses: list[str] = []
        done = threading.Event()
        result: dict = {}
        stops = [
            bus.on(SUMMARY_STATUS, lambda p: statuses.append(p["text"])),
            bus.on(SUMMARY_READY, lambda p: (result.update(p), done.set())),
            bus.on(SUMMARY_ERROR, lambda p: (result.update(p), done.set())),
        ]

        t0 = time.time()
        assert service.generate_summary(mid).get("ok"), "разбор не запустился"
        assert done.wait(1800), "разбор длинной встречи не завершился"
        for stop in stops:
            stop()

        assert "error" not in result, f"разбор упал: {result.get('error')}"
        summary = (result.get("summary") or "").strip()
        assert summary, "саммари длинной встречи пустое"
        assert statuses, "человеку не показали, что идёт разбор по частям"
        print(f"[ok] длинная встреча разобрана за {time.time() - t0:.0f}с, "
              f"этапов {len(statuses)}")
        print(f"     первый этап: {statuses[0]}")
        print("-" * 60)
        print(summary[:1200])
        print("-" * 60)

        # Чат по той же встрече тоже не должен упираться в окно.
        answer_done = threading.Event()
        chat: dict = {}
        stop_msg = bus.on('chat.message', lambda p: (
            chat.update(p.get("message") or {}), answer_done.set()
        ) if p.get("done") else None)
        stop_err = bus.on('chat.error', lambda p: (chat.update(p), answer_done.set()))

        assert service.ask(mid, "О чём договорились в конце?").get("ok")
        assert answer_done.wait(600), "ответа по длинной встрече не дождались"
        stop_msg()
        stop_err()
        assert "error" not in chat, f"чат по длинной встрече упал: {chat.get('error')}"
        assert (chat.get("text") or "").strip(), "ответ по длинной встрече пустой"
        print("[ok] чат по длинной встрече отвечает, а не упирается в окно")

        return 0
    finally:
        service.shutdown()


def main() -> int:
    test_chunking()
    return test_real_meeting()


if __name__ == "__main__":
    raise SystemExit(main())
