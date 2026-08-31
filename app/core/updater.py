"""Тихое обновление: скачать новую версию и поставить самим.

Отправлять человека на страницу релиза за установщиком — это работа,
которую программа может сделать за него: скачать, сверить контрольную
сумму, запустить установку. Наш установщик кладёт файлы в папку
пользователя и не просит прав администратора, поэтому обновление
проходит без единого окна.

Чего здесь намеренно нет:

- Тихой подмены без проверки. Файл скачан из сети, поэтому перед
  запуском сверяем SHA256 с суммой из релиза. Не совпало — выбрасываем
  и молчим: лучше остаться на старой версии, чем запустить неизвестно
  что от имени пользователя.
- Перезапуска посреди работы. Установщик закрывает программу, и если
  сделать это во время встречи, человек потеряет запись. Поэтому
  скачивание идёт фоном, а установка — при выходе или по кнопке, когда
  запись не идёт.
- Скачивания откуда угодно. Адрес собирается из имени нашего
  репозитория и номера версии, а не берётся из ответа сервера.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx

from app import __version__

from . import paths
from .events import UPDATE_STATE, bus

log = logging.getLogger(__name__)

REPO = "lamver/konspekt-releases"
DOWNLOAD = f"https://github.com/{REPO}/releases/download"
USER_AGENT = f"Konspekt/{__version__} ({platform.system()} {platform.machine()})"
TIMEOUT = 30.0

# Номер версии подставляется в адрес и в имя файла, поэтому пускаем
# только цифры с точками: испорченный latest.json не должен уводить
# загрузку в чужое место.
VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}$")


@dataclass
class Ready:
    """Скачанный и проверенный установщик, готовый к запуску."""

    version: str
    path: Path


def _updates_dir() -> Path:
    d = paths.data_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _expected_sum(version: str, name: str) -> str | None:
    """Забрать контрольную сумму установщика из файла релиза."""
    url = f"{DOWNLOAD}/v{version}/SHA256SUMS"
    r = httpx.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=TIMEOUT)
    r.raise_for_status()
    for line in r.text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


class Updater:
    """Качает обновление фоном и ставит его, когда это безопасно."""

    def __init__(self, service) -> None:
        self._service = service
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.ready: Ready | None = None

    # --- скачивание ------------------------------------------------------

    def download_later(self, version: str) -> None:
        """Начать загрузку в фоне. Повторные вызовы не плодят потоков."""
        if not VERSION_RE.match(version or ""):
            log.warning("Странный номер версии, обновление пропущено: %r", version)
            return
        with self._lock:
            if self.ready and self.ready.version == version:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._download, args=(version,), daemon=True, name="update"
            )
            self._thread.start()

    def _download(self, version: str) -> None:
        name = f"konspekt-{version}-setup.exe"
        target = _updates_dir() / name
        try:
            expected = _expected_sum(version, name)
            if not expected:
                log.warning("В релизе %s нет суммы для %s, обновление отменено", version, name)
                bus.emit(UPDATE_STATE, {"state": "error"})
                return

            # Файл мог остаться с прошлого раза: не качаем 60 МБ заново.
            if target.exists() and _sha256(target) == expected:
                self._finish(version, target)
                return

            bus.emit(UPDATE_STATE, {"state": "downloading", "percent": 0, "version": version})
            tmp = target.with_suffix(".part")
            url = f"{DOWNLOAD}/v{version}/{name}"
            done = 0
            last = -1
            with httpx.stream(
                "GET", url, headers={"User-Agent": USER_AGENT},
                follow_redirects=True, timeout=TIMEOUT,
            ) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0)
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = int(done * 100 / total)
                            # Событие на каждый кусок — это сотни вызовов
                            # в секунду через мост в браузер.
                            if pct != last:
                                last = pct
                                bus.emit(UPDATE_STATE, {
                                    "state": "downloading", "percent": pct, "version": version,
                                })

            got = _sha256(tmp)
            if got != expected:
                log.error("Сумма не сошлась (%s вместо %s), файл удалён", got, expected)
                tmp.unlink(missing_ok=True)
                bus.emit(UPDATE_STATE, {"state": "error"})
                return

            tmp.replace(target)
            self._finish(version, target)

        except Exception as e:
            log.info("Не удалось скачать обновление: %s", e)
            bus.emit(UPDATE_STATE, {"state": "error"})

    def _finish(self, version: str, path: Path) -> None:
        with self._lock:
            self.ready = Ready(version=version, path=path)
        log.info("Обновление %s скачано и проверено", version)
        bus.emit(UPDATE_STATE, {"state": "ready", "version": version})

    # --- установка -------------------------------------------------------

    def install_now(self) -> bool:
        """Запустить установку. Программа при этом закрывается.

        Во время записи не трогаем: установщик закрывает Konspekt, и
        незаконченная встреча пропала бы вместе с ним.
        """
        ready = self.ready
        if not ready or not ready.path.exists():
            return False
        # `is_recording` — метод: без вызова получился бы всегда истинный
        # объект функции, и обновление не ставилось бы никогда.
        busy = getattr(self._service, "is_recording", None)
        if callable(busy) and busy():
            log.info("Идёт запись, установка отложена")
            return False

        try:
            # /VERYSILENT — без окон, /NORESTART — не перезагружать
            # систему, /SP- — без вопроса «продолжить установку?».
            # Запускаем открепившись: установщик закроет нас самих.
            subprocess.Popen(
                [str(ready.path), "/VERYSILENT", "/SP-", "/NORESTART", "/NOCANCEL"],
                creationflags=(
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                ),
                close_fds=True,
                cwd=os.environ.get("TEMP") or str(Path.home()),
            )
            log.info("Запущена установка %s", ready.version)
            return True
        except Exception as e:
            log.error("Не удалось запустить установку: %s", e)
            return False

    def install_on_quit(self) -> None:
        """Поставить обновление при выходе из программы.

        Самый спокойный момент: человек сам закрыл окно, ничего не
        потеряется, а при следующем запуске он уже на новой версии.
        """
        if self.ready:
            self.install_now()
