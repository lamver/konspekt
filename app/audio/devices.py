"""Перечисление и выбор аудиоустройств.

Отдельный модуль, потому что список устройств нужен и записи, и настройкам
в UI. Здесь же прячем особенности `soundcard`: системный звук в нём — это
«микрофон» с флагом loopback, и получить его можно только по имени
устройства вывода.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# Дорожки. Их ровно две и они не сводятся в одну:
# распознавать и показывать реплики надо раздельно.
TRACK_ME = "me"       # микрофон — то, что говорю я
TRACK_THEM = "them"   # системный выход — то, что говорит собеседник


@dataclass(frozen=True)
class Device:
    """Устройство в виде, пригодном и для UI, и для повторного открытия."""

    id: str          # то, что кладём в настройки
    name: str        # то, что показываем человеку
    kind: str        # "mic" | "loopback"
    is_default: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "is_default": self.is_default,
        }


def _sc():
    """Импортируем soundcard лениво.

    На машине без звуковой подсистемы импорт может упасть, а приложение
    обязано подняться и остаться блокнотом даже без записи.
    """
    import soundcard as sc

    return sc


def list_microphones() -> list[Device]:
    """Настоящие микрофоны, без loopback-псевдоустройств."""
    try:
        sc = _sc()
        try:
            default_id = str(sc.default_microphone().id)
        except Exception:
            default_id = ""
        out: list[Device] = []
        for m in sc.all_microphones(include_loopback=False):
            out.append(
                Device(
                    id=str(m.id),
                    name=m.name,
                    kind="mic",
                    is_default=str(m.id) == default_id,
                )
            )
        return out
    except Exception:
        log.exception("Не удалось получить список микрофонов")
        return []


def list_speakers() -> list[Device]:
    """Устройства вывода: с них снимаем звук собеседника через loopback."""
    try:
        sc = _sc()
        try:
            default_id = str(sc.default_speaker().id)
        except Exception:
            default_id = ""
        out: list[Device] = []
        for s in sc.all_speakers():
            out.append(
                Device(
                    id=str(s.id),
                    name=s.name,
                    kind="loopback",
                    is_default=str(s.id) == default_id,
                )
            )
        return out
    except Exception:
        log.exception("Не удалось получить список устройств вывода")
        return []


def open_microphone(device_id: str | None):
    """Микрофон по id, с откатом на устройство по умолчанию."""
    sc = _sc()
    if device_id:
        try:
            return sc.get_microphone(device_id, include_loopback=False)
        except Exception:
            # Наушники отключили, а в настройках остался их id.
            log.warning("Микрофон %s недоступен, беру устройство по умолчанию", device_id)
    return sc.default_microphone()


def open_loopback(device_id: str | None):
    """Loopback устройства вывода.

    В `soundcard` системный звук снимается «микрофоном», созданным по id
    динамика с include_loopback=True.
    """
    sc = _sc()
    if device_id:
        try:
            return sc.get_microphone(device_id, include_loopback=True)
        except Exception:
            log.warning("Loopback %s недоступен, беру устройство по умолчанию", device_id)
    return sc.get_microphone(str(sc.default_speaker().id), include_loopback=True)


def describe() -> dict[str, Any]:
    """Сводка для UI и для диагностики проблем со звуком."""
    return {
        "microphones": [d.to_dict() for d in list_microphones()],
        "speakers": [d.to_dict() for d in list_speakers()],
    }
