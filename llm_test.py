"""Проверка связки с локальной моделью через llama.cpp.

Не запускается в общем прогоне: требует скачанных весов и движка.
Если их нет, тихо выходит.
"""

import time

from app.llm import LlmClient, summary_messages
from app.llm.local import LocalServer, default_binary, find_model

TRANSCRIPT = """Валерий: Давайте решим по срокам. Я предлагаю выкатить билд к пятнице.
Дмитрий: К пятнице не успею, у меня ещё не закрыта задача с удалением данных.
Валерий: Тогда перенесём на понедельник. Дмитрий, возьмёшь удаление на себя?
Дмитрий: Да, беру. К понедельнику сделаю.
Валерий: Хорошо. И неплохо бы когда-нибудь заняться поиском, но это не сейчас.
Дмитрий: Согласен. Ещё непонятно, что делать с антивирусами, они ругаются на сборку.
Валерий: Это открытый вопрос, вернёмся к нему позже."""


def main() -> int:
    model = find_model()
    if model is None or not default_binary().exists():
        print("[skip] нет модели или движка, проверка пропущена")
        return 0

    print(f"модель: {model.name}")
    server = LocalServer(model)
    t0 = time.time()
    base_url = server.ensure_started()
    print(f"[ok] сервер поднялся за {time.time() - t0:.1f}с: {base_url}")

    try:
        client = LlmClient(base_url=base_url, model="local")

        check = client.check()
        assert check["ok"], f"проверка соединения провалилась: {check}"
        print(f"[ok] соединение проверено, модели: {check['models']}")

        messages = summary_messages(
            title="Планёрка по релизу",
            transcript=TRANSCRIPT,
            notes="решить по срокам билда",
        )

        chunks: list[str] = []
        t0 = time.time()
        text = client.stream(messages, on_chunk=chunks.append, max_tokens=600)
        elapsed = time.time() - t0

        assert text.strip(), "модель вернула пустое саммари"
        assert len(chunks) > 1, "поток пришёл одним куском, стриминг не работает"
        print(f"[ok] саммари получено за {elapsed:.1f}с, {len(chunks)} кусков")
        print("-" * 60)
        print(text.strip())
        print("-" * 60)
        return 0
    finally:
        server.stop()
        print("[ok] сервер остановлен")


if __name__ == "__main__":
    raise SystemExit(main())
