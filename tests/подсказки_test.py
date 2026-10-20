# -*- coding: utf-8 -*-
"""Подсказки по ходу записи и история переписок (этап 38, первая часть).

Просьба владельца 20 октября: пока идёт запись, модель должна
подсказывать по контексту разговора, крупно, и подсказки должны
оставаться в чате. А «Очистить» в чате не должно стирать переписку
насовсем: нужна история.

Что сторожим:

1. Реплика во время записи через паузу даёт подсказку в чат с ролью
   hint, в запросе модели есть описание разговора и последние реплики.
2. Выключенные подсказки молчат; короткое «угу» не даёт подсказки.
3. Занятая модель не теряет подсказку, а переносит её.
4. «Очистить» убирает переписку в историю, оттуда её можно стереть.
5. Подсказки не попадают в историю переписки для модели.
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

import testenv  # noqa: F401  песочница вместо боевого профиля

from app.core import подсказки as подсказки_mod
from app.core.events import CHAT_MESSAGE, bus
from app.core.models import ChatMessage, Meeting, Speaker, TranscriptSegment
from app.core.service import AppService
from app.llm.prompts import chat_messages, hint_messages
from app.storage.db import Store

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


# Ждать паузу в разговоре три секунды в проверке незачем.
подсказки_mod.ПАУЗА = 0.2
подсказки_mod.ПОВТОР = 0.3


class Клиент:
    def __init__(self, ответ: str) -> None:
        self.ответ = ответ
        self.запросы: list[list[dict]] = []

    def stream(self, messages, on_chunk=None, should_stop=None, **kw):
        self.запросы.append(messages)
        if on_chunk:
            on_chunk(self.ответ)
        return self.ответ

    def complete(self, messages, **kw):
        # Прогрев на старте записи (один токен) — не подсказка, не считаем.
        if kw.get("max_tokens") != 1:
            self.запросы.append(messages)
        return self.ответ


class Модель:
    enabled = True
    backend = "local"
    tier = "fast"

    def __init__(self) -> None:
        self.клиент = Клиент("Скажите про опыт с asyncio и приведите пример из последнего проекта.")

    def client(self):
        return self.клиент

    def status(self):
        return {"model_ready": True}

    def shutdown(self):
        pass


def ждать_подсказку(секунд: float = 3.0) -> dict | None:
    итог: dict = {}
    done = threading.Event()

    def ловить(p):
        m = p.get("message") or {}
        if p.get("done") and m.get("role") == "hint":
            итог.update(m)
            done.set()

    стоп = bus.on(CHAT_MESSAGE, ловить)
    try:
        return итог if done.wait(секунд) else None
    finally:
        стоп()


def реплика(service, mid, текст, кто=Speaker.THEM, начало=0.0):
    service._on_segment(TranscriptSegment(
        meeting_id=mid, speaker=кто, text=текст, start=начало, end=начало + 5,
    ))


tmp = Path(tempfile.mkdtemp()) / "t.db"
service = AppService(store=Store(str(tmp)))
service.llm = Модель()
service._ensure_llm_model = lambda: None
try:
    mid = service.store.create_meeting(Meeting(title="Собеседование")).id

    # --- 1. Настройки доходят до подсказчика -------------------------------
    s = service.сохранить_подсказки(включены=True, контекст="Собеседование на Python, подсказывай ответы")
    проверить(s["включены"] and "Python" in s["контекст"], "настройки подсказок сохранились")
    проверить(service.подсказчик.включены and "Python" in service.подсказчик.контекст,
              "подсказчик применил настройки сразу")

    # --- 2. Реплика во время записи даёт подсказку -------------------------
    service.подсказчик.начать(mid)
    ловушка = threading.Thread(target=lambda: None)
    вопрос = "Расскажите, пожалуйста, как вы работали с асинхронным кодом и какие были сложности в последнем проекте"
    итог: dict = {}
    done = threading.Event()

    def ловить(p):
        m = p.get("message") or {}
        if p.get("done") and m.get("role") == "hint":
            итог.update(m)
            done.set()

    стоп = bus.on(CHAT_MESSAGE, ловить)
    реплика(service, mid, вопрос)
    done.wait(3)
    стоп()
    проверить(bool(итог) and "asyncio" in итог.get("text", ""), f"подсказка пришла в чат: {итог.get('text', '')[:40]}")
    в_базе = service.store.list_chat_messages(mid)
    проверить(len(в_базе) == 1 and в_базе[0].role == "hint" and "asyncio" in в_базе[0].text,
              "подсказка сохранена в переписке встречи с ролью hint")
    запрос = service.llm.клиент.запросы[-1]
    текст_запроса = "\n".join(m["content"] for m in запрос)
    проверить("Собеседование на Python" in текст_запроса, "в запросе есть описание разговора")
    проверить("Собеседник: " + вопрос in текст_запроса, "в запросе есть последние реплики с говорящим")
    проверить(not service._llm_busy, "после подсказки модель свободна")

    # --- 2б. Короткая реплика не даёт новой подсказки ----------------------
    было = len(service.llm.клиент.запросы)
    реплика(service, mid, "Угу, понятно.", начало=10)
    time.sleep(0.6)
    проверить(len(service.llm.клиент.запросы) == было, "на «угу» подсказки нет: слишком мало нового")

    # --- 2б2. Мусор распознавания не считается и не уходит модели ----------
    было = len(service.llm.клиент.запросы)
    service._on_segment(TranscriptSegment(meeting_id=mid, speaker=Speaker.ME, text="*dje*", lang="sr", start=12, end=13))
    service._on_segment(TranscriptSegment(meeting_id=mid, speaker=Speaker.ME, text="*dje se je značila*", lang="sr", start=13, end=14))
    service._on_segment(TranscriptSegment(meeting_id=mid, speaker=Speaker.ME, text="...", lang="ru", start=14, end=15))
    time.sleep(0.6)
    проверить(len(service.llm.клиент.запросы) == было, "обрывки на чужом языке и строки без букв подсказку не зовут")
    проверить("dje" not in service.подсказчик._хвост(mid), "мусор не попадает в реплики для модели")

    # --- 2б3. Свои ответы подсказку не зовут, когда есть собеседник ----------
    было = len(service.llm.клиент.запросы)
    реплика(service, mid, "Горутины это лёгкие потоки, которыми управляет планировщик Go, а не система", кто=Speaker.ME, начало=16)
    time.sleep(0.6)
    проверить(len(service.llm.клиент.запросы) == было, "свой ответ при живом собеседнике подсказку не зовёт")

    # --- 2б4. «ПРОПУСК» от модели убирает карточку и ничего не хранит --------
    service.llm.клиент.ответ = "ПРОПУСК"
    было_в_базе = len(service.store.list_chat_messages(mid))
    реплика(service, mid, "Ага, понятно, спасибо, это я и хотел услышать от вас", начало=18)
    time.sleep(1.0)
    проверить(len(service.store.list_chat_messages(mid)) == было_в_базе, "ПРОПУСК не оставляет карточки в чате")
    service.llm.клиент.ответ = "Скажите про опыт с asyncio и приведите пример из последнего проекта."

    # --- 2в. Прошлая подсказка уходит в запрос, чтобы не повторяться --------
    реплика(service, mid, "А теперь расскажите про опыт с базами данных, какие использовали и почему выбрали именно их", начало=20)
    пришла = ждать_подсказку()
    проверить(пришла is not None, "вторая подсказка пришла")
    текст_запроса = "\n".join(m["content"] for m in service.llm.клиент.запросы[-1])
    проверить("<прошлая подсказка>" in текст_запроса and "asyncio" in текст_запроса,
              "прошлая подсказка приложена к запросу")

    # --- 3. Занятая модель: подсказка переносится, а не теряется ------------
    with service._llm_lock:
        service._llm_busy = True
    было = len(service.llm.клиент.запросы)
    реплика(service, mid, "Какие у вас ожидания по зарплате и когда готовы выйти на работу, расскажите подробнее", начало=30)
    time.sleep(0.5)
    проверить(len(service.llm.клиент.запросы) == было, "пока модель занята, подсказка не считается")
    with service._llm_lock:
        service._llm_busy = False
    пришла = ждать_подсказку(2.0)
    проверить(пришла is not None and len(service.llm.клиент.запросы) == было + 1,
              "модель освободилась — подсказка посчиталась сама")

    # --- 3б. Запись остановлена: подсказок больше нет ----------------------
    service.подсказчик.остановить()
    было = len(service.llm.клиент.запросы)
    реплика(service, mid, "Спасибо за разговор, мы свяжемся с вами на следующей неделе по итогам встречи", начало=40)
    time.sleep(0.6)
    проверить(len(service.llm.клиент.запросы) == было, "после остановки записи подсказки молчат")

    # --- 3в. Выключенные подсказки молчат ----------------------------------
    service.сохранить_подсказки(включены=False)
    service.подсказчик.начать(mid)
    реплика(service, mid, "Ещё один длинный вопрос про архитектуру сервисов, очереди и обработку ошибок в проде", начало=50)
    time.sleep(0.6)
    проверить(len(service.llm.клиент.запросы) == было, "выключенные подсказки молчат")
    service.подсказчик.остановить()

    # --- 4. Подсказки не попадают в историю для модели ----------------------
    history = [{"role": m.role, "content": m.text} for m in service.store.list_chat_messages(mid)]
    msgs = chat_messages(title="x", transcript="Я: привет", history=history, question="что было?")
    проверить(not any("asyncio" in m["content"] for m in msgs[:-1]),
              "подсказки не уходят модели как переписка")

    # --- 5. История переписок вместо удаления ------------------------------
    service.store.add_chat_message(ChatMessage(meeting_id=mid, role="user", text="кто что обещал?"))
    service.store.add_chat_message(ChatMessage(meeting_id=mid, role="assistant", text="Лёвин — к пятнице."))
    service.store.add_chat_message(ChatMessage(meeting_id=mid, role="assistant", text=""))  # заготовка
    service.clear_chat(mid)
    проверить(service.store.list_chat_messages(mid) == [], "после «Очистить» открытая переписка пуста")
    история = service.chat_history(mid)
    проверить(len(история) == 1 and len(история[0]["messages"]) >= 2,
              f"переписка ушла в историю одной группой: {len(история)}")
    тексты = [m["text"] for m in история[0]["messages"]]
    проверить("Лёвин — к пятнице." in тексты and "" not in тексты,
              "в истории ответы на месте, а пустая заготовка выброшена")
    service.store.add_chat_message(ChatMessage(meeting_id=mid, role="user", text="второй разговор"))
    time.sleep(0.01)
    service.clear_chat(mid)
    проверить(len(service.chat_history(mid)) == 2, "вторая очистка — вторая группа, новая сверху")
    проверить(service.chat_history(mid)[0]["messages"][0]["text"] == "второй разговор",
              "новая группа идёт первой")
    service.forget_chat(mid, история[0]["archived_at"])
    проверить(len(service.chat_history(mid)) == 1, "стереть можно одну группу, остальные целы")

    # --- 5б. Сервер модели гасится в простое, но не во время подсказок ------
    import app.core.service as service_mod
    гашений = []
    class Сервер(Модель):
        server_running = True
        def shutdown(self):
            гашений.append(1)
    service.llm = Сервер()
    service_mod.LLM_IDLE = 0.5
    service._llm_last_use = time.time() - 1
    service.сохранить_подсказки(включены=True)
    service.подсказчик.начать(mid)
    class Захват:
        is_recording = True
    захват_был = service.capture
    service.capture = Захват()
    проверить(not service._llm_idle_check(), "во время записи с подсказками сервер не гасим")
    service.подсказчик.остановить()
    Захват.is_recording = False
    service._llm_last_use = time.time() - 1
    проверить(service._llm_idle_check() and гашений == [1], "без записи простой гасит сервер и отдаёт видеокарту")
    service._llm_last_use = time.time()
    проверить(not service._llm_idle_check(), "сразу после ответа сервер не трогаем")
    service.capture = захват_был
    service.сохранить_подсказки(включены=False)

    # --- 6. Сам запрос подсказки -------------------------------------------
    msgs = hint_messages("звонок клиенту", "Собеседник: сколько стоит доставка?", "")
    проверить(msgs[0]["role"] == "system" and "<о разговоре>" in msgs[1]["content"]
              and "<последние реплики>" in msgs[1]["content"] and "<прошлая подсказка>" not in msgs[1]["content"],
              "запрос подсказки собран: описание, реплики, без пустой прошлой")
finally:
    service.подсказчик.остановить()
    service.store.close()

if БЕДЫ:
    print(f"\nБед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("\nПодсказки идут по ходу записи и остаются в чате, а переписка не теряется.")
