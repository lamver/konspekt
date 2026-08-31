"""Проверка новой версии приложения.

Смотрит `latest.json` в публичном репозитории релизов. Ничего не
скачивает и не устанавливает, только оповещает: обновление ставится
руками. Тихой подмены бинарника не будет никогда.

Почему публичный репозиторий, а не GitHub API приватного: Releases
приватного репозитория отдаются только по токену, а класть в
дистрибутив токен с доступом к исходникам нельзя.

- Запрос не чаще раза в сутки
- Наружу не уходит ничего, кроме версии и платформы в User-Agent
- Отключается в настройках
- Старт приложения не задерживает: работа идёт в фоновом потоке
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from app import __version__

from . import settings as settings_mod
from .events import NEW_VERSION, bus

if TYPE_CHECKING:
    from app.core.service import AppService

log = logging.getLogger(__name__)

# Адрес можно подменить локальным, чтобы проверить обновление на живой
# сборке, а не на пользователях. Обычный запуск переменной не видит.
URL = os.environ.get(
    "KONSPEKT_LATEST_URL",
    "https://raw.githubusercontent.com/lamver/konspekt-releases/master/latest.json",
)
USER_AGENT = f"Konspekt/{__version__} ({platform.system()} {platform.machine()})"
CHECK_INTERVAL = 86400  # сутки
TIMEOUT = 8.0


@dataclass
class Release:
    version: str
    url: str
    notes: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Release":
        return cls(
            version=str(data["version"]).lstrip("v"),
            url=str(data.get("url", "")),
            notes=str(data.get("notes", "")),
        )


def fetch_latest() -> Release:
    """Забрать `latest.json`. Бросает исключение, если не вышло.

    Отдельно от `_check`, потому что кнопка «Проверить сейчас» в
    настройках должна отдать результат в ответ на нажатие, а не через
    событие: человек стоит и ждёт.
    """
    response = httpx.get(
        URL,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return Release.from_json(response.json())


class VersionChecker:
    def __init__(self, service: "AppService") -> None:
        self._service = service
        self._thread: threading.Thread | None = None

    def check_later(self) -> None:
        """Запустить проверку в фоне, не блокируя старт."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._check, daemon=True, name="version")
        self._thread.start()

    def _check(self) -> None:
        settings = self._service.settings
        if not settings.check_updates:
            return

        last = settings.last_version_check
        if last and 0 < time.time() - last < CHECK_INTERVAL:
            return

        try:
            log.debug("Проверяю новую версию")
            release = fetch_latest()

            if is_newer(release.version, __version__):
                log.info("Доступна версия %s", release.version)
                bus.emit(
                    NEW_VERSION,
                    {
                        "current": __version__,
                        "latest": release.version,
                        "url": release.url,
                        "notes": release.notes,
                    },
                )
            else:
                log.debug("Установлена свежая версия %s", __version__)

        except Exception as e:
            # Нет сети или репозиторий недоступен — это не повод шуметь.
            log.debug("Не удалось проверить версию: %s", e)
        finally:
            settings.last_version_check = time.time()
            settings_mod.save(settings)


def is_newer(candidate: str, current: str) -> bool:
    """Больше ли `candidate` чем `current`.

    Версии вида `x.y.z`, хвост после третьего числа игнорируется. Любой
    мусор в ответе означает «новой версии нет»: лучше промолчать, чем
    звать обновляться непонятно куда.
    """
    try:
        a = _parts(candidate)
        b = _parts(current)
    except (ValueError, TypeError, AttributeError):
        return False
    return a > b


def _parts(version: str) -> tuple[int, int, int]:
    numbers = [int(part) for part in version.strip().lstrip("v").split(".")[:3]]
    while len(numbers) < 3:
        numbers.append(0)
    return numbers[0], numbers[1], numbers[2]
