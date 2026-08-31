"""Проверка новой версии приложения.

Запускается один раз при старте, один раз в сутки. Ничего не скачивает
и не устанавливает — только оповещает. Обновление происходит вручную,
через сайт. Автоустановка не будет никогда.

- Запрос на GitHub API только раз в сутки
- Никаких данных не отправляется, кроме версии приложения и платформы
- Можно отключить в настройках
- Не блокирует старт приложения
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from app import __version__

if TYPE_CHECKING:
    from app.core.service import AppService

log = logging.getLogger(__name__)

URL = "https://api.github.com/repos/lamver/konspekt/releases/latest"
USER_AGENT = f"Konspekt/{__version__} (+https://konspekt.app)"
CHECK_INTERVAL = 86400  # сутки
TIMEOUT = 8.0


@dataclass
class Release:
    version: str
    url: str
    published_at: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Release":
        return cls(
            version=data["tag_name"].lstrip("v"),
            url=data["html_url"],
            published_at=data["published_at"],
        )


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
        if not self._service.settings.check_updates:
            return

        last_check = self._service.settings.last_version_check
        if last_check and time.time() - last_check < CHECK_INTERVAL:
            return

        try:
            log.debug("Проверяю новую версию")
            response = httpx.get(
                URL,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()

            release = Release.from_api(data)
            log.debug("Доступна версия %s", release.version)

            if self._newer(release.version, __version__):
                bus.emit(
                    "NEW_VERSION",
                    {
                        "current": __version__,
                        "latest": release.version,
                        "url": release.url,
                    },
                )

        except Exception as e:
            log.debug("Не удалось проверить версию: %s", e)
        finally:
            self._service.settings.last_version_check = time.time()
            settings_mod.save(self._service.settings)

    @staticmethod
    def _newer(a: str, b: str) -> bool:
        """Сравнить версии вида x.y.z."""
        try:
            va = tuple(int(part) for part in a.split(".")[:3])
            vb = tuple(int(part) for part in b.split(".")[:3])
            return va > vb
        except (ValueError, TypeError):
            return False
