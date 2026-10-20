"""Подсказки по ходу разговора.

Пока идёт запись, модель читает последние реплики и говорит человеку,
что ответить, о чём спросить или что уточнить. Это первая часть
этапа 38: без базы документов, только по самому разговору и по тому,
что человек написал про него заранее («собеседование на Python,
подсказывай ответы на вопросы интервьюера»).

Как устроено:

- Реплики приходят из распознавания (service._on_segment). Каждая
  переводит будильник: подсказка считается через ПАУЗА секунд после
  последней реплики, а не на каждую, иначе модель не успевала бы.
- Считаем только когда накопилось хотя бы МИН_НОВЫХ_СЛОВ новых слов:
  на «угу» и «да» подсказывать нечего.
- Модель одна на программу. Если она занята заметками или вопросом,
  подсказка переносится, а не ждёт в очереди: через полминуты она уже
  не про то.
- Подсказка ложится в чат встречи репликой с ролью «hint»: она
  сохраняется вместе с перепиской, и после встречи видно, что
  советовала программа. В историю для модели такие реплики не идут
  (prompts.chat_messages берёт только user и assistant).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ..llm.client import LlmError
from ..llm.prompts import hint_messages
from .events import CHAT_CHUNK, CHAT_MESSAGE, bus
from .models import ChatMessage, Speaker, TranscriptSegment

log = logging.getLogger(__name__)

# Сколько ждать тишины после последней реплики. Человек договорил
# фразу, распознавание отдало её целиком, и только тогда есть смысл
# советовать. Три секунды: меньше ловит половину фразы, больше делает
# подсказку запоздалой.
ПАУЗА = 3.0
# Меньше этого новых слов с прошлой подсказки — молчим.
МИН_НОВЫХ_СЛОВ = 12
# Сколько последних знаков расшифровки отдаём модели. Примерно пять
# минут разговора: дальше контекст для ответа на текущий вопрос не
# нужен, а читать его на процессоре дорого.
ХВОСТ_ЗНАКОВ = 4000
# Если модель занята, пробуем снова через столько секунд.
ПОВТОР = 5.0


class Подсказчик:
    """Следит за репликами идущей записи и подсказывает через модель."""

    def __init__(self, service: Any) -> None:
        self._service = service
        self._lock = threading.Lock()
        self._включены = False
        self._контекст = ""
        self._meeting_id: str | None = None
        self._будильник: threading.Timer | None = None
        self._слов_на_подсказке = 0
        self._последняя = ""
        self._идёт = False

    # --- настройки и жизненный цикл --------------------------------------

    def настроить(self, включены: bool, контекст: str) -> None:
        with self._lock:
            self._включены = bool(включены)
            self._контекст = (контекст or "").strip()

    @property
    def включены(self) -> bool:
        return self._включены

    @property
    def контекст(self) -> str:
        return self._контекст

    @property
    def активен(self) -> bool:
        return self._meeting_id is not None

    def начать(self, meeting_id: str) -> None:
        """Запись пошла: с этой встречи подсказываем."""
        with self._lock:
            self._meeting_id = meeting_id
            self._слов_на_подсказке = self._слов(meeting_id)
            self._последняя = ""
            # Греем и когда галочка снята, но разговор описан: человек
            # собирается жать «Подсказать сейчас», и первая подсказка не
            # должна идти 13 секунд (проба 20.10).
            греть = self._включены or bool(self._контекст)
        if греть:
            threading.Thread(target=self._прогреть, name="hint-warmup", daemon=True).start()

    def _прогреть(self) -> None:
        """Поднять модель заранее.

        Первый запрос после запуска сервера идёт долго: на видеокарте
        около 17 секунд, пока веса лягут в память, следующие меньше
        секунды (замер 20.10). Первая подсказка встречи не должна быть
        самой медленной, поэтому модель будим на старте записи.
        """
        service = self._service
        try:
            if not service.llm.enabled or not service._can_use_model(self._meeting_id or ""):
                return
            with service._llm_lock:
                if service._llm_busy:
                    return
                service._llm_busy = True
            try:
                service._ensure_llm_model()
                service.llm.client().complete(
                    [{"role": "user", "content": "ok"}],
                    max_tokens=1, **service.опции_своей_модели(),
                )
            finally:
                with service._llm_lock:
                    service._llm_free()
        except Exception:
            log.debug("Прогрев модели для подсказок не удался", exc_info=True)

    def остановить(self) -> None:
        with self._lock:
            self._meeting_id = None
            self._отменить_будильник()

    # --- реплики ---------------------------------------------------------

    def реплика(self, segment: TranscriptSegment) -> None:
        """Новая распознанная реплика: перевести будильник."""
        with self._lock:
            if not self._включены or self._meeting_id is None:
                return
            if segment.meeting_id != self._meeting_id:
                return
            if not (segment.text or "").strip():
                return
            self._завести_будильник(ПАУЗА)

    def подсказать_сейчас(self, meeting_id: str) -> dict[str, Any]:
        """Человек нажал «подсказать»: считаем не дожидаясь паузы."""
        with self._lock:
            if self._идёт:
                return {"ok": False, "error": self._service._msg("python.chat.busy")}
            self._meeting_id = self._meeting_id or meeting_id
            self._отменить_будильник()
        return self._посчитать(meeting_id, принудительно=True)

    # --- внутренности ----------------------------------------------------

    def _завести_будильник(self, через: float) -> None:
        self._отменить_будильник()
        meeting_id = self._meeting_id
        if meeting_id is None:
            return
        будильник = threading.Timer(через, self._посчитать, args=(meeting_id,))
        будильник.daemon = True
        будильник.name = "hint"
        self._будильник = будильник
        будильник.start()

    def _отменить_будильник(self) -> None:
        будильник = self._будильник
        if будильник is not None:
            будильник.cancel()
            self._будильник = None

    def _реплики(self, meeting_id: str) -> list[tuple[str, str]]:
        """Реплики, по которым есть смысл подсказывать: (кто, текст).

        Мусор отсеиваем здесь, а не надеемся на модель: на пробе 20.10
        распознавание на секунду решило, что человек говорит по-сербски,
        отдало «*dje*», и модель всерьёз объясняла, что это за слово.
        Реплики под сомнением (человек сказал, что это был чужой ролик),
        обрывки короче пяти слов на чужом языке и строки без букв не идут.
        """
        try:
            сегменты = self._service.store.list_segments(meeting_id)
        except Exception:
            log.exception("Подсказки: не прочитать расшифровку")
            return []
        основной = getattr(getattr(self._service.settings, "asr", None), "language", "") or ""
        итог: list[tuple[str, str]] = []
        for s in сегменты:
            if getattr(s, "doubtful", False):
                continue
            текст = (s.text or "").replace("*", "").strip()
            слова = текст.split()
            if sum(ch.isalpha() for ch in текст) < 3:
                continue
            if основной and s.lang and s.lang != основной and len(слова) < 5:
                continue
            кто = s.voice_label or ("Я" if s.speaker == Speaker.ME else "Собеседник")
            итог.append((кто, текст))
        return итог

    def _слов(self, meeting_id: str) -> int:
        return sum(len(текст.split()) for _, текст in self._реплики(meeting_id))

    def _хвост(self, meeting_id: str) -> str:
        """Последние реплики, как их видит человек: кто и что сказал."""
        строки = [f"{кто}: {текст}" for кто, текст in self._реплики(meeting_id)]
        текст = "\n".join(строки)
        if len(текст) > ХВОСТ_ЗНАКОВ:
            # Режем по началу строки, чтобы не начинать с полслова.
            обрез = текст[-ХВОСТ_ЗНАКОВ:]
            перенос = обрез.find("\n")
            текст = обрез[перенос + 1:] if 0 <= перенос < 200 else обрез
        return текст

    def _посчитать(self, meeting_id: str, принудительно: bool = False) -> dict[str, Any]:
        service = self._service
        with self._lock:
            self._будильник = None
            if self._идёт:
                return {"ok": False, "error": service._msg("python.chat.busy")}
            if not принудительно and (not self._включены or self._meeting_id != meeting_id):
                return {"ok": False}
            слов = self._слов(meeting_id)
            if not принудительно and слов - self._слов_на_подсказке < МИН_НОВЫХ_СЛОВ:
                return {"ok": False}
        if not service._can_use_model(meeting_id):
            return {"ok": False, "error": service._msg("python.trial.llm"), "trial": True}
        if not service.llm.enabled:
            return {"ok": False, "error": service._msg("python.chat.disabled")}
        with service._llm_lock:
            if service._llm_busy:
                # Заметки или вопрос считаются: попробуем чуть позже, когда
                # освободится. Принудительную просьбу не переносим: человеку
                # надо сказать, что модель занята.
                if принудительно:
                    return {"ok": False, "error": service._msg("python.chat.busy")}
                with self._lock:
                    if self._meeting_id == meeting_id:
                        self._завести_будильник(ПОВТОР)
                return {"ok": False}
            service._llm_busy = True
            service._llm_cancel = False
        with self._lock:
            self._идёт = True
            self._слов_на_подсказке = слов
        хвост = self._хвост(meeting_id)
        запись = service.store.add_chat_message(
            ChatMessage(meeting_id=meeting_id, role="hint", text="")
        )
        bus.emit(CHAT_MESSAGE, {"meeting_id": meeting_id, "message": запись.to_dict()})
        поток = threading.Thread(
            target=self._ответить, args=(meeting_id, запись.id, хвост),
            name="hint-llm", daemon=True,
        )
        поток.start()
        return {"ok": True, "message_id": запись.id}

    def _ответить(self, meeting_id: str, message_id: str, хвост: str) -> None:
        service = self._service
        начало = time.monotonic()
        try:
            messages = hint_messages(
                контекст=self._контекст, хвост=хвост, прошлая=self._последняя,
            )

            def on_chunk(piece: str) -> None:
                bus.emit(CHAT_CHUNK, {"meeting_id": meeting_id,
                                      "message_id": message_id, "text": piece})

            service._ensure_llm_model()
            # Те же параметры, что у чата: без раздумий Qwen3 вслух, иначе
            # подсказка приходит через десять секунд тишины.
            # Температура ниже, чем в чате: подсказка должна быть точной,
            # а не разнообразной. С 0.7 модель на пробе 20.10 сочиняла.
            опции = {**service.опции_своей_модели(), "temperature": 0.3}
            текст = service.llm.client().stream(
                service._for_model(messages), on_chunk=on_chunk,
                should_stop=lambda: service._llm_cancel,
                max_tokens=220, **опции,
            ).strip()
            service.store.update_chat_message(message_id, текст)
            with self._lock:
                self._последняя = текст
            bus.emit(CHAT_MESSAGE, {
                "meeting_id": meeting_id,
                "message": {"id": message_id, "meeting_id": meeting_id,
                            "role": "hint", "text": текст},
                "done": True,
            })
            log.info("Подсказка за %.1f с: %d знаков", time.monotonic() - начало, len(текст))
        except LlmError as exc:
            log.warning("Подсказка не получилась: %s", exc)
            self._убрать(meeting_id, message_id)
        except Exception:
            log.exception("Подсказка упала")
            self._убрать(meeting_id, message_id)
        finally:
            with service._llm_lock:
                service._llm_free()
            with self._lock:
                self._идёт = False

    def _убрать(self, meeting_id: str, message_id: str) -> None:
        """Пустая подсказка в чате выглядит как зависшая программа."""
        try:
            self._service.store.delete_chat_message(message_id)
        except Exception:
            log.debug("Не удалось убрать пустую подсказку", exc_info=True)
        bus.emit(CHAT_MESSAGE, {
            "meeting_id": meeting_id,
            "message": {"id": message_id, "meeting_id": meeting_id, "role": "hint", "text": ""},
            "done": True,
            "removed": True,
        })
