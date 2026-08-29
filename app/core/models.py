"""Модели данных.

Обычные dataclass без ORM: схема маленькая, а лишняя зависимость
в бинарнике дороже, чем десяток строк ручного маппинга.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def now() -> float:
    return time.time()


class MeetingStatus(str, Enum):
    DRAFT = "draft"          # создана, запись не начата
    RECORDING = "recording"  # идёт запись
    PROCESSING = "processing"  # запись кончилась, идёт синтез
    READY = "ready"          # саммари готово


class Speaker(str, Enum):
    ME = "me"        # микрофон
    THEM = "them"    # системный звук (loopback)


@dataclass
class Meeting:
    id: str = field(default_factory=new_id)
    title: str = "Новая встреча"
    created_at: float = field(default_factory=now)
    started_at: float | None = None
    ended_at: float | None = None
    status: MeetingStatus = MeetingStatus.DRAFT
    template: str = "general"
    notes: str = ""       # сырые тезисы пользователя
    summary: str = ""     # результат синтеза (этап 4)
    audio_path: str | None = None

    @property
    def duration(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.ended_at if self.ended_at is not None else now()
        return max(0.0, end - self.started_at)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["duration"] = self.duration
        return d


@dataclass
class NoteLine:
    """Строка заметок с привязкой к моменту встречи.

    Таймстемп нужен не для красоты: на этапе синтеза он позволяет
    сопоставить тезис с тем куском транскрипта, который его породил.
    """

    id: str = field(default_factory=new_id)
    meeting_id: str = ""
    text: str = ""
    offset: float = 0.0  # секунды от начала встречи
    created_at: float = field(default_factory=now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TranscriptSegment:
    """Фрагмент распознанной речи (этап 3, схема готова заранее)."""

    id: str = field(default_factory=new_id)
    meeting_id: str = ""
    speaker: Speaker = Speaker.THEM
    text: str = ""
    start: float = 0.0
    end: float = 0.0
    lang: str = "ru"
    # Кто говорит внутри дорожки. Дорожка отвечает, откуда пришёл звук,
    # а эти поля — кто именно из людей его произнёс.
    voice_id: str = ""        # участник этой встречи
    person_id: str | None = None   # он же в базе знакомых голосов
    voice_label: str = ""     # то, что видит человек: «Я», «Собеседник 2», имя

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["speaker"] = self.speaker.value
        return d


@dataclass
class Person:
    """Знакомый голос: узнаём его между встречами.

    Вектор хранится усреднённым по всем услышанным фразам, поэтому чем
    больше человек говорил, тем устойчивее узнавание.
    """

    id: str = field(default_factory=new_id)
    name: str = ""
    kind: str = "other"        # owner — владелец программы, other — остальные
    embedding: list[float] = field(default_factory=list)
    samples: int = 1
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Вектор в UI не нужен и весит больше всей остальной записи.
        d.pop("embedding", None)
        d["dim"] = len(self.embedding)
        return d
