"""Перечисление и выбор аудиоустройств.

Отдельный модуль, потому что список устройств нужен и записи, и настройкам
в UI. Здесь же прячем особенности `soundcard`: системный звук в нём — это
«микрофон» с флагом loopback, и получить его можно только по имени
устройства вывода.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# Дорожки. Их ровно две и они не сводятся в одну:
# распознавать и показывать реплики надо раздельно.
TRACK_ME = "me"       # микрофон — то, что говорю я
TRACK_THEM = "them"   # системный выход — то, что говорит собеседник

# COM, инициализированный для текущего потока. Хранить обязательно.
_com_local = threading.local()


def hold_com() -> None:
    """Поднять COM в текущем потоке и не отпускать до его конца.

    Здесь легко напороться на неочевидное. `soundcard` поднимает COM сам,
    но временным объектом, а в его деструкторе стоит `CoUninitialize`.
    Стоит сборщику мусора добраться до этого объекта, и COM в потоке гаснет,
    хотя поток ещё работает. Первый заход обычно успевает отработать, а вот
    повторный старт записи падает с 0x800401f0 «COM не инициализирован».

    Поэтому инициализируем COM сами и просто больше его не выключаем: поток
    живёт ровно столько, сколько идёт запись, и отдать COM всё равно некому.
    Вызов идемпотентен, повторные заходы в тот же поток бесплатны.
    """
    if getattr(_com_local, "ready", False):
        return
    try:
        import ctypes

        # Порядок важен. При первом импорте soundcard сам инициализирует COM
        # и считает ошибкой ответ «уже поднят», падая с 0x100000001. Поэтому
        # сначала даём ему импортироваться, и только потом поднимаем COM для
        # текущего потока.
        import soundcard  # noqa: F401

        # COINIT_MULTITHREADED: запись идёт из фоновых потоков, окно STA нам
        # тут не нужно и только мешало бы.
        hr = ctypes.windll.ole32.CoInitializeEx(None, 0x0)
    except Exception:
        # Не Windows или нет ole32: пусть soundcard разбирается сам, как раньше.
        log.debug("COM поднять не удалось", exc_info=True)
        _com_local.ready = True
        return

    # S_OK - подняли, S_FALSE - уже был поднят, RPC_E_CHANGED_MODE - поднят
    # в другом режиме. Во всех трёх случаях COM в потоке рабочий.
    if hr not in (0, 1) and hr + 2**32 != 0x80010106:
        log.warning("CoInitializeEx вернул 0x%08x", hr + 2**32 if hr < 0 else hr)
    _com_local.ready = True


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

    # Любое обращение к звуку требует COM в текущем потоке, а потоков у нас
    # много: две дорожки записи, мост UI, фоновые задачи.
    hold_com()
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
