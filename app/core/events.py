"""Простая синхронная шина событий.

Нужна, чтобы аудио- и ASR-подсистемы из фоновых потоков могли
толкать апдейты в UI, ничего про UI не зная.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.RLock()

    def on(self, topic: str, handler: Handler) -> Callable[[], None]:
        with self._lock:
            self._handlers[topic].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._handlers[topic]:
                    self._handlers[topic].remove(handler)

        return unsubscribe

    def emit(self, topic: str, payload: dict[str, Any] | None = None) -> None:
        with self._lock:
            handlers = list(self._handlers[topic]) + list(self._handlers["*"])
        data = payload or {}
        for handler in handlers:
            try:
                handler({"topic": topic, **data})
            except Exception:
                # Один упавший подписчик не должен ронять остальных.
                log.exception("Ошибка в обработчике события %s", topic)


# Имена событий собраны в одном месте, чтобы не ловить опечатки в строках.
MEETINGS_CHANGED = "meetings.changed"
MEETING_UPDATED = "meeting.updated"
RECORDING_STARTED = "recording.started"
RECORDING_STOPPED = "recording.stopped"
RECORDING_LEVEL = "recording.level"
RECORDING_ERROR = "recording.error"
# Дорожка открылась, но звука в ней нет. Это предупреждение, а не
# обрыв записи: запись идёт дальше, и путать его с ошибкой старта
# нельзя — окно от такой ошибки считает, что запись кончилась, и
# кнопка перестаёт слушаться, хотя встреча всё ещё пишется.
RECORDING_SILENT = "recording.silent"
# В системном звуке слышен разговор. Не ошибка и не повод что-то менять
# молча: программа пишет системный звук всегда, и человек мог не знать,
# что в расшифровку уезжает ролик из соседней вкладки. Спрашиваем один
# раз за запись, решает человек.
RECORDING_FOREIGN_SPEECH = "recording.foreign_speech"
# На компьютере идёт разговор, а запись не включена вовсе. Программа
# висит в трее, человек открыл Zoom и забыл нажать кнопку: это самая
# обидная потеря встречи, потому что вспоминают о ней в конце. Ничего
# не пишем, просто предлагаем начать.
SYSTEM_SPEECH_NOTICED = "system.speech_noticed"
TRANSCRIPT_SEGMENT = "transcript.segment"
# Черновик речи, которая ещё идёт: показать и заменить настоящей.
TRANSCRIPT_DRAFT = "transcript.draft"
MODEL_DOWNLOAD = "model.download"
# Импорт готовых записей: список файлов целиком и прогресс по одному.
IMPORT_CHANGED = "import.changed"
IMPORT_PROGRESS = "import.progress"
SUMMARY_READY = "summary.ready"
# Саммари и ответы чата приходят по кускам: ждать минуту на пустом
# экране невыносимо, а текст, который печатается на глазах, ощущается
# быстрым даже при той же общей задержке.
SUMMARY_CHUNK = "summary.chunk"
# Длинная встреча разбирается по частям, и это минуты молчания.
# Сообщаем словами, что происходит, иначе это читается как зависание.
SUMMARY_STATUS = "summary.status"
SUMMARY_ERROR = "summary.error"
# Веса модели приезжают по требованию, как и веса распознавания.
LLM_DOWNLOAD = "llm.download"
CHAT_CHUNK = "chat.chunk"
CHAT_MESSAGE = "chat.message"
CHAT_ERROR = "chat.error"
WINDOW_SHOW = "window.show"
WINDOW_HIDE = "window.hide"
WINDOW_TOGGLE = "window.toggle"
APP_QUIT = "app.quit"
# Вышла свежая сборка.
NEW_VERSION = "app.new_version"
# Ход тихого обновления: качаем, готово к установке, не вышло.
UPDATE_STATE = "app.update_state"
# Докат распознавания: живая очередь не успела за какими-то кусками
# речи, и после «стоп» они досчитываются по записанному звуку отдельно.
RECOGNITION_BACKFILL = "recognition.backfill"

bus = EventBus()
